"""Runs with `pytest` or plain `python -m unittest discover tests`."""

from __future__ import annotations

import json
import os
import sqlite3
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
sys.path.insert(0, os.path.dirname(__file__))

import fixtures  # noqa: E402
from wabridge import pipeline  # noqa: E402
from wabridge.android import crypt15, msgstore  # noqa: E402
from wabridge.ios import backup as iosbackup  # noqa: E402
from wabridge.ios import chatstorage  # noqa: E402
from wabridge.model import MsgKind  # noqa: E402

KEY = "0123456789abcdef" * 4


class Crypt15Tests(unittest.TestCase):
    def test_parse_key_accepts_spaced_hex(self):
        spaced = " ".join(KEY[i:i + 8] for i in range(0, 64, 8))
        self.assertEqual(crypt15.parse_key(spaced), bytes.fromhex(KEY))

    def test_parse_key_rejects_garbage(self):
        with self.assertRaises(crypt15.Crypt15Error):
            crypt15.parse_key("not-a-key")

    def test_derive_is_hkdf_sha256(self):
        from cryptography.hazmat.primitives import hashes
        from cryptography.hazmat.primitives.kdf.hkdf import HKDF

        root = bytes.fromhex(KEY)
        ref = HKDF(algorithm=hashes.SHA256(), length=32, salt=b"\x00" * 32,
                   info=b"backup encryption").derive(root)
        self.assertEqual(crypt15.derive_aes_key(root), ref)

    def test_roundtrip(self):
        with tempfile.TemporaryDirectory() as d:
            db = os.path.join(d, "m.db")
            fixtures.make_msgstore(db)
            with open(db, "rb") as fh:
                plain = fh.read()
            enc = crypt15.encrypt_bytes(plain, KEY)
            self.assertNotIn(b"SQLite format 3", enc)
            self.assertEqual(crypt15.decrypt_bytes(enc, KEY), plain)
            # trailing 16-byte trailer variant is tolerated
            self.assertEqual(crypt15.decrypt_bytes(enc + b"\x00" * 16, KEY), plain)

    def test_iv_is_found_even_when_a_16_char_string_precedes_it(self):
        """A 16-byte version string in the prefix must not be mistaken for the IV."""
        import zlib
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM

        root = bytes.fromhex(KEY)
        iv = bytes(range(16))
        plain = b"SQLite format 3\x00" + b"z" * 64
        ct = AESGCM(crypt15.derive_aes_key(root)).encrypt(iv, zlib.compress(plain), None)
        version = b"2.25.12.75-beta1"                       # exactly 16 bytes, comes first
        inner_iv = bytes([0x0A, 16]) + iv
        header = bytes([0x12, 16]) + version + bytes([0x1A, len(inner_iv)]) + inner_iv
        enc = bytes([len(header)]) + header + ct
        h = crypt15.parse_header(enc)
        self.assertEqual(len(h.iv_candidates), 2)
        self.assertEqual(crypt15.decrypt_bytes(enc, KEY), plain)

    def test_wrong_key_fails_cleanly(self):
        enc = crypt15.encrypt_bytes(b"SQLite format 3\x00" + b"x" * 100, KEY)
        with self.assertRaises(crypt15.Crypt15Error):
            crypt15.decrypt_bytes(enc, "f" * 64)


class MsgstoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = os.path.join(self.tmp.name, "msgstore.db")
        fixtures.make_msgstore(self.db)

    def tearDown(self):
        self.tmp.cleanup()

    def test_parse_chats_and_types(self):
        arc = msgstore.parse(self.db, contacts={"61400000002": "Alice"})
        by = {c.jid: c for c in arc.chats}
        self.assertEqual(set(by), {fixtures.ALICE, fixtures.GROUP})     # status@broadcast dropped
        alice = by[fixtures.ALICE]
        self.assertEqual(alice.name, "Alice")
        kinds = [m.kind for m in alice.messages]
        self.assertEqual(kinds, [MsgKind.TEXT, MsgKind.TEXT, MsgKind.IMAGE, MsgKind.VOICE, MsgKind.LOCATION,
                                 MsgKind.CONTACT, MsgKind.STICKER, MsgKind.DOCUMENT])
        self.assertEqual(alice.messages[1].quoted_key_id, "K100")
        self.assertTrue(alice.messages[1].starred)
        self.assertEqual(alice.messages[2].media.caption, "look at this")
        self.assertAlmostEqual(alice.messages[4].location.latitude, -37.81)
        self.assertIn("FN:Bob Example", alice.messages[5].vcard)

    def test_group_lid_mapping(self):
        arc = msgstore.parse(self.db)
        group = next(c for c in arc.chats if c.is_group)
        self.assertEqual(group.name, "Family")
        senders = [m.sender_jid for m in group.messages]
        self.assertEqual(senders, [fixtures.ALICE, fixtures.BOB_PN, None])   # LID resolved via jid_map
        self.assertEqual({p.jid for p in group.participants}, {fixtures.ME, fixtures.ALICE, fixtures.BOB_PN})
        self.assertTrue(next(p for p in group.participants if p.jid == fixtures.ME).is_admin)

    def test_include_system(self):
        arc = msgstore.parse(self.db, include_system=True, include_revoked=True)
        alice = next(c for c in arc.chats if c.jid == fixtures.ALICE)
        self.assertEqual(len(alice.messages), 10)

    def test_resolve_name_suffix(self):
        self.assertEqual(msgstore.resolve_name("61400000002@s.whatsapp.net", {"0400000002": "A"}), "A")
        self.assertIsNone(msgstore.resolve_name("x@g.us", {"0400000002": "A"}))


class ChatStorageWriterTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.msg = os.path.join(self.tmp.name, "msgstore.db")
        self.cs = os.path.join(self.tmp.name, "ChatStorage.sqlite")
        self.media = os.path.join(self.tmp.name, "Media")
        fixtures.make_msgstore(self.msg)
        fixtures.make_chatstorage(self.cs)
        fixtures.make_media_tree(self.media)
        self.archive = msgstore.parse(self.msg, contacts={"61400000002": "Alice", "61400000003": "Bob"})

    def tearDown(self):
        self.tmp.cleanup()

    def test_import(self):
        w = chatstorage.Writer(self.cs)
        rep = w.import_archive(self.archive, media_root=self.media)
        w.finalize()

        self.assertEqual(rep.sessions_reused, 1)          # Alice existed
        self.assertEqual(rep.sessions_created, 1)         # group
        self.assertEqual(rep.messages_skipped_duplicate, 1)  # K101 already on the iPhone
        self.assertEqual(rep.messages_written, 10)
        self.assertEqual(rep.media_linked, 4)
        self.assertEqual(rep.media_missing, 1)            # VID-001.mp4 not pulled
        self.assertEqual(len(rep.media_jobs), 4)
        self.assertTrue(all(j.relative_path.startswith("Message/Media/") for j in rep.media_jobs))

        conn = sqlite3.connect(self.cs)
        conn.row_factory = sqlite3.Row
        # Core Data bookkeeping
        pk = {r["Z_NAME"]: r["Z_MAX"] for r in conn.execute("SELECT Z_NAME, Z_MAX FROM Z_PRIMARYKEY")}
        self.assertEqual(pk["WAMessage"], conn.execute("SELECT MAX(Z_PK) FROM ZWAMESSAGE").fetchone()[0])
        self.assertEqual(pk["WAChatSession"], 2)
        self.assertEqual(pk["WAGroupMember"], conn.execute("SELECT COUNT(*) FROM ZWAGROUPMEMBER").fetchone()[0])
        ents = {r[0] for r in conn.execute("SELECT DISTINCT Z_ENT FROM ZWAMESSAGE")}
        self.assertEqual(ents, {7})
        # ZSORT strictly increasing, no duplicates
        sorts = [r[0] for r in conn.execute("SELECT ZSORT FROM ZWAMESSAGE ORDER BY Z_PK")]
        self.assertEqual(len(sorts), len(set(sorts)))
        # group session
        g = conn.execute("SELECT * FROM ZWACHATSESSION WHERE ZCONTACTJID=?", (fixtures.GROUP,)).fetchone()
        self.assertEqual(g["ZSESSIONTYPE"], 1)
        self.assertEqual(g["ZPARTNERNAME"], "Family")
        self.assertEqual(g["ZMESSAGECOUNTER"], 3)
        self.assertIsNotNone(g["ZGROUPINFO"])
        self.assertIsNotNone(g["ZLASTMESSAGE"])
        # incoming group message links to a member row with resolved LID
        bob_msg = conn.execute("SELECT * FROM ZWAMESSAGE WHERE ZSTANZAID='K111'").fetchone()
        member = conn.execute("SELECT * FROM ZWAGROUPMEMBER WHERE Z_PK=?", (bob_msg["ZGROUPMEMBER"],)).fetchone()
        self.assertEqual(member["ZMEMBERJID"], fixtures.BOB_PN)
        self.assertEqual(member["ZCONTACTNAME"], "Bob")
        self.assertEqual(bob_msg["ZFROMJID"], fixtures.GROUP)
        self.assertEqual(bob_msg["ZPUSHNAME"], "Bob")
        # outgoing message conventions
        mine = conn.execute("SELECT * FROM ZWAMESSAGE WHERE ZSTANZAID='K112'").fetchone()
        self.assertEqual(mine["ZISFROMME"], 1)
        self.assertEqual(mine["ZTOJID"], fixtures.GROUP)
        self.assertIsNone(mine["ZFROMJID"])
        self.assertEqual(mine["ZMESSAGETYPE"], 2)   # video
        # media item wiring
        img = conn.execute("SELECT * FROM ZWAMESSAGE WHERE ZSTANZAID='K102'").fetchone()
        self.assertEqual(img["ZMESSAGETYPE"], 1)
        self.assertIsNone(img["ZTEXT"])
        mi = conn.execute("SELECT * FROM ZWAMEDIAITEM WHERE Z_PK=?", (img["ZMEDIAITEM"],)).fetchone()
        self.assertEqual(mi["ZMESSAGE"], img["Z_PK"])
        self.assertEqual(mi["ZTITLE"], "look at this")
        self.assertEqual(mi["ZVCARDSTRING"], "image/jpeg")
        self.assertTrue(mi["ZMEDIALOCALPATH"].endswith("-IMG-001.jpg"))
        self.assertTrue(mi["ZMEDIALOCALPATH"].startswith(f"Message/Media/{fixtures.ALICE}/"))
        voice = conn.execute("SELECT ZMESSAGETYPE FROM ZWAMESSAGE WHERE ZSTANZAID='K103'").fetchone()[0]
        self.assertEqual(voice, 3)
        doc = conn.execute(
            "SELECT m.ZTITLE FROM ZWAMEDIAITEM m JOIN ZWAMESSAGE g ON g.ZMEDIAITEM=m.Z_PK WHERE g.ZSTANZAID='K109'"
        ).fetchone()[0]
        self.assertEqual(doc, "report.pdf")
        loc = conn.execute(
            "SELECT m.ZLATITUDE FROM ZWAMEDIAITEM m JOIN ZWAMESSAGE g ON g.ZMEDIAITEM=m.Z_PK WHERE g.ZSTANZAID='K104'"
        ).fetchone()[0]
        self.assertAlmostEqual(loc, -37.81)
        card = conn.execute(
            "SELECT m.ZVCARDNAME FROM ZWAMEDIAITEM m JOIN ZWAMESSAGE g ON g.ZMEDIAITEM=m.Z_PK WHERE g.ZSTANZAID='K105'"
        ).fetchone()[0]
        self.assertEqual(card, "Bob Example")
        # Cocoa epoch conversion
        d = conn.execute("SELECT ZMESSAGEDATE FROM ZWAMESSAGE WHERE ZSTANZAID='K100'").fetchone()[0]
        self.assertAlmostEqual(d, 1_726_000_001 - 978307200, places=3)
        # single-file DB after finalize
        self.assertFalse(os.path.exists(self.cs + "-wal"))
        conn.close()

    def test_existing_newer_iphone_message_keeps_last_and_sort_order(self):
        # Make the pre-existing iPhone message (K101 in fixture) far newer than the Android history.
        conn = sqlite3.connect(self.cs)
        conn.execute("UPDATE ZWAMESSAGE SET ZMESSAGEDATE=800000000.0 WHERE Z_PK=1")
        conn.execute("UPDATE ZWACHATSESSION SET ZLASTMESSAGEDATE=800000000.0, ZLASTMESSAGE=1 WHERE Z_PK=1")
        conn.commit()
        conn.close()
        w = chatstorage.Writer(self.cs)
        w.import_archive(self.archive, media_root=self.media)
        w.finalize()
        conn = sqlite3.connect(self.cs)
        self.assertEqual(conn.execute("SELECT ZLASTMESSAGE FROM ZWACHATSESSION WHERE Z_PK=1").fetchone()[0], 1)
        rows = conn.execute("SELECT ZMESSAGEDATE FROM ZWAMESSAGE ORDER BY ZSORT").fetchall()
        dates = [r[0] for r in rows]
        self.assertEqual(dates, sorted(dates))                       # ZSORT follows chronology
        self.assertEqual(conn.execute("SELECT MAX(ZSORT), COUNT(*) FROM ZWAMESSAGE").fetchone(), (11, 11))
        conn.close()

    def test_media_filenames_do_not_collide(self):
        self.assertNotEqual(
            chatstorage.media_relative_path("x@g.us", "a1-IMG.jpg"),
            chatstorage.media_relative_path("x@g.us", "b2-IMG.jpg"),
        )

    def test_import_is_idempotent(self):
        w = chatstorage.Writer(self.cs)
        w.import_archive(self.archive, media_root=self.media)
        w.finalize()
        w2 = chatstorage.Writer(self.cs)
        rep2 = w2.import_archive(self.archive, media_root=self.media)
        w2.finalize()
        self.assertEqual(rep2.messages_written, 0)
        self.assertEqual(rep2.sessions_created, 0)


class BackupTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.cs = os.path.join(self.tmp.name, "ChatStorage.sqlite")
        fixtures.make_chatstorage(self.cs)
        self.root = fixtures.make_ios_backup(os.path.join(self.tmp.name, "backup", "UDID1"), self.cs)

    def tearDown(self):
        self.tmp.cleanup()

    def test_file_id_matches_known_value(self):
        self.assertEqual(iosbackup.file_id(iosbackup.WA_GROUP_DOMAIN, "ChatStorage.sqlite"),
                         "7c7fba66680ef796b916b067077cc246adacf01d")

    def test_find_backup_dir(self):
        self.assertEqual(iosbackup.find_backup_dir(os.path.join(self.tmp.name, "backup")), self.root)

    def test_extract_put_remove(self):
        with iosbackup.Backup(self.root) as b:
            self.assertTrue(b.has_whatsapp())
            out = b.extract_chatstorage(os.path.join(self.tmp.name, "x"))
            self.assertTrue(os.path.isfile(out))
            self.assertTrue(os.path.isfile(out + "-wal"))

            # replace ChatStorage with a bigger file; Size in the MBFile must follow
            with open(out, "ab") as fh:
                fh.write(b"\x00" * 4096)
            b.put(iosbackup.WA_GROUP_DOMAIN, iosbackup.CHATSTORAGE, out)
            entry = b.get(iosbackup.WA_GROUP_DOMAIN, iosbackup.CHATSTORAGE)
            meta = iosbackup.read_mbfile(entry.blob)
            self.assertEqual(meta["Size"], os.path.getsize(out))
            self.assertEqual(meta["RelativePath"], "ChatStorage.sqlite")
            self.assertEqual(meta["InodeNumber"], 424242)     # kept from original row

            # add a media file: directories are created, blob written to <xx>/<fileID>
            media = os.path.join(self.tmp.name, "IMG-1.jpg")
            with open(media, "wb") as fh:
                fh.write(b"jpeg" * 100)
            rel = "Message/Media/61400000002@s.whatsapp.net/a/b/IMG-1.jpg"
            fid = b.put(iosbackup.WA_GROUP_DOMAIN, rel, media, mtime=1_700_000_100)
            self.assertTrue(os.path.isfile(b.blob_path(fid)))
            e = b.get(iosbackup.WA_GROUP_DOMAIN, rel)
            self.assertEqual(e.flags, 1)
            m = iosbackup.read_mbfile(e.blob)
            self.assertEqual((m["Size"], m["Mode"], m["LastModified"]), (400, 0o100644, 1_700_000_100))
            self.assertEqual(m["RelativePath"], rel)
            self.assertGreater(m["InodeNumber"], 424244)
            for d in ("Message", "Message/Media", "Message/Media/61400000002@s.whatsapp.net",
                      "Message/Media/61400000002@s.whatsapp.net/a",
                      "Message/Media/61400000002@s.whatsapp.net/a/b"):
                de = b.get(iosbackup.WA_GROUP_DOMAIN, d)
                self.assertIsNotNone(de, d)
                self.assertEqual(de.flags, 2)
                self.assertEqual(iosbackup.read_mbfile(de.blob)["Mode"], 0o040755)
            self.assertEqual(b.conn.execute(
                "SELECT COUNT(*) FROM Files WHERE relativePath='Message'").fetchone()[0], 1)

            self.assertTrue(b.remove(iosbackup.WA_GROUP_DOMAIN, iosbackup.CHATSTORAGE + "-wal"))
            self.assertIsNone(b.get(iosbackup.WA_GROUP_DOMAIN, iosbackup.CHATSTORAGE + "-wal"))
            b.touch_status()

    def test_exception_rolls_back_manifest_changes(self):
        with self.assertRaises(RuntimeError):
            with iosbackup.Backup(self.root) as b:
                b.remove(iosbackup.WA_GROUP_DOMAIN, iosbackup.CHATSTORAGE + "-wal")
                raise RuntimeError("boom")
        with iosbackup.Backup(self.root) as b:
            self.assertIsNotNone(b.get(iosbackup.WA_GROUP_DOMAIN, iosbackup.CHATSTORAGE + "-wal"))

    def test_digest_written_for_new_files(self):
        import hashlib
        media = os.path.join(self.tmp.name, "f.bin")
        with open(media, "wb") as fh:
            fh.write(b"data")
        with iosbackup.Backup(self.root) as b:
            b.put(iosbackup.WA_GROUP_DOMAIN, "Message/Media/x/f.bin", media)
            m = iosbackup.read_mbfile(b.get(iosbackup.WA_GROUP_DOMAIN, "Message/Media/x/f.bin").blob)
            self.assertEqual(m["Digest"], hashlib.sha1(b"data").digest())

    def test_encrypted_backup_refused(self):
        import plistlib
        p = os.path.join(self.root, "Manifest.plist")
        with open(p, "wb") as fh:
            plistlib.dump({"IsEncrypted": True}, fh)
        with self.assertRaises(iosbackup.BackupError):
            iosbackup.Backup(self.root)


