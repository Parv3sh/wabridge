"""Talk to the iPhone through pymobiledevice3 (pure Python; Windows needs Apple's
"Apple Mobile Device Support"/iTunes drivers, Linux needs `usbmuxd`, macOS needs nothing).

pymobiledevice3 >= 5 is asyncio-based and a LockdownClient is bound to the event loop that
created it, so every public function here runs ONE coroutine (connect → service → action)
on ONE loop. Older sync releases are handled by `_aw`, which awaits only when needed.

Everything degrades to shelling out to the `pymobiledevice3` CLI when the Python API
signature differs from what we expect; the CLI flags have been stable for years.
"""

from __future__ import annotations

import asyncio
import inspect
import os
import shutil
import subprocess
import sys
from collections.abc import Callable
from dataclasses import dataclass

Progress = Callable[[float], None]


class DeviceError(Exception):
    pass


def _pmd3_available() -> bool:
    try:
        import pymobiledevice3  # noqa: F401
        return True
    except ImportError:
        return False


def _require() -> None:
    if not _pmd3_available():
        raise DeviceError(
            "pymobiledevice3 is not installed. Run: pip install 'wabridge[ios]'  "
            "(Windows: also install iTunes / Apple Mobile Device Support; Linux: apt install usbmuxd)."
        )


@dataclass
class IDevice:
    udid: str
    name: str
    ios_version: str
    will_encrypt: bool
    disk_capacity: int | None = None    # bytes of user data partition
    disk_available: int | None = None   # bytes free on the iPhone

    @property
    def disk_used(self) -> int | None:
        if self.disk_capacity is None or self.disk_available is None:
            return None
        return max(0, self.disk_capacity - self.disk_available)


async def _aw(value):
    """Await if awaitable (async pymobiledevice3), else return as-is (old sync releases)."""
    return await value if inspect.isawaitable(value) else value


def _run(coro):
    """Run one coroutine to completion on a fresh loop, translating pymobiledevice3 errors."""
    _require()
    from pymobiledevice3 import exceptions as ex  # type: ignore

    try:
        return asyncio.run(coro)
    except ex.ConnectionTerminatedError as e:
        raise DeviceError("The iPhone dropped the connection (locked? passcode prompt?). Unlock it; retrying.") from e
    except ex.NoDeviceConnectedError as e:
        raise DeviceError("No iPhone found over USB. Unlock it, plug it in, and tap 'Trust'.") from e
    except getattr(ex, "PairingError", ()) as e:
        raise DeviceError(f"Pairing failed: {e}. Unlock the iPhone and tap 'Trust this computer'.") from e
    except getattr(ex, "PasswordRequiredError", ()) as e:
        raise DeviceError("The iPhone is locked. Unlock it and try again.") from e
    except getattr(ex, "UserDeniedPairingError", ()) as e:
        raise DeviceError("You tapped 'Don't Trust' on the iPhone. Unplug, replug, and tap Trust.") from e


async def _connect(udid: str | None):
    from pymobiledevice3.lockdown import create_using_usbmux  # type: ignore

    return await _aw(create_using_usbmux(serial=udid) if udid else create_using_usbmux())


async def _close(lockdown) -> None:
    close = getattr(lockdown, "close", None)
    if close:
        try:
            await _aw(close())
        except Exception:  # noqa: BLE001
            pass


async def _service(lockdown):
    from pymobiledevice3.services.mobilebackup2 import Mobilebackup2Service  # type: ignore

    last: Exception | None = None
    for attempt in range(2):
        svc = Mobilebackup2Service(lockdown)
        # some releases require an explicit async connect step
        connect = getattr(svc, "connect", None)
        if not (connect and inspect.iscoroutinefunction(connect)):
            return svc
        try:
            await connect()
            return svc
        except Exception as e:  # noqa: BLE001 — pymobiledevice3's own hierarchy (ConnectionTerminatedError, …)
            last = e
            if attempt == 0:
                # Verified on iPhone 13 / iOS 27.2 (2026-09-23): the FIRST connect to the backup service fails
                # with "SSL handshake is taking longer than 10 seconds" (pymobiledevice3's limit) and the
                # retry 1.5 s later succeeds; the 41 GB backup then ran to completion.
                await asyncio.sleep(1.5)
    assert last is not None
    raise last


async def _will_encrypt(svc, lockdown) -> bool:
    if hasattr(svc, "get_will_encrypt"):            # pymobiledevice3 >= 11
        return bool(await _aw(svc.get_will_encrypt()))
    if hasattr(svc, "will_encrypt"):                # older releases (property)
        return bool(await _aw(svc.will_encrypt))
    try:
        return bool(await _aw(lockdown.get_value("com.apple.mobile.backup", "WillEncrypt")))
    except Exception:  # noqa: BLE001
        return False


async def _close_svc(svc) -> None:
    close = getattr(svc, "close", None)
    if close:
        try:
            await _aw(close())
        except Exception:  # noqa: BLE001
            pass


