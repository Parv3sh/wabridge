"""Write the intermediate model into WhatsApp-for-iOS `ChatStorage.sqlite`.

ChatStorage.sqlite is a Core Data store. We write to it with plain SQL (the approach
proven by mukulkadel/mwatoi) instead of through Core Data + the app's `.momd` model
(residentsummer/watoi), which keeps the tool cross-platform and independent of the IPA.

Rules for hand-written Core Data rows:
  * `Z_ENT` for an entity comes from `Z_PRIMARYKEY.Z_NAME` — never hard-code it.
  * `Z_OPT` starts at 1.
  * New `Z_PK` = `Z_PRIMARYKEY.Z_MAX + 1`, and `Z_MAX` must be bumped afterwards or
    WhatsApp will collide with our rows on its next save.
  * Dates are Cocoa epoch seconds (2001-01-01), stored as REAL.
  * Only columns that actually exist are written (introspected with PRAGMA), so minor
    model changes between WhatsApp versions don't break the import.

Field conventions (from watoi / mwatoi / KnugiHK ios_handler and forensic write-ups;
those marked "unverified" should be validated on a real device — see DESIGN.md §7):
  * ZWAMESSAGE.ZFROMJID = chat JID for incoming messages, NULL when from me;
    ZTOJID = chat JID for outgoing messages. Group senders go through ZGROUPMEMBER.
  * ZMESSAGESTATUS: 8 = read (outgoing), 6 = delivered (incoming)   [weakly verified]
  * Captions live in ZWAMEDIAITEM.ZTITLE; ZWAMESSAGE.ZTEXT stays NULL for media.
"""

from __future__ import annotations

import functools
import hashlib
import logging
import os
import sqlite3
from dataclasses import dataclass, field

from ..model import Archive, Chat, Message, MsgKind

log = logging.getLogger(__name__)

COCOA_EPOCH_OFFSET = 978307200  # seconds between 1970-01-01 and 2001-01-01

# MsgKind -> ZMESSAGETYPE
IOS_TYPE: dict[MsgKind, int] = {
    MsgKind.TEXT: 0,
    MsgKind.IMAGE: 1,
    MsgKind.VIDEO: 2,
    MsgKind.AUDIO: 3,
    MsgKind.VOICE: 3,
    MsgKind.CONTACT: 4,
    MsgKind.LOCATION: 5,
    MsgKind.SYSTEM: 6,
    MsgKind.DOCUMENT: 8,
    MsgKind.GIF: 11,
    MsgKind.REVOKED: 14,      # unverified
    MsgKind.STICKER: 15,
    MsgKind.UNKNOWN: 0,
}
MEDIA_KINDS = {MsgKind.IMAGE, MsgKind.VIDEO, MsgKind.AUDIO, MsgKind.VOICE, MsgKind.DOCUMENT,
               MsgKind.GIF, MsgKind.STICKER}

STATUS_OUTGOING = 8
STATUS_INCOMING = 6
SESSION_INDIVIDUAL = 0
SESSION_GROUP = 1


class ChatStorageError(Exception):
    pass


def to_cocoa(ms: int) -> float:
    return ms / 1000.0 - COCOA_EPOCH_OFFSET


def media_relative_path(chat_jid: str, filename: str) -> str:
    """Message/Media/<chatJID>/<x>/<y>/<filename>. x,y are single hex chars; WhatsApp reads
    ZMEDIALOCALPATH verbatim so the exact bucketing scheme doesn't matter for correctness."""
    h = hashlib.sha1(filename.encode()).hexdigest()
    return f"Message/Media/{chat_jid}/{h[0]}/{h[1]}/{filename}"


@dataclass
class MediaJob:
    local_file: str
    relative_path: str


@dataclass
class ImportReport:
    sessions_created: int = 0
    sessions_reused: int = 0
    messages_written: int = 0
    messages_skipped_duplicate: int = 0
    media_linked: int = 0
    media_missing: int = 0
    media_jobs: list[MediaJob] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


