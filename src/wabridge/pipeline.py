"""Orchestrates the end-to-end migration with resumable checkpoints.

Everything lives under one work directory:

    <work>/state.json                 what has been completed
    <work>/android/Databases/…        pulled from the phone
    <work>/android/Media/…            pulled media (optional)
    <work>/android/msgstore.db        decrypted
    <work>/ios_backup/<UDID>/…        unencrypted iPhone backup (this is what gets modified)
    <work>/ios_backup_pristine/…      untouched copy, so a failed restore can be retried
    <work>/convert/ChatStorage.sqlite modified WhatsApp database
    <work>/convert/report.json        import statistics + media jobs
"""

from __future__ import annotations

import dataclasses
import json
import logging
import os
import shutil
import time
from collections.abc import Callable

from .android import adb, crypt15, msgstore
from .ios import backup as iosbackup
from .ios import chatstorage, device

log = logging.getLogger(__name__)
Log = Callable[[str], None]


class PipelineError(Exception):
    pass


class Work:
    def __init__(self, root: str):
        self.root = os.path.abspath(root)
        os.makedirs(self.root, exist_ok=True)
        self.state_path = os.path.join(self.root, "state.json")
        self.state: dict = {}
        if os.path.isfile(self.state_path):
            with open(self.state_path, encoding="utf-8") as fh:
                self.state = json.load(fh)

    def save(self, **kw) -> None:
        self.state.update(kw)
        self.state["updated"] = time.strftime("%Y-%m-%d %H:%M:%S")
        with open(self.state_path, "w", encoding="utf-8") as fh:
            json.dump(self.state, fh, indent=2)

    def path(self, *parts: str) -> str:
        return os.path.join(self.root, *parts)

    # convenience accessors
    @property
    def android_dir(self) -> str:
        return self.path("android")

    @property
    def msgstore_db(self) -> str:
        return self.path("android", "msgstore.db")

    @property
    def media_dir(self) -> str | None:
        p = self.path("android", "Media")
        return p if os.path.isdir(p) else None

    @property
    def backup_root(self) -> str:
        return self.path("ios_backup")

    @property
    def convert_dir(self) -> str:
        return self.path("convert")


# ------------------------------------------------------------------------------ stages

def android_pull(work: Work, *, serial: str | None = None, include_media: bool = True,
                 business: bool = False, out: Log = print) -> dict:
    out("→ Looking for the Android phone …")
    dev = adb.require_device(serial)
    out(f"  found {dev.model or dev.serial}")
    summary = adb.pull_whatsapp(work.android_dir, serial=dev.serial, include_media=include_media,
                                business=business, progress=lambda line: out("  " + line))
    summary["business"] = business
    contacts = adb.export_contacts(dev.serial)
    if contacts:
        with open(work.path("android", "contacts.json"), "w", encoding="utf-8") as fh:
            json.dump(contacts, fh)
        out(f"  exported {len(contacts)} contact numbers for name resolution")
    else:
        out("  (could not read contacts over ADB — pass --contacts <export.vcf> later for names)")
    work.save(android_pull=summary, android_serial=dev.serial)
    return summary


def newest_crypt15(databases_dir: str) -> str:
    cands = [os.path.join(databases_dir, f) for f in os.listdir(databases_dir) if f.endswith(".crypt15")]
    if not cands:
        raise PipelineError(f"No .crypt15 file in {databases_dir}")
    # msgstore.db.crypt15 is the latest; dated files are older snapshots
    plain = [c for c in cands if os.path.basename(c) == "msgstore.db.crypt15"]
    return plain[0] if plain else max(cands, key=os.path.getmtime)


def android_decrypt(work: Work, key: str, *, crypt_file: str | None = None, force: bool = False,
                    out: Log = print) -> str:
    src = crypt_file or newest_crypt15(work.path("android", "Databases"))
    out(f"→ Decrypting {os.path.basename(src)} …")
    try:
        crypt15.decrypt_file(src, work.msgstore_db, key, force=force)
    except crypt15.Crypt15Error as e:
        out(f"  built-in decryptor failed ({e}); trying wa-crypt-tools if installed …")
        if not crypt15.wadecrypt_fallback(src, work.msgstore_db, key):
            raise
    size = os.path.getsize(work.msgstore_db)
    out(f"  OK → {work.msgstore_db} ({size/1e6:.1f} MB)")
    work.save(android_decrypt={"source": src, "output": work.msgstore_db})
    return work.msgstore_db


