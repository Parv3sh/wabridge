/**
 * Browser stand-in for the Python engine — development builds only.
 *
 * The desktop app spawns `wabridge serve` as a Tauri sidecar. When the same frontend is opened in
 * a plain browser (`npm run dev` → http://localhost:1420) there is no sidecar, so `engine.ts`
 * loads this module instead. It speaks the same JSON-lines protocol from a scripted world, which
 * lets every screen be rendered, walked and screenshotted without phones (`npm run screenshots`).
 *
 * Choose the world with URL flags, comma-separated:  http://localhost:1420/?mock=android,iphone
 *
 *   no-adb                Android tools missing (Start offers to install them)
 *   low-disk              8 GB free on this computer
 *   resume-android        a previous session already decrypted the Android chats
 *   resume-iphone         … and backed up the iPhone
 *   android               Android phone connected, end-to-end encrypted backup present
 *   android-unauthorized  phone plugged in, USB-debugging prompt not yet accepted
 *   android-no-backup     phone connected, WhatsApp has no crypt15 backup yet
 *   android-no-whatsapp   phone connected, WhatsApp not installed
 *   wrong-key             the key never decrypts the backup
 *   iphone                iPhone connected, unencrypted backups, 11.4 GB used
 *   iphone-encrypted      iPhone connected, backup encryption on
 *   iphone-big            iPhone uses more space than this computer has free
 *   find-my               restore fails because Find My iPhone is on
 *   crash                 the engine exits right after starting
 *
 * `speed=<ms>` sets the delay per progress step (default 40; 0 for instant).
 *
 * Keep the shapes in step with `src/wabridge/serve.py` and `../types.ts` — this is a mock, not a
 * second implementation: it only knows enough to drive the screens.
 */
import type { AndroidDevice, IPhoneInfo, Stages } from "../types";

export interface MockProcess {
  write(data: string): Promise<void>;
  kill(): Promise<void>;
}

interface Handlers {
  onLine(line: string): void;
  onClose(code: number | null): void;
}

const HEAVY = new Set([
  "android.fetch", "android.pull_media", "ios.disable_encryption", "ios.backup", "convert",
  "inject", "ios.restore", "ios.rollback", "clean", "adb.install",
]);
const GB = 1e9;
const WORK = "/Users/you/Library/Application Support/dev.wabridge.desktop/work";
const ADB = `${WORK}/tools/platform-tools/adb`;
const UDID = "00008110-000A1B2C3D4E5F67";
const NO_IPHONE = "No iPhone found over USB. Unlock it, plug it in, and tap 'Trust'.";
const NO_ADB = "adb not found. Install Android platform-tools (https://developer.android.com/tools/releases/platform-tools) and add it to PATH.";

interface World {
  flags: Set<string>;
  tick: number;
  adb: string | null;
  free: number;
  stages: Stages;
  android: AndroidDevice[];
  iphone: IPhoneInfo | null;
}

class Fail extends Error {
  constructor(
    public code: string,
    message: string,
    public hint?: string
  ) {
    super(message);
  }
}

export function buildWorld(params: URLSearchParams): World {
  const flags = new Set(
    (params.get("mock") ?? "")
      .split(",")
      .map((s) => s.trim())
      .filter(Boolean)
  );
  const has = (f: string) => flags.has(f);
  const speed = params.get("speed");

  const stages: Stages = {
    android_decrypt: has("resume-android") || has("resume-iphone"),
    media: false,
    media_skipped: false,
    ios_backup: has("resume-iphone"),
    convert: false,
    convert_media: false,
    inject: false,
    ios_restore: false,
  };

  const androidReady = has("android") || has("android-no-backup") || has("android-no-whatsapp") || has("wrong-key");
  const android: AndroidDevice[] = has("android-unauthorized")
    ? [{ serial: "R5CT1234ABC", state: "unauthorized", model: null }]
    : androidReady
      ? [{ serial: "R5CT1234ABC", state: "device", model: "SM_F936B" }]
      : [];

  const iphoneReady = has("iphone") || has("iphone-encrypted") || has("iphone-big");
  const used = has("iphone-big") ? 61.2 * GB : 11.4 * GB;
  const iphone: IPhoneInfo | null = iphoneReady
    ? {
        udid: UDID,
        name: "Sam’s iPhone",
        ios_version: "27.2",
        will_encrypt: has("iphone-encrypted"),
        disk_capacity: 128 * GB,
        disk_available: 128 * GB - used,
        disk_used: used,
      }
    : null;

  return {
    flags,
    tick: speed === null ? 40 : Math.max(0, Number(speed) || 0),
    adb: has("no-adb") ? null : ADB,
    free: has("low-disk") ? 8.2 * GB : 58 * GB,
    stages,
    android,
    iphone,
  };
}

