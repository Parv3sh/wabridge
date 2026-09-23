/**
 * Client for the Python engine sidecar (`wabridge serve`).
 *
 * One long-lived process per app session. Requests are JSON lines on stdin; every response
 * event carries the request id so several requests can be in flight (the engine itself only
 * allows one *heavy* action at a time and answers `busy` otherwise).
 *
 * Outside the Tauri window (plain browser via `npm run dev`) development builds talk to the
 * scripted mock in `dev/mockEngine.ts` instead, so screens can be rendered without phones.
 */
import { Command } from "@tauri-apps/plugin-shell";
import { appDataDir, join } from "@tauri-apps/api/path";
import { EngineError, type EngineEvent, type ErrorEvent, type ResultEvent } from "./types";

type Listener = (e: EngineEvent) => void;

interface Pending {
  resolve: (data: Record<string, unknown>) => void;
  reject: (err: EngineError) => void;
  onEvent?: Listener;
}

/** What the client needs from a running engine: Tauri's `Child` or the browser mock. */
interface EngineProcess {
  write(data: string): Promise<void>;
  kill(): Promise<void>;
}

/** True inside the Tauri window; false when the frontend is opened in a plain browser. */
export const IN_TAURI = typeof window !== "undefined" && "__TAURI_INTERNALS__" in window;

const SIDECAR = "binaries/wabridge-engine";
const HELLO_TIMEOUT_MS = 30_000;

export class EngineClient {
  private child: EngineProcess | null = null;
  private seq = 0;
  private pending = new Map<string, Pending>();
  private listeners = new Set<Listener>();
  private alive = false;
  private starting: Promise<void> | null = null;
  private stopping: Promise<void> | null = null;
  private generation = 0; // bumped per spawn so a late "close" from an old engine cannot clobber the new one
  private expectingExit = false;
  private buffer = "";
  workDir = "";
  version = "";

  onEvent(fn: Listener): () => void {
    this.listeners.add(fn);
    return () => this.listeners.delete(fn);
  }

  get running(): boolean {
    return this.alive;
  }

  /** Idempotent: concurrent callers share one spawn. A start issued after a stop() — even one that
   *  is still waiting for the previous spawn to finish — waits for that stop and then spawns afresh,
   *  so React StrictMode's mount → unmount → mount never ends up talking to a dying engine. */
  start(): Promise<void> {
    if (this.starting) return this.starting;
    if (this.alive && !this.stopping) return Promise.resolve();
    const afterStop = this.stopping ? this.stopping.catch(() => undefined) : Promise.resolve();
    const p = afterStop
      .then(() => this.doStart())
      .finally(() => {
        if (this.starting === p) this.starting = null;
      });
    this.starting = p;
    return p;
  }

  private async doStart(): Promise<void> {
    this.expectingExit = false;
    this.buffer = "";
    const gen = ++this.generation;
    // An engine that dies before saying hello must fail the boot at once, not after the timeout.
    let failHello: ((e: EngineError) => void) | null = null;

    const onClose = (code: number | null) => {
      // The close event of a process arrives asynchronously; after stop()+start() it can land once
      // the next engine is already running. That exit was handled by stop() — ignore it here.
      if (gen !== this.generation) return;
      const expected = this.expectingExit;
      this.alive = false;
      this.child = null;
      const err = new EngineError(
        "engine_exit",
        expected ? "The engine has stopped." : `The engine stopped unexpectedly (exit code ${code ?? "?"}).`,
        expected ? undefined : "Open the console for details, then restart WaBridge."
      );
      for (const p of this.pending.values()) p.reject(err);
      this.pending.clear();
      failHello?.(err);
      if (!expected) this.emit({ type: "error", id: null, code: err.code, message: err.message, hint: err.hint });
    };

    // Listen for hello *before* spawning so a fast engine can't slip past us.
    const hello = new Promise<void>((resolve, reject) => {
      const timer = setTimeout(() => {
        off();
        reject(
          new EngineError(
            "engine_timeout",
            `The engine did not start within ${HELLO_TIMEOUT_MS / 1000} seconds.`,
            "If this is the first launch, the engine may still be unpacking; try again. The console shows its output."
          )
        );
      }, HELLO_TIMEOUT_MS);
      const off = this.onEvent((e) => {
        if (e.type === "hello") {
          clearTimeout(timer);
          off();
          failHello = null;
          this.version = e.version;
          resolve();
        }
      });
      failHello = (err) => {
        clearTimeout(timer);
        off();
        failHello = null;
        reject(err);
      };
    });

    try {
      this.child = IN_TAURI ? await this.spawnSidecar(onClose) : await this.spawnBrowserMock(onClose);
      this.alive = true;
      await hello;
    } catch (e) {
      // Leave nothing running behind a failed start, so "Try again" really retries.
      this.expectingExit = true;
      try {
        await this.child?.kill();
      } catch {
        /* already gone */
      }
      this.child = null;
      this.alive = false;
      throw asEngineError(e);
    }
  }

