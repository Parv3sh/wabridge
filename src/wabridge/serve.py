"""`wabridge serve` — long-lived JSON-lines engine used by the desktop GUI.

The GUI spawns this once as a Tauri *sidecar* and talks over stdin/stdout:

    →  {"id": "7", "cmd": "android.fetch", "args": {"serial": "R5C…", "key": "3409…"}}
    ←  {"id": "7", "type": "log",      "text": "→ Decrypting msgstore.db.crypt15 …"}
    ←  {"id": "7", "type": "progress", "stage": "android_pull", "pct": 42.0}
    ←  {"id": "7", "type": "data",     "key": "archive_stats", "value": {...}}
    ←  {"id": "7", "type": "result",   "data": {...}}          # terminal
    ←  {"id": "7", "type": "error",    "code": "wrong_key", "message": "…", "hint": "…"}  # terminal

Rules that keep the GUI simple and the user safe:
  * stdout carries ONLY protocol lines; everything else (library prints, warnings) goes to stderr.
  * Secrets (64-digit key, backup password) arrive inside request args and are never echoed.
  * One heavy action at a time (`busy` error otherwise); cheap queries run concurrently.
  * While an iPhone action runs, `devices` does not touch the iPhone (returns last-known info).
  * Every error carries a stable `code` so the GUI can show the right guidance.
"""

from __future__ import annotations

import dataclasses
import json
import logging
import os
import shutil
import sys
import threading
import traceback
from typing import Any, TextIO

from . import __version__, pipeline
from .android import adb, crypt15
from .ios import backup as iosbackup
from .ios import device
from .reporting import Reporter

HEAVY = {"android.fetch", "android.pull_media", "ios.disable_encryption", "ios.backup", "convert",
         "inject", "ios.restore", "ios.rollback", "clean", "adb.install"}
IOS_ACTIONS = {"ios.disable_encryption", "ios.backup", "ios.restore", "ios.rollback"}

PLATFORM_TOOLS_URL = {
    "darwin": "https://dl.google.com/android/repository/platform-tools-latest-darwin.zip",
    "linux": "https://dl.google.com/android/repository/platform-tools-latest-linux.zip",
    "win32": "https://dl.google.com/android/repository/platform-tools-latest-windows.zip",
}


class RpcError(Exception):
    def __init__(self, code: str, message: str, hint: str | None = None):
        super().__init__(message)
        self.code = code
        self.hint = hint


_ctx = threading.local()


class Transport:
    """Thread-safe JSON-lines writer bound to the real stdout."""

    def __init__(self, stream: TextIO):
        self.stream = stream
        self.lock = threading.Lock()

    def send(self, obj: dict) -> None:
        line = json.dumps(obj, ensure_ascii=False, default=_json_default)
        with self.lock:
            self.stream.write(line + "\n")
            self.stream.flush()


def _json_default(o: Any):
    if dataclasses.is_dataclass(o):
        return dataclasses.asdict(o)
    if isinstance(o, bytes):
        return o.hex()
    return str(o)


class JsonReporter(Reporter):
    def __init__(self, transport: Transport, req_id: str):
        self.t = transport
        self.id = req_id
        self._last: dict[str, int] = {}

    def log(self, text: str) -> None:
        if text.strip():
            self.t.send({"id": self.id, "type": "log", "level": "info", "text": text.rstrip()})

    def progress(self, stage: str, pct: float, detail: str | None = None) -> None:
        p = int(pct)
        if p == self._last.get(stage) and p < 100:
            return                                    # don't flood the GUI with identical percents
        self._last[stage] = p
        msg: dict = {"id": self.id, "type": "progress", "stage": stage, "pct": round(float(pct), 1)}
        if detail:
            msg["detail"] = detail
        self.t.send(msg)

    def data(self, key: str, value: Any) -> None:
        self.t.send({"id": self.id, "type": "data", "key": key, "value": value})


class _LogForwarder(logging.Handler):
    """Routes Python logging (ours and pymobiledevice3's passcode prompts etc.) to the GUI."""

    def __init__(self, transport: Transport):
        super().__init__(level=logging.INFO)
        self.t = transport

    def emit(self, record: logging.LogRecord) -> None:
        try:
            text = record.getMessage()
        except Exception:  # noqa: BLE001
            return
        if record.name.startswith("pymobiledevice3") and record.levelno < logging.WARNING:
            return
        self.t.send({"id": getattr(_ctx, "req_id", None), "type": "log",
                     "level": record.levelname.lower(), "text": text, "logger": record.name})