# ---------------------------------------------------------------------------- public API

def info(udid: str | None = None) -> IDevice:
    async def go():
        lockdown = await _connect(udid)
        svc = None
        try:
            svc = await _service(lockdown)
            cap = avail = None
            try:
                du = await _aw(lockdown.get_value("com.apple.disk_usage")) or {}
                cap = du.get("TotalDataCapacity")
                avail = du.get("TotalDataAvailable")
            except Exception:  # noqa: BLE001 — informational only
                pass
            return IDevice(
                udid=lockdown.udid,
                name=await _aw(lockdown.get_value(key="DeviceName")) or "",
                ios_version=await _aw(lockdown.get_value(key="ProductVersion")) or "",
                will_encrypt=await _will_encrypt(svc, lockdown),
                disk_capacity=int(cap) if cap else None,
                disk_available=int(avail) if avail else None,
            )
        finally:
            if svc is not None:
                await _close_svc(svc)
            await _close(lockdown)

    return _run(go())


def _cli(args: list[str], secret: str | None = None) -> None:
    """Run the pymobiledevice3 CLI. Its stdout must not reach fd 1: in `serve` mode that pipe carries
    only protocol JSON, so the CLI's own output goes to stderr. `secret` is redacted from error text."""
    exe = shutil.which("pymobiledevice3") or os.path.join(os.path.dirname(sys.executable), "pymobiledevice3")
    if not os.path.isfile(exe):
        raise DeviceError("pymobiledevice3 CLI not found")
    err_stream = sys.__stderr__ if sys.__stderr__ is not None else subprocess.DEVNULL
    r = subprocess.run([exe, *args], text=True, stdout=err_stream)
    if r.returncode != 0:
        shown = " ".join("••••" if secret and a == secret else a for a in args)
        raise DeviceError(f"pymobiledevice3 {shown} failed with code {r.returncode}")


def disable_encryption(backup_dir: str, current_password: str, udid: str | None = None) -> None:
    """Turn off 'Encrypt local backup'. The phone will ask for its passcode."""
    os.makedirs(backup_dir, exist_ok=True)

    async def go():
        lockdown = await _connect(udid)
        svc = None
        try:
            svc = await _service(lockdown)
            await _aw(svc.change_password(backup_directory=backup_dir, old=current_password, new=""))
        finally:
            if svc is not None:
                await _close_svc(svc)
            await _close(lockdown)

    try:
        _run(go())
    except TypeError:
        _cli(["backup2", "encryption", "OFF", current_password, backup_dir], secret=current_password)


def backup(backup_dir: str, udid: str | None = None, progress: Progress | None = None) -> str:
    """Full, unencrypted backup into backup_dir/<UDID>/. Returns that folder."""
    os.makedirs(backup_dir, exist_ok=True)
    cb = progress or (lambda _p: None)
    result: dict = {}

    async def go():
        lockdown = await _connect(udid)
        svc = None
        try:
            result["udid"] = lockdown.udid
            svc = await _service(lockdown)
            if await _will_encrypt(svc, lockdown):
                raise DeviceError(
                    "Backup encryption is ON for this iPhone. Untick 'Encrypt local backup' in Finder, "
                    "or let the wizard turn it off, then retry."
                )
            await _aw(svc.backup(full=True, backup_directory=backup_dir, progress_callback=cb))
        finally:
            if svc is not None:
                await _close_svc(svc)
            await _close(lockdown)

    try:
        _run(go())
    except TypeError:
        _cli(["backup2", "backup", "--full", backup_dir])
        if "udid" not in result:
            result["udid"] = info(udid).udid
    out = os.path.join(backup_dir, result["udid"])
    if not os.path.isfile(os.path.join(out, "Manifest.db")):
        raise DeviceError(f"Backup finished but {out}/Manifest.db is missing")
    return out


def restore(backup_dir: str, udid: str | None = None, *, system: bool = False, reboot: bool = True,
            progress: Progress | None = None) -> None:
    """Restore backup_dir (the folder that contains <UDID>/) to the phone.

    `system=True` maps to `--system`; some iOS versions only restore app-group data with it
    (pymobiledevice3 issue #1053). We default to False and let the pipeline retry with True.
    """
    cb = progress or (lambda _p: None)
    result: dict = {}

    async def go():
        lockdown = await _connect(udid)
        svc = None
        try:
            result["udid"] = lockdown.udid
            svc = await _service(lockdown)
            await _aw(svc.restore(
                backup_directory=backup_dir, system=system, reboot=reboot, copy=False,
                settings=True, remove=False, password="", source=lockdown.udid, progress_callback=cb,
            ))
        finally:
            if svc is not None:
                await _close_svc(svc)
            await _close(lockdown)

    try:
        _run(go())
    except TypeError:
        src = result.get("udid") or info(udid).udid
        args = ["backup2", "restore", "--no-copy", "--settings", "--source", src]
        if system:
            args.append("--system")
        if not reboot:
            args.append("--no-reboot")
        _cli([*args, backup_dir])
