"""Drives `wabridge wizard` end to end against fake phones."""

from __future__ import annotations

import builtins
import os
import shutil
import sqlite3
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
sys.path.insert(0, os.path.dirname(__file__))

import fixtures  # noqa: E402

from wabridge import pipeline, wizard  # noqa: E402
from wabridge.android import adb, crypt15  # noqa: E402
from wabridge.ios import device  # noqa: E402

KEY = "ab" * 32


class FakePhones:
    """Stands in for adb + pymobiledevice3. Tracks what the wizard did."""

    def __init__(self, tmp: str):
        self.tmp = tmp
        self.msgstore = os.path.join(tmp, "src_msgstore.db")
        fixtures.make_msgstore(self.msgstore)
        with open(self.msgstore, "rb") as fh:
            self.crypt15 = crypt15.encrypt_bytes(fh.read(), KEY)
        self.media_src = os.path.join(tmp, "phone_media")
        fixtures.make_media_tree(self.media_src)
        self.cs = os.path.join(tmp, "ChatStorage.sqlite")
        fixtures.make_chatstorage(self.cs, with_existing_alice=False)
        self.restores: list[dict] = []
        self.will_encrypt = True
        self.android_polls = 0

    # adb ------------------------------------------------------------
    def devices(self):
        self.android_polls += 1
        if self.android_polls == 1:
            return [adb.Device("SER1", "unauthorized")]
        return [adb.Device("SER1", "device", "Pixel_7")]

    def pull_databases(self, dest, root, *, serial=None, progress=None):
        local = os.path.join(dest, "Databases")
        os.makedirs(local, exist_ok=True)
        with open(os.path.join(local, "msgstore.db.crypt15"), "wb") as fh:
            fh.write(self.crypt15)
        return local

    def pull_media(self, dest, root, *, serial=None, progress=None):
        shutil.copytree(self.media_src, os.path.join(dest, "Media"), dirs_exist_ok=True)
        return ["WhatsApp Images"]

    # pymobiledevice3 -------------------------------------------------
    def info(self, udid=None):
        return device.IDevice("UDID1", "Test iPhone", "18.0", self.will_encrypt)

    def disable_encryption(self, backup_dir, pw, udid=None):
        if pw != "pw":
            raise device.DeviceError("Invalid password")
        self.will_encrypt = False

    def backup(self, backup_dir, udid=None, progress=None):
        folder = os.path.join(backup_dir, "UDID1")
        if os.path.isdir(folder):
            shutil.rmtree(folder)
        fixtures.make_ios_backup(folder, self.cs)
        if progress:
            progress(50.0)
        return folder

    def restore(self, backup_dir, udid=None, *, system=False, reboot=True, progress=None):
        self.restores.append({"dir": backup_dir, "system": system})


class WizardTests(unittest.TestCase):
    def run_wizard(self, answers: list[str], secrets: list[str], phones: FakePhones, work: pipeline.Work):
        answers = list(answers)
        secrets = list(secrets)
        log: list[str] = []
        patches = [
            mock.patch.object(adb, "find_adb", return_value="/fake/adb"),
            mock.patch.object(adb, "devices", phones.devices),
            mock.patch.object(adb, "detect_wa_root", return_value="/sdcard/Android/media/com.whatsapp/WhatsApp"),
            mock.patch.object(adb, "has_crypt15", return_value=True),
            mock.patch.object(adb, "pull_databases", phones.pull_databases),
            mock.patch.object(adb, "pull_media", phones.pull_media),
            mock.patch.object(adb, "export_contacts", return_value={"61400000002": "Alice"}),
            mock.patch.object(adb, "dir_size_bytes", return_value=1_500_000_000),
            mock.patch.object(device, "_require", lambda: None),
            mock.patch.object(device, "info", phones.info),
            mock.patch.object(device, "disable_encryption", phones.disable_encryption),
            mock.patch.object(device, "backup", phones.backup),
            mock.patch.object(device, "restore", phones.restore),
            mock.patch.object(builtins, "input", lambda prompt="": answers.pop(0) if answers else ""),
            mock.patch.object(wizard.getpass, "getpass", lambda prompt="": secrets.pop(0)),
            mock.patch.object(wizard.time, "sleep", lambda s: None),
        ]
        for p in patches:
            p.start()
        try:
            rc = wizard.run(work, out=log.append)
        finally:
            for p in patches:
                p.stop()
        return rc, log, answers, secrets

    def test_full_happy_path_text_then_media(self):
        with tempfile.TemporaryDirectory() as d:
            phones = FakePhones(d)
            work = pipeline.Work(os.path.join(d, "work"))
            answers = [
                "",        # Enter: backup finished
                "y",       # looks like your WhatsApp?
                "y",       # copy media?
                "",        # Enter: iPhone registered
                "y",       # type backup password?
                "y",       # text-only first?
                "",        # Enter: start restore
                "y",       # see chats?
                "y",       # run media pass?
                "",        # Enter: start restore (media pass)
                "y",       # see chats?
            ]
            secrets = ["garbage", KEY, "wrong", "pw"]   # bad key first, then bad backup pw
            # note: "wrong" pw -> "Try again?" consumes one answer; add it
            answers.insert(5, "y")
            rc, log, left_answers, left_secrets = self.run_wizard(answers, secrets, phones, work)
            text = "\n".join(log)
            self.assertEqual(rc, 0, text)
            self.assertEqual(left_answers, [])
            self.assertEqual(left_secrets, [])
            self.assertIn("unauthorized", text)                      # waited for ALLOW
            self.assertIn("Key must be the 64-digit key", text)      # rejected garbage key
            self.assertIn("Invalid password", text)                  # rejected wrong backup pw
            self.assertEqual(len(phones.restores), 2)                # text pass + media pass
            self.assertFalse(phones.restores[0]["system"])
            # the second (media) pass linked media because it converted from the pristine copy
            cs = os.path.join(work.convert_dir, "ChatStorage.sqlite")
            conn = sqlite3.connect(cs)
            n_media_paths = conn.execute(
                "SELECT COUNT(*) FROM ZWAMEDIAITEM WHERE ZMEDIALOCALPATH IS NOT NULL").fetchone()[0]
            self.assertEqual(n_media_paths, 4)
            conn.close()
            self.assertTrue(os.path.isdir(work.state["ios_backup"]["pristine"]))
            self.assertIn("Migration complete", text)

    def test_resume_skips_done_stages_and_system_retry(self):
        with tempfile.TemporaryDirectory() as d:
            phones = FakePhones(d)
            phones.will_encrypt = False
            work = pipeline.Work(os.path.join(d, "work"))
            # pretend android + ios backup already done
            os.makedirs(work.path("android", "Databases"))
            fixtures.make_msgstore(work.msgstore_db)
            folder = phones.backup(work.backup_root)
            pristine = os.path.join(work.path("ios_backup_pristine"), "UDID1")
            shutil.copytree(folder, pristine)
            work.save(ios_backup={"folder": folder, "pristine": pristine},
                      android_pull={"media_dir": None, "business": False})
            answers = [
                "",     # start restore
                "n",    # see chats? no
                "y",    # retry with --system?
                "",     # Enter after reboot
                "y",    # see chats now
            ]
            rc, log, left, _ = self.run_wizard(answers, [], phones, work)
            text = "\n".join(log)
            self.assertEqual(rc, 0, text)
            self.assertEqual(left, [])
            self.assertIn("already pulled", text)
            self.assertIn("already taken", text)
            self.assertEqual([r["system"] for r in phones.restores], [False, True])


if __name__ == "__main__":
    unittest.main()
