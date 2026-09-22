"""Parse a decrypted Android `msgstore.db` into the intermediate model.

Targets the "new" schema WhatsApp has shipped since 2022 (tables `message`, `chat`, `jid`,
`message_media`, ...). Column presence is checked at runtime with PRAGMA table_info so
minor schema drift between WhatsApp versions degrades gracefully instead of crashing.

Reference material: KnugiHK/WhatsApp-Chat-Exporter (android_handler.py),
andreas-mausch/whatsapp-viewer schema dump, Belkasoft/Binary Hick forensic write-ups.
"""

from __future__ import annotations

import logging
import pathlib
import re
import sqlite3
from collections.abc import Iterable

from ..model import Archive, Chat, Location, Media, Message, MsgKind, Participant

log = logging.getLogger(__name__)

# message.message_type -> MsgKind. 15 is *revoked*, 20 is *sticker* (a common mix-up).
ANDROID_TYPE_MAP: dict[int, MsgKind] = {
    0: MsgKind.TEXT,
    1: MsgKind.IMAGE,
    2: MsgKind.AUDIO,      # refined to VOICE below using mime/duration
    3: MsgKind.VIDEO,
    4: MsgKind.CONTACT,
    5: MsgKind.LOCATION,
    7: MsgKind.SYSTEM,
    8: MsgKind.SYSTEM,     # legacy call
    9: MsgKind.DOCUMENT,
    10: MsgKind.SYSTEM,    # missed call
    13: MsgKind.GIF,
    14: MsgKind.CONTACT,   # multi-vCard (first card imported)
    15: MsgKind.REVOKED,
    16: MsgKind.LOCATION,  # live location
    20: MsgKind.STICKER,
    24: MsgKind.SYSTEM,    # group invite
}

STATUS_BROADCAST = "status@broadcast"


class MsgstoreError(Exception):
    pass


class _Schema:
    """Introspected table/column map for one database."""

    def __init__(self, conn: sqlite3.Connection):
        self.tables: dict[str, set[str]] = {}
        for (name,) in conn.execute("SELECT name FROM sqlite_master WHERE type IN ('table','view')"):
            cols = {r[1] for r in conn.execute(f'PRAGMA table_info("{name}")')}
            self.tables[name] = cols

    def has(self, table: str, column: str | None = None) -> bool:
        if table not in self.tables:
            return False
        return column is None or column in self.tables[table]

    def col(self, table: str, column: str, default: str = "NULL") -> str:
        """SQL expression: the column if it exists, else a literal default."""
        return f"{table}.{column}" if self.has(table, column) else f"{default} AS {column}"


