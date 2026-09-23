"""`wabridge wizard` — the whole migration as one guided, self-resuming session.

Design goal: the person only acts when a finger on a phone screen is unavoidable.
Everything else (waiting for devices, retrying, ordering, verification) is automatic.
Progress is recorded in <work>/state.json so re-running picks up where it stopped.
"""

from __future__ import annotations

import getpass
import os
import sys
import time
from collections.abc import Callable

from . import pipeline
from .android import adb, crypt15, msgstore
from .ios import device

Log = Callable[[str], None]

BANNER = """
╭──────────────────────────────────────────────────────────────╮
│  WaBridge — Android → iPhone WhatsApp migration              │
│  I'll do everything I can. I only stop when you must tap     │
│  something on a phone. Ctrl-C any time; re-run to resume.    │
╰──────────────────────────────────────────────────────────────╯
"""


class _UI:
    def __init__(self, out: Log):
        self.out = out

    def step(self, title: str) -> None:
        self.out("")
        self.out(f"━━━ {title} " + "━" * max(0, 58 - len(title)))

    def say(self, *lines: str) -> None:
        for line in lines:
            self.out(line)

    def ask_enter(self, prompt: str = "Press Enter when done") -> None:
        self.out("")
        input(f"  ▶ {prompt} … ")

    def ask_yes(self, prompt: str, default: bool = True) -> bool:
        hint = "Y/n" if default else "y/N"
        ans = input(f"  ▶ {prompt} [{hint}] ").strip().lower()
        if not ans:
            return default
        return ans.startswith("y")

    def ask_secret(self, prompt: str) -> str:
        return getpass.getpass(f"  ▶ {prompt}: ")

    def wait(self, what: str, check: Callable[[], object], *, hint: str = "", every: float = 3.0):
        """Poll `check` until it returns truthy. Shows `hint` once while waiting."""
        shown = False
        spinner = "|/-\\"
        i = 0
        while True:
            try:
                result = check()
            except Exception as e:      # noqa: BLE001 — show and keep polling
                result = None
                msg = (str(e).splitlines() or [type(e).__name__])[0]
                if not shown:
                    self.out(f"  … {what}: {msg}")
            if result:
                sys.stdout.write("\r" + " " * 70 + "\r")
                return result
            if not shown:
                if hint:
                    self.out(hint)
                shown = True
            sys.stdout.write(f"\r  {spinner[i % 4]} waiting for {what} …")
            sys.stdout.flush()
            i += 1
            time.sleep(every)


