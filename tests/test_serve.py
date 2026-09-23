"""Drives the JSON-lines engine (`wabridge serve`) through a full offline migration with fake phones."""

from __future__ import annotations

import io
import json
import logging
import os
import shutil
import sqlite3
import sys
import tempfile
import threading
import time
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
sys.path.insert(0, os.path.dirname(__file__))

import fixtures  # noqa: E402
from test_wizard import KEY, FakePhones  # noqa: E402

from wabridge import pipeline, serve  # noqa: E402
from wabridge.android import adb  # noqa: E402
from wabridge.ios import backup as iosbackup  # noqa: E402
from wabridge.ios import device  # noqa: E402


class Harness:
    """In-process transport: feed requests, collect events per id."""

    def __init__(self, work_dir: str):
        self.out = io.StringIO()
        self.transport = serve.Transport(self.out)
        self.engine = serve.Engine(pipeline.Work(work_dir), self.transport)
        self.n = 0

    def call(self, cmd: str, timeout: float = 30, **args) -> tuple[dict, list[dict]]:
        self.n += 1
        rid = str(self.n)
        start = self.out.tell()
        self.engine.handle({"id": rid, "cmd": cmd, "args": args})
        deadline = time.time() + timeout
        while time.time() < deadline:
            events = self._events_since(start, rid)
            final = [e for e in events if e["type"] in ("result", "error")]
            if final:
                return final[0], events
            time.sleep(0.02)
        raise AssertionError(f"{cmd} did not finish: {self._events_since(start, rid)}")

    def _events_since(self, pos: int, rid: str) -> list[dict]:
        with self.transport.lock:
            text = self.out.getvalue()[pos:]
        out = []
        for line in text.splitlines():
            try:
                e = json.loads(line)
            except json.JSONDecodeError as exc:                # pragma: no cover
                raise AssertionError(f"non-JSON on protocol stream: {line!r}") from exc
            if e.get("id") == rid:
                out.append(e)
        return out


class ServeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.phones = FakePhones(self.tmp.name)
        self.phones.will_encrypt = True
        self.patches = [
            mock.patch.object(adb, "find_adb", return_value="/fake/adb"),
            mock.patch.object(adb, "devices", self.phones.devices),
            mock.patch.object(adb, "detect_wa_root", return_value="/sdcard/Android/media/com.whatsapp/WhatsApp"),
            mock.patch.object(adb, "has_crypt15", return_value=True),
            mock.patch.object(adb, "pull_databases", self.phones.pull_databases),
            mock.patch.object(adb, "pull_media", self._pull_media_with_percent),
            mock.patch.object(adb, "export_contacts", return_value={"61400000002": "Alice"}),
            mock.patch.object(adb, "dir_size_bytes", return_value=1_500_000_000),
            mock.patch.object(device, "info", self._info),
            mock.patch.object(device, "disable_encryption", self.phones.disable_encryption),
            mock.patch.object(device, "backup", self.phones.backup),
            mock.patch.object(device, "restore", self.phones.restore),
        ]
        for p in self.patches:
            p.start()
        self.h = Harness(os.path.join(self.tmp.name, "work"))

    def tearDown(self):
        for p in self.patches:
            p.stop()
        self.tmp.cleanup()

    def _info(self, udid=None):
        i = self.phones.info(udid)
        i.disk_capacity, i.disk_available = 128_000_000_000, 100_000_000_000   # 28 GB used
        return i

    def _pull_media_with_percent(self, dest, root, *, serial=None, progress=None, percent=None):
        if percent:
            percent(50.0)
        r = self.phones.pull_media(dest, root, serial=serial, progress=progress)
        if percent:
            percent(100.0)
        return r

    def test_full_flow(self):
        h = self.h
        final, _ = h.call("ping")
        self.assertEqual(final["type"], "result")
        self.assertIn("version", final["data"])

        final, _ = h.call("devices")
        self.assertEqual(final["data"]["iphone"]["name"], "Test iPhone")
        self.assertTrue(final["data"]["iphone"]["will_encrypt"])
        self.assertEqual(final["data"]["iphone"]["disk_used"], 28_000_000_000)

        # bad key -> stable error code, nothing written
        final, _ = h.call("android.fetch", key="nope")
        self.assertEqual((final["type"], final["code"]), ("error", "bad_key"))
        # wrong (but well-formed) key -> wrong_key with a hint about re-backing up
        final, _ = h.call("android.fetch", key="f" * 64)
        self.assertEqual(final["code"], "wrong_key")
        self.assertIn("Back up", final["hint"])

        final, events = h.call("android.fetch", key=KEY)
        self.assertEqual(final["type"], "result", final)
        self.assertEqual(final["data"]["stats"]["chats"], 2)
        self.assertEqual(final["data"]["top_chats"][0]["name"], "Alice")     # contacts applied
        self.assertTrue(any(e["type"] == "log" for e in events))

        final, events = h.call("android.pull_media")
        self.assertEqual(final["type"], "result")
        pcts = [e["pct"] for e in events if e["type"] == "progress" and e["stage"] == "android_media"]
        self.assertEqual(pcts[-1], 100.0)

        final, _ = h.call("state")
        st = final["data"]["stages"]
        self.assertTrue(st["android_decrypt"] and st["media"])
        self.assertFalse(st["ios_backup"])

        # backup refused while encryption is on; disable it (wrong pw first) then back up
        final, _ = h.call("ios.backup")
        self.assertEqual(final["code"], "encrypted_backup")
        final, _ = h.call("ios.disable_encryption", password="wrong")
        self.assertEqual(final["type"], "error")
        final, _ = h.call("ios.disable_encryption", password="pw")
        self.assertEqual(final["type"], "result")
        self.assertFalse(final["data"]["will_encrypt"])

        # disk-space guard uses the iPhone's used bytes vs free space here
        with mock.patch.object(serve.shutil, "disk_usage", return_value=mock.Mock(free=1_000_000_000)):
            final, _ = h.call("ios.backup")
        self.assertEqual(final["code"], "disk_space")
        final, events = h.call("ios.backup", force=True)
        self.assertEqual(final["type"], "result", final)
        self.assertTrue(os.path.isfile(os.path.join(final["data"]["folder"], "Manifest.db")))
        self.assertTrue(os.path.isdir(final["data"]["pristine"]))

        final, events = h.call("convert", media=False)
        self.assertEqual(final["type"], "result", final)
        self.assertEqual(final["data"]["messages_written"], 11)
        self.assertTrue(any(e["type"] == "data" and e["key"] == "archive_stats" for e in events))

        final, _ = h.call("inject")
        self.assertEqual(final["type"], "result")
        final, _ = h.call("ios.restore")
        self.assertEqual(final["type"], "result")
        self.assertEqual(self.phones.restores[-1]["system"], False)

        # media pass rebuilds from the pristine copy
        final, _ = h.call("convert", media=True)
        self.assertEqual(final["data"]["media_linked"], 4)
        final, events = h.call("inject")
        self.assertEqual(final["data"]["media_files"], 4)
        self.assertTrue(any(e["type"] == "progress" and e["stage"] == "inject" for e in events))
        final, _ = h.call("ios.restore", system=True)
        self.assertEqual(self.phones.restores[-1]["system"], True)

        final, _ = h.call("state")
        self.assertTrue(final["data"]["stages"]["ios_restore"])

        final, _ = h.call("clean")
        self.assertEqual(final["type"], "result")
        self.assertFalse(os.path.exists(h.engine.work.msgstore_db))
        final, _ = h.call("state")
        self.assertFalse(any(final["data"]["stages"].values()))

    def test_busy_guard_and_devices_during_ios_action(self):
        h = self.h
        self.phones.will_encrypt = False
        os.makedirs(h.engine.work.path("android", "Databases"))
        fixtures.make_msgstore(h.engine.work.msgstore_db)

        gate = threading.Event()
        real_backup = self.phones.backup

        def slow_backup(backup_dir, udid=None, progress=None):
            gate.wait(5)
            return real_backup(backup_dir, udid, progress)

        with mock.patch.object(device, "backup", slow_backup):
            h.n += 1
            h.engine.handle({"id": str(h.n), "cmd": "ios.backup", "args": {"force": True}})
            time.sleep(0.1)
            final, _ = h.call("inject")
            self.assertEqual(final["code"], "busy")
            final, _ = h.call("devices")                       # cheap query still answers
            self.assertEqual(final["type"], "result")
            self.assertTrue(final["data"].get("iphone_busy"))  # ...but never touches the iPhone
            gate.set()
            time.sleep(0.5)
        final, _ = h.call("state")
        self.assertTrue(final["data"]["stages"]["ios_backup"])

    def test_error_classification(self):
        self.assertEqual(serve.classify(Exception("Device link error: {'ErrorCode': 211, ... MBErrorDomain/211")).code,
                         "find_my")
        self.assertEqual(serve.classify(Exception("device needs more than 11442486863 bytes free")).code,
                         "disk_space")
        self.assertEqual(serve.classify(adb.AdbError("Device found but state is 'unauthorized'. Accept the USB "
                                                     "debugging prompt on the phone.")).code, "unauthorized")
        self.assertEqual(serve.classify(device.DeviceError("No iPhone found over USB.")).code, "no_iphone")
        self.assertEqual(serve.classify(pipeline.PipelineError("WhatsApp data is not in this backup.")).code,
                         "no_whatsapp_ios")

    def test_rpc_audit_log_never_carries_args(self):
        """engine.log gets one line per request and per answer (never the args, which may hold secrets)."""
        with self.assertLogs("wabridge.rpc", level="INFO") as cm:
            final, _ = self.h.call("ping")
            self.assertEqual(final["type"], "result")
            final, _ = self.h.call("android.fetch", key="0" * 64, serial="R5C0")
            self.assertEqual((final["type"], final["code"]), ("error", "no_android"))   # no phone with that serial
        text = "\n".join(cm.output)
        self.assertIn("ping", text)
        self.assertIn("android.fetch", text)
        self.assertIn("result", text)
        self.assertIn("error", text)
        self.assertNotIn("0" * 64, text)
        self.assertNotIn("R5C0", text)

    def test_rpc_audit_log_skips_device_polling_and_stays_out_of_gui_console(self):
        with self.assertLogs("wabridge.rpc", level="INFO") as cm:
            self.h.call("devices")
            self.h.call("ping")
        self.assertFalse(any("devices" in line for line in cm.output), cm.output)
        # The GUI already sees every protocol event; the audit trail must not be echoed back to it.
        out = io.StringIO()
        fwd = serve._LogForwarder(serve.Transport(out))
        rec = logging.LogRecord("wabridge.rpc", logging.INFO, __file__, 1, "\u2190 1 ping", None, None)
        fwd.emit(rec)
        self.assertEqual(out.getvalue(), "")

    def test_unknown_argument_is_bad_args(self):
        final, _ = self.h.call("ping", bogus=1)
        self.assertEqual((final["type"], final["code"]), ("error", "bad_args"))

    def test_backup_without_whatsapp_never_becomes_the_pristine_copy(self):
        """Backing up before WhatsApp exists on the iPhone must not leave a WhatsApp-less pristine copy
        behind: the retry after installing WhatsApp has to produce a pristine copy convert() can use."""
        h = self.h
        self.phones.will_encrypt = False
        h.call("devices")                                   # FakePhones: first poll is 'unauthorized'
        final, _ = h.call("android.fetch", key=KEY)
        self.assertEqual(final["type"], "result", final)

        real_backup = self.phones.backup
        calls = {"n": 0}

        def backup_without_whatsapp_first(*a, **kw):
            folder = real_backup(*a, **kw)
            calls["n"] += 1
            if calls["n"] == 1:
                conn = sqlite3.connect(os.path.join(folder, "Manifest.db"))
                conn.execute("DELETE FROM Files WHERE relativePath LIKE 'ChatStorage.sqlite%'")
                conn.commit()
                conn.close()
            return folder

        pristine_dir = os.path.join(h.engine.work.path("ios_backup_pristine"), "UDID1")
        with mock.patch.object(device, "backup", backup_without_whatsapp_first):
            final, _ = h.call("ios.backup", force=True)
            self.assertEqual((final["type"], final["code"]), ("error", "no_whatsapp_ios"), final)
            self.assertFalse(os.path.isdir(pristine_dir))
            final, _ = h.call("ios.backup", force=True)
            self.assertEqual(final["type"], "result", final)
        with iosbackup.Backup(final["data"]["pristine"]) as b:
            self.assertTrue(b.has_whatsapp())
        final, _ = h.call("convert", media=False)
        self.assertEqual(final["type"], "result", final)

    def test_main_loop_over_pipes(self):
        stdin = io.StringIO('{"id":"a","cmd":"ping"}\nnot json\n{"id":"b","cmd":"shutdown"}\n')
        out = io.StringIO()
        rc = serve.main(os.path.join(self.tmp.name, "w2"), stdin=stdin, stdout=out)
        self.assertEqual(rc, 0)
        lines = [json.loads(line) for line in out.getvalue().splitlines()]
        types = [(line.get("id"), line["type"]) for line in lines]
        self.assertEqual(types[0], (None, "hello"))
        self.assertIn((None, "error"), types)        # bad json reported, loop continues
        self.assertIn(("b", "result"), types)
        # 'a' finishes on a thread; give it a moment then check it arrived
        time.sleep(0.2)
        lines = [json.loads(line) for line in out.getvalue().splitlines()]
        self.assertTrue(any(line.get("id") == "a" and line["type"] == "result" for line in lines))
        shutil.rmtree(os.path.join(self.tmp.name, "w2"), ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