class Writer:
    def __init__(self, db_path: str):
        if not os.path.isfile(db_path):
            raise ChatStorageError(f"{db_path} not found")
        self.path = db_path
        self.conn = sqlite3.connect(db_path)
        self.conn.row_factory = sqlite3.Row
        self.columns: dict[str, list[str]] = {}
        for (name,) in self.conn.execute("SELECT name FROM sqlite_master WHERE type='table'"):
            self.columns[name] = [r[1] for r in self.conn.execute(f'PRAGMA table_info("{name}")')]
        for required in ("Z_PRIMARYKEY", "ZWACHATSESSION", "ZWAMESSAGE"):
            if required not in self.columns:
                raise ChatStorageError(f"{db_path} lacks {required}; not a WhatsApp ChatStorage.sqlite")
        self.entities = {
            r["Z_NAME"]: {"ent": r["Z_ENT"], "max": r["Z_MAX"] or 0}
            for r in self.conn.execute("SELECT Z_ENT, Z_NAME, Z_MAX FROM Z_PRIMARYKEY")
        }
        row = self.conn.execute("SELECT MAX(ZSORT) FROM ZWAMESSAGE").fetchone()
        self._sort = int(row[0] or 0)
        self.conv = self._learn_conventions()

    # ------------------------------------------------------------------ conventions
    def _mode(self, sql: str, params=(), default=None):
        """Most common value of a column among rows WhatsApp itself wrote."""
        try:
            row = self.conn.execute(sql + " GROUP BY 1 ORDER BY COUNT(*) DESC LIMIT 1", params).fetchone()
        except sqlite3.DatabaseError:
            return default
        return row[0] if row and row[0] is not None else default

    def _learn_conventions(self) -> dict:
        """WhatsApp for iOS (2025+) stores protobuf blobs in ZPUSHNAME / ZLASTMESSAGETEXT and expects
        specific ZFLAGS bits. Rather than hard-code a moving target, copy what the real rows in this
        very database do; fall back to values observed in Sept-2026 builds."""
        c = {
            "msg_flags_in": self._mode("SELECT ZFLAGS FROM ZWAMESSAGE WHERE ZISFROMME=0 AND ZMESSAGETYPE=0",
                                       default=16777216),
            "msg_flags_out": self._mode("SELECT ZFLAGS FROM ZWAMESSAGE WHERE ZISFROMME=1", default=16777280),
            "msg_dataver": self._mode("SELECT ZDATAITEMVERSION FROM ZWAMESSAGE", default=3),
            "msg_spot": self._mode("SELECT ZSPOTLIGHTSTATUS FROM ZWAMESSAGE", default=-32768),
            "msg_status_in": self._mode("SELECT ZMESSAGESTATUS FROM ZWAMESSAGE WHERE ZISFROMME=0 AND ZMESSAGETYPE=0",
                                        default=0),
            "msg_status_out": self._mode("SELECT ZMESSAGESTATUS FROM ZWAMESSAGE WHERE ZISFROMME=1", default=8),
            "sess_flags_1to1": self._mode("SELECT ZFLAGS FROM ZWACHATSESSION WHERE ZSESSIONTYPE=0", default=272),
            "sess_flags_group": self._mode("SELECT ZFLAGS FROM ZWACHATSESSION WHERE ZSESSIONTYPE=1", default=256),
            "sess_spot_1to1": self._mode("SELECT ZSPOTLIGHTSTATUS FROM ZWACHATSESSION WHERE ZSESSIONTYPE=0",
                                         default=-5),
            "sess_spot_group": self._mode("SELECT ZSPOTLIGHTSTATUS FROM ZWACHATSESSION WHERE ZSESSIONTYPE=1",
                                          default=1),
        }
        # Is ZPUSHNAME a plain name or an opaque blob in this build? Sample one real row.
        sample = self.conn.execute(
            "SELECT ZPUSHNAME FROM ZWAMESSAGE WHERE ZPUSHNAME IS NOT NULL AND ZPUSHNAME<>'' LIMIT 1").fetchone()
        c["pushname_is_blob"] = bool(sample) and _looks_like_base64_blob(sample[0])
        sample = self.conn.execute(
            "SELECT ZLASTMESSAGETEXT FROM ZWACHATSESSION WHERE ZLASTMESSAGETEXT IS NOT NULL LIMIT 1").fetchone()
        c["lasttext_is_blob"] = bool(sample) and _looks_like_base64_blob(sample[0])
        # Modern builds do not use WAChatProperties at all
        c["use_chat_properties"] = bool(self.conn.execute(
            "SELECT COUNT(*) FROM ZWACHATSESSION WHERE ZPROPERTIES IS NOT NULL").fetchone()[0]) \
            if self.has_table("ZWACHATPROPERTIES") else False
        log.info("conventions learned from existing rows: %s", c)
        return c

    # ------------------------------------------------------------------ core helpers
    def has_table(self, t: str) -> bool:
        return t in self.columns

    def _next_pk(self, entity: str) -> int:
        e = self.entities.get(entity)
        if not e:
            raise ChatStorageError(f"Entity {entity} not in Z_PRIMARYKEY (schema too new/old?)")
        e["max"] += 1
        return e["max"]

    def insert(self, table: str, entity: str, values: dict) -> int:
        cols = self.columns[table]
        pk = self._next_pk(entity)
        row = {"Z_PK": pk, "Z_ENT": self.entities[entity]["ent"], "Z_OPT": 1}
        for k, v in values.items():
            if k in cols:
                row[k] = v
        names = ", ".join(row)
        qs = ", ".join("?" for _ in row)
        self.conn.execute(f"INSERT INTO {table} ({names}) VALUES ({qs})", list(row.values()))
        return pk

    def update(self, table: str, pk: int, values: dict) -> None:
        cols = self.columns[table]
        vals = {k: v for k, v in values.items() if k in cols}
        if not vals:
            return
        sets = ", ".join(f"{k}=?" for k in vals)
        self.conn.execute(f"UPDATE {table} SET {sets} WHERE Z_PK=?", [*vals.values(), pk])

    def _flush_primary_keys(self) -> None:
        for name, e in self.entities.items():
            self.conn.execute("UPDATE Z_PRIMARYKEY SET Z_MAX=? WHERE Z_NAME=?", (e["max"], name))

    def commit(self) -> None:
        self._flush_primary_keys()
        self.conn.commit()

    def finalize(self) -> None:
        """Commit, fold the WAL into the main file and close, leaving a single-file DB."""
        self.commit()
        try:
            self.conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            self.conn.execute("PRAGMA journal_mode=DELETE")
        except sqlite3.DatabaseError as e:   # pragma: no cover
            log.warning("checkpoint failed: %s", e)
        self.conn.close()

    # ------------------------------------------------------------------ lookups
    def find_session(self, *jids: str | None) -> sqlite3.Row | None:
        cands = [j for j in jids if j]
        if not cands:
            return None
        qs = ",".join("?" for _ in cands)
        return self.conn.execute(
            f"SELECT * FROM ZWACHATSESSION WHERE ZCONTACTJID IN ({qs}) OR ZCONTACTIDENTIFIER IN ({qs}) "
            "ORDER BY ZSESSIONTYPE LIMIT 1", cands + cands).fetchone()

    def existing_stanza_ids(self, session_pk: int) -> set[str]:
        return {
            r[0] for r in self.conn.execute(
                "SELECT ZSTANZAID FROM ZWAMESSAGE WHERE ZCHATSESSION=? AND ZSTANZAID IS NOT NULL", (session_pk,)
            )
        }

    def existing_members(self, session_pk: int) -> dict[str, int]:
        if not self.has_table("ZWAGROUPMEMBER"):
            return {}
        return {
            r["ZMEMBERJID"]: r["Z_PK"] for r in self.conn.execute(
                "SELECT Z_PK, ZMEMBERJID FROM ZWAGROUPMEMBER WHERE ZCHATSESSION=?", (session_pk,)
            )
        }

    # ------------------------------------------------------------------ import
    def import_archive(self, archive: Archive, *, media_root: str | None = None,
                       include_media: bool = True) -> ImportReport:
        rep = ImportReport()
        for chat in archive.chats:
            self._import_chat(chat, archive, rep, media_root, include_media)
        if rep.messages_written:
            self.renumber_sort()
        self.commit()
        return rep

    def _import_chat(self, chat: Chat, archive: Archive, rep: ImportReport,
                     media_root: str | None, include_media: bool) -> None:
        display = chat.name or chat.phone or chat.jid.split("@")[0]
        session = self.find_session(chat.jid, chat.lid)
        cv = self.conv
        if session:
            spk = session["Z_PK"]
            rep.sessions_reused += 1
        else:
            # Modern 1:1 sessions: ZCONTACTJID = LID (if known), ZCONTACTIDENTIFIER = phone JID.
            values = {
                "ZCONTACTJID": (chat.lid or chat.jid) if not chat.is_group else chat.jid,
                "ZCONTACTIDENTIFIER": None if chat.is_group else chat.jid,
                "ZPARTNERNAME": display,
                "ZSESSIONTYPE": SESSION_GROUP if chat.is_group else SESSION_INDIVIDUAL,
                "ZMESSAGECOUNTER": 0,
                "ZUNREADCOUNT": 0,
                "ZARCHIVED": 1 if chat.archived else 0,
                "ZHIDDEN": 0,
                "ZREMOVED": 0,
                "ZCONTACTABID": 0,
                "ZIDENTITYVERIFICATIONEPOCH": 0,
                "ZIDENTITYVERIFICATIONSTATE": 0,
                "ZFLAGS": cv["sess_flags_group"] if chat.is_group else cv["sess_flags_1to1"],
                "ZSPOTLIGHTSTATUS": cv["sess_spot_group"] if chat.is_group else cv["sess_spot_1to1"],
                "ZLASTMESSAGEDATE": to_cocoa(chat.messages[-1].timestamp_ms) if chat.messages else None,
            }
            spk = self.insert("ZWACHATSESSION", "WAChatSession", values)
            rep.sessions_created += 1
            if chat.is_group and self.has_table("ZWAGROUPINFO") and "WAGroupInfo" in self.entities:
                gpk = self.insert("ZWAGROUPINFO", "WAGroupInfo", {
                    "ZCHATSESSION": spk,
                    "ZCREATIONDATE": to_cocoa(chat.created_ms) if chat.created_ms else None,
                    "ZGENERATION": 0,
                })
                self.update("ZWACHATSESSION", spk, {"ZGROUPINFO": gpk})
            if cv["use_chat_properties"] and "WAChatProperties" in self.entities:
                ppk = self.insert("ZWACHATPROPERTIES", "WAChatProperties", {"ZCHATSESSION": spk})
                self.update("ZWACHATSESSION", spk, {"ZPROPERTIES": ppk})

        members: dict[str, int] = self.existing_members(spk) if chat.is_group else {}
        if chat.is_group:
            for p in chat.participants:
                if p.jid not in members:
                    members[p.jid] = self._add_member(spk, p.jid, p.name or archive.contacts.get(p.jid),
                                                      p.is_admin, p.active)

        seen = self.existing_stanza_ids(spk)
        last_pk = last_text = None
        last_date = None
        count = 0
        for msg in chat.messages:
            if msg.key_id in seen:
                rep.messages_skipped_duplicate += 1
                continue
            seen.add(msg.key_id)
            member_pk = None
            if chat.is_group and not msg.from_me and msg.sender_jid:
                member_pk = members.get(msg.sender_jid)
                if member_pk is None:
                    member_pk = self._add_member(spk, msg.sender_jid, archive.contacts.get(msg.sender_jid),
                                                 False, False)
                    members[msg.sender_jid] = member_pk
            mpk = self._write_message(chat, spk, msg, member_pk, archive, rep, media_root, include_media)
            last_pk, last_date = mpk, to_cocoa(msg.timestamp_ms)
            last_text = msg.text if msg.kind is MsgKind.TEXT else last_text
            count += 1
            rep.messages_written += 1

        if count:
            total = self.conn.execute(
                "SELECT COUNT(*) FROM ZWAMESSAGE WHERE ZCHATSESSION=?", (spk,)
            ).fetchone()[0]
            values = {"ZMESSAGECOUNTER": total}
            existing_last = session["ZLASTMESSAGEDATE"] if session else None
            # Only move the "last message" pointer forward: the iPhone may already hold a
            # message newer than all of the Android history.
            if existing_last is None or (last_date is not None and last_date >= existing_last):
                values.update({"ZLASTMESSAGE": last_pk, "ZLASTMESSAGEDATE": last_date})
                if not self.conv["lasttext_is_blob"]:
                    values["ZLASTMESSAGETEXT"] = last_text
            self.update("ZWACHATSESSION", spk, values)

    def renumber_sort(self) -> None:
        """Re-assign ZSORT in chronological order across the whole table, so imported history
        interleaves correctly with messages that already existed on the iPhone."""
        rows = self.conn.execute("SELECT Z_PK FROM ZWAMESSAGE ORDER BY ZMESSAGEDATE, Z_PK").fetchall()
        self.conn.executemany("UPDATE ZWAMESSAGE SET ZSORT=? WHERE Z_PK=?",
                              [(i, r[0]) for i, r in enumerate(rows, 1)])
        self._sort = len(rows)

    def _add_member(self, spk: int, jid: str, name: str | None, admin: bool, active: bool) -> int:
        return self.insert("ZWAGROUPMEMBER", "WAGroupMember", {
            "ZCHATSESSION": spk,
            "ZMEMBERJID": jid,
            "ZCONTACTNAME": name or jid.split("@")[0],
            "ZISACTIVE": 1 if active else 0,
            "ZISADMIN": 1 if admin else 0,
        })

    def _write_message(self, chat: Chat, spk: int, msg: Message, member_pk: int | None,
                       archive: Archive, rep: ImportReport, media_root: str | None,
                       include_media: bool) -> int:
        self._sort += 1
        ztype = IOS_TYPE.get(msg.kind, 0)
        is_media = msg.kind in MEDIA_KINDS
        text = None if is_media else msg.text
        if msg.kind is MsgKind.LOCATION and not msg.location:
            ztype, text = 0, msg.text or "[Location]"
        if msg.kind is MsgKind.CONTACT and not msg.vcard:
            ztype, text = 0, msg.text or "[Contact card]"

        sender_name = None
        if not msg.from_me and msg.sender_jid and not self.conv["pushname_is_blob"]:
            sender_name = archive.contacts.get(msg.sender_jid)   # legacy builds only: plain push name

        cv = self.conv
        values = {
            "ZCHATSESSION": spk,
            "ZLASTSESSION": spk,
            "ZISFROMME": 1 if msg.from_me else 0,
            "ZMESSAGETYPE": ztype,
            "ZMESSAGESTATUS": cv["msg_status_out"] if msg.from_me else cv["msg_status_in"],
            "ZMESSAGEERRORSTATUS": 0,
            "ZSORT": self._sort,
            "ZSTARRED": 1 if msg.starred else 0,
            "ZFLAGS": cv["msg_flags_out"] if msg.from_me else cv["msg_flags_in"],
            "ZSPOTLIGHTSTATUS": cv["msg_spot"],
            "ZDOCID": 0,
            "ZCHILDMESSAGESDELIVEREDCOUNT": 0,
            "ZCHILDMESSAGESPLAYEDCOUNT": 0,
            "ZCHILDMESSAGESREADCOUNT": 0,
            "ZDATAITEMVERSION": cv["msg_dataver"],
            "ZFILTEREDRECIPIENTCOUNT": 0,
            "ZENCRETRYCOUNT": 0,
            "ZGROUPEVENTTYPE": 0,
            "ZMESSAGEDATE": to_cocoa(msg.timestamp_ms),
            "ZSENTDATE": to_cocoa(msg.timestamp_ms),
            "ZFROMJID": None if msg.from_me else chat.jid,
            "ZTOJID": chat.jid if msg.from_me else None,
            "ZSTANZAID": msg.key_id,
            "ZTEXT": text,
            "ZPUSHNAME": sender_name,
            "ZGROUPMEMBER": member_pk,
        }
        mpk = self.insert("ZWAMESSAGE", "WAMessage", values)

        media_pk = None
        if (is_media or msg.kind in (MsgKind.LOCATION, MsgKind.CONTACT)) and self.has_table("ZWAMEDIAITEM"):
            media_pk = self._write_media_item(chat, mpk, msg, rep, media_root, include_media)
        if media_pk:
            self.update("ZWAMESSAGE", mpk, {"ZMEDIAITEM": media_pk})
        return mpk

    def _write_media_item(self, chat: Chat, mpk: int, msg: Message, rep: ImportReport,
                          media_root: str | None, include_media: bool) -> int | None:
        item: dict = {"ZMESSAGE": mpk, "ZCLOUDSTATUS": 0, "ZMEDIAORIGIN": 0}
        if msg.kind is MsgKind.LOCATION and msg.location:
            item.update({
                "ZLATITUDE": msg.location.latitude,
                "ZLONGITUDE": msg.location.longitude,
                "ZTITLE": msg.location.name,
            })
            return self.insert("ZWAMEDIAITEM", "WAMediaItem", item)
        if msg.kind is MsgKind.CONTACT and msg.vcard:
            name = _vcard_name(msg.vcard) or msg.text
            item.update({"ZVCARDSTRING": msg.vcard, "ZVCARDNAME": name, "ZTITLE": name})
            return self.insert("ZWAMEDIAITEM", "WAMediaItem", item)

        m = msg.media
        if m is None:
            return None
        item.update({
            "ZFILESIZE": m.size,
            "ZMOVIEDURATION": m.duration_s,
            "ZVCARDSTRING": m.mime_type,           # WhatsApp iOS stores the MIME type here
            "ZTITLE": m.file_name if msg.kind is MsgKind.DOCUMENT else m.caption,
        })
        local = _locate_media(m.android_path, media_root) if (include_media and media_root) else None
        if local:
            # Prefix with a short hash of the Android path so `Images/IMG-1.jpg` and
            # `Images/Sent/IMG-1.jpg` in the same chat never overwrite each other.
            tag = hashlib.sha1((m.android_path or local).encode()).hexdigest()[:8]
            filename = f"{tag}-{os.path.basename(local)}"
            rel = media_relative_path(chat.jid, filename)
            item["ZMEDIALOCALPATH"] = rel
            rep.media_jobs.append(MediaJob(local_file=local, relative_path=rel))
            rep.media_linked += 1
        else:
            rep.media_missing += 1
            if m.caption and msg.kind is not MsgKind.DOCUMENT:
                item["ZTITLE"] = m.caption
            # No local file: WhatsApp shows a placeholder the user can re-download from the
            # sender ("tap to download" works only if the sender still has the file).
        return self.insert("ZWAMEDIAITEM", "WAMediaItem", item)