def run(work: pipeline.Work, *, out: Log = print, text_only_first: bool | None = None) -> int:
    ui = _UI(out)
    out(BANNER)
    out(f"Work folder: {work.root}")
    out(f"Log:         {work.path('wizard.log')}  (send this to get help)")

    # ─────────────────────────────────────────────────────────── 1. Android
    if not os.path.isfile(work.msgstore_db):
        ui.step("1/4  Android phone")
        try:
            adb.find_adb()
        except adb.AdbError as e:
            out(f"  ✗ {e}")
            return 1

        def android_ready():
            devs = adb.devices()
            ready = [d for d in devs if d.state == "device"]
            if ready:
                return ready[0]
            if any(d.state == "unauthorized" for d in devs):
                raise adb.AdbError("phone says 'unauthorized' → look at the Android screen and tap ALLOW")
            return None

        dev = ui.wait(
            "the Android phone", android_ready, hint=(
                "  On the Android phone:\n"
                "    Settings → About phone → tap 'Build number' 7 times\n"
                "    Settings → System → Developer options → turn on 'USB debugging'\n"
                "    Plug it in; when asked 'Allow USB debugging?', tick 'Always allow' and tap ALLOW"
            ),
        )
        out(f"  ✓ Android connected: {dev.model or dev.serial}")

        root = ui.wait("WhatsApp on the phone", lambda: adb.detect_wa_root(dev.serial), hint=(
            "  Open WhatsApp on the Android phone once, then come back."
        ))

        if not adb.has_crypt15(root, dev.serial):
            out("  The phone has no end-to-end encrypted (crypt15) backup yet.")
        ui.say(
            "",
            "  On the Android phone, get your 64-digit key (this is the ONLY secret we need):",
            "    WhatsApp → ⋮ → Settings → Chats → Chat backup → End-to-end encrypted backup → Turn on",
            "    If it offers a passkey/password: More options → 'Use 64-digit encryption key instead'",
            "    → Generate your 64-digit key → LONG-PRESS to copy → paste it in a note → Continue → Create",
            "    (Already on with a password? → Change password → 'I lost my encryption key' → new key)",
            "  Then back on 'Chat backup', tap the green BACK UP button and wait for it to finish.",
        )
        ui.ask_enter("Press Enter when the backup has finished")
        ui.wait("the crypt15 backup file", lambda: adb.has_crypt15(root, dev.serial), hint=(
            "  Still no .crypt15 file — make sure end-to-end encrypted backup is ON and tap BACK UP."
        ))

        key = None
        key_file = work.path("key.txt")
        if os.path.isfile(key_file):
            try:
                key = crypt15.parse_key(key_file).hex()
                out(f"  ✓ Using the 64-digit key from {key_file}")
            except crypt15.Crypt15Error as e:
                out(f"  ✗ key.txt is not a valid key ({e}); I'll ask instead")
        while key is None:
            raw = ui.ask_secret("Paste the 64-digit key (input is hidden)")
            try:
                key = crypt15.parse_key(raw).hex()
            except crypt15.Crypt15Error as e:
                out(f"  ✗ {e}")

        # pull DB first, verify the key, only then spend time on media
        out("  Pulling the encrypted database …")
        adb.pull_databases(work.android_dir, root, serial=dev.serial)
        while True:
            try:
                pipeline.android_decrypt(work, key, out=out)
                break
            except crypt15.Crypt15Error as e:
                out(f"  ✗ {e}")
                ui.say(
                    "  Most likely the backup on the phone was written BEFORE this key existed.",
                    "  On the phone: Settings → Chats → Chat backup → tap BACK UP again.",
                )
                ui.ask_enter("Press Enter when the new backup has finished")
                adb.pull_databases(work.android_dir, root, serial=dev.serial)

        archive = msgstore.parse(work.msgstore_db)
        st = archive.stats()
        out(f"  ✓ Decrypted: {st['chats']} chats ({st['groups']} groups), {st['messages']} messages, "
            f"{st['media']} media attachments")
        for chat in sorted(archive.chats, key=lambda c: -len(c.messages))[:8]:
            out(f"      {len(chat.messages):>6}  {'[group] ' if chat.is_group else ''}{chat.name or chat.jid}")
        if not ui.ask_yes("Does that look like your WhatsApp?"):
            out("  Stopping so you can check the phone. Re-run to continue.")
            return 1

        contacts = adb.export_contacts(dev.serial)
        if contacts:
            import json
            with open(work.path("android", "contacts.json"), "w", encoding="utf-8") as fh:
                json.dump(contacts, fh)
            out(f"  ✓ Read {len(contacts)} contact numbers for names")
        else:
            out("  (Couldn't read the address book over USB — names may show as numbers; fixable later.)")

        size = adb.dir_size_bytes(f"{root}/Media", dev.serial)
        size_txt = f" (~{size / 1e9:.1f} GB)" if size else ""
        if ui.ask_yes(f"Copy photos/videos/voice notes too{size_txt}? This is the slow part"):
            out("  Pulling media — go make a coffee …")
            pulled = adb.pull_media(work.android_dir, root, serial=dev.serial)
            out(f"  ✓ Media pulled: {', '.join(pulled) or 'nothing found'}")
            work.save(android_pull={"root": root, "media_dir": work.path("android", "Media"), "business": False,
                                    "media_pulled": pulled}, android_serial=dev.serial)
        else:
            work.save(android_pull={"root": root, "media_dir": None, "business": False, "media_pulled": []},
                      android_serial=dev.serial)
        out("  ✓ Android side done. Your Android chats are untouched; you can unplug that phone.")
    else:
        out("  ✓ Android data already pulled and decrypted (skipping)")

    # ─────────────────────────────────────────────────────────── 2. iPhone backup
    folder = work.state.get("ios_backup", {}).get("folder")
    if not (folder and os.path.isfile(os.path.join(folder, "Manifest.db"))):
        ui.step("2/4  iPhone")
        try:
            device._require()
        except device.DeviceError as e:
            out(f"  ✗ {e}")
            return 1
        ui.say(
            "  On the iPhone:",
            "    1. Install WhatsApp and register with the SAME phone number.",
            "       (It will say the number is in use elsewhere — that's expected. Skip iCloud restore.)",
            "    2. Send ONE message to anyone, so WhatsApp creates its database.",
            "    3. Plug the iPhone into this Mac, unlock it, and tap TRUST if asked.",
        )
        ui.ask_enter("Press Enter when WhatsApp is registered on the iPhone and it's plugged in")

        info = ui.wait("the iPhone", lambda: device.info(), hint=(
            "  Unlock the iPhone. If it shows 'Trust This Computer?', tap Trust and enter the passcode."
        ))
        out(f"  ✓ iPhone connected: {info.name} (iOS {info.ios_version})")

        if info.will_encrypt:
            ui.say(
                "  This iPhone has 'Encrypt local backup' turned ON. It must be OFF for the migration.",
                "  I can turn it off for you if you type the backup password, or you can untick it in",
                "  Finder → iPhone → 'Encrypt local backup'.",
            )
            if ui.ask_yes("Type the backup password here so I turn it off?"):
                while True:
                    pw = ui.ask_secret("Backup password")
                    try:
                        device.disable_encryption(work.backup_root, pw, info.udid)
                        break
                    except Exception as e:  # noqa: BLE001
                        out(f"  ✗ {(str(e).splitlines() or [type(e).__name__])[0]}")
                        if not ui.ask_yes("Try again?"):
                            break
            ui.wait("backup encryption to be OFF", lambda: not device.info(info.udid).will_encrypt,
                    hint="  Untick 'Encrypt local backup' in Finder, then wait here.")
            out("  ✓ Backup encryption is off")

        while True:
            try:
                folder = pipeline.ios_backup(work, udid=info.udid, out=out)
                break
            except pipeline.PipelineError as e:
                out(f"  ✗ {e}")
                ui.ask_enter("Fix that on the iPhone, then press Enter to back up again")
            except Exception as e:  # noqa: BLE001
                out(f"  ✗ Backup failed: {(str(e).splitlines() or [type(e).__name__])[0]}")
                if not ui.ask_yes("Retry the backup? (keep the iPhone unlocked)"):
                    return 1
    else:
        out("  ✓ iPhone backup already taken (skipping)")

    # ─────────────────────────────────────────────────────────── 3. Convert + inject
    ui.step("3/4  Convert and inject")
    has_media = bool(work.media_dir)
    if text_only_first is None:
        text_only_first = has_media and ui.ask_yes(
            "First run TEXT ONLY (recommended: proves the core path; media is a second pass)")
    include_media = has_media and not text_only_first
    pipeline.convert(work, backup_folder=folder, include_media=include_media, out=out)
    pipeline.inject(work, backup_folder=folder, out=out)

    # ─────────────────────────────────────────────────────────── 4. Restore
    ui.step("4/4  Restore to the iPhone")
    ui.say(
        "  Apple refuses any restore while Find My iPhone is on. On the iPhone:",
        "    Settings → [your name] → Find My → Find My iPhone → OFF  (turn it back on afterwards)",
        "  The iPhone will reboot. Keep it plugged in and unlocked until it shows the lock screen.",
    )
    ui.ask_enter("Press Enter to start the restore")
    system = False
    while True:
        try:
            pipeline.ios_restore(work, system=system, out=out)
        except Exception as e:  # noqa: BLE001
            msg = (str(e).splitlines() or [type(e).__name__])[0]
            out(f"  ✗ Restore failed: {msg}")
            if "MBErrorDomain/211" in msg or "Find My" in msg:
                ui.say("  → Turn OFF Find My iPhone (Settings → [your name] → Find My → Find My iPhone).")
            if not ui.ask_yes("Retry?"):
                return 1
            continue
        ui.say(
            "",
            "  Once the iPhone is back at the lock screen, unlock it and open WhatsApp.",
            "  Groups should show immediately. 1:1 chats sometimes only appear after the next message",
            "  arrives from that person — open a chat and check the history is there.",
        )
        if ui.ask_yes("Do you see your Android chats in WhatsApp on the iPhone?"):
            break
        if not system:
            ui.say("  Some iOS versions only restore app data with the --system flag. Trying that next.")
            system = True
            if ui.ask_yes("Run the restore again with --system?"):
                ui.ask_enter("Wait for the iPhone to finish rebooting, unlock it, then press Enter")
                continue
        ui.say(
            "  It didn't take. Nothing is lost — your Android phone still has everything.",
            f"  Please send the log ({work.path('wizard.log')}) and convert/report.json for a fix.",
        )
        if ui.ask_yes("Restore the iPhone to exactly how it was before (rollback)?", default=False):
            pipeline.ios_rollback(work, system=system, out=out)
        return 1

    if text_only_first and has_media:
        out("")
        if ui.ask_yes("Text worked. Run the media pass now (convert + inject + restore again)?"):
            return run(work, out=out, text_only_first=False)

    ui.step("Done")
    ui.say(
        "  ✓ Migration complete.",
        f"  When you're happy, delete {work.root} — it holds your decrypted chat history.",
    )
    return 0
