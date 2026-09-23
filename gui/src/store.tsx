/**
 * App-wide state: the engine client, its `state`, live device polling, the console log and the
 * currently running action (with progress). Screens read this through `useApp()`.
 */
import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
  type ReactNode,
} from "react";
import { EngineClient, asEngineError } from "./engine";
import { EngineError as EngineErrorCtor, type Devices, type EngineError, type EngineEvent, type EngineState, type LogLevel } from "./types";

export interface ConsoleLine {
  n: number;
  ts: number;
  level: LogLevel;
  text: string;
}

export interface Activity {
  cmd: string;
  label: string;
  pct: number | null; // null = indeterminate
  stage: string | null;
  lastLog: string;
}

export interface RunOptions {
  label: string;
  /** Progress events whose `stage` is not in this list are ignored (defaults to all). */
  stages?: string[];
  onData?: (key: string, value: unknown) => void;
}

interface AppContext {
  client: EngineClient;
  booted: boolean;
  bootError: EngineError | null;
  retryBoot: () => void;
  state: EngineState | null;
  refreshState: () => Promise<EngineState | null>;
  devices: Devices | null;
  setPolling: (on: boolean) => void;
  activity: Activity | null;
  run: <T = Record<string, unknown>>(cmd: string, args: Record<string, unknown>, opts: RunOptions) => Promise<T>;
  lines: ConsoleLine[];
  consoleOpen: boolean;
  setConsoleOpen: (v: boolean) => void;
}

const Ctx = createContext<AppContext | null>(null);
const MAX_LINES = 600;

export function AppProvider({ children }: { children: ReactNode }) {
  const client = useMemo(() => new EngineClient(), []);
  const [booted, setBooted] = useState(false);
  const [bootError, setBootError] = useState<EngineError | null>(null);
  const [bootAttempt, setBootAttempt] = useState(0);
  const [state, setState] = useState<EngineState | null>(null);
  const [devices, setDevices] = useState<Devices | null>(null);
  const [polling, setPolling] = useState(false);
  const [activity, setActivity] = useState<Activity | null>(null);
  const [lines, setLines] = useState<ConsoleLine[]>([]);
  const [consoleOpen, setConsoleOpen] = useState(false);
  const lineNo = useRef(0);
  const activityRef = useRef<Activity | null>(null);

  const pushLine = useCallback((level: LogLevel, text: string) => {
    lineNo.current += 1;
    const line: ConsoleLine = { n: lineNo.current, ts: Date.now(), level, text };
    setLines((prev) => (prev.length >= MAX_LINES ? [...prev.slice(prev.length - MAX_LINES + 1), line] : [...prev, line]));
  }, []);

  // Console mirror of every event; progress is folded into the activity instead.
  useEffect(() => {
    return client.onEvent((e: EngineEvent) => {
      if (e.type === "log") {
        pushLine(e.level, e.text);
        if (activityRef.current && e.level !== "debug") {
          const next = { ...activityRef.current, lastLog: e.text.trim() };
          activityRef.current = next;
          setActivity(next);
        }
      } else if (e.type === "error" && e.id === null) {
        pushLine("error", `${e.message}${e.hint ? ` — ${e.hint}` : ""}`);
      } else if (e.type === "hello") {
        pushLine("info", `engine ${e.version} ready, work folder ${e.work}`);
      }
    });
  }, [client, pushLine]);

  const refreshState = useCallback(async () => {
    try {
      const s = await client.request<EngineState>("state");
      setState(s);
      return s;
    } catch (e) {
      pushLine("error", asEngineError(e).message);
      return null;
    }
  }, [client, pushLine]);

  // Boot the engine once (and again on retry).
  useEffect(() => {
    let cancelled = false;
    setBooted(false);
    setBootError(null);
    (async () => {
      try {
        await client.start();
        if (cancelled) return;
        await refreshState();
        setBooted(true);
      } catch (e) {
        if (!cancelled) setBootError(asEngineError(e));
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [client, refreshState, bootAttempt]);

  useEffect(() => {
    const onUnload = () => {
      void client.stop();
    };
    window.addEventListener("beforeunload", onUnload);
    return () => window.removeEventListener("beforeunload", onUnload);
  }, [client]);

  // Device polling: every 3s while a screen asks for it. The engine avoids touching the iPhone
  // during iPhone actions, so polling is always safe to leave on.
  useEffect(() => {
    if (!booted || !polling) return;
    let stopped = false;
    let timer: ReturnType<typeof setTimeout> | undefined;
    const tick = async () => {
      try {
        const d = await client.request<Devices>("devices");
        if (!stopped) setDevices(d);
      } catch {
        /* engine busy or down; keep last value */
      }
      if (!stopped) timer = setTimeout(tick, 3000);
    };
    void tick();
    return () => {
      stopped = true;
      if (timer) clearTimeout(timer);
    };
  }, [client, booted, polling]);

  const run = useCallback(
    async <T,>(cmd: string, args: Record<string, unknown>, opts: RunOptions): Promise<T> => {
      if (activityRef.current) {
        throw new EngineErrorCtor("busy", `Wait for “${activityRef.current.label}” to finish first.`);
      }
      const act: Activity = { cmd, label: opts.label, pct: null, stage: null, lastLog: "" };
      activityRef.current = act;
      setActivity(act);
      try {
        const result = await client.request<T>(cmd, args, (e) => {
          if (e.type === "progress") {
            if (opts.stages && !opts.stages.includes(e.stage)) return;
            const next = { ...(activityRef.current ?? act), pct: e.pct, stage: e.stage };
            activityRef.current = next;
            setActivity(next);
          } else if (e.type === "data") {
            opts.onData?.(e.key, e.value);
          }
        });
        return result;
      } finally {
        activityRef.current = null;
        setActivity(null);
        void refreshState();
      }
    },
    [client, refreshState]
  );

  const value: AppContext = {
    client,
    booted,
    bootError,
    retryBoot: () => setBootAttempt((n) => n + 1),
    state,
    refreshState,
    devices,
    setPolling,
    activity,
    run,
    lines,
    consoleOpen,
    setConsoleOpen,
  };
  return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
}

export function useApp(): AppContext {
  const v = useContext(Ctx);
  if (!v) throw new Error("useApp outside AppProvider");
  return v;
}

export function formatBytes(n: number | null | undefined): string {
  if (n === null || n === undefined) return "unknown size";
  if (n >= 1e9) return `${(n / 1e9).toFixed(1)} GB`;
  if (n >= 1e6) return `${Math.round(n / 1e6)} MB`;
  if (n >= 1e3) return `${Math.round(n / 1e3)} kB`;
  return `${n} B`;
}

export function formatCount(n: number): string {
  return n.toLocaleString();
}
