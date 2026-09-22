"""Synthetic fixtures: a small Android msgstore.db (new schema), an empty iOS ChatStorage.sqlite,
and a minimal unencrypted iOS backup folder. Shapes follow the schemas documented in DESIGN.md.

Real databases have many more columns; the writer/parsers only rely on the ones created here.
"""

from __future__ import annotations

import os
import plistlib
import sqlite3

from wabridge.ios.backup import CHATSTORAGE, WA_GROUP_DOMAIN, build_mbfile, file_id

ME = "61400000001@s.whatsapp.net"
ALICE = "61400000002@s.whatsapp.net"
BOB_LID = "123456789012345@lid"
BOB_PN = "61400000003@s.whatsapp.net"
GROUP = "61400000001-1700000000@g.us"


def make_msgstore(path: str) -> None:
    conn = sqlite3.connect(path)
    c = conn.cursor()
    c.executescript(
        """
        CREATE TABLE jid (_id INTEGER PRIMARY KEY, user TEXT, server TEXT, agent INTEGER, device INTEGER,
                          type INTEGER, raw_string TEXT UNIQUE);
        CREATE TABLE chat (_id INTEGER PRIMARY KEY, jid_row_id INTEGER UNIQUE, hidden INTEGER, subject TEXT,
                           created_timestamp INTEGER, archived INTEGER, sort_timestamp INTEGER);
        CREATE TABLE message (_id INTEGER PRIMARY KEY AUTOINCREMENT, chat_row_id INTEGER NOT NULL,
                              from_me INTEGER NOT NULL, key_id TEXT NOT NULL, sender_jid_row_id INTEGER,
                              status INTEGER, timestamp INTEGER, received_timestamp INTEGER,
                              message_type INTEGER, text_data TEXT, starred INTEGER, sort_id INTEGER);
        CREATE TABLE message_media (message_row_id INTEGER PRIMARY KEY, chat_row_id INTEGER, file_path TEXT,
                                    file_size INTEGER, mime_type TEXT, media_name TEXT, media_duration INTEGER,
                                    width INTEGER, height INTEGER);
        CREATE TABLE message_location (message_row_id INTEGER PRIMARY KEY, chat_row_id INTEGER,
                                       latitude REAL, longitude REAL, place_name TEXT, place_address TEXT);
        CREATE TABLE message_vcard (_id INTEGER PRIMARY KEY, message_row_id INTEGER, vcard TEXT);
        CREATE TABLE message_quoted (message_row_id INTEGER PRIMARY KEY, chat_row_id INTEGER, key_id TEXT);
        CREATE TABLE group_participant_user (_id INTEGER PRIMARY KEY, group_jid_row_id INTEGER,
                                             user_jid_row_id INTEGER, rank INTEGER, pending INTEGER);
        CREATE TABLE jid_map (lid_row_id INTEGER PRIMARY KEY, jid_row_id INTEGER, sort_id INTEGER);
        """
    )
    jids = [(1, ME), (2, ALICE), (3, BOB_LID), (4, BOB_PN), (5, GROUP), (6, "status@broadcast")]
    for i, raw in jids:
        user, server = raw.split("@")
        c.execute("INSERT INTO jid VALUES (?,?,?,?,?,?,?)", (i, user, server, 0, 0, 1 if server == "g.us" else 0, raw))
    c.execute("INSERT INTO jid_map VALUES (3, 4, 0)")           # Bob's LID -> phone number
    c.execute("INSERT INTO chat VALUES (10, 2, 0, NULL, NULL, 0, 0)")            # Alice
    c.execute("INSERT INTO chat VALUES (11, 5, 0, 'Family', 1700000000000, 0, 0)")  # group
    c.execute("INSERT INTO chat VALUES (12, 6, 0, NULL, NULL, 0, 0)")            # status (ignored)
    c.execute("INSERT INTO group_participant_user VALUES (1, 5, 1, 1, 0)")
    c.execute("INSERT INTO group_participant_user VALUES (2, 5, 2, 0, 0)")
    c.execute("INSERT INTO group_participant_user VALUES (3, 5, 3, 0, 0)")

    t0 = 1_726_000_000_000
    rows = [
        # id, chat, from_me, key, sender, status, ts, type, text, starred
        (100, 10, 0, "K100", 2, 0, t0 + 1000, 0, "hi from alice", 0),
        (101, 10, 1, "K101", None, 13, t0 + 2000, 0, "hello!", 1),
        (102, 10, 0, "K102", 2, 0, t0 + 3000, 1, "look at this", 0),         # image w/ caption
        (103, 10, 1, "K103", None, 13, t0 + 4000, 2, None, 0),                 # voice note
        (104, 10, 0, "K104", 2, 0, t0 + 5000, 5, None, 0),                     # location
        (105, 10, 0, "K105", 2, 0, t0 + 6000, 4, "Bob", 0),                    # contact card
        (106, 10, 0, "K106", 2, 6, t0 + 7000, 7, None, 0),                     # system (skipped)
        (107, 10, 0, "K107", 2, 0, t0 + 8000, 15, None, 0),                    # revoked (skipped)
        (108, 10, 0, "K108", 2, 0, t0 + 9000, 20, None, 0),                    # sticker
        (109, 10, 0, "K109", 2, 0, t0 + 9500, 9, None, 0),                     # document
        (110, 11, 0, "K110", 2, 0, t0 + 10000, 0, "group msg from alice", 0),
        (111, 11, 0, "K111", 3, 0, t0 + 11000, 0, "group msg from bob (lid)", 0),
        (112, 11, 1, "K112", None, 13, t0 + 12000, 3, None, 0),               # video from me
        (113, 12, 0, "K113", 2, 0, t0 + 13000, 1, None, 0),                    # status update (ignored)
    ]
    for r in rows:
        c.execute(
            "INSERT INTO message (_id, chat_row_id, from_me, key_id, sender_jid_row_id, status, timestamp, "
            "received_timestamp, message_type, text_data, starred, sort_id) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (r[0], r[1], r[2], r[3], r[4], r[5], r[6], r[6], r[7], r[8], r[9], r[0]),
        )
    c.execute("INSERT INTO message_media VALUES (102, 10, 'Media/WhatsApp Images/IMG-001.jpg', 1234, 'image/jpeg', NULL, NULL, 640, 480)")
    c.execute("INSERT INTO message_media VALUES (103, 10, 'Media/WhatsApp Voice Notes/202437/PTT-001.opus', 999, 'audio/ogg; codecs=opus', NULL, 7, NULL, NULL)")
    c.execute("INSERT INTO message_media VALUES (108, 10, 'Media/WhatsApp Stickers/STK-001.webp', 50, 'image/webp', NULL, NULL, 512, 512)")
    c.execute("INSERT INTO message_media VALUES (109, 10, 'Media/WhatsApp Documents/report.pdf', 4096, 'application/pdf', 'report.pdf', NULL, NULL, NULL)")
    c.execute("INSERT INTO message_media VALUES (112, 11, 'Media/WhatsApp Video/Sent/VID-001.mp4', 55555, 'video/mp4', NULL, 12, 1280, 720)")
    c.execute("INSERT INTO message_location VALUES (104, 10, -37.81, 144.96, 'Melbourne', 'VIC')")
    c.execute("INSERT INTO message_vcard VALUES (1, 105, 'BEGIN:VCARD\nVERSION:3.0\nFN:Bob Example\nTEL:+61400000003\nEND:VCARD')")
    c.execute("INSERT INTO message_quoted VALUES (101, 10, 'K100')")
    conn.commit()
    conn.close()


