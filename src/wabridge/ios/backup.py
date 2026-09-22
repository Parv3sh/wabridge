"""Read and modify an *unencrypted* iTunes/Finder-style iOS backup on disk.

Layout (iOS 10+):

    <backup>/Manifest.db      SQLite:  Files(fileID, domain, relativePath, flags, file BLOB)
    <backup>/Manifest.plist   IsEncrypted, Applications, Lockdown, ...
    <backup>/Status.plist     backupState/snapshotState/IsFullBackup
    <backup>/Info.plist
    <backup>/ab/abcdef...     one blob per file, fileID = sha1(domain + "-" + relativePath)

`Files.file` is an NSKeyedArchiver plist describing an `MBFile` (Size, Mode, InodeNumber,
LastModified, Birth, ProtectionClass, UserID/GroupID, RelativePath, ...). To add a file we
clone the blob of an existing WhatsApp file and rewrite those fields, so whatever
this iOS version expects is preserved.

WhatsApp domains:
    AppDomainGroup-group.net.whatsapp.WhatsApp.shared   ChatStorage.sqlite, Message/Media/...
    AppDomain-net.whatsapp.WhatsApp                     app sandbox (prefs)
"""

from __future__ import annotations

import hashlib
import os
import plistlib
import shutil
import sqlite3
import time
from dataclasses import dataclass

WA_GROUP_DOMAIN = "AppDomainGroup-group.net.whatsapp.WhatsApp.shared"
WA_APP_DOMAIN = "AppDomain-net.whatsapp.WhatsApp"
WA_BUSINESS_GROUP_DOMAIN = "AppDomainGroup-group.net.whatsapp.WhatsAppSMB.shared"
CHATSTORAGE = "ChatStorage.sqlite"

FLAG_FILE = 1
FLAG_DIR = 2
MODE_FILE = 0o100644   # 33188
MODE_DIR = 0o040755    # 16877


class BackupError(Exception):
    pass


def file_id(domain: str, relative_path: str) -> str:
    return hashlib.sha1(f"{domain}-{relative_path}".encode()).hexdigest()


@dataclass
class ManifestEntry:
    file_id: str
    domain: str
    relative_path: str
    flags: int
    blob: bytes | None


