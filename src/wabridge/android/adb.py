"""Thin wrapper around the `adb` binary.

We deliberately shell out rather than speak the ADB protocol ourselves: the official
platform-tools are free, tiny, and handle every quirk of USB/OEM drivers.

No root is needed. Since Android 11 the *only* WhatsApp folder we need is
`/sdcard/Android/media/com.whatsapp/WhatsApp/`, which stays readable by the `shell`
user (scoped storage restricts apps, not ADB).
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from collections.abc import Callable
from dataclasses import dataclass

WA_MEDIA_ROOT = "/sdcard/Android/media/com.whatsapp/WhatsApp"
WA_MEDIA_ROOT_ALTS = (
    "/storage/emulated/0/Android/media/com.whatsapp/WhatsApp",
    "/sdcard/WhatsApp",                       # very old installs (pre-2021)
)
WA_BUSINESS_MEDIA_ROOT = "/sdcard/Android/media/com.whatsapp.w4b/WhatsApp Business"

# Sub-folders of WhatsApp/Media we migrate. Order = priority when disk space is limited.
MEDIA_DIRS = [
    "WhatsApp Images",
    "WhatsApp Video",
    "WhatsApp Voice Notes",
    "WhatsApp Audio",
    "WhatsApp Documents",
    "WhatsApp Animated Gifs",
    "WhatsApp Stickers",
    "WhatsApp Video Notes",
]


class AdbError(Exception):
    pass


@dataclass
class Device:
    serial: str
    state: str
    model: str | None = None


def find_adb() -> str:
    exe = shutil.which("adb")
    if exe:
        return exe
    candidates = [
        os.path.expandvars(r"%LOCALAPPDATA%\Android\Sdk\platform-tools\adb.exe"),
        os.path.expanduser("~/Library/Android/sdk/platform-tools/adb"),
        os.path.expanduser("~/Android/Sdk/platform-tools/adb"),
        "/opt/homebrew/bin/adb",
    ]
    for c in candidates:
        if os.path.isfile(c):
            return c
    raise AdbError(
        "adb not found. Install Android platform-tools "
        "(https://developer.android.com/tools/releases/platform-tools) and add it to PATH."
    )


def run(args: list[str], *, check: bool = True, serial: str | None = None, timeout: int | None = None) -> str:
    cmd = [find_adb()]
    if serial:
        cmd += ["-s", serial]
    cmd += args
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, errors="replace")
    if check and r.returncode != 0:
        raise AdbError(f"adb {' '.join(args)} failed ({r.returncode}): {r.stderr.strip() or r.stdout.strip()}")
    return r.stdout


def devices() -> list[Device]:
    out = run(["devices", "-l"])
    result = []
    for line in out.splitlines()[1:]:
        line = line.strip()
        if not line:
            continue
        parts = line.split()
        serial, state = parts[0], parts[1]
        model = None
        m = re.search(r"model:(\S+)", line)
        if m:
            model = m.group(1)
        result.append(Device(serial, state, model))
    return result


def require_device(serial: str | None = None) -> Device:
    devs = devices()
    if not devs:
        raise AdbError(
            "No Android device found. Enable Developer options > USB debugging, plug in via USB, "
            "and accept the 'Allow USB debugging?' prompt on the phone."
        )
    if serial:
        for d in devs:
            if d.serial == serial:
                if d.state != "device":
                    raise AdbError(f"Device {serial} is '{d.state}' (unauthorized? check the phone screen)")
                return d
        raise AdbError(f"Device {serial} not connected")
    ready = [d for d in devs if d.state == "device"]
    if not ready:
        raise AdbError(f"Device found but state is '{devs[0].state}'. Accept the USB debugging prompt on the phone.")
    if len(ready) > 1:
        raise AdbError("Several devices connected; pass --serial " + " or ".join(d.serial for d in ready))
    return ready[0]


def shell(cmd: str, serial: str | None = None, check: bool = True) -> str:
    return run(["shell", cmd], serial=serial, check=check)


def detect_wa_root(serial: str | None = None, business: bool = False) -> str:
    roots = [WA_BUSINESS_MEDIA_ROOT] if business else [WA_MEDIA_ROOT, *WA_MEDIA_ROOT_ALTS]
    for root in roots:
        out = shell(f'ls "{root}/Databases" 2>/dev/null', serial=serial, check=False)
        if "msgstore" in out:
            return root
    raise AdbError(
        "Could not find WhatsApp/Databases on the phone. Open WhatsApp > Settings > Chats > "
        "Chat backup and tap BACK UP, then retry."
    )


def list_backups(root: str, serial: str | None = None) -> list[str]:
    out = shell(f'ls -1 "{root}/Databases"', serial=serial)
    return [line.strip() for line in out.splitlines() if line.strip().startswith("msgstore")]


def dir_size_bytes(path: str, serial: str | None = None) -> int | None:
    out = shell(f'du -sk "{path}" 2>/dev/null', serial=serial, check=False)
    m = re.match(r"(\d+)", out.strip())
    return int(m.group(1)) * 1024 if m else None


def pull(remote: str, local: str, serial: str | None = None, progress: Callable[[str], None] | None = None) -> None:
    os.makedirs(local, exist_ok=True)
    cmd = [find_adb()]
    if serial:
        cmd += ["-s", serial]
    cmd += ["pull", "-a", remote, local]
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, errors="replace")
    assert proc.stdout
    last = ""
    for line in proc.stdout:
        last = line.rstrip()
        if progress and last:
            progress(last)
    proc.wait()
    if proc.returncode != 0:
        raise AdbError(f"adb pull {remote} failed: {last}")


NO_CRYPT15_MSG = (
    "No .crypt15 backup on the phone. Turn on WhatsApp > Settings > Chats > Chat backup > "
    "End-to-end encrypted backup (choose the 64-digit key), then tap BACK UP and retry."
)


def has_crypt15(root: str, serial: str | None = None) -> bool:
    return any(b.endswith(".crypt15") for b in list_backups(root, serial))


def pull_databases(dest: str, root: str, *, serial: str | None = None,
                   progress: Callable[[str], None] | None = None) -> str:
    """Pull only the newest msgstore.db.crypt15 (the dated snapshots are older copies)."""
    if not has_crypt15(root, serial):
        raise AdbError(NO_CRYPT15_MSG)
    local = os.path.join(dest, "Databases")
    os.makedirs(local, exist_ok=True)
    pull(f"{root}/Databases/msgstore.db.crypt15", local, serial, progress)
    return local


def pull_media(dest: str, root: str, *, serial: str | None = None,
               progress: Callable[[str], None] | None = None) -> list[str]:
    media_local = os.path.join(dest, "Media")
    os.makedirs(media_local, exist_ok=True)
    pulled = []
    for d in MEDIA_DIRS:
        remote = f"{root}/Media/{d}"
        exists = shell(f'[ -d "{remote}" ] && echo yes', serial=serial, check=False).strip()
        if exists != "yes":
            continue
        pull(remote, media_local, serial, progress)
        pulled.append(d)
    return pulled


def pull_whatsapp(dest: str, *, serial: str | None = None, include_media: bool = True,
                  business: bool = False, progress: Callable[[str], None] | None = None) -> dict:
    """Pull Databases/ (always) and Media/<dirs> (optional) into `dest`.

    Returns a summary dict with local paths."""
    root = detect_wa_root(serial, business)
    os.makedirs(dest, exist_ok=True)
    summary = {
        "root": root,
        "databases_dir": pull_databases(dest, root, serial=serial, progress=progress),
        "media_dir": None,
        "media_pulled": [],
    }
    if include_media:
        summary["media_pulled"] = pull_media(dest, root, serial=serial, progress=progress)
        summary["media_dir"] = os.path.join(dest, "Media")
    return summary


def export_contacts(serial: str | None = None) -> dict[str, str]:
    """Best-effort phone-number -> name map using the Contacts content provider.

    Works on most devices from the ADB shell without root. Returns {} if denied."""
    out = shell(
        "content query --uri content://com.android.contacts/data/phones "
        "--projection display_name:data1",
        serial=serial,
        check=False,
    )
    result: dict[str, str] = {}
    for line in out.splitlines():
        m = re.search(r"display_name=(.*?), data1=(.*)$", line)
        if not m:
            continue
        name, number = m.group(1).strip(), m.group(2).strip()
        digits = re.sub(r"\D", "", number)
        if name and name != "NULL" and digits:
            result[digits] = name
    return result