# ------------------------------------------------------------------------------ error mapping

def classify(exc: BaseException) -> RpcError:
    if isinstance(exc, RpcError):
        return exc
    msg = (str(exc).splitlines() or [type(exc).__name__])[0]
    low = msg.lower()
    if isinstance(exc, crypt15.Crypt15Error):
        if "key must be" in low:
            return RpcError("bad_key", msg, "Paste the 64 digits WhatsApp showed you; spaces are fine.")
        return RpcError("wrong_key", "The key doesn't decrypt this backup.",
                        "The backup on the phone was probably written before this key existed. "
                        "On Android: Settings → Chats → Chat backup → Back up, then try again.")
    if isinstance(exc, adb.AdbError):
        if "unauthorized" in low or "accept the usb debugging" in low:
            return RpcError("unauthorized", msg, "Look at the Android screen and tap Allow.")
        if "no android device" in low or "not connected" in low:
            return RpcError("no_android", msg, "Enable USB debugging and plug the phone in.")
        if "no .crypt15" in low:
            return RpcError("no_crypt15", msg, None)
        if "could not find whatsapp" in low:
            return RpcError("no_whatsapp", msg, None)
        if "adb not found" in low:
            return RpcError("no_adb", msg, "Use the 'Install Android tools' button.")
        return RpcError("adb", msg)
    if isinstance(exc, device.DeviceError):
        if "no iphone" in low:
            return RpcError("no_iphone", msg, "Unlock the iPhone, plug it in, tap Trust.")
        if "locked" in low:
            return RpcError("iphone_locked", msg, "Unlock the iPhone and try again.")
        if "trust" in low or "pairing" in low:
            return RpcError("pairing", msg, "Unlock the iPhone and tap 'Trust this computer'.")
        if "encryption is on" in low:
            return RpcError("encrypted_backup", msg, None)
        if "dropped the connection" in low:
            return RpcError("iphone_dropped", msg, "Unlock the iPhone and try again.")
        return RpcError("iphone", msg)
    if "mberrordomain/211" in low or "find my" in low:
        return RpcError("find_my", "Apple refuses to restore while Find My iPhone is on.",
                        "On the iPhone: Settings → your name → Find My → Find My iPhone → off. "
                        "Turn it back on afterwards.")
    if "mberrordomain/105" in low or "needs more than" in low:
        return RpcError("disk_space", msg, "Free up space on this computer or use an external drive.")
    if isinstance(exc, pipeline.PipelineError):
        if "encrypted" in low:
            return RpcError("encrypted_backup", msg)
        if "whatsapp data is not in this backup" in low:
            return RpcError("no_whatsapp_ios", msg,
                            "Install WhatsApp on the iPhone, register the same number, send one message.")
        if "outside the work directory" in low:
            return RpcError("bad_args", msg)
        return RpcError("pipeline", msg)
    if isinstance(exc, iosbackup.BackupError):
        return RpcError("encrypted_backup" if "encrypted" in low else "backup", msg)
    return RpcError("internal", msg)


# ------------------------------------------------------------------------------ the engine