class Backup:
    def __init__(self, path: str):
        self.path = os.path.abspath(path)
        self.manifest_db = os.path.join(self.path, "Manifest.db")
        self.manifest_plist = os.path.join(self.path, "Manifest.plist")
        if not os.path.isfile(self.manifest_db):
            raise BackupError(f"{self.manifest_db} not found — is this a backup folder (it contains Manifest.db)?")
        self.info = self._load_plist(self.manifest_plist)
        if self.info.get("IsEncrypted"):
            raise BackupError(
                "This backup is encrypted. Turn off 'Encrypt local backup' (or run "
                "`wabridge ios disable-encryption`) and back up again."
            )
        self.conn = sqlite3.connect(self.manifest_db)
        self.conn.row_factory = sqlite3.Row
        self._next_inode = self._max_inode() + 1

    # ------------------------------------------------------------------ basic access
    @staticmethod
    def _load_plist(p: str) -> dict:
        if not os.path.isfile(p):
            return {}
        with open(p, "rb") as fh:
            return plistlib.load(fh)

    def close(self) -> None:
        self.conn.commit()
        self.conn.close()

    def __enter__(self) -> Backup:
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        if exc_type is not None:
            # never leave a half-applied inject behind
            self.conn.rollback()
            self.conn.close()
        else:
            self.close()

    def blob_path(self, fid: str) -> str:
        return os.path.join(self.path, fid[:2], fid)

    def get(self, domain: str, relative_path: str) -> ManifestEntry | None:
        row = self.conn.execute(
            "SELECT fileID, domain, relativePath, flags, file FROM Files WHERE domain=? AND relativePath=?",
            (domain, relative_path),
        ).fetchone()
        if not row:
            return None
        return ManifestEntry(row["fileID"], row["domain"], row["relativePath"], row["flags"], row["file"])

    def list_domain(self, domain: str, prefix: str = "") -> list[ManifestEntry]:
        rows = self.conn.execute(
            "SELECT fileID, domain, relativePath, flags, file FROM Files "
            "WHERE domain=? AND relativePath LIKE ? ORDER BY relativePath",
            (domain, prefix + "%"),
        ).fetchall()
        return [ManifestEntry(r["fileID"], r["domain"], r["relativePath"], r["flags"], r["file"]) for r in rows]

    def has_whatsapp(self, domain: str = WA_GROUP_DOMAIN) -> bool:
        return self.get(domain, CHATSTORAGE) is not None

    def extract(self, domain: str, relative_path: str, dest: str) -> str:
        entry = self.get(domain, relative_path)
        if not entry or entry.flags != FLAG_FILE:
            raise BackupError(f"{domain}/{relative_path} not in backup")
        src = self.blob_path(entry.file_id)
        if not os.path.isfile(src):
            raise BackupError(f"Blob missing on disk for {relative_path} ({src})")
        os.makedirs(os.path.dirname(os.path.abspath(dest)) or ".", exist_ok=True)
        shutil.copyfile(src, dest)
        return dest

    def extract_chatstorage(self, dest_dir: str, domain: str = WA_GROUP_DOMAIN) -> str:
        """Copy ChatStorage.sqlite and, if present, its -wal/-shm siblings."""
        os.makedirs(dest_dir, exist_ok=True)
        out = self.extract(domain, CHATSTORAGE, os.path.join(dest_dir, CHATSTORAGE))
        for suffix in ("-wal", "-shm"):
            if self.get(domain, CHATSTORAGE + suffix):
                self.extract(domain, CHATSTORAGE + suffix, os.path.join(dest_dir, CHATSTORAGE + suffix))
        return out

    # ------------------------------------------------------------------ MBFile blobs
    def _max_inode(self) -> int:
        """Highest InodeNumber in the manifest. iOS uses it to detect hard links, so new files
        must never collide with an existing one."""
        best = 1_000_000
        for (blob,) in self.conn.execute("SELECT file FROM Files WHERE file IS NOT NULL"):
            try:
                d = _mbfile_dict(plistlib.loads(blob))
                best = max(best, int(d.get("InodeNumber", 0)))
            except Exception:
                continue
        return best

    def _template_blob(self, domain: str) -> bytes | None:
        row = self.conn.execute(
            "SELECT file FROM Files WHERE domain=? AND flags=1 AND file IS NOT NULL LIMIT 1", (domain,)
        ).fetchone()
        return row[0] if row else None

    def _make_blob(self, domain: str, relative_path: str, size: int, *, is_dir: bool,
                   mtime: int | None = None, digest: bytes | None = None) -> bytes:
        mtime = int(mtime or time.time())
        template = self._template_blob(domain)
        inode = self._next_inode
        self._next_inode += 1
        if template:
            try:
                return _rewrite_mbfile(template, relative_path, size, MODE_DIR if is_dir else MODE_FILE,
                                       mtime, inode, digest)
            except Exception:
                pass
        return build_mbfile(relative_path, size, MODE_DIR if is_dir else MODE_FILE, mtime, inode, digest=digest)

    # ------------------------------------------------------------------ mutations
    def ensure_dir(self, domain: str, relative_path: str) -> None:
        """Make sure every parent directory of `relative_path` has a flags=2 row."""
        parts = relative_path.split("/")[:-1]
        for i in range(1, len(parts) + 1):
            rel = "/".join(parts[:i])
            if self.get(domain, rel):
                continue
            fid = file_id(domain, rel)
            self.conn.execute(
                "INSERT INTO Files(fileID, domain, relativePath, flags, file) VALUES (?,?,?,?,?)",
                (fid, domain, rel, FLAG_DIR, self._make_blob(domain, rel, 0, is_dir=True)),
            )

    def put(self, domain: str, relative_path: str, local_file: str, *, mtime: int | None = None) -> str:
        """Add or replace a file in the backup. Returns the fileID."""
        size = os.path.getsize(local_file)
        fid = file_id(domain, relative_path)
        self.ensure_dir(domain, relative_path)
        dest = self.blob_path(fid)
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        shutil.copyfile(local_file, dest)
        digest = _sha1_file(dest)
        existing = self.get(domain, relative_path)
        if existing and existing.blob:
            try:
                blob = _rewrite_mbfile(existing.blob, relative_path, size, MODE_FILE,
                                       int(mtime or time.time()), None, digest)
            except Exception:
                blob = self._make_blob(domain, relative_path, size, is_dir=False, mtime=mtime, digest=digest)
            self.conn.execute("UPDATE Files SET flags=?, file=? WHERE fileID=?", (FLAG_FILE, blob, fid))
        else:
            blob = self._make_blob(domain, relative_path, size, is_dir=False, mtime=mtime, digest=digest)
            self.conn.execute(
                "INSERT INTO Files(fileID, domain, relativePath, flags, file) VALUES (?,?,?,?,?)",
                (fid, domain, relative_path, FLAG_FILE, blob),
            )
        return fid

    def remove(self, domain: str, relative_path: str) -> bool:
        entry = self.get(domain, relative_path)
        if not entry:
            return False
        self.conn.execute("DELETE FROM Files WHERE fileID=?", (entry.file_id,))
        p = self.blob_path(entry.file_id)
        if os.path.isfile(p):
            os.remove(p)
        return True

    def touch_status(self) -> None:
        """Refresh the Date in Manifest.plist / Status.plist so the device sees a fresh snapshot."""
        import datetime as _dt

        # plistlib wants a naive UTC datetime
        now = _dt.datetime.now(_dt.timezone.utc).replace(tzinfo=None)
        for name in ("Manifest.plist", "Status.plist"):
            p = os.path.join(self.path, name)
            if not os.path.isfile(p):
                continue
            with open(p, "rb") as fh:
                d = plistlib.load(fh)
            d["Date"] = now
            with open(p, "wb") as fh:
                plistlib.dump(d, fh, fmt=plistlib.FMT_BINARY)