function summary() {
  return {
    stats: { chats: 192, groups: 22, messages: 57816, media: 15459 },
    top_chats: [
      { name: "Family", messages: 9412, is_group: true, named: true },
      { name: "Alice Nguyen", messages: 7230, is_group: false, named: true },
      { name: "Weekend hikers", messages: 5108, is_group: true, named: true },
      { name: "Ben Okafor", messages: 4877, is_group: false, named: true },
      { name: "+61 4•• ••• 221", messages: 3902, is_group: false, named: false },
      { name: "Book club", messages: 2650, is_group: true, named: true },
      { name: "Chloe Martin", messages: 2211, is_group: false, named: true },
      { name: "Flat 12", messages: 1980, is_group: true, named: true },
      { name: "Dev Patel", messages: 1744, is_group: false, named: true },
      { name: "Mum", messages: 1602, is_group: false, named: true },
      { name: "Five-a-side", messages: 1419, is_group: true, named: true },
      { name: "Erin Walsh", messages: 1288, is_group: false, named: true },
    ],
    unnamed_chats: 14,
    media_bytes: 3.2 * GB,
  };
}

export function spawnMockEngine(h: Handlers, params = new URLSearchParams(window.location.search)): MockProcess {
  const w = buildWorld(params);
  let busy = false;
  let closed = false;

  const send = (obj: unknown) => {
    if (!closed) h.onLine(JSON.stringify(obj) + "\n");
  };
  const close = (code: number | null) => {
    if (closed) return;
    closed = true;
    setTimeout(() => h.onClose(code), 0);
  };
  const sleep = (ms: number) => (ms > 0 ? new Promise<void>((r) => setTimeout(r, ms)) : Promise.resolve());
  const noIphone = () => new Fail("no_iphone", NO_IPHONE, "Unlock the iPhone, plug it in, tap Trust.");

  async function progress(id: string, stage: string, steps: number, logs: string[] = []): Promise<void> {
    for (let i = 0; i <= steps; i++) {
      send({ id, type: "progress", stage, pct: Math.round((100 * i) / steps) });
      logs.forEach((text, k) => {
        if (i === Math.round(((k + 1) * steps) / (logs.length + 1))) send({ id, type: "log", level: "info", text });
      });
      await sleep(w.tick);
    }
  }

  async function dispatch(id: string, cmd: string, args: Record<string, unknown>): Promise<Record<string, unknown>> {
    const log = (text: string) => send({ id, type: "log", level: "info", text });
    switch (cmd) {
      case "ping":
        return { version: "0.1.0 (mock)", python: "browser", platform: "mock", work: WORK, pid: 4242 };

      case "state":
        return {
          version: "0.1.0 (mock)",
          work: WORK,
          log_path: `${WORK}/engine.log`,
          free_bytes: w.free,
          adb: w.adb,
          stages: { ...w.stages },
          raw: {},
        };

      case "devices":
        return {
          android: w.adb ? w.android : [],
          android_error: w.adb ? null : NO_ADB,
          iphone: w.iphone,
          iphone_error: w.iphone ? null : NO_IPHONE,
          adb: w.adb,
        };

      case "android.check": {
        const phone = w.android.find((d) => d.state === "device");
        if (!phone) throw new Fail("no_android", "No Android device connected.", "Enable USB debugging and plug the phone in.");
        if (w.flags.has("android-no-whatsapp")) throw new Fail("no_whatsapp", "Could not find WhatsApp on the phone.");
        return {
          serial: phone.serial,
          model: phone.model,
          root: "/sdcard/Android/media/com.whatsapp/WhatsApp",
          has_crypt15: !w.flags.has("android-no-backup"),
          media_bytes: 3.2 * GB,
        };
      }

      case "android.fetch": {
        const key = String(args.key ?? "").replace(/[\s\-:]/g, "");
        if (!/^[0-9a-fA-F]{64}$/.test(key)) {
          throw new Fail("bad_key", "Key must be 64 hex digits.", "Paste the 64 digits WhatsApp showed you; spaces are fine.");
        }
        log("→ Copying the encrypted database from the phone …");
        await sleep(w.tick * 15);
        log("→ Decrypting the backup …");
        await sleep(w.tick * 15);
        if (w.flags.has("wrong-key")) {
          throw new Fail(
            "wrong_key",
            "The key doesn’t open this backup.",
            "Either the key was copied wrongly, or the backup on the phone is older than the key. Check the key, or in WhatsApp go to ⋮ › Settings › Chats › Chat backup, tap Back up, wait for it to finish, then try again."
          );
        }
        log("  192 chats, 57,816 messages");
        log("  read 412 contact numbers for names");
        await sleep(w.tick * 10);
        w.stages.android_decrypt = true;
        return summary();
      }

      case "android.inspect":
        if (!w.stages.android_decrypt) throw new Fail("pipeline", "No decrypted database yet.");
        return summary();

      case "android.pull_media":
        log("→ Copying photos, videos and voice notes (this is the slow part) …");
        await progress(id, "android_media", 50, ["  WhatsApp Images …", "  WhatsApp Video …", "  WhatsApp Voice Notes …"]);
        w.stages.media = true;
        return { pulled: ["Media/WhatsApp Images", "Media/WhatsApp Video", "Media/WhatsApp Voice Notes"], bytes: 3.2 * GB };

      case "ios.info":
        if (!w.iphone) throw noIphone();
        return { ...w.iphone };

      case "ios.disable_encryption":
        if (!w.iphone) throw noIphone();
        log("→ Turning off backup encryption (enter the passcode on the iPhone if asked) …");
        await sleep(w.tick * 20);
        if (!args.password) {
          throw new Fail("encrypted_backup", "Backup encryption is still on.", "Check the password, or untick 'Encrypt local backup' in Finder.");
        }
        w.iphone = { ...w.iphone, will_encrypt: false };
        return { ...w.iphone };

      case "ios.backup": {
        if (!w.iphone) throw noIphone();
        if (w.iphone.will_encrypt) throw new Fail("encrypted_backup", "Backup encryption is on for this iPhone.");
        const used = w.iphone.disk_used ?? 0;
        if (!args.force && w.free < used * 1.05) {
          throw new Fail(
            "disk_space",
            `The iPhone holds about ${(used / GB).toFixed(1)} GB but this computer has only ${(w.free / GB).toFixed(1)} GB free.`,
            "Free up space — empty the Trash, clear Downloads, delete old iPhone backups — then try again."
          );
        }
        log("→ Backing up the iPhone (enter the passcode on the iPhone if asked) …");
        await progress(id, "ios_backup", 60, ["  receiving files …", "  snapshot …"]);
        log("  kept a pristine copy as the rollback point");
        w.stages.ios_backup = true;
        return { folder: `${WORK}/ios_backup/${UDID}`, pristine: `${WORK}/ios_backup_pristine/${UDID}` };
      }

      case "convert": {
        const media = !!args.media;
        log("→ Reading the Android archive …");
        await sleep(w.tick * 10);
        log("→ Writing 192 chats into ChatStorage.sqlite …");
        await sleep(w.tick * 25);
        w.stages.convert = true;
        w.stages.convert_media = media;
        return {
          sessions_created: 188,
          sessions_reused: 4,
          messages_written: 57816,
          messages_skipped_duplicate: 12,
          media_linked: media ? 7130 : 0,
          media_missing: media ? 8329 : 0,
          warnings: [],
          media,
        };
      }

      case "inject":
        log("→ Adding the chats (and any media) to the iPhone backup …");
        await progress(id, "inject", 20);
        w.stages.inject = true;
        return { media_files: w.stages.convert_media ? 7130 : 0 };

      case "ios.restore":
        if (!w.iphone) throw noIphone();
        log("→ Restoring the backup (the iPhone restarts when this finishes) …");
        if (w.flags.has("find-my")) {
          await progress(id, "ios_restore", 3);
          throw new Fail(
            "find_my",
            "The iPhone refused the restore because Find My iPhone is on.",
            "Nothing on the iPhone has changed. On the iPhone: Settings › your name › Find My › Find My iPhone › off (it asks for your Apple ID password). Then try again."
          );
        }
        await progress(id, "ios_restore", 40, ["  sending files …", "  the iPhone is applying the backup …"]);
        w.stages.ios_restore = true;
        return { system: !!args.system };

      case "ios.rollback":
        log("→ Restoring the untouched backup …");
        await progress(id, "ios_rollback", 40);
        return { system: !!args.system };

      case "clean":
        await sleep(w.tick * 10);
        for (const k of Object.keys(w.stages) as (keyof Stages)[]) w.stages[k] = false;
        log("  deleted 31.2 GB of migration data");
        return { freed_bytes: 31.2 * GB };

      case "adb.install":
        log("→ Downloading Android platform-tools from dl.google.com …");
        await progress(id, "adb_install", 30);
        w.adb = ADB;
        return { adb: w.adb };

      default:
        throw new Fail("unknown_cmd", `unknown cmd ${cmd}`);
    }
  }

  async function handle(id: string, cmd: string, args: Record<string, unknown>): Promise<void> {
    const heavy = HEAVY.has(cmd);
    if (heavy && busy) {
      send({ id, type: "error", code: "busy", message: "Another step is still running." });
      return;
    }
    if (heavy) busy = true;
    try {
      const data = await dispatch(id, cmd, args);
      send({ id, type: "result", data });
    } catch (e) {
      const f = e instanceof Fail ? e : new Fail("internal", e instanceof Error ? e.message : String(e));
      const payload: Record<string, unknown> = { id, type: "error", code: f.code, message: f.message };
      if (f.hint) payload.hint = f.hint;
      send(payload);
    } finally {
      if (heavy) busy = false;
    }
  }

  setTimeout(() => {
    if (w.flags.has("crash")) {
      close(1);
      return;
    }
    send({ type: "hello", version: "0.1.0 (mock)", work: WORK, pid: 4242 });
  }, 0);

  return {
    async write(data: string): Promise<void> {
      for (const raw of String(data).split("\n")) {
        const line = raw.trim();
        if (!line) continue;
        let req: { id?: unknown; cmd?: unknown; args?: unknown };
        try {
          req = JSON.parse(line) as typeof req;
        } catch (e) {
          send({ id: null, type: "error", code: "bad_json", message: String(e) });
          continue;
        }
        const id = String(req.id ?? "");
        const args = (req.args && typeof req.args === "object" ? req.args : {}) as Record<string, unknown>;
        if (typeof req.cmd !== "string") {
          send({ id, type: "error", code: "bad_args", message: "cmd/args malformed" });
          continue;
        }
        if (req.cmd === "shutdown") {
          send({ id, type: "result", data: { bye: true } });
          close(0);
          continue;
        }
        void handle(id, req.cmd, args);
      }
    },
    async kill(): Promise<void> {
      close(null);
    },
  };
}