class Engine:
    def __init__(self, work: pipeline.Work, transport: Transport):
        self.work = work
        self.t = transport
        self.action_lock = threading.Lock()
        self.ios_busy = threading.Event()
        self.last_iphone: dict | None = None
        self.stop = threading.Event()
        tools = os.path.join(work.root, "tools", "platform-tools")
        if os.path.isdir(tools) and tools not in adb.EXTRA_DIRS:
            adb.EXTRA_DIRS.append(tools)

    # ---------------------------------------------------------------- dispatch
    def handle(self, req: dict) -> None:
        rid = str(req.get("id", ""))
        cmd = req.get("cmd")
        args = req.get("args") or {}
        if not isinstance(cmd, str) or not isinstance(args, dict):
            self.t.send({"id": rid, "type": "error", "code": "bad_args", "message": "cmd/args malformed"})
            return
        if cmd == "shutdown":
            self.t.send({"id": rid, "type": "result", "data": {"bye": True}})
            self.stop.set()
            return
        fn = getattr(self, "cmd_" + cmd.replace(".", "_"), None)
        if fn is None:
            self.t.send({"id": rid, "type": "error", "code": "unknown_cmd", "message": f"unknown cmd {cmd}"})
            return
        threading.Thread(target=self._run, args=(rid, cmd, fn, args), daemon=True).start()

    def _run(self, rid: str, cmd: str, fn, args: dict) -> None:
        _ctx.req_id = rid
        rep = JsonReporter(self.t, rid)
        heavy = cmd in HEAVY
        if heavy and not self.action_lock.acquire(blocking=False):
            self.t.send({"id": rid, "type": "error", "code": "busy",
                         "message": "Another step is still running."})
            return
        if cmd in IOS_ACTIONS:
            self.ios_busy.set()
        try:
            data = fn(rep, **args)
            self.t.send({"id": rid, "type": "result", "data": data if data is not None else {}})
        except TypeError as e:
            if "unexpected keyword" in str(e) or "required positional" in str(e):
                self.t.send({"id": rid, "type": "error", "code": "bad_args", "message": str(e)})
            else:
                self._error(rid, e)
        except Exception as e:  # noqa: BLE001
            self._error(rid, e)
        finally:
            if cmd in IOS_ACTIONS:
                self.ios_busy.clear()
            if heavy:
                self.action_lock.release()

    def _error(self, rid: str, exc: BaseException) -> None:
        err = classify(exc)
        payload = {"id": rid, "type": "error", "code": err.code, "message": str(err)}
        if err.hint:
            payload["hint"] = err.hint
        if err.code == "internal":
            payload["trace"] = traceback.format_exc()
        self.t.send(payload)

    # ---------------------------------------------------------------- cheap queries
    def cmd_ping(self, rep: Reporter) -> dict:
        return {"version": __version__, "python": sys.version.split()[0], "platform": sys.platform,
                "work": self.work.root, "pid": os.getpid()}

    def cmd_state(self, rep: Reporter) -> dict:
        w = self.work
        w.__init__(w.root)                       # re-read state.json from disk
        st = w.state
        folder = st.get("ios_backup", {}).get("folder")
        media_dir = w.media_dir
        try:
            adb_path: str | None = adb.find_adb()
        except adb.AdbError:
            adb_path = None
        return {
            "version": __version__,
            "work": w.root,
            "log_path": w.path("engine.log"),
            "free_bytes": shutil.disk_usage(w.root).free,
            "adb": adb_path,
            "stages": {
                "android_decrypt": os.path.isfile(w.msgstore_db),
                "media": bool(media_dir and next(os.scandir(media_dir), None) is not None),
                "media_skipped": bool(st.get("android_pull")) and not st.get("android_pull", {}).get("media_dir"),
                "ios_backup": bool(folder and os.path.isfile(os.path.join(folder, "Manifest.db"))),
                "convert": os.path.isfile(w.path("convert", "report.json")),
                "convert_media": bool(st.get("convert", {}).get("media_jobs")),
                "inject": bool(st.get("inject")),
                "ios_restore": bool(st.get("ios_restore")),
            },
            "raw": st,
        }

    def cmd_devices(self, rep: Reporter) -> dict:
        out: dict = {"android": [], "android_error": None, "iphone": None, "iphone_error": None, "adb": None}
        try:
            out["adb"] = adb.find_adb()
            out["android"] = [dataclasses.asdict(d) for d in adb.devices()]
        except adb.AdbError as e:
            out["android_error"] = str(e).splitlines()[0]
        if self.ios_busy.is_set():
            out["iphone"] = self.last_iphone
            out["iphone_busy"] = True
            return out
        try:
            info = device.info()
            self.last_iphone = out["iphone"] = _idevice_dict(info)
        except device.DeviceError as e:
            out["iphone_error"] = str(e).splitlines()[0]
        except Exception as e:  # noqa: BLE001 — pymobiledevice3 internals
            out["iphone_error"] = (str(e).splitlines() or [type(e).__name__])[0]
        return out

    def cmd_android_check(self, rep: Reporter, serial: str | None = None) -> dict:
        dev = adb.require_device(serial)
        root = adb.detect_wa_root(dev.serial)
        return {
            "serial": dev.serial, "model": dev.model, "root": root,
            "has_crypt15": adb.has_crypt15(root, dev.serial),
            "media_bytes": adb.dir_size_bytes(f"{root}/Media", dev.serial),
        }

    # ---------------------------------------------------------------- heavy actions
    def cmd_android_fetch(self, rep: Reporter, key: str, serial: str | None = None) -> dict:
        """Pull the encrypted database, decrypt it with `key`, read contacts, summarise."""
        key_hex = crypt15.parse_key(key).hex()
        dev = adb.require_device(serial)
        root = adb.detect_wa_root(dev.serial)
        if not adb.has_crypt15(root, dev.serial):
            raise RpcError("no_crypt15", adb.NO_CRYPT15_MSG)
        rep("→ Copying the encrypted database from the phone …")
        adb.pull_databases(self.work.android_dir, root, serial=dev.serial)
        pipeline.android_decrypt(self.work, key_hex, out=rep)
        contacts = adb.export_contacts(dev.serial)
        if contacts:
            with open(self.work.path("android", "contacts.json"), "w", encoding="utf-8") as fh:
                json.dump(contacts, fh)
            rep(f"  read {len(contacts)} contact numbers for names")
        self.work.save(android_pull={"root": root, "media_dir": None, "business": False, "media_pulled": []},
                       android_serial=dev.serial)
        summary = self._summary()
        summary["media_bytes"] = adb.dir_size_bytes(f"{root}/Media", dev.serial)
        return summary

    def cmd_android_inspect(self, rep: Reporter) -> dict:
        if not os.path.isfile(self.work.msgstore_db):
            raise RpcError("pipeline", "No decrypted database yet.")
        return self._summary()

    def _summary(self) -> dict:
        archive = pipeline.load_archive(self.work)
        st = archive.stats()
        top = sorted(archive.chats, key=lambda c: -len(c.messages))[:12]
        return {
            "stats": st,
            "top_chats": [{"name": c.name or c.phone or c.jid, "messages": len(c.messages),
                           "is_group": c.is_group, "named": bool(c.name)} for c in top],
            "unnamed_chats": sum(1 for c in archive.chats if not c.is_group and not c.name),
        }

    def cmd_android_pull_media(self, rep: Reporter, serial: str | None = None) -> dict:
        dev = adb.require_device(serial or self.work.state.get("android_serial"))
        root = self.work.state.get("android_pull", {}).get("root") or adb.detect_wa_root(dev.serial)
        rep("→ Copying photos, videos and voice notes (this is the slow part) …")
        pulled = adb.pull_media(self.work.android_dir, root, serial=dev.serial,
                                percent=lambda p: rep.progress("android_media", p))
        ap = dict(self.work.state.get("android_pull", {}))
        ap.update({"media_dir": self.work.path("android", "Media"), "media_pulled": pulled})
        self.work.save(android_pull=ap)
        return {"pulled": pulled, "bytes": adb.local_tree_bytes(self.work.path("android", "Media"))}

    def cmd_ios_info(self, rep: Reporter) -> dict:
        info = device.info()
        self.last_iphone = _idevice_dict(info)
        return self.last_iphone

    def cmd_ios_disable_encryption(self, rep: Reporter, password: str) -> dict:
        rep("→ Turning off backup encryption (enter the passcode on the iPhone if asked) …")
        device.disable_encryption(self.work.backup_root, password)
        info = device.info()
        self.last_iphone = _idevice_dict(info)
        if info.will_encrypt:
            raise RpcError("encrypted_backup", "Backup encryption is still on.",
                           "Check the password, or untick 'Encrypt local backup' in Finder.")
        return self.last_iphone

    def cmd_ios_backup(self, rep: Reporter, force: bool = False) -> dict:
        info = device.info()
        self.last_iphone = _idevice_dict(info)
        if info.will_encrypt:
            raise RpcError("encrypted_backup", "Backup encryption is on for this iPhone.")
        free = shutil.disk_usage(self.work.root).free
        used = info.disk_used
        if used and not force and free < used * 1.05:
            raise RpcError("disk_space",
                           f"The iPhone holds about {used / 1e9:.1f} GB but this computer has only "
                           f"{free / 1e9:.1f} GB free.",
                           "Free up space (Trash, Downloads, old backups) or move WaBridge to an external drive.")
        folder = pipeline.ios_backup(self.work, udid=info.udid, out=rep)
        return {"folder": folder, "pristine": self.work.state.get("ios_backup", {}).get("pristine")}

    def cmd_convert(self, rep: Reporter, media: bool = False, include_system: bool = False,
                    contacts_vcf: str | None = None) -> dict:
        if media and not self.work.media_dir:
            raise RpcError("bad_args", "No media was copied from the Android phone.",
                           "Go back to the Android step and copy media first.")
        r = pipeline.convert(self.work, include_media=media, include_system=include_system,
                             contacts_vcf=contacts_vcf or None, out=rep)
        d = dataclasses.asdict(r)
        d.pop("media_jobs", None)
        d["media"] = media
        return d

    def cmd_inject(self, rep: Reporter) -> dict:
        pipeline.inject(self.work, out=rep)
        return {"media_files": self.work.state.get("inject", {}).get("media", 0)}

    def cmd_ios_restore(self, rep: Reporter, system: bool = False) -> dict:
        pipeline.ios_restore(self.work, system=system, out=rep)
        return {"system": system}

    def cmd_ios_rollback(self, rep: Reporter, system: bool = False) -> dict:
        pipeline.ios_rollback(self.work, system=system, out=rep)
        return {"system": system}

    def cmd_clean(self, rep: Reporter) -> dict:
        """Delete everything personal: pulled data, decrypted DB, backups, state. Keeps tools/."""
        freed = 0
        for name in ("android", "ios_backup", "ios_backup_pristine", "convert"):
            p = self.work.path(name)
            if os.path.isdir(p):
                freed += adb.local_tree_bytes(p)
                shutil.rmtree(p, ignore_errors=True)
        for name in ("state.json", "key.txt", "wizard.log"):
            p = self.work.path(name)
            if os.path.isfile(p):
                os.remove(p)
        self.work.state = {}
        rep(f"  deleted {freed / 1e9:.1f} GB of migration data")
        return {"freed_bytes": freed}

    def cmd_adb_install(self, rep: Reporter) -> dict:
        """Download Google's platform-tools (Apache-2.0) into <work>/tools — no admin rights needed."""
        import io
        import stat
        import urllib.request
        import zipfile

        url = PLATFORM_TOOLS_URL.get(sys.platform)
        if not url:
            raise RpcError("internal", f"No platform-tools download for {sys.platform}")
        tools = self.work.path("tools")
        os.makedirs(tools, exist_ok=True)
        rep("→ Downloading Android platform-tools from dl.google.com …")
        buf = io.BytesIO()
        with urllib.request.urlopen(url, timeout=60) as resp:  # noqa: S310 — fixed https URL
            total = int(resp.headers.get("Content-Length") or 0)
            got = 0
            while True:
                chunk = resp.read(1 << 16)
                if not chunk:
                    break
                buf.write(chunk)
                got += len(chunk)
                if total:
                    rep.progress("adb_install", 100.0 * got / total)
        with zipfile.ZipFile(buf) as z:
            z.extractall(tools)
        target = os.path.join(tools, "platform-tools")
        if os.name != "nt":
            for exe in ("adb", "fastboot"):
                p = os.path.join(target, exe)
                if os.path.isfile(p):
                    os.chmod(p, os.stat(p).st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
        if target not in adb.EXTRA_DIRS:
            adb.EXTRA_DIRS.insert(0, target)
        return {"adb": adb.find_adb()}


def _idevice_dict(info: device.IDevice) -> dict:
    d = dataclasses.asdict(info)
    d["disk_used"] = info.disk_used
    return d


# ------------------------------------------------------------------------------ entry point

def main(work_dir: str, stdin: TextIO | None = None, stdout: TextIO | None = None) -> int:
    if stdout is None and hasattr(sys.stdout, "reconfigure"):
        # Windows consoles default to cp1252; the protocol (and our arrows/ellipses) are UTF-8.
        sys.stdout.reconfigure(encoding="utf-8", errors="backslashreplace")  # type: ignore[union-attr]
    if stdin is None and hasattr(sys.stdin, "reconfigure"):
        sys.stdin.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]
    real_out = stdout or sys.stdout
    stdin = stdin or sys.stdin
    # Nothing but protocol may reach stdout: redirect stray prints from libraries to stderr.
    if stdout is None:
        sys.stdout = sys.stderr
    transport = Transport(real_out)
    work = pipeline.Work(work_dir)

    root = logging.getLogger()
    root.setLevel(logging.INFO)
    for h in list(root.handlers):
        root.removeHandler(h)
    root.addHandler(_LogForwarder(transport))
    file_handler = logging.FileHandler(work.path("engine.log"), encoding="utf-8")
    file_handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    root.addHandler(file_handler)
    logging.captureWarnings(True)

    engine = Engine(work, transport)
    transport.send({"type": "hello", "version": __version__, "work": work.root, "pid": os.getpid()})

    for line in stdin:
        line = line.strip()
        if not line:
            continue
        try:
            req = json.loads(line)
        except json.JSONDecodeError as e:
            transport.send({"id": None, "type": "error", "code": "bad_json", "message": str(e)})
            continue
        engine.handle(req)
        if engine.stop.is_set():
            break
    return 0
