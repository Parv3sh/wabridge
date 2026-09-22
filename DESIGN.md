# WaBridge — technical design

Version 0.1 · September 2026 · living document, edit freely.

## 1. Goal and non-goals

WaBridge moves a WhatsApp account's history (1:1 chats, groups, photos, videos, voice notes, documents, stickers, locations, contact cards) from an Android phone to an iPhone that is already set up, for free, with open-source code, on Windows, macOS and Linux.

Out of scope for 1.0: iOS → Android (planned; the model layer is direction-agnostic), WhatsApp Business (planned), call history, status updates, channels, payments, and anything requiring a rooted or jailbroken device.

## 2. Why this is possible without WhatsApp's cooperation

There is no API. Every commercial tool relies on two facts:

**Android.** Since WhatsApp introduced end-to-end encrypted backups, the *local* backup at `Android/media/com.whatsapp/WhatsApp/Databases/msgstore.db.crypt15` is encrypted under the same 32-byte root key the user can display as a 64-digit code. That folder is readable over ADB on every Android version to date because scoped storage constrains apps, not the `shell` user. So: no root, no downgrade trick, no `adb backup` exploit. (The older `.crypt14` files use a device-bound key at `/data/data/com.whatsapp/files/key` and are useless without root; we ignore them.)

**iOS.** iTunes-style backups are plain folders: a SQLite `Manifest.db` listing every file and a blob per file named by `sha1(domain + "-" + relativePath)`. When the backup is unencrypted nothing is signed. WhatsApp's data lives in the app-group domain `AppDomainGroup-group.net.whatsapp.WhatsApp.shared` (`ChatStorage.sqlite`, `Message/Media/…`). Rewriting that database inside a backup and restoring the backup is how Dr.Fone, MobileTrans and watoi all work, and Apple has never blocked it because it is indistinguishable from a legitimate restore.

The alternative free route, Apple's *Move to iOS* + WhatsApp's official transfer, requires a factory-reset iPhone. WaBridge's reason to exist is exactly the users who won't or can't do that.

## 3. Architecture

```
                 ┌──────────────── engine (Python, this repo) ────────────────┐
 adb ──────────▶ │ android/adb.py      android/crypt15.py    android/msgstore │
 (platform-tools)│        └── pull ──────▶ decrypt ──────────▶ parse ──┐       │
                 │                                                     ▼       │
                 │                                             model.py (IR)   │
                 │                                                     │       │
 pymobiledevice3 │ ios/device.py ─backup─▶ ios/backup.py ─extract─▶ ios/chatstorage.py
 (usbmuxd) ◀──── │        ▲                     │  ◀──── inject ────── writer  │
                 │        └──── restore ────────┘                              │
                 │ pipeline.py (stages + state.json)      cli.py               │
                 └──────────────────────────────────────────────────────────────┘
                                          ▲
                        GUI (phase 2): Tauri shell spawning the engine as a sidecar,
                        streaming its JSON-lines progress into a wizard UI.
```

**Why Python for the engine.** The two hard dependencies exist only in Python: the crypt15 format knowledge (wa-crypt-tools, re-implemented here in ~150 lines so we have no GPL-incompatibility or API-drift risk) and the iOS backup protocol (`pymobiledevice3`, pure Python, cross-platform, actively maintained). Node has neither; a pure-Electron build would have to bundle `libimobiledevice` binaries per OS and reimplement the protocol quirks.

**Why a CLI before a GUI.** A migration either works or destroys trust. The GUI adds nothing until the engine has succeeded on real devices; a CLI is also what testers can paste logs from.

**Why Tauri for the GUI later.** ~10 MB installer versus 150 MB+ for Electron, and Tauri's sidecar mechanism is built for exactly this: bundle a PyInstaller-frozen `wabridge` binary, spawn it with `--json`, render progress. Electron would work equally well if contributors prefer it; the engine boundary is the same either way.