def load_archive(work: Work, *, contacts_vcf: str | None = None, include_system: bool = False,
                 skip_archived: bool = False):
    contacts: dict[str, str] = {}
    cj = work.path("android", "contacts.json")
    if os.path.isfile(cj):
        with open(cj, encoding="utf-8") as fh:
            contacts.update(json.load(fh))
    if contacts_vcf:
        contacts.update(msgstore.load_vcf(contacts_vcf))
    return msgstore.parse(work.msgstore_db, contacts=contacts, include_system=include_system,
                          skip_archived=skip_archived)


def _domain(work: Work) -> str:
    business = work.state.get("android_pull", {}).get("business", False)
    return iosbackup.WA_BUSINESS_GROUP_DOMAIN if business else iosbackup.WA_GROUP_DOMAIN


def _check_inside_work(work: Work, folder: str) -> None:
    """Refuse to modify a backup that is not our own copy (e.g. the user's real Finder backup)."""
    f = os.path.abspath(folder)
    if os.path.commonpath([f, work.root]) != work.root:
        raise PipelineError(
            f"{folder} is outside the work directory. WaBridge only modifies backups it created itself "
            f"(copy the folder into {work.backup_root} if you really want to use it)."
        )


def ios_backup(work: Work, *, udid: str | None = None, out: Log = print) -> str:
    out("→ Backing up the iPhone (this can take a while; keep it unlocked and plugged in) …")
    out(f"  free disk space here: {free_bytes(work.root) / 1e9:.1f} GB "
        "(a full iPhone backup usually needs roughly the iPhone's used storage)")
    last = [-1]

    def prog(p: float) -> None:
        pct = int(p)
        if pct != last[0] and pct % 5 == 0:
            last[0] = pct
            out(f"  {pct}%")

    # A previous (possibly already modified) backup must not be incrementally updated.
    prev = work.state.get("ios_backup", {}).get("folder")
    if prev and os.path.isdir(prev):
        shutil.rmtree(prev)
    folder = device.backup(work.backup_root, udid=udid, progress=prog)

    # Keep the FIRST backup of this phone untouched forever: it is the rollback point.
    pristine = os.path.join(work.path("ios_backup_pristine"), os.path.basename(folder))
    if os.path.isdir(pristine):
        out(f"  keeping existing pristine copy at {pristine}")
    else:
        _clone_tree(folder, pristine)
        out(f"  pristine copy saved to {pristine}")

    with iosbackup.Backup(folder) as b:
        if not b.has_whatsapp(_domain(work)):
            raise PipelineError(
                "WhatsApp data is not in this backup. Install WhatsApp on the iPhone, register the SAME "
                "phone number, send one message to anyone, then run `wabridge ios backup` again."
            )
    out(f"  OK → {folder}")
    work.save(ios_backup={"folder": folder, "pristine": pristine})
    return folder


def _clone_tree(src: str, dst: str) -> None:
    """Copy a directory; on macOS/APFS use clonefile (`cp -c`) so it costs ~no disk space."""
    import platform
    import subprocess

    os.makedirs(os.path.dirname(dst), exist_ok=True)
    if platform.system() == "Darwin":
        r = subprocess.run(["cp", "-Rc", src, dst], capture_output=True, text=True)
        if r.returncode == 0:
            return
        shutil.rmtree(dst, ignore_errors=True)
    shutil.copytree(src, dst)


def free_bytes(path: str) -> int:
    return shutil.disk_usage(path).free


def ios_rollback(work: Work, *, udid: str | None = None, system: bool = False, out: Log = print) -> None:
    """Restore the untouched pre-migration backup to the iPhone."""
    pristine = work.state.get("ios_backup", {}).get("pristine")
    if not pristine or not os.path.isfile(os.path.join(pristine, "Manifest.db")):
        raise PipelineError("No pristine backup recorded in this work directory")
    out(f"→ Restoring the PRISTINE backup ({pristine}) to the iPhone …")
    device.restore(os.path.dirname(pristine), udid=udid, system=system,
                   progress=lambda p: out(f"  {int(p)}%") if int(p) % 10 == 0 else None)
    work.save(ios_rollback={"done": True})