def open_db(path: str) -> sqlite3.Connection:
    # as_uri() percent-escapes '?', '#', '%' and yields file:///C:/... on Windows
    uri = pathlib.Path(path).resolve().as_uri() + "?mode=ro"
    conn = sqlite3.connect(uri, uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def _digits(s: str | None) -> str:
    return re.sub(r"\D", "", s or "")


def resolve_name(jid: str, contacts: dict[str, str]) -> str | None:
    """contacts is {digits: name}. Match exact, then by 9+ digit suffix (country-code variance)."""
    if not jid.endswith("@s.whatsapp.net"):
        return None
    num = jid.split("@", 1)[0]
    if num in contacts:
        return contacts[num]
    for k, v in contacts.items():
        if len(k) >= 9 and (num.endswith(k[-9:]) or k.endswith(num[-9:])):
            return v
    return None


def parse(path: str, *, contacts: dict[str, str] | None = None, owner_jid: str | None = None,
          include_system: bool = False, include_revoked: bool = False,
          skip_archived: bool = False) -> Archive:
    contacts = contacts or {}
    conn = open_db(path)
    schema = _Schema(conn)
    if not schema.has("message") and schema.has("messages"):
        raise MsgstoreError(
            "This msgstore.db uses the pre-2022 legacy schema. Update WhatsApp on the Android "
            "phone, make a fresh backup, and retry (legacy support is tracked in the roadmap)."
        )
    if not schema.has("message") or not schema.has("chat") or not schema.has("jid"):
        raise MsgstoreError("Not a WhatsApp msgstore.db (missing message/chat/jid tables)")

    lid_map = _load_lid_map(conn, schema)
    phone_to_lid = {pn: lid for lid, pn in lid_map.items()}
    lid_names = _load_lid_names(conn, schema)
    chats: dict[int, Chat] = {}

    # ---- chats ---------------------------------------------------------------------------
    sql = f"""
        SELECT chat._id AS chat_id, jid.raw_string AS jid, jid.server AS server,
               {schema.col('chat', 'subject')}, {schema.col('chat', 'created_timestamp')},
               {schema.col('chat', 'archived', '0')}, {schema.col('chat', 'hidden', '0')}
        FROM chat JOIN jid ON jid._id = chat.jid_row_id
    """
    for row in conn.execute(sql):
        raw = row["jid"]
        if not raw or raw == STATUS_BROADCAST or raw.endswith("@broadcast") or raw.endswith("@newsletter"):
            continue
        original = raw
        raw = lid_map.get(raw, raw)
        if raw.endswith("@lid"):
            log.warning("Chat %s is a LID-only chat with no phone mapping; importing under its LID", raw)
        lid = original if original.endswith("@lid") else phone_to_lid.get(raw)
        if skip_archived and row["archived"]:
            continue
        is_group = raw.endswith("@g.us")
        name = row["subject"] if is_group else (resolve_name(raw, contacts) or lid_names.get(raw))
        chats[row["chat_id"]] = Chat(
            jid=raw, lid=None if is_group else lid, name=name, is_group=is_group,
            created_ms=row["created_timestamp"] or None, archived=bool(row["archived"]),
        )

    # ---- group participants --------------------------------------------------------------
    if schema.has("group_participant_user"):
        gp_sql = """
            SELECT g.raw_string AS group_jid, u.raw_string AS user_jid,
                   COALESCE(gpu.rank, 0) AS rank
            FROM group_participant_user gpu
            JOIN jid g ON g._id = gpu.group_jid_row_id
            JOIN jid u ON u._id = gpu.user_jid_row_id
        """
        by_jid = {c.jid: c for c in chats.values()}
        for row in conn.execute(gp_sql):
            chat = by_jid.get(row["group_jid"])
            if not chat:
                continue
            ujid = lid_map.get(row["user_jid"], row["user_jid"])
            chat.participants.append(Participant(
                jid=ujid, name=resolve_name(ujid, contacts) or lid_names.get(ujid),
                is_admin=int(row["rank"] or 0) > 0,
            ))

    # ---- messages ------------------------------------------------------------------------
    joins = []
    select_extra = []

    def opt_join(table: str, cols: Iterable[str], key: str = "message_row_id") -> None:
        if schema.has(table):
            joins.append(f"LEFT JOIN {table} ON {table}.{key} = message._id")
            for c in cols:
                select_extra.append(f"{schema.col(table, c)}" if schema.has(table, c) else f"NULL AS {c}")
        else:
            select_extra.extend(f"NULL AS {c}" for c in cols)

    opt_join("message_media", ["file_path", "file_size", "mime_type", "media_name",
                               "media_duration", "width", "height", "media_caption"])
    opt_join("message_location", ["latitude", "longitude", "place_name", "place_address"])
    if schema.has("message_quoted", "key_id"):
        joins.append("LEFT JOIN message_quoted ON message_quoted.message_row_id = message._id")
        select_extra.append("message_quoted.key_id AS quoted_key_id")
    else:
        select_extra.append("NULL AS quoted_key_id")
    if schema.has("message_vcard", "vcard"):
        joins.append(
            "LEFT JOIN message_vcard ON message_vcard._id = "
            "(SELECT _id FROM message_vcard v WHERE v.message_row_id = message._id ORDER BY _id LIMIT 1)"
        )
        select_extra.append("message_vcard.vcard AS vcard")
    else:
        select_extra.append("NULL AS vcard")

    msg_sql = f"""
        SELECT message._id AS id, message.chat_row_id, message.from_me, message.key_id,
               message.timestamp, message.message_type, message.text_data,
               {schema.col('message', 'status')}, {schema.col('message', 'starred', '0')},
               sender.raw_string AS sender_jid,
               {', '.join(select_extra)}
        FROM message
        LEFT JOIN jid sender ON sender._id = message.sender_jid_row_id
        {' '.join(joins)}
        WHERE message.chat_row_id IN ({','.join(str(i) for i in chats) or 'NULL'})
        ORDER BY message.chat_row_id, message.timestamp, message._id
    """
    n_skipped = 0
    for row in conn.execute(msg_sql):
        chat = chats.get(row["chat_row_id"])
        if chat is None:
            continue
        mtype = row["message_type"] if row["message_type"] is not None else 0
        kind = ANDROID_TYPE_MAP.get(int(mtype), MsgKind.UNKNOWN)
        if kind is MsgKind.SYSTEM and not include_system:
            n_skipped += 1
            continue
        if kind is MsgKind.REVOKED and not include_revoked:
            n_skipped += 1
            continue
        if not row["key_id"] or row["timestamp"] is None:
            n_skipped += 1
            continue

        media = None
        if row["file_path"] or kind in (MsgKind.IMAGE, MsgKind.VIDEO, MsgKind.AUDIO, MsgKind.DOCUMENT,
                                        MsgKind.GIF, MsgKind.STICKER):
            media = Media(
                android_path=row["file_path"], mime_type=row["mime_type"], size=row["file_size"],
                duration_s=row["media_duration"], width=row["width"], height=row["height"],
                file_name=row["media_name"], caption=row["media_caption"] or row["text_data"],
            )
            if kind is MsgKind.AUDIO and _is_voice_note(row["mime_type"], row["file_path"]):
                kind = MsgKind.VOICE

        location = None
        if kind is MsgKind.LOCATION and row["latitude"] is not None:
            location = Location(
                latitude=float(row["latitude"]), longitude=float(row["longitude"] or 0),
                name=row["place_name"], address=row["place_address"], live=int(mtype) == 16,
            )

        sender = row["sender_jid"]
        if sender:
            sender = lid_map.get(sender, sender)
        text = row["text_data"]
        if kind is MsgKind.UNKNOWN and not text:
            text = "[Unsupported message type from Android — not migrated]"

        chat.messages.append(Message(
            key_id=row["key_id"], from_me=bool(row["from_me"]), timestamp_ms=int(row["timestamp"]),
            kind=kind, text=text, sender_jid=None if row["from_me"] else sender,
            media=media, location=location, vcard=row["vcard"], quoted_key_id=row["quoted_key_id"],
            starred=bool(row["starred"]), android_status=row["status"], android_type=int(mtype),
        ))

    conn.close()
    result = [c for c in chats.values() if c.messages]
    log.info("Parsed %d chats, skipped %d system/revoked messages", len(result), n_skipped)

    # jid -> display name for everyone we could resolve (used for ZPUSHNAME / group members)
    names: dict[str, str] = {}
    for chat in result:
        if not chat.is_group and chat.name:
            names[chat.jid] = chat.name
        for p in chat.participants:
            if p.name:
                names.setdefault(p.jid, p.name)
        for m in chat.messages:
            if m.sender_jid and m.sender_jid not in names:
                n = resolve_name(m.sender_jid, contacts) or lid_names.get(m.sender_jid)
                if n:
                    names[m.sender_jid] = n
    return Archive(owner_jid=owner_jid, chats=result, contacts=names)


def _is_voice_note(mime: str | None, path: str | None) -> bool:
    if mime and "opus" in mime.lower():
        return True
    return bool(path and "/WhatsApp Voice Notes/" in path)


def _load_lid_map(conn: sqlite3.Connection, schema: _Schema) -> dict[str, str]:
    """`<n>@lid` -> `<phone>@s.whatsapp.net` via jid_map (WhatsApp 2025+). Empty if absent."""
    if not schema.has("jid_map", "lid_row_id"):
        return {}
    out = {}
    for row in conn.execute(
        "SELECT l.raw_string AS lid, p.raw_string AS pn FROM jid_map m "
        "JOIN jid l ON l._id = m.lid_row_id JOIN jid p ON p._id = m.jid_row_id"
    ):
        if row["lid"] and row["pn"]:
            out[row["lid"]] = row["pn"]
    return out


def _load_lid_names(conn: sqlite3.Connection, schema: _Schema) -> dict[str, str]:
    if not schema.has("lid_display_name"):
        return {}
    cols = schema.tables["lid_display_name"]
    jid_col = "lid_row_id" if "lid_row_id" in cols else ("jid_row_id" if "jid_row_id" in cols else None)
    name_col = "display_name" if "display_name" in cols else None
    if not jid_col or not name_col:
        return {}
    out = {}
    for row in conn.execute(
        f"SELECT j.raw_string AS jid, d.{name_col} AS name FROM lid_display_name d JOIN jid j ON j._id = d.{jid_col}"
    ):
        if row["jid"] and row["name"]:
            out[row["jid"]] = row["name"]
    return out


def load_vcf(path: str) -> dict[str, str]:
    """Parse a contacts .vcf export (Android Contacts > Export) into {digits: name}."""
    out: dict[str, str] = {}
    name = None
    with open(path, encoding="utf-8", errors="replace") as fh:
        for line in fh:
            line = line.strip()
            if line.startswith("BEGIN:VCARD"):
                name = None
            elif line.startswith("FN"):
                name = line.split(":", 1)[1].strip()
            elif line.startswith("TEL") and name:
                digits = _digits(line.split(":", 1)[-1])
                if digits:
                    out[digits] = name
    return out