class PipelineOfflineTests(unittest.TestCase):
    """convert + inject without any device: simulates what `android pull` and `ios backup` produce."""

    def test_convert_then_inject(self):
        with tempfile.TemporaryDirectory() as d:
            work = pipeline.Work(os.path.join(d, "work"))
            os.makedirs(work.path("android", "Databases"))
            fixtures.make_msgstore(work.msgstore_db)
            fixtures.make_media_tree(work.path("android", "Media"))
            with open(work.path("android", "contacts.json"), "w") as fh:
                json.dump({"61400000002": "Alice"}, fh)
            cs = os.path.join(d, "ChatStorage.sqlite")
            fixtures.make_chatstorage(cs, with_existing_alice=False)
            folder = fixtures.make_ios_backup(os.path.join(work.backup_root, "UDID1"), cs)

            # also exercise the decrypt stage with our own encoder
            with open(work.msgstore_db, "rb") as fh:
                enc = crypt15.encrypt_bytes(fh.read(), KEY)
            with open(work.path("android", "Databases", "msgstore.db.crypt15"), "wb") as fh:
                fh.write(enc)
            os.remove(work.msgstore_db)
            logs: list[str] = []
            pipeline.android_decrypt(work, KEY, out=logs.append)
            self.assertTrue(os.path.isfile(work.msgstore_db))

            rep = pipeline.convert(work, backup_folder=folder, out=logs.append)
            self.assertEqual(rep.messages_written, 11)
            self.assertEqual(rep.sessions_created, 2)
            pipeline.inject(work, backup_folder=folder, out=logs.append)

            with iosbackup.Backup(folder) as b:
                entries = b.list_domain(iosbackup.WA_GROUP_DOMAIN, "Message/Media/")
                files = [e for e in entries if e.flags == 1]
                self.assertEqual(len(files), 4)
                for e in files:
                    self.assertTrue(os.path.isfile(b.blob_path(e.file_id)))
                self.assertIsNone(b.get(iosbackup.WA_GROUP_DOMAIN, "ChatStorage.sqlite-wal"))
                # the injected ChatStorage is the converted one
                cs_entry = b.get(iosbackup.WA_GROUP_DOMAIN, iosbackup.CHATSTORAGE)
                conn = sqlite3.connect(b.blob_path(cs_entry.file_id))
                self.assertEqual(conn.execute("SELECT COUNT(*) FROM ZWAMESSAGE").fetchone()[0], 11)
                conn.close()
            self.assertIn("inject", work.state)
            self.assertTrue(any("media: 4 linked" in line for line in logs))

            # a backup outside the work dir is never modified
            outside = fixtures.make_ios_backup(os.path.join(d, "finder-backup", "UDID9"), cs)
            with self.assertRaises(pipeline.PipelineError):
                pipeline.inject(work, backup_folder=outside, out=logs.append)

    def test_msgstore_path_with_special_chars(self):
        with tempfile.TemporaryDirectory() as d:
            weird = os.path.join(d, "what? 100%ab#1")
            os.makedirs(weird)
            db = os.path.join(weird, "msgstore.db")
            fixtures.make_msgstore(db)
            self.assertEqual(len(msgstore.parse(db).chats), 2)
            self.assertEqual(sorted(os.listdir(weird)), ["msgstore.db"])   # nothing created by the URI


class CliTests(unittest.TestCase):
    def test_help_and_version(self):
        from wabridge import cli
        with self.assertRaises(SystemExit) as cm:
            cli.main(["--version"])
        self.assertEqual(cm.exception.code, 0)
        parser = cli.build_parser()
        self.assertIn("migrate", parser.format_help())


if __name__ == "__main__":
    unittest.main()