def convert(work: Work, *, backup_folder: str | None = None, contacts_vcf: str | None = None,
            include_media: bool = True, include_system: bool = False, skip_archived: bool = False,
            out: Log = print) -> chatstorage.ImportReport:
    folder = backup_folder or work.state.get("ios_backup", {}).get("folder") or \
        iosbackup.find_backup_dir(work.backup_root)
    _check_inside_work(work, folder)
    domain = _domain(work)
    out("→ Reading Android database …")
    archive = load_archive(work, contacts_vcf=contacts_vcf, include_system=include_system,
                           skip_archived=skip_archived)
    st = archive.stats()
    out(f"  {st['chats']} chats ({st['groups']} groups), {st['messages']} messages, {st['media']} media")

    # Always start from the untouched database so a re-run (e.g. the media pass after a
    # text-only pass) rebuilds everything instead of deduplicating against our own output.
    pristine = work.state.get("ios_backup", {}).get("pristine")
    source = pristine if pristine and os.path.isfile(os.path.join(pristine, "Manifest.db")) else folder
    out(f"→ Extracting ChatStorage.sqlite from the {'pristine' if source == pristine else 'iPhone'} backup …")
    os.makedirs(work.convert_dir, exist_ok=True)
    for stale in ("ChatStorage.sqlite", "ChatStorage.sqlite-wal", "ChatStorage.sqlite-shm"):
        p = os.path.join(work.convert_dir, stale)
        if os.path.exists(p):
            os.remove(p)
    with iosbackup.Backup(source) as b:
        db = b.extract_chatstorage(work.convert_dir, domain=domain)

    out("→ Writing messages into ChatStorage.sqlite …")
    w = chatstorage.Writer(db)
    rep = w.import_archive(archive, media_root=work.media_dir if include_media else None,
                           include_media=include_media)
    w.finalize()
    media_note = (f"media: {rep.media_linked} linked, {rep.media_missing} missing on Android"
                  if include_media else "media: skipped (--no-media)")
    out(f"  sessions: {rep.sessions_created} new, {rep.sessions_reused} existing; "
        f"messages: {rep.messages_written} written, {rep.messages_skipped_duplicate} already present; "
        + media_note)
    with open(work.path("convert", "report.json"), "w", encoding="utf-8") as fh:
        json.dump(dataclasses.asdict(rep), fh, indent=2)
    work.save(convert={"chatstorage": db, "backup_folder": folder, "domain": domain,
                       "messages": rep.messages_written, "media_jobs": len(rep.media_jobs)})
    return rep


def inject(work: Work, *, backup_folder: str | None = None, out: Log = print) -> None:
    folder = backup_folder or work.state.get("convert", {}).get("backup_folder") or \
        iosbackup.find_backup_dir(work.backup_root)
    _check_inside_work(work, folder)
    domain = work.state.get("convert", {}).get("domain") or _domain(work)
    rp = work.path("convert", "report.json")
    if not os.path.isfile(rp):
        raise PipelineError("Run `wabridge convert` first")
    with open(rp, encoding="utf-8") as fh:
        report = json.load(fh)
    db = work.path("convert", "ChatStorage.sqlite")
    out("→ Injecting ChatStorage.sqlite and media into the backup …")
    with iosbackup.Backup(folder) as b:     # rolls back Manifest.db if anything below raises
        b.put(domain, iosbackup.CHATSTORAGE, db)
        for suffix in ("-wal", "-shm"):
            b.remove(domain, iosbackup.CHATSTORAGE + suffix)
        jobs = report.get("media_jobs", [])
        for i, job in enumerate(jobs, 1):
            b.put(domain, job["relative_path"], job["local_file"],
                  mtime=int(os.path.getmtime(job["local_file"])))
            if i % 200 == 0 or i == len(jobs):
                out(f"  media {i}/{len(jobs)}")
        b.touch_status()
    out("  OK")
    work.save(inject={"backup_folder": folder, "media": len(report.get("media_jobs", []))})


def ios_restore(work: Work, *, udid: str | None = None, system: bool = False, out: Log = print) -> None:
    root = work.backup_root
    out("→ Restoring the modified backup to the iPhone …")
    out("  The phone will reboot. Do NOT unplug it until it shows the lock screen again.")
    last = [-1]

    def prog(p: float) -> None:
        pct = int(p)
        if pct != last[0] and pct % 5 == 0:
            last[0] = pct
            out(f"  {pct}%")

    device.restore(root, udid=udid, system=system, progress=prog)
    out("  Restore command finished. Open WhatsApp on the iPhone once it has rebooted.")
    work.save(ios_restore={"done": True, "system": system})


def migrate(work: Work, *, key: str, serial: str | None = None, udid: str | None = None,
            include_media: bool = True, contacts_vcf: str | None = None, system: bool = False,
            out: Log = print) -> None:
    android_pull(work, serial=serial, include_media=include_media, out=out)
    android_decrypt(work, key, out=out)
    folder = ios_backup(work, udid=udid, out=out)
    convert(work, backup_folder=folder, contacts_vcf=contacts_vcf, include_media=include_media, out=out)
    inject(work, backup_folder=folder, out=out)
    ios_restore(work, udid=udid, system=system, out=out)
