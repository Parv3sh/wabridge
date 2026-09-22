# WaBridge

**Free, open-source WhatsApp migration from Android to iPhone — chats, groups and media, without factory-resetting the iPhone.**

The paid tools (Dr.Fone, MobileTrans, Mutsapper, Wazzap Migrator…) all do the same thing under the hood: decrypt the Android database, rewrite it into WhatsApp-for-iOS's `ChatStorage.sqlite`, slip it into an iPhone backup and restore that backup. Every one of those steps can be done with open-source software. WaBridge is that pipeline, automated, GPL-licensed, and free forever.

> **Status: alpha, tested on real devices (Sept 2026: Samsung Galaxy Z Fold4 / Android → iPhone 13 / iOS 27.2).** Confirmed working on hardware: crypt15 decryption, parsing a 57k-message database, full iPhone backup, injection and restore through pymobiledevice3 11.x. The ChatStorage writer follows the conventions of the current WhatsApp iOS build (see DESIGN.md §5.2 and §7). **Always keep your Android backup.**

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

## Quick start (no setup knowledge needed)

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
  wizard.py             guided interactive session (polls devices, resumes, logs)
  cli.py
start.sh / start.bat    zero-setup launchers (uv + Python 3.12 + adb, no admin)
tests/                  synthetic fixtures + 25 tests, no devices required
DESIGN.md               architecture, schema mapping, risks, roadmap
```

Run the tests: `python -m unittest discover tests` (or `pytest`).

## Roadmap

- [x] Engine: decrypt, parse, convert, inject (offline-tested)
- [ ] **First successful real-device migration** (help wanted — see DESIGN.md §7 for the checklist)
- [ ] Replies/quotes, reactions, edited messages
- [ ] Group event messages (joined/left/subject changed)
- [ ] WhatsApp Business
- [ ] Legacy (pre-2022) Android schema
- [ ] Desktop GUI (Tauri shell around this engine; ~10 MB installer)
- [ ] iOS → Android (the model layer is already direction-agnostic)

## Prior art and thanks

WaBridge stands on years of reverse-engineering by others: [wa-crypt-tools](https://github.com/ElDavoo/wa-crypt-tools) (crypt15 format), [watoi](https://github.com/residentsummer/watoi) and [mwatoi](https://github.com/mukulkadel/mwatoi) (ChatStorage import), [WhatsApp-Chat-Exporter](https://github.com/KnugiHK/WhatsApp-Chat-Exporter) (both schemas), [pymobiledevice3](https://github.com/doronz88/pymobiledevice3) (iOS backup protocol), and the forensic community's schema documentation.

## Licence

GPL-3.0-or-later. That is deliberate: anyone may use, study and improve WaBridge, and nobody may take it closed-source and sell it back to you.

WhatsApp is a trademark of Meta Platforms, Inc. This project is not affiliated with or endorsed by Meta or Apple.