def _locate_media(android_path: str | None, media_root: str) -> str | None:
    """Map `Media/WhatsApp Images/IMG-...jpg` to a file under the pulled media folder."""
    if not android_path:
        return None
    rel = android_path.lstrip("/")
    if rel.startswith("Media/"):
        rel = rel[len("Media/"):]
    cand = os.path.join(media_root, rel)
    if os.path.isfile(cand):
        return cand
    # fallback: search by basename (handles Sent/ vs root differences)
    return _media_index(media_root).get(os.path.basename(rel))


@functools.lru_cache(maxsize=4)
def _media_index(media_root: str) -> dict[str, str]:
    index: dict[str, str] = {}
    for dirpath, _dirs, files in os.walk(media_root):
        for f in files:
            index.setdefault(f, os.path.join(dirpath, f))
    return index


def _looks_like_base64_blob(value: str) -> bool:
    """Modern WhatsApp stores base64-encoded protobuf in a few former text columns."""
    import base64
    import re

    if not isinstance(value, str) or len(value) < 8 or " " in value:
        return False
    if not re.fullmatch(r"[A-Za-z0-9+/]+={0,2}", value):
        return False
    try:
        raw = base64.b64decode(value, validate=True)
    except Exception:  # noqa: BLE001
        return False
    # protobuf: first byte is a field tag; real words would decode to junk with high bytes anyway
    return len(raw) >= 4 and raw[0] in range(0x08, 0x80)


def _vcard_name(vcard: str) -> str | None:
    for line in vcard.splitlines():
        if line.startswith("FN"):
            return line.split(":", 1)[-1].strip()
    return None