def make_media_tree(root: str) -> None:
    files = {
        "WhatsApp Images/IMG-001.jpg": b"\xff\xd8jpegdata",
        "WhatsApp Voice Notes/202437/PTT-001.opus": b"OggS-opus",
        "WhatsApp Stickers/STK-001.webp": b"RIFFwebp",
        "WhatsApp Documents/report.pdf": b"%PDF-1.4",
        # VID-001.mp4 intentionally missing -> exercises the "media missing" path
    }
    for rel, data in files.items():
        p = os.path.join(root, rel)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "wb") as fh:
            fh.write(data)


IOS_ENTITIES = [
    # Z_ENT, Z_NAME
    (1, "WABlacklistItem"), (2, "WAChatProperties"), (3, "WAChatSession"), (4, "WAGroupInfo"),
    (5, "WAGroupMember"), (6, "WAMediaItem"), (7, "WAMessage"), (8, "WAMessageDataItem"),
    (9, "WAMessageInfo"), (10, "WAProfilePictureItem"), (11, "WAProfilePushName"),
]


def make_chatstorage(path: str, *, with_existing_alice: bool = True) -> None:
    conn = sqlite3.connect(path)
    c = conn.cursor()
    c.executescript(
        """
        CREATE TABLE Z_PRIMARYKEY (Z_ENT INTEGER PRIMARY KEY, Z_NAME VARCHAR, Z_SUPER INTEGER, Z_MAX INTEGER);
        CREATE TABLE Z_METADATA (Z_VERSION INTEGER PRIMARY KEY, Z_UUID VARCHAR(255), Z_PLIST BLOB);
        CREATE TABLE ZWACHATSESSION (Z_PK INTEGER PRIMARY KEY, Z_ENT INTEGER, Z_OPT INTEGER, ZARCHIVED INTEGER,
            ZCONTACTABID INTEGER, ZFLAGS INTEGER, ZHIDDEN INTEGER, ZMESSAGECOUNTER INTEGER, ZREMOVED INTEGER,
            ZSESSIONTYPE INTEGER, ZUNREADCOUNT INTEGER, ZGROUPINFO INTEGER, ZLASTMESSAGE INTEGER,
            ZPROPERTIES INTEGER, ZLASTMESSAGEDATE TIMESTAMP, ZCONTACTIDENTIFIER VARCHAR, ZCONTACTJID VARCHAR,
            ZLASTMESSAGETEXT VARCHAR, ZPARTNERNAME VARCHAR, ZSAVEDINPUT VARCHAR);
        CREATE TABLE ZWAMESSAGE (Z_PK INTEGER PRIMARY KEY, Z_ENT INTEGER, Z_OPT INTEGER,
            ZCHILDMESSAGESDELIVEREDCOUNT INTEGER, ZCHILDMESSAGESPLAYEDCOUNT INTEGER, ZCHILDMESSAGESREADCOUNT INTEGER,
            ZDATAITEMVERSION INTEGER, ZDOCID INTEGER, ZENCRETRYCOUNT INTEGER, ZFILTEREDRECIPIENTCOUNT INTEGER,
            ZFLAGS INTEGER, ZGROUPEVENTTYPE INTEGER, ZISFROMME INTEGER, ZMESSAGEERRORSTATUS INTEGER,
            ZMESSAGESTATUS INTEGER, ZMESSAGETYPE INTEGER, ZSORT INTEGER, ZSPOTLIGHTSTATUS INTEGER, ZSTARRED INTEGER,
            ZCHATSESSION INTEGER, ZGROUPMEMBER INTEGER, ZLASTSESSION INTEGER, ZMEDIAITEM INTEGER,
            ZMESSAGEINFO INTEGER, ZPARENTMESSAGE INTEGER, ZMESSAGEDATE TIMESTAMP, ZSENTDATE TIMESTAMP,
            ZFROMJID VARCHAR, ZMEDIASECTIONID VARCHAR, ZPHASH VARCHAR, ZPUSHNAME VARCHAR, ZSTANZAID VARCHAR,
            ZTEXT VARCHAR, ZTOJID VARCHAR);
        CREATE TABLE ZWAMEDIAITEM (Z_PK INTEGER PRIMARY KEY, Z_ENT INTEGER, Z_OPT INTEGER, ZCLOUDSTATUS INTEGER,
            ZFILESIZE INTEGER, ZMEDIAORIGIN INTEGER, ZMOVIEDURATION INTEGER, ZMESSAGE INTEGER, ZLATITUDE FLOAT,
            ZLONGITUDE FLOAT, ZMEDIALOCALPATH VARCHAR, ZMEDIAURL VARCHAR, ZTITLE VARCHAR, ZVCARDNAME VARCHAR,
            ZVCARDSTRING VARCHAR, ZXMPPTHUMBPATH VARCHAR, ZMEDIAKEY BLOB, ZMETADATA BLOB);
        CREATE TABLE ZWAGROUPMEMBER (Z_PK INTEGER PRIMARY KEY, Z_ENT INTEGER, Z_OPT INTEGER, ZISACTIVE INTEGER,
            ZISADMIN INTEGER, ZCHATSESSION INTEGER, ZCONTACTNAME VARCHAR, ZMEMBERJID VARCHAR);
        CREATE TABLE ZWAGROUPINFO (Z_PK INTEGER PRIMARY KEY, Z_ENT INTEGER, Z_OPT INTEGER, ZGENERATION INTEGER,
            ZCHATSESSION INTEGER, ZCREATIONDATE TIMESTAMP, ZOWNERJID VARCHAR);
        CREATE TABLE ZWACHATPROPERTIES (Z_PK INTEGER PRIMARY KEY, Z_ENT INTEGER, Z_OPT INTEGER, ZCHATSESSION INTEGER);
        """
    )
    for ent, name in IOS_ENTITIES:
        c.execute("INSERT INTO Z_PRIMARYKEY VALUES (?,?,?,?)", (ent, name, 0, 0))
    if with_existing_alice:
        # The user already exchanged one message with Alice on the iPhone.
        c.execute("UPDATE Z_PRIMARYKEY SET Z_MAX=1 WHERE Z_NAME IN ('WAChatSession','WAMessage')")
        c.execute(
            "INSERT INTO ZWACHATSESSION (Z_PK, Z_ENT, Z_OPT, ZSESSIONTYPE, ZMESSAGECOUNTER, ZCONTACTJID, ZPARTNERNAME) "
            "VALUES (1, 3, 1, 0, 1, ?, 'Alice')", (ALICE,)
        )
        c.execute(
            "INSERT INTO ZWAMESSAGE (Z_PK, Z_ENT, Z_OPT, ZISFROMME, ZMESSAGETYPE, ZSORT, ZCHATSESSION, "
            "ZMESSAGEDATE, ZTOJID, ZSTANZAID, ZTEXT) VALUES (1, 7, 1, 1, 0, 1, 1, 748000000.0, ?, 'K101', 'hello!')",
            (ALICE,),
        )
    conn.commit()
    conn.close()


