# WaBridge

**Free, open-source WhatsApp migration from Android to iPhone — chats, groups and media, without factory-resetting the iPhone.**

The paid tools (Dr.Fone, MobileTrans, Mutsapper, Wazzap Migrator…) all do the same thing under the hood: decrypt the Android database, rewrite it into WhatsApp-for-iOS's `ChatStorage.sqlite`, slip it into an iPhone backup and restore that backup. Every one of those steps can be done with open-source software. WaBridge is that pipeline, automated, GPL-licensed, and free forever.

> **Status: alpha — text migration works on real devices; media does not yet.** Tested 2026-09-22 on Samsung Galaxy Z Fold4 (Android) → iPhone 13 (iOS 27.2): all 57,816 messages across 192 chats and groups restored and open correctly in WhatsApp. Media (photos, videos, voice notes) imported as placeholders that didn't open in that test; a likely cause (directory permissions in the backup manifest) has since been fixed in code but **not yet re-tested on a device** — see DESIGN.md §7. If you run the media pass, please report the result in an issue. **Always keep your Android backup.**

## How it works

```
Android phone ─adb pull─▶ msgstore.db.crypt15 + Media/   (no root needed)
                            │  your 64-digit backup key
                            ▼
                        msgstore.db ──parse──▶ neutral model ──write──▶ ChatStorage.sqlite
                                                                             │
iPhone ─backup─▶ unencrypted backup folder ─────────────── inject ◀──────────┘
                                                              │
iPhone ◀─────────────────────────── restore modified backup ──┘
```

1. On Android, WhatsApp's end-to-end encrypted backup gives you a **64-digit key**. With it, the local `msgstore.db.crypt15` decrypts without root.
2. On the iPhone, WhatsApp is installed and registered with the **same phone number**. A normal (unencrypted) backup of the iPhone is taken.
3. WaBridge converts the Android messages into WhatsApp-for-iOS's Core Data database inside that backup, adds the media files, and restores the backup. WhatsApp on the iPhone opens with your history.

The official free path, Apple's *Move to iOS*, also works but requires a **factory-reset** iPhone. WaBridge is for everyone who already set the iPhone up.

## Requirements

* Python 3.10+
* [Android platform-tools](https://developer.android.com/tools/releases/platform-tools) (`adb`) on your PATH
* USB debugging enabled on the Android phone
* iPhone: WhatsApp installed and activated with the same number; **"Encrypt local backup" turned off**
* Windows only: iTunes (or Apple Mobile Device Support) for the USB driver. Linux: `usbmuxd`. macOS: nothing extra.

## Desktop app

A guided app (Tauri + React shell around the same engine) lives in [`gui/`](gui/). It walks
through the five steps, watches for both phones, shows progress, and only stops when a tap on a
phone is needed. Build it with `./build-app.sh` (Rust + Node 20 required) or run it in dev mode
with `./build-app.sh dev`; installers for macOS, Windows and Linux are produced by the release
workflow on every `v*` tag. See [`gui/README.md`](gui/README.md).

## Quick start in the terminal (no setup knowledge needed)

Download/unzip, open a terminal in the folder, and run **one command**:

```bash
./start.sh          # macOS / Linux
start.bat           # Windows (install iTunes from the Microsoft Store first, for the USB driver)
```

It installs Python, adb and the tool into the folder without admin rights, then launches
`wabridge wizard`, which does everything automatically and only stops when you must tap
something on a phone (allow USB debugging, copy the 64-digit key, tap Trust, …). Ctrl-C and
re-run any time; it resumes where it stopped. A full log lands in `wabridge-work/wizard.log`.

## Manual usage

```bash
pip install "wabridge[ios]"        # once published; until then: pip install -e ".[ios]"
wabridge doctor                     # checks adb, pymobiledevice3, connected devices
```

The one-shot command:

```bash
wabridge migrate --key "1234 5678 …(64 digits)…"
```

Or step by step (each step is resumable; everything lands in `./wabridge-work/`):

```bash
wabridge android pull                  # Databases/ + Media/ over ADB
wabridge android decrypt --key …       # -> msgstore.db
wabridge android inspect               # sanity check: chats & message counts
wabridge ios backup                    # full unencrypted iPhone backup
wabridge convert [--contacts my.vcf]   # write chats into the backup's ChatStorage.sqlite
wabridge inject                        # ChatStorage.sqlite + media into the backup
wabridge ios restore [--system]        # push it back to the iPhone (it will reboot)
wabridge ios rollback                  # undo: restore the untouched pre-migration backup
```

Step-by-step instructions for non-technical users, including how to find the 64-digit key, are in [docs/USER_GUIDE.md](docs/USER_GUIDE.md).

## Project layout

```
src/wabridge/
  model.py              platform-neutral Chat / Message / Media dataclasses
  android/adb.py        find device, pull WhatsApp folder, export contacts
  android/crypt15.py    .crypt15 decryption (HKDF + AES-256-GCM + zlib), no external tool
  android/msgstore.py   new-schema msgstore.db parser, LID -> phone mapping
  ios/backup.py         Manifest.db reader/writer, MBFile blobs, media injection
  ios/chatstorage.py    ChatStorage.sqlite writer (Core Data via plain SQL)
  ios/device.py         pymobiledevice3 wrapper (backup / restore / encryption)
  pipeline.py           resumable stages + `migrate`
  wizard.py             guided interactive terminal session (polls devices, resumes, logs)
  serve.py              JSON-lines engine for the desktop app (`wabridge serve`)
  reporting.py          structured log/progress shared by CLI, wizard and GUI
  cli.py
gui/                    Tauri 2 + React desktop app (engine runs as a sidecar)
start.sh / start.bat    zero-setup terminal launchers (uv + Python 3.12 + adb, no admin)
build-app.sh            build or dev-run the desktop app
tests/                  synthetic fixtures + 29 tests, no devices required
DESIGN.md               architecture, schema mapping, risks, roadmap
```

Run the tests: `python -m unittest discover tests` (or `pytest`).

## Roadmap

- [x] Engine: decrypt, parse, convert, inject
- [x] **Text + group chats migrate on real devices** (verified 2026-09-22)
- [ ] **Media files open on the iPhone** — bubbles render but files show as missing; help wanted (DESIGN.md §7 item 1)
- [ ] Replies/quotes, reactions, edited messages
- [ ] Group event messages (joined/left/subject changed)
- [ ] WhatsApp Business
- [ ] Legacy (pre-2022) Android schema
- [x] Desktop GUI (Tauri shell around this engine) — built, not yet run on hardware
- [ ] iOS → Android (the model layer is already direction-agnostic)

## Prior art and thanks

WaBridge stands on years of reverse-engineering by others: [wa-crypt-tools](https://github.com/ElDavoo/wa-crypt-tools) (crypt15 format), [watoi](https://github.com/residentsummer/watoi) and [mwatoi](https://github.com/mukulkadel/mwatoi) (ChatStorage import), [WhatsApp-Chat-Exporter](https://github.com/KnugiHK/WhatsApp-Chat-Exporter) (both schemas), [pymobiledevice3](https://github.com/doronz88/pymobiledevice3) (iOS backup protocol), and the forensic community's schema documentation.

## Licence

GPL-3.0-or-later. That is deliberate: anyone may use, study and improve WaBridge, and nobody may take it closed-source and sell it back to you.

WhatsApp is a trademark of Meta Platforms, Inc. This project is not affiliated with or endorsed by Meta or Apple.