### 3.1 Intermediate model (`model.py`)

`Archive → Chat → Message (→ Media | Location | vcard)`. Timestamps are Unix milliseconds; JIDs are canonical (`<phone>@s.whatsapp.net`, `<id>@g.us`). Android and iOS code never import each other; anything that needs both goes through this model. That is what makes iOS → Android a matter of writing one parser and one writer, not a rewrite.

### 3.2 Resumable pipeline (`pipeline.py`)

Six stages, each idempotent and recorded in `<work>/state.json`: `android_pull → android_decrypt → ios_backup → convert → inject → ios_restore`. The *first* backup of the iPhone is copied to `ios_backup_pristine/` and never overwritten; `wabridge ios rollback` restores it, so a botched migration is always reversible. Re-running `ios backup` deletes the previous (possibly modified) backup folder first so the device never does an incremental backup on top of our edits. `convert`/`inject` refuse to touch any backup folder outside the work directory, so a user can't accidentally point the tool at their real Finder backup. `Backup.__exit__` rolls back Manifest.db if an inject fails midway.

## 4. Android side

### 4.1 Pulling (`android/adb.py`)

`adb pull -a /sdcard/Android/media/com.whatsapp/WhatsApp/Databases` and the `Media/<folder>` set (Images, Video, Voice Notes, Audio, Documents, Animated Gifs, Stickers, Video Notes; each with `Sent/` and `Private/` subfolders). Alternate roots are probed for old installs. Contacts are exported best-effort with `content query --uri content://com.android.contacts/data/phones --projection display_name:data1`; a `.vcf` export is the fallback because `wa.db` (WhatsApp's own contact cache) lives in `/data/data` and needs root.

### 4.2 Decryption (`android/crypt15.py`)

```
file    = varint(header_len) ‖ protobuf BackupPrefix ‖ AES-256-GCM(ciphertext) ‖ tag[16]
aes_key = HKDF-SHA256(salt = 0x00×32, ikm = root_key, info = "backup encryption", L = 32)
        = HMAC(HMAC(0×32, root), "backup encryption" ‖ 0x01)
plain   = zlib.decompress(...)  →  starts with "SQLite format 3\0"
```

The 16-byte IV sits in a nested message inside the prefix. We do not hard-code protobuf field numbers; a generic walker collects every 16-byte `bytes` field (a 16-character version string would also qualify) and GCM authentication decides which one is the IV. Tolerances: an optional 16-byte trailer after the tag (seen in newer builds), uncompressed payloads, and a `--force` mode that skips tag verification but still requires a valid SQLite header. If all else fails and `wadecrypt` is installed we shell out to it. The test-suite round-trips through our own encoder and checks the KDF against `cryptography`'s HKDF.

### 4.3 Parsing (`android/msgstore.py`)

Targets the post-2022 schema. Core query joins `message ← chat ← jid` with optional `LEFT JOIN`s to `message_media`, `message_location`, `message_quoted`, `message_vcard`, `jid sender`. Every optional table/column is checked with `PRAGMA table_info` first.

`message.message_type` mapping (verified against Belkasoft/whatsapp-viewer; note the classic mistake — **15 is revoked, 20 is sticker**):

| Android | Meaning | MsgKind |
|---|---|---|
| 0 | text | TEXT |
| 1 | image | IMAGE |
| 2 | audio | AUDIO, or VOICE if mime contains `opus` / path is under Voice Notes |
| 3 | video | VIDEO |
| 4 / 14 | vCard / multi-vCard | CONTACT (first card) |
| 5 / 16 | location / live location | LOCATION |
| 7, 8, 10, 24 | system, legacy call, missed call, group invite | SYSTEM (skipped by default) |
| 9 | document | DOCUMENT |
| 13 | GIF (mp4) | GIF |
| 15 | revoked | REVOKED (skipped by default) |
| 20 | sticker | STICKER |
| other (polls 46, payments, view-once…) | | UNKNOWN → text placeholder |

**LIDs.** From 2025 WhatsApp addresses some users by `<n>@lid` instead of a phone number. `jid_map(lid_row_id, jid_row_id)` maps most of them back; unmapped LIDs are imported as-is with a warning, and `lid_display_name` supplies their names when present.

Captions are `message.text_data` on the media row (`message_media.media_caption` exists only on some builds; we read whichever is present). Timestamps are milliseconds UTC.

## 5. iOS side

### 5.1 Backup container (`ios/backup.py`)

```sql
CREATE TABLE Files (fileID TEXT PRIMARY KEY, domain TEXT, relativePath TEXT, flags INTEGER, file BLOB);
-- flags: 1 file, 2 directory, 4 symlink.  fileID = sha1(domain || '-' || relativePath)
-- blob on disk at <backup>/<fileID[:2]>/<fileID>
```

`Files.file` is an NSKeyedArchiver plist of an `MBFile`: `Size, Mode (0o100644 / 0o040755), InodeNumber, LastModified, LastStatusChange, Birth, ProtectionClass, UserID/GroupID (501), Flags, RelativePath` (+ `EncryptionKey` only in encrypted backups). To add a file we clone the blob of an existing WhatsApp row and rewrite those fields, so whatever this iOS version emits is preserved; if no template exists we build the archive from scratch (`build_mbfile`). Parent directories get `flags=2` rows. `Manifest.plist`/`Status.plist` dates are refreshed. Encrypted backups are refused with an actionable message; supporting them would mean re-implementing the keybag unwrap and re-encrypting every injected file, which is real work for no benefit when the user can untick one box.

Known fileID sanity check: `sha1("AppDomainGroup-group.net.whatsapp.WhatsApp.shared-ChatStorage.sqlite") = 7c7fba66…f01d`.

### 5.2 ChatStorage.sqlite writer (`ios/chatstorage.py`)

Core Data store, written with plain SQL (mwatoi's approach) instead of through Core Data and the app's `.momd` model (watoi's approach, which chains you to macOS, Xcode and an IPA of the exact installed version). Rules:

* `Z_ENT` is looked up in `Z_PRIMARYKEY` by `Z_NAME` (`WAChatSession`, `WAMessage`, `WAMediaItem`, `WAGroupMember`, `WAGroupInfo`, `WAChatProperties`). Never hard-coded.
* New `Z_PK = Z_MAX + 1`; `Z_MAX` is bumped on commit or WhatsApp's next save collides with our rows.
* `Z_OPT = 1`. Dates are Cocoa epoch seconds (Unix − 978307200) as REAL.
* Only columns that exist are written (`PRAGMA table_info`), so a renamed or new column degrades to "not set" rather than a crash.
* Existing sessions are reused (matched on `ZCONTACTJID`); existing messages are skipped by `ZSTANZAID`, making the import idempotent.

Entity mapping:

| Model | ZWAMESSAGE | Notes |
|---|---|---|
| chat | `ZCHATSESSION` → ZWACHATSESSION.Z_PK | `ZSESSIONTYPE` 0 individual / 1 group; `ZPARTNERNAME` = name or number |
| from_me | `ZISFROMME`; `ZTOJID` = chat JID | `ZFROMJID` NULL |
| incoming | `ZFROMJID` = chat JID | group sender via `ZGROUPMEMBER` → ZWAGROUPMEMBER; `ZPUSHNAME` = name |
| kind | `ZMESSAGETYPE` | 0 text, 1 image, 2 video, 3 audio, 4 contact, 5 location, 6 system, 8 document, 11 gif, 14 revoked*, 15 sticker |
| timestamp | `ZMESSAGEDATE`, `ZSENTDATE` | Cocoa seconds |
| key_id | `ZSTANZAID` | dedupe key |
| status | `ZMESSAGESTATUS` 8 out / 6 in* | |
| order | `ZSORT` | global running counter from MAX(ZSORT) |
| media | ZWAMEDIAITEM: `ZMESSAGE`↔`ZMEDIAITEM`, `ZMEDIALOCALPATH`, `ZFILESIZE`, `ZMOVIEDURATION`, `ZVCARDSTRING` = MIME type, `ZTITLE` = caption (documents: filename) | `ZTEXT` stays NULL for media |
| location | ZWAMEDIAITEM `ZLATITUDE/ZLONGITUDE/ZTITLE` | |
| contact | ZWAMEDIAITEM `ZVCARDSTRING` = vCard, `ZVCARDNAME` | |

Media files land at `Message/Media/<chatJID>/<x>/<y>/<hash8>-<filename>` in the group domain (the 8-hex prefix is derived from the Android path so `Images/IMG-1.jpg` and `Images/Sent/IMG-1.jpg` can't collide); WhatsApp reads `ZMEDIALOCALPATH` verbatim so the bucketing digits only need to be consistent. After import `ZSORT` is renumbered for the whole table in `ZMESSAGEDATE` order so Android history interleaves correctly with messages already on the iPhone, and a session's `ZLASTMESSAGE` pointer only ever moves forward in time. Sessions are finished with `ZLASTMESSAGE`, `ZLASTMESSAGEDATE`, `ZLASTMESSAGETEXT`, `ZMESSAGECOUNTER`. Before writing back we `wal_checkpoint(TRUNCATE)` + `journal_mode=DELETE` so a single file is injected and stale `-wal/-shm` rows are removed from the manifest.

**Conventions of the 2025/2026 WhatsApp iOS build (learned from a real device, 2026-09-22).** Several columns that older tools treated as text now hold **base64-encoded protobuf**: `ZWAMESSAGE.ZPUSHNAME` (per-message metadata, present on every row) and `ZWACHATSESSION.ZLASTMESSAGETEXT`. Writing plain text there makes WhatsApp show *"having trouble reading your chat history"* and crash on recovery — that was the first real-device failure. Other observations: every message has `ZFLAGS = 16777216` (incoming) / `16777280` (outgoing), `ZDATAITEMVERSION = 3`, `ZSPOTLIGHTSTATUS = -32768` (unindexed), `ZLASTSESSION = ZCHATSESSION`; 1:1 sessions are keyed by `ZCONTACTJID = <n>@lid` with the phone JID in `ZCONTACTIDENTIFIER`, `ZFLAGS = 272`, `ZSPOTLIGHTSTATUS = -5`; groups use `ZFLAGS = 256`, `ZSPOTLIGHTSTATUS = 1`; `ZWACHATPROPERTIES` is unused (0 rows); `Z_MODELCACHE` exists. Rather than hard-code these, `Writer._learn_conventions()` reads the most common values from the rows WhatsApp itself wrote in the extracted database and detects whether `ZPUSHNAME`/`ZLASTMESSAGETEXT` are blobs, falling back to the constants above. The Android parser now also carries each contact's LID (reverse of `jid_map`) so new sessions can be keyed the modern way.

\* = weakly verified, see §7.

### 5.3 Device I/O (`ios/device.py`)

`Mobilebackup2Service.backup(full=True)` into `<work>/ios_backup/<UDID>/`; `restore(system=?, reboot=True, copy=False, settings=True, remove=False, source=UDID)`. `will_encrypt` gates the backup; `change_password(old, new="")` turns encryption off. Every call falls back to the `pymobiledevice3` CLI (`backup2 backup --full`, `backup2 restore --no-copy --settings --source …`) if the Python signature differs, because that API has moved between releases while the CLI has not.

## 6. Security and privacy

Everything runs locally; there is no network code in the engine. The 64-digit key is accepted on the command line (visible in shell history); `--key` also accepts a path to a file containing it, and the GUI will use a masked field. The work directory contains the entire decrypted chat history; `.gitignore` excludes it, and the user guide says to delete it when done. We never modify the Android phone.

## 7. What is *not* yet verified — the on-device checklist

The test-suite proves the engine is internally consistent against synthetic databases shaped like the real ones. The following can only be proven on hardware, roughly in order of risk:

0. ~~Restore of a modified backup~~ **Verified 2026-09-22**: pymobiledevice3 11.16 `restore(system=False, source=udid)` restores the modified app-group data on iOS 27.2 (Find My must be off; MBErrorDomain/211 otherwise). WhatsApp opened our ChatStorage.sqlite — the first attempt failed on content conventions (see §5.2), not on the restore mechanism.
1. **Restore accepts our Manifest.db rows for new media files.** watoi/mwatoi only ever *replaced* ChatStorage.sqlite in place; only the non-free MobitrixWATransfer is known to add rows. If the device rejects the backup, first retry with media rows omitted (`convert --no-media`) to isolate the cause, then compare a device-produced media row's MBFile plist against ours (`read_mbfile`).
2. **`restore(system=…)`.** pymobiledevice3 issue #1053 reports app-group data only restoring with `--system` and an explicit `--source` on some iOS versions. Default is off; the guide tells users to retry with `--system`.
3. **`ZMESSAGESTATUS` values** (8/6) and whether WhatsApp needs `ZFLAGS` bits for outgoing messages to render ticks correctly.
4. **`ZMESSAGETYPE 14` for revoked** and whether `ZGROUPEVENTTYPE` mappings are worth importing system messages (currently skipped).
5. **Voice notes**: whether WhatsApp iOS needs `ZMEDIAORIGIN`/`ZFLAGS` to show the PTT player rather than a generic audio file, and whether `.opus` files play (Android records Opus-in-Ogg; iOS WhatsApp records `.m4a`/Opus-in-CAF, but plays Ogg/Opus received from Android users, so it should).
6. **Stickers**: `.webp` rendering with type 15 and whether a `ZWAMEDIAITEM.ZMETADATA` blob is required.
7. **Individual chats appearing in the list** only after a new message (watoi-era behaviour) — if still true, investigate `ZWACHATSESSION.ZFLAGS`/`ZLASTMESSAGE` semantics.
8. **WhatsApp Business**: `android pull --business` pulls from `com.whatsapp.w4b` and the pipeline then targets the `…WhatsAppSMB.shared` domain — wired end to end but untested; the Business ChatStorage schema may also differ.
9. **Very large archives** (50k+ messages, 20 GB media): SQLite is fine; the restore time and iPhone free-space checks need a pre-flight estimate.

Each item, once verified, should move into §5 with the WhatsApp/iOS version it was confirmed on.

## 8. Roadmap

**0.1 (now)** engine + tests + docs. **0.2** first confirmed real-device migration; fix everything §7 turns up; publish to PyPI. **0.3** quotes/replies (`ZWAMESSAGEINFO` / `ZPARENTMESSAGE`), reactions (`message_add_on_reaction`), edits, group events; `--key-file`; JSON-lines progress output for the GUI. **0.4** Tauri GUI wizard with PyInstaller sidecar; signed macOS/Windows builds via CI. **0.5** WhatsApp Business; legacy Android schema via compatibility views. **1.0** iOS → Android.

## 9. References

wa-crypt-tools (ElDavoo) · WhatsApp-Chat-Exporter (KnugiHK) · watoi (residentsummer; kwvg fork) · mwatoi (mukulkadel) · MobitrixWATransfer (non-free licence; do not copy code) · pymobiledevice3 (doronz88) · whatsapp-viewer schema dump (andreas-mausch) · Belkasoft and Group-IB WhatsApp forensic articles · kacos2000 ChatStorage SQL queries · Rich Infante, "Reverse engineering the iOS backup" · fatbobman on Core Data `Z_PRIMARYKEY`.