def make_ios_backup(root: str, chatstorage_src: str) -> str:
    """Minimal unencrypted backup folder containing WhatsApp's ChatStorage.sqlite."""
    os.makedirs(root, exist_ok=True)
    with open(os.path.join(root, "Manifest.plist"), "wb") as fh:
        plistlib.dump({"IsEncrypted": False, "Version": "10.0", "Applications": {}, "Lockdown": {}}, fh)
    with open(os.path.join(root, "Status.plist"), "wb") as fh:
        plistlib.dump({"BackupState": "new", "IsFullBackup": True, "Version": "3.3"}, fh)
    conn = sqlite3.connect(os.path.join(root, "Manifest.db"))
    conn.executescript(
        """
        CREATE TABLE Files (fileID TEXT PRIMARY KEY, domain TEXT, relativePath TEXT, flags INTEGER, file BLOB);
        CREATE TABLE Properties (key TEXT PRIMARY KEY, value BLOB);
        """
    )
    size = os.path.getsize(chatstorage_src)
    fid = file_id(WA_GROUP_DOMAIN, CHATSTORAGE)
    blob = build_mbfile(CHATSTORAGE, size, 0o100644, 1_700_000_000, 424242)
    conn.execute("INSERT INTO Files VALUES (?,?,?,?,?)", (fid, WA_GROUP_DOMAIN, CHATSTORAGE, 1, blob))
    # a pre-existing directory row and a WAL sibling, as real backups often have
    conn.execute("INSERT INTO Files VALUES (?,?,?,?,?)",
                 (file_id(WA_GROUP_DOMAIN, "Message"), WA_GROUP_DOMAIN, "Message", 2,
                  build_mbfile("Message", 0, 0o040755, 1_700_000_000, 424243)))
    wal_fid = file_id(WA_GROUP_DOMAIN, CHATSTORAGE + "-wal")
    conn.execute("INSERT INTO Files VALUES (?,?,?,?,?)",
                 (wal_fid, WA_GROUP_DOMAIN, CHATSTORAGE + "-wal", 1,
                  build_mbfile(CHATSTORAGE + "-wal", 0, 0o100644, 1_700_000_000, 424244)))
    conn.commit()
    conn.close()
    os.makedirs(os.path.join(root, fid[:2]), exist_ok=True)
    with open(chatstorage_src, "rb") as src, open(os.path.join(root, fid[:2], fid), "wb") as dst:
        dst.write(src.read())
    os.makedirs(os.path.join(root, wal_fid[:2]), exist_ok=True)
    open(os.path.join(root, wal_fid[:2], wal_fid), "wb").close()
    return root