# ---------------------------------------------------------------------- MBFile helpers

def _mbfile_dict(plist: dict) -> dict:
    objs = plist["$objects"]
    root = plist["$top"]["root"]
    idx = root.data if isinstance(root, plistlib.UID) else int(root)
    d = objs[idx]
    if not isinstance(d, dict):
        raise BackupError("Unexpected MBFile structure")
    return d


def _rewrite_mbfile(blob: bytes, relative_path: str, size: int, mode: int, mtime: int,
                    inode: int | None, digest: bytes | None) -> bytes:
    plist = plistlib.loads(blob)
    objs = plist["$objects"]
    d = _mbfile_dict(plist)
    d["Size"] = size
    d["Mode"] = mode
    d["LastModified"] = mtime
    d["LastStatusChange"] = mtime
    d["Birth"] = mtime
    if inode is not None:
        d["InodeNumber"] = inode
    if digest is not None:
        d["Digest"] = digest          # only ever set when we know the blob's SHA-1
    elif "Digest" in d:
        del d["Digest"]               # stale digest from the template would be wrong
    if "EncryptionKey" in d:       # cannot exist in an unencrypted backup, but be safe
        del d["EncryptionKey"]
    rp = d.get("RelativePath")
    if isinstance(rp, plistlib.UID):
        objs[rp.data] = relative_path
    else:
        d["RelativePath"] = relative_path
    return plistlib.dumps(plist, fmt=plistlib.FMT_BINARY)


def build_mbfile(relative_path: str, size: int, mode: int, mtime: int, inode: int, *,
                 protection_class: int = 3, uid: int = 501, gid: int = 501,
                 digest: bytes | None = None) -> bytes:
    """Construct an MBFile archive from scratch (used when no template row exists)."""
    body = {
        "$class": plistlib.UID(3),
        "Birth": mtime,
        "Flags": 0,
        "GroupID": gid,
        "InodeNumber": inode,
        "LastModified": mtime,
        "LastStatusChange": mtime,
        "Mode": mode,
        "ProtectionClass": protection_class,
        "RelativePath": plistlib.UID(2),
        "Size": size,
        "UserID": uid,
    }
    if digest is not None:
        body["Digest"] = digest
    plist = {
        "$version": 100000,
        "$archiver": "NSKeyedArchiver",
        "$top": {"root": plistlib.UID(1)},
        "$objects": [
            "$null",
            body,
            relative_path,
            {"$classes": ["MBFile", "NSObject"], "$classname": "MBFile"},
        ],
    }
    return plistlib.dumps(plist, fmt=plistlib.FMT_BINARY)


def _sha1_file(path: str) -> bytes:
    h = hashlib.sha1()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.digest()


def read_mbfile(blob: bytes) -> dict:
    """Decode an MBFile blob into a plain dict (RelativePath resolved). For debugging/tests."""
    plist = plistlib.loads(blob)
    d = dict(_mbfile_dict(plist))
    rp = d.get("RelativePath")
    if isinstance(rp, plistlib.UID):
        d["RelativePath"] = plist["$objects"][rp.data]
    d.pop("$class", None)
    return d


def find_backup_dir(root: str) -> str:
    """pymobiledevice3 writes <root>/<UDID>/Manifest.db; accept either level."""
    if os.path.isfile(os.path.join(root, "Manifest.db")):
        return root
    subs = [os.path.join(root, d) for d in os.listdir(root) if os.path.isdir(os.path.join(root, d))]
    subs = [s for s in subs if os.path.isfile(os.path.join(s, "Manifest.db"))]
    if len(subs) == 1:
        return subs[0]
    if not subs:
        raise BackupError(f"No Manifest.db under {root}")
    raise BackupError(f"Several backups under {root}; pass the UDID folder explicitly")