  private async spawnSidecar(onClose: (code: number | null) => void): Promise<EngineProcess> {
    this.workDir = await join(await appDataDir(), "work");
    const cmd = Command.sidecar(SIDECAR, ["serve", "--work", this.workDir]);
    cmd.stdout.on("data", (chunk: string) => this.onChunk(chunk));
    cmd.stderr.on("data", (line: string) => {
      const text = String(line).trimEnd();
      if (text) this.emit({ type: "log", id: null, level: "debug", text });
    });
    cmd.on("close", (data: { code: number | null; signal: number | null }) => onClose(data.code));
    cmd.on("error", (msg: string) => {
      this.emit({ type: "log", id: null, level: "error", text: String(msg) });
    });
    return cmd.spawn();
  }

  /** Plain browser: development builds get the scripted mock; production pages get a clear error. */
  private async spawnBrowserMock(onClose: (code: number | null) => void): Promise<EngineProcess> {
    if (!import.meta.env.DEV) {
      throw new EngineError("no_shell", "WaBridge has to run inside its desktop window.", "Open the WaBridge app rather than this page.");
    }
    const { spawnMockEngine } = await import("./dev/mockEngine");
    this.workDir = "/mock/work";
    this.emit({
      type: "log",
      id: null,
      level: "warning",
      text: "Browser preview: this is the scripted mock engine, not a real one. Add ?mock=android,iphone to the URL to change the scene (see src/dev/mockEngine.ts).",
    });
    return spawnMockEngine({ onLine: (line) => this.onChunk(line), onClose });
  }

  /** Stops the engine. If a start is still spawning it, the stop waits for that spawn (so nothing is
   *  orphaned) and detaches it: the next start() will not join it but wait for this stop instead. */
  stop(): Promise<void> {
    if (this.stopping) return this.stopping;
    const pendingStart = this.starting;
    this.starting = null;
    const p = (pendingStart ? pendingStart.catch(() => undefined) : Promise.resolve())
      .then(() => this.doStop())
      .finally(() => {
        if (this.stopping === p) this.stopping = null;
      });
    this.stopping = p;
    return p;
  }

  private async doStop(): Promise<void> {
    const child = this.child;
    this.alive = false; // requests fail fast from here on
    this.expectingExit = true;
    if (!child) return;
    try {
      await child.write(JSON.stringify({ id: "bye", cmd: "shutdown", args: {} }) + "\n");
    } catch {
      /* ignore */
    }
    try {
      await child.kill();
    } catch {
      /* already gone */
    }
    this.child = null;
  }

  /** Send a command; resolves with `result.data`, rejects with EngineError. */
  request<T = Record<string, unknown>>(cmd: string, args: Record<string, unknown> = {}, onEvent?: Listener): Promise<T> {
    if (!this.child || !this.alive) {
      return Promise.reject(new EngineError("engine_down", "The engine is not running.", "Restart WaBridge."));
    }
    const id = String(++this.seq);
    return new Promise<T>((resolve, reject) => {
      this.pending.set(id, { resolve: (d) => resolve(d as T), reject, onEvent });
      this.write({ id, cmd, args }).catch((e: unknown) => {
        this.pending.delete(id);
        reject(new EngineError("engine_write", String(e)));
      });
    });
  }

  private async write(obj: unknown): Promise<void> {
    if (!this.child) throw new Error("no engine process");
    await this.child.write(JSON.stringify(obj) + "\n");
  }

  /** The shell plugin delivers one line per event today; buffer anyway so chunked delivery is fine. */
  private onChunk(chunk: string): void {
    this.buffer += String(chunk);
    let nl = this.buffer.indexOf("\n");
    while (nl !== -1) {
      const line = this.buffer.slice(0, nl);
      this.buffer = this.buffer.slice(nl + 1);
      this.onLine(line);
      nl = this.buffer.indexOf("\n");
    }
    // A complete JSON object with no trailing newline (last line before exit) still counts.
    if (this.buffer.trim().startsWith("{") && this.buffer.trim().endsWith("}")) {
      const line = this.buffer;
      this.buffer = "";
      this.onLine(line);
    }
  }

  private onLine(raw: string): void {
    const line = raw.trim();
    if (!line) return;
    let ev: EngineEvent;
    try {
      ev = JSON.parse(line) as EngineEvent;
    } catch {
      this.emit({ type: "log", id: null, level: "debug", text: line });
      return;
    }
    this.emit(ev);
    if (ev.type === "result" || ev.type === "error") {
      const id = ev.id;
      const p = id ? this.pending.get(id) : undefined;
      if (p && id) {
        this.pending.delete(id);
        if (ev.type === "result") p.resolve((ev as ResultEvent).data);
        else {
          const er = ev as ErrorEvent;
          p.reject(new EngineError(er.code, er.message, er.hint));
        }
      }
    } else if ("id" in ev && ev.id) {
      this.pending.get(ev.id)?.onEvent?.(ev);
    }
  }

  private emit(e: EngineEvent): void {
    for (const fn of this.listeners) {
      try {
        fn(e);
      } catch {
        /* listener errors must not break the stream */
      }
    }
  }
}

export function asEngineError(e: unknown): EngineError {
  if (e instanceof EngineError) return e;
  return new EngineError("internal", e instanceof Error ? e.message : String(e));
}

/** Open a URL in the system browser without leaving an unhandled rejection behind. */
export async function openExternal(url: string): Promise<void> {
  try {
    const { openUrl } = await import("@tauri-apps/plugin-opener");
    await openUrl(url);
  } catch {
    /* the URL is also shown in the UI copy; nothing else to do */
  }
}
