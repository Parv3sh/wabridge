# WaBridge — working notes for AI assistants

Read this first. It is the hand-off from the sessions that built the project (Sept 2026) and it
records things that are not derivable from the code: what has been proven on real phones, what
has not, and the traps already hit. Keep it current when you learn something new here.

## What this is

Free, GPL-3 desktop tool that moves WhatsApp history from an Android phone to an iPhone that is
already set up (Apple's official path needs a factory reset). Owner: Parvesh Kumar
(GitHub `parveshkumar`; he wants proper attribution — name in pyproject, LICENSE, CITATION.cff).

How it works: decrypt the Android `msgstore.db.crypt15` with the user's 64-digit key → parse →
write into WhatsApp-for-iOS `ChatStorage.sqlite` inside an unencrypted iPhone backup → restore
that backup with pymobiledevice3. Full design: `DESIGN.md`. User-facing steps: `docs/USER_GUIDE.md`.

## Layout

```
src/wabridge/
  model.py             platform-neutral Chat/Message/Media (Android and iOS code never import each other)
  android/adb.py       adb wrapper: devices, pull, contacts export, size-polling media progress
  android/crypt15.py   crypt15 decryption implemented from scratch (HKDF-SHA256 + AES-256-GCM + zlib)
  android/msgstore.py  new-schema msgstore.db parser, LID↔phone mapping (jid_map)
  ios/backup.py        Manifest.db reader/writer, MBFile plist blobs, media injection
  ios/chatstorage.py   ChatStorage.sqlite writer — Core Data via plain SQL; learns conventions from existing rows
  ios/device.py        pymobiledevice3 11.x (async) wrapper: info/backup/restore/change_password
  pipeline.py          resumable stages + state.json; pristine backup kept as rollback point
  wizard.py            interactive terminal flow (`wabridge wizard`)
  serve.py             JSON-lines engine for the desktop app (`wabridge serve --work DIR`)
  reporting.py         Reporter abstraction: text vs JSON log/progress
  cli.py, __main__.py
gui/                   Tauri 2 + React/TS desktop app; engine runs as sidecar. See gui/README.md, gui/DESIGN.md
tests/                 29 tests, all offline with synthetic fixtures + fake phones (tests/fixtures.py, test_wizard.py FakePhones)
start.sh / start.bat   zero-setup terminal launcher (uv → Python 3.12 → adb → editable install → wizard). `--setup-only` skips launch
build-app.sh           `./build-app.sh dev` (Tauri dev, venv-wrapper sidecar) | `./build-app.sh` (PyInstaller freeze + tauri build)
.github/workflows/     ci.yml (pytest + ruff on 3 OSes), release.yml (tauri-action installers on v* tags)
```

## Commands

```bash
python -m unittest discover -s tests        # or pytest; must stay green
ruff check src tests                        # line length 120; tests ignore E501/E402
cd gui && npm run typecheck                 # tsc --noEmit
./build-app.sh dev                          # first-ever real Tauri build has NOT happened yet (see below)
```

## Status: verified vs not (be honest in README/DESIGN when this changes)

Verified on real devices, 2026-09-22 (Samsung Galaxy Z Fold4 → iPhone 13, iOS 27.2, WhatsApp
iOS current build, pymobiledevice3 11.16.3):
- crypt15 decryption, parsing 57,816 msgs / 192 chats, contact export over adb, full iPhone backup,
  injection, restore (Find My must be OFF; error MBErrorDomain/211 otherwise), **text + group chats
  appear correctly in WhatsApp on the iPhone**.

NOT verified / open:
- **Media (parked by owner — he does not want to run more restores for now).** Media bubbles render
  but files don't open. Most likely cause fixed in code but untested: injected *directory* rows in
  Manifest.db had Mode 0o40755 / ProtectionClass 3; iOS writes 0o40775 / ProtectionClass 0; also
  removed a `Digest` field iOS never writes. Next candidates in DESIGN.md §7 item 1 (ZMETADATA blob,
  thumbnails). Do not claim media works until someone confirms on a device.
- **Desktop app has never been built or run** — the sandbox that wrote it had no Rust/npm/PyInstaller.
  Frontend type-checks with stubs; Python side of the protocol is tested. Expect small first-build
  fixups (npm versions, tauri capability names, PyInstaller hidden imports).
- Quotes/replies, reactions, edits, group events (skipped by default), WhatsApp Business (wired,
  untested), legacy pre-2022 Android schema (unsupported), iOS→Android (model layer is ready).

## Hard-won facts (do not rediscover)

WhatsApp iOS ChatStorage (2025/26 builds):
- `ZWAMESSAGE.ZPUSHNAME` and `ZWACHATSESSION.ZLASTMESSAGETEXT` hold **base64 protobuf**, not text.
  Writing plain text there → "having trouble reading your chat history" + crash on recovery.
- Every message: ZFLAGS 16777216 (incoming) / 16777280 (outgoing), ZDATAITEMVERSION 3,
  ZSPOTLIGHTSTATUS −32768, ZLASTSESSION = ZCHATSESSION. Numeric columns that are never NULL in
  WhatsApp's own rows must not be NULL in ours (Core Data non-optional scalars) — the writer learns
  these from existing rows (`Writer._learn_conventions`, `_never_null_numeric`).
- 1:1 sessions are keyed by `<n>@lid` in ZCONTACTJID with the phone JID in ZCONTACTIDENTIFIER
  (ZFLAGS 272, ZSPOTLIGHTSTATUS −5); groups ZFLAGS 256, ZSPOTLIGHTSTATUS 1. ZWACHATPROPERTIES unused.
- MIME type does NOT go in ZVCARDSTRING (that's for real vCards). ZMEDIASECTIONID = "YYYY-MM".
- Z_ENT comes from Z_PRIMARYKEY by name; bump Z_MAX after inserts; convert always starts from the
  pristine ChatStorage so re-runs (e.g. media pass) rebuild rather than dedupe.

Android:
- E2E backup with 64-digit key makes the LOCAL `Android/media/com.whatsapp/WhatsApp/Databases/
  msgstore.db.crypt15` decryptable; adb pull works unrooted (Android 11–15).
- message_type 20 = sticker, 15 = revoked (common mix-up). wa.db needs root → names come from the
  Contacts content provider or a .vcf.

iOS backup / pymobiledevice3:
- 11.x API is async and a LockdownClient is bound to the loop that created it: run connect→service→
  action in ONE coroutine per operation (`device.py` does this). `get_will_encrypt()` replaced the
  `will_encrypt` property. Restore worked with `system=False, source=udid`.
- Full backup needs roughly the iPhone's used bytes free on the Mac (device error MBErrorDomain/105);
  the engine pre-checks using `com.apple.disk_usage`. Pristine copy uses APFS clone (`cp -c`).
- Find My iPhone must be off to restore; greyed toggle = Screen Time restriction.

Tauri/GUI gotchas already handled:
- Sidecar is named `wabridge-engine` — it must differ from productName `WaBridge` or the bundler
  overwrites the app binary on case-insensitive filesystems.
- PyInstaller entry `src/wabridge/__main__.py` must use an absolute import.
- `serve.py` reconfigures stdio to UTF-8 (Windows cp1252) and redirects stray prints to stderr;
  stdout carries only protocol JSON lines. Secrets travel only in request args.
- Engine protocol contract = `serve.py` docstring ↔ `gui/src/types.ts`; change both + `tests/test_serve.py`.

## Rules

1. Never hard-code schema assumptions you can introspect (PRAGMA table_info, Z_PRIMARYKEY, existing rows).
2. Every pipeline stage must be re-runnable; never corrupt earlier outputs; never touch the Android phone.
3. Only modify backups inside the work dir; keep the pristine copy untouched; `Backup.__exit__` rolls back on error.
4. Nothing personal in git: `wabridge-work/`, `tools/`, `*.crypt15`, `msgstore*.db`, `ChatStorage.sqlite*` are ignored.
   (Note: `tools/platform-tools` was committed once by mistake — `git rm -r --cached tools` if still tracked.)
5. Mark inferred-but-unverified behaviour with "unverified" in code comments and DESIGN.md §7.
6. Docs must state exactly what is verified on hardware; the owner will not run restores casually.

## Immediate next steps

1. `git rm -r --cached tools -q && ./build-app.sh dev` on the owner's Mac; fix whatever the first
   Tauri/npm/PyInstaller build surfaces; then `./build-app.sh` for a .dmg and try a `v0.2.0` tag for release.yml.
2. README checklist: quotes/replies (`ZPARENTMESSAGE`), reactions (`message_add_on_reaction`), edits,
   group events, WhatsApp Business, legacy schema; PyPI publish.
3. Media, only when the owner is willing to restore again: receive one photo natively on the iPhone,
   take a backup, diff that ZWAMEDIAITEM row + its Manifest.db entry against ours.
