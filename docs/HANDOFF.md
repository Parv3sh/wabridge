# WaBridge — full hand-off (sessions of 22–23 September 2026)

This is the long-form companion to `../CLAUDE.md`. `CLAUDE.md` is the short operational brief
that every AI session loads automatically; this file is the complete story for whoever picks the
project up next — human or AI — including everything that was tried, what failed and why, and
what remains. Nothing in here is personal data: phone numbers, chat names, device serials and
the backup key have been deliberately left out because this repository is public.

**Picking up in VS Code with Claude Code:** open `~/Documents/wabridge`, start Claude Code, and say
*"Read CLAUDE.md and docs/HANDOFF.md, then continue from the backlog."* Claude Code runs on the
Mac itself, so unlike the sandboxed session that wrote most of this code it can run `npm`,
`cargo`, `PyInstaller` and talk to phones over USB directly.

---

## 1. What the project is, in one paragraph

WaBridge moves a WhatsApp account's history from an Android phone to an iPhone that is already
set up, for free, under GPL-3. Apple and Meta's official route requires a factory-reset iPhone;
the paid tools that avoid that (Dr.Fone, MobileTrans, Mutsapper, Wazzap Migrator) all do the same
two hacks WaBridge does in the open: decrypt the Android `msgstore.db.crypt15` with the user's own
64-digit backup key, rewrite the messages into WhatsApp-for-iOS's Core Data database
(`ChatStorage.sqlite`) inside an unencrypted iPhone backup, and restore that backup. The owner is
Parvesh Kumar (GitHub `parvesh-rm`); he wants proper attribution and a project that helps the
next person, and he prefers short answers, one command at a time, and as little hands-on work as
the task genuinely requires.

## 2. Timeline

### Day 1 (22 Sept) — research, engine, first real migration

1. **Research** (two parallel sub-agents, GitHub itself was unreachable from the sandbox so
   findings came from search snippets plus prior knowledge). Key prior art: `ElDavoo/wa-crypt-tools`
   (crypt15 format), `residentsummer/watoi` and `kwvg/watoi` (macOS/Core Data importer),
   `mukulkadel/mwatoi` (Python, direct SQL, no media), `MobitrixTechnology/MobitrixWATransfer`
   (C, has media, non-commercial licence — consulted for behaviour only, never for code),
   `KnugiHK/WhatsApp-Chat-Exporter` (both schemas), `doronz88/pymobiledevice3` (iOS backup protocol).
2. **Engine scaffold**: `model.py` (neutral IR), `android/{adb,crypt15,msgstore}.py`,
   `ios/{backup,chatstorage,device}.py`, `pipeline.py`, `cli.py`, fixtures and tests.
   crypt15 decryption was re-implemented from the published description (HKDF-SHA256 with zero
   salt and info `backup encryption`, AES-256-GCM, zlib) so the project has no hard dependency on
   wa-crypt-tools' unstable internal API.
3. **Independent code review** found and we fixed: rollback copy being overwritten, partial
   inject leaving Manifest.db half-applied (now rolled back in `Backup.__exit__`), ZSORT ordering
   when the iPhone already had newer messages, IV ambiguity in the crypt15 header (a 16-char
   version string could be mistaken for the IV — now every 16-byte candidate is tried and GCM
   authentication decides), SQLite URI escaping, inode collisions, media filename collisions.
4. **Interactive wizard** (`wabridge wizard`) and zero-setup launcher (`start.sh`: uv → Python
   3.12 → adb → editable install). Key can be pre-placed in `wabridge-work/key.txt`.
5. **Real-device run** — Samsung Galaxy Z Fold4 (Android) → iPhone 13 on iOS 27.2, WhatsApp iOS
   current build, pymobiledevice3 11.16.3, macOS with ~4 GB free at first. Failures in order:
   - `'coroutine' object has no attribute 'udid'` → pymobiledevice3 11.x is fully async.
   - `Future attached to a different loop` → a LockdownClient is bound to the loop that created
     it; rewrote `device.py` so each operation runs connect→service→action in one coroutine.
   - `no attribute 'will_encrypt'` → became `get_will_encrypt()`; explicit `svc.connect()`.
   - `ConnectionTerminatedError` with an empty message → passcode prompt on the phone; the
     wizard's wait loop also crashed on empty error strings (fixed).
   - `MBErrorDomain/105` → the iPhone needed ~11.4 GB free on the Mac, 4.4 GB available. Owner
     freed space. Pristine copy switched to APFS clone (`cp -c`) so it costs no disk.
   - `MBErrorDomain/211` → Find My iPhone must be off. Toggle was greyed by a Screen Time
     restriction (Content & Privacy → Location Services → Allow changes).
   - Restore succeeded but WhatsApp showed *"having trouble reading your chat history… begin
     recovery"* and crashed. Diagnosis by diffing our rows against WhatsApp's own rows in the
     pristine database: `ZPUSHNAME` and `ZLASTMESSAGETEXT` are base64 protobuf in current builds
     and we had written plain text; several flag/status columns had different conventions;
     `ZWACHATPROPERTIES` is unused; 1:1 sessions are keyed by `@lid`. Rewrote the writer to
     *learn* conventions from existing rows (`Writer._learn_conventions`).
   - **Second restore: all 57,816 messages across 192 chats (22 groups) appeared correctly.**
   - Media pass: recovery screen again. Cause: NULL in numeric columns that Core Data treats as
     non-optional scalars (`ZLATITUDE`, `ZASPECTRATIO`, …) and MIME type wrongly placed in
     `ZVCARDSTRING`. Fixed (`_never_null_numeric`); third restore loaded, media bubbles rendered
     but files did not open. Owner parked media; see §6.
6. Owner published the repo, deleted the 31 GB work folder (key, decrypted DB, backups).

### Day 2 (23 Sept) — desktop app

7. **Engine `serve` mode**: JSON-lines protocol over stdin/stdout for the GUI (`serve.py`,
   `reporting.py`), with a threaded engine, busy guard, iPhone-contention guard, stable error
   codes, secrets only in request args, UTF-8 stdio. `tests/test_serve.py` drives a full offline
   migration through it with fake phones.
8. **Tauri 2 + React/TypeScript app** in `gui/` (design in `gui/DESIGN.md`). Rust is ~15 lines.
   The sandbox had no Rust, npm registry or PyInstaller, so the frontend was type-checked
   against hand-written stubs and everything else was reviewed rather than run.
9. **Second independent review** caught two would-be-fatal issues before the first build:
   PyInstaller's entry script had a relative import; the sidecar was named `wabridge`, which
   collides with the app binary `WaBridge` on case-insensitive filesystems (renamed
   `wabridge-engine`). Also fixed: non-idempotent engine start under React StrictMode, swallowed
   `android.check` errors, cp1252 stdout on Windows, CSP `connect-src` for Tauri IPC,
   overlapping actions, unused dialog plugin.
10. **First real build on the Mac succeeded** (`bash build-app.sh dev`): `target/debug/wabridge-
    desktop` exists, sidecar copied, capability schemas generated. Whether the window opened and
    the engine booted was not yet confirmed when this was written. Script permission bits were
    lost crossing from the sandbox (files arrived as 0644); all docs now say `bash build-app.sh`.
11. `CLAUDE.md` and this file written; `tools/platform-tools` (adb binaries) had been committed
    by mistake and was untracked; every link now points at `github.com/parvesh-rm/wabridge`.

## 3. Architecture and the reasoning behind it

- **Python engine, GUI shell on top.** The two hard dependencies — crypt15 knowledge and the iOS
  backup protocol — exist only in Python. Node has neither; a pure Electron app would have had
  to bundle `libimobiledevice` per platform.
- **Tauri over Electron** for the shell: a few MB instead of 150 MB, and the sidecar mechanism
  is exactly the "spawn a frozen binary and stream its stdout" pattern we need. The frozen
  Python engine dominates installer size (expect 40–70 MB total).
- **Plain SQL into ChatStorage.sqlite** (mwatoi's approach) rather than Core Data + the app's
  `.momd` (watoi's): cross-platform and independent of having the exact IPA. The cost is that
  every convention WhatsApp's Core Data model expects has to be matched by hand — which is what
  the day-1 failures were about, and why the writer now copies conventions from real rows.
- **Pristine backup as the rollback point**, never overwritten; `convert` always starts from it
  so re-runs rebuild rather than deduplicate against our own output.
- **Everything resumable through `state.json`**; the GUI derives its current step from the
  engine's `state` command, so a half-finished migration survives an app restart.
- **Direction-agnostic model layer** (`model.py`): iOS → Android later means one new parser and
  one new writer, not a rewrite.
- **Design system** (`gui/DESIGN.md`): harbour-bridge metaphor, fog/deck/steel/rivet palette with
  harbour teal for actions and sodium amber only for motion, Avenir Next on macOS, the
  transit-line rail as the single bold element. Reviewed against the common "generated UI"
  defaults before coding. It has never been seen rendered — a screenshot-based critique is owed.

## 4. Facts learned the hard way

All of these are in `CLAUDE.md` too; here with a little more colour.

**WhatsApp iOS `ChatStorage.sqlite`, 2025/26 builds** (observed on the pristine database):
- Tables present: `ZWABLACKLISTITEM, ZWACHATPROPERTIES, ZWACHATPUSHCONFIG, ZWACHATSESSION,
  ZWAGROUPINFO, ZWAGROUPMEMBER, ZWAGROUPMEMBERSCHANGE, ZWAMEDIAITEM, ZWAMESSAGE,
  ZWAMESSAGEDATAITEM, ZWAMESSAGEINFO, ZWAPROFILEPICTUREITEM, ZWAPROFILEPUSHNAME, ZWAVCARDMENTION,
  ZWAZ1PAYMENTTRANSACTION, Z_METADATA, Z_MODELCACHE, Z_PRIMARYKEY`. No reactions table — reactions
  presumably live inside `ZWAMESSAGEINFO`'s blob (relevant to the backlog).
- `ZPUSHNAME` on every message and `ZLASTMESSAGETEXT` on every session are base64-encoded
  protobuf. Plain text there breaks the app.
- Messages: `ZFLAGS` 16777216 incoming / 16777280 outgoing (plus 18874368 on some system
  rows), `ZDATAITEMVERSION` 3, `ZSPOTLIGHTSTATUS` −32768 (occasionally 7), `ZLASTSESSION =
  ZCHATSESSION`, `ZMESSAGESTATUS` 8 on the outgoing sample and 0/6/8 on incoming.
- Sessions: 1:1 = `ZSESSIONTYPE 0`, `ZCONTACTJID <n>@lid`, `ZCONTACTIDENTIFIER <phone>@s.whatsapp.net`,
  `ZFLAGS 272`, `ZSPOTLIGHTSTATUS −5`; groups = `ZSESSIONTYPE 1`, `ZFLAGS 256` (variants 268435712,
  524544, 4718848 seen), `ZSPOTLIGHTSTATUS 1`; newsletters `ZSESSIONTYPE 5`; broadcast 2; a
  community-style group used 4. `ZWACHATPROPERTIES` had zero rows.
- `ZWAMEDIAITEM` real rows: `ZFILESIZE, ZMEDIAORIGIN, ZMOVIEDURATION, ZASPECTRATIO, ZHACCURACY,
  ZLATITUDE, ZLONGITUDE` are never NULL (0 / 0.0); each carries a 3-byte `ZMETADATA` blob
  (`c0 0d 01` on system-message rows). Our rows still have `ZMETADATA` NULL — a media suspect.
- Group event messages are `ZMESSAGETYPE 6` with `ZGROUPEVENTTYPE` values (2, 12, 68 observed);
  the semantics are only partially known.

**Android `msgstore.db`, current schema:** `message_type` 20 is sticker and 15 is revoked (most
online tables get this backwards); captions are `message.text_data` on the media row; `jid_map`
maps `@lid` rows to phone JIDs (incomplete — one LID-only chat had no mapping); `wa.db` (contact
names) is root-only, so names come from the Contacts content provider over adb or a `.vcf`.

**iOS backups and pymobiledevice3 11.x:** async API bound to its event loop; `get_will_encrypt()`;
restore worked with `system=False`, `source=udid`; Find My must be off; a full backup needs about
the iPhone's used space free on the Mac, which the engine now checks via `com.apple.disk_usage`
before starting. Manifest.db rows: files `Mode 0o100644`, `ProtectionClass 3`, no `Digest`;
directories `Mode 0o40775`, `ProtectionClass 0` (ours were 0o40755 / 3 during the failed media
run — fixed, untested).

## 5. Current state of verification

| Area | Status |
|---|---|
| crypt15 decrypt, Android parse, contacts export | verified on device |
| iPhone backup, inject, restore | verified on device |
| Text + group chats visible in WhatsApp iOS | **verified on device** |
| Media files openable on iPhone | **not working**; likely fix in code, untested |
| Quotes/replies, reactions, edits, group events | not implemented / skipped |
| WhatsApp Business | wired (domain + Android root), untested |
| Legacy (pre-2022) Android schema | unsupported, raises a clear error |
| Terminal wizard | verified on device end to end |
| Engine `serve` protocol | 29 offline tests green |
| Desktop app | compiles on the Mac; window/engine boot unconfirmed; never used with phones |
| Production build (`bash build-app.sh`, PyInstaller) | never run |
| Release workflow (tauri-action on `v*` tags) | never run |
| Windows / Linux | never run anywhere |

## 6. The media problem, precisely

Symptom after the third restore: chats open, media bubbles render with the right shapes, but
tapping does nothing and nothing plays. The database was accepted (no recovery screen), the
Manifest.db rows and blobs for all 7,130 injected files were present on disk in the backup that
was restored. Of 15,459 media references in the Android database, 7,365 files still existed on
the phone; the rest are placeholders by necessity.

Hypotheses, in the order to test:
1. Directory rows had `Mode 0o40755 / ProtectionClass 3` instead of iOS's `0o40775 / 0`, and file
   rows carried a `Digest` iOS never writes. If iOS skipped creating those folders on restore,
   every file inside is unreachable — matching the symptom exactly. **Fixed in code, untested.**
2. `ZWAMEDIAITEM.ZMETADATA` is NULL on our rows; every real row has a small protobuf blob.
3. Thumbnails: `ZTHUMBNAILLOCALPATH` / `ZXMPPTHUMBPATH` and a thumbnail file may be required for
   the bubble to be tappable.
4. `ZCLOUDSTATUS` / `ZMEDIAORIGIN` semantics.

The cheapest diagnostic: receive one photo natively in WhatsApp on the iPhone, take a backup
(`wabridge ios backup --work ~/wabridge-diag` — read-only, no restore), and diff that photo's
`ZWAMEDIAITEM` row plus its `Manifest.db` entry against one of ours. The owner does not want to
run more restores for now; when he does, run the media pass once with hypothesis 1 in place
before investigating 2–4.

## 7. Backlog, prioritised, with implementation notes

1. **Confirm the app boots and walk the Android + iPhone steps** with phones plugged in (no
   restore needed until the Transfer screen). Take screenshots; do a design critique against
   `gui/DESIGN.md`. Fix whatever the first real run of the UI shows.
2. **Production build**: `bash build-app.sh` → PyInstaller freeze is the untested piece; the
   smoke test in `gui/scripts/build-sidecar.sh` must print `"type": "hello"`. Likely fixes are
   `--hidden-import` entries for pymobiledevice3 submodules. Then tag `v0.2.0` and check the
   draft release the workflow produces. Unsigned builds: document right-click › Open on macOS;
   signing/notarisation later via `bundle.macOS.signingIdentity` and `APPLE_*` secrets.
3. **Quoted replies**: Android `message_quoted.key_id` is already parsed into
   `Message.quoted_key_id`. On iOS set `ZWAMESSAGE.ZPARENTMESSAGE` to the Z_PK of the message with
   that `ZSTANZAID` (two-pass write: insert all, then update parents). Verify whether the quote
   bubble also needs `ZWAMESSAGEDATAITEM` rows; the pristine DB had none for text quotes.
4. **Reactions**: Android `message_add_on` + `message_add_on_reaction`. iOS has no reactions
   table; they are almost certainly encoded in `ZWAMESSAGEINFO` (a `WAMessageReceiptInfo`-style
   protobuf). Needs a protobuf reverse-engineering pass — take a real reacted message from a
   fresh backup and decode the blob. Medium-hard.
5. **Edited messages**: Android `message_edit_info` / `message_future.version`; iOS likely marks
   edits in the same info blob. Same approach as reactions.
6. **Group events**: Android `message_system*` tables → iOS `ZMESSAGETYPE 6` +
   `ZGROUPEVENTTYPE`. Map at least join/leave/subject-change (2, 3, 1 in older docs; verify).
   Currently skipped by default (`--include-system` / the GUI checkbox writes them as type 0
   text placeholders — improve).
7. **WhatsApp Business**: Android root `com.whatsapp.w4b`, iOS domain
   `AppDomainGroup-group.net.whatsapp.WhatsAppSMB.shared`. Wired end to end; the Business
   ChatStorage schema may differ. Needs a tester with a Business account.
8. **Legacy Android schema** (pre-2022 `messages` table): build compatibility views as the
   `n4ze3m` write-up did, or refuse with the current clear message. Low priority; WhatsApp forces
   updates.
9. **Names**: `android.fetch` exports the phone's Contacts over adb; the GUI could also accept a
   `.vcf` (the engine's `convert` already takes `contacts_vcf`). The dialog plugin was removed as
   unused; re-add `@tauri-apps/plugin-dialog` if a picker is wanted.
10. **PyPI publish** (`python -m build && twine upload`) once media is settled or clearly
    labelled.
11. **iOS → Android**: parser for `ChatStorage.sqlite` (KnugiHK's `ios_handler.py` is the
    reference) and a writer for `msgstore.db` + re-encryption to crypt15 (`crypt15.encrypt_bytes`
    exists for tests; WhatsApp Android would also need the matching Google Drive/local backup
    metadata — research needed).
12. **Windows**: `start.bat`, `build-sidecar.ps1`, and the whole iPhone path (needs Apple Mobile
    Device Support / iTunes for usbmuxd) are unrun.

## 8. Working conventions

- Tests must stay green: `python -m unittest discover -s tests` (or `pytest`); `ruff check src
  tests` (line length 120); `cd gui && npm run typecheck`.
- Change the engine protocol in `serve.py` and `gui/src/types.ts` together and extend
  `tests/test_serve.py`; the `FakePhones` class in `tests/test_wizard.py` stands in for both phones.
- Never hard-code a schema fact you can introspect. Mark anything inferred as "unverified" in
  code and in `DESIGN.md` §7, and keep README claims exactly aligned with what has been seen on a
  device.
- Never commit personal data. `wabridge-work/`, `tools/`, `*.crypt15`, `msgstore*.db`,
  `ChatStorage.sqlite*` are git-ignored; the GUI's work folder is under the OS app-data dir.
- Scripts are invoked with `bash …` / `sh …` in docs so lost executable bits can't bite.

## 9. Environment on the owner's Mac (as of 23 Sept 2026)

Apple-silicon Mac, macOS 27-era, Rust from Homebrew (`brew install rust`, so no rustup — fine
for host-target builds), Node 22, Python via `uv` in `.venv` (editable install of the package),
adb in `tools/platform-tools` (git-ignored) plus whatever `start.sh` finds. The terminal
migration used `./wabridge-work` (deleted afterwards); the desktop app uses
`~/Library/Application Support/dev.wabridge.desktop/work`. GitHub remote is
`https://github.com/parvesh-rm/wabridge.git`; the owner's local git identity is a different
account, which is fine — attribution is by name in `pyproject.toml`, `LICENSE`, `CITATION.cff`.

## 10. Things that went wrong in the process itself (so they aren't repeated)

- The sandbox could not reach GitHub, PyPI or npm, and had no Rust. Everything network- or
  toolchain-dependent was therefore written blind and reviewed by independent agents instead of
  run. Two of those reviews caught fatal issues; keep using them for anything that can't execute.
- Files edited from the sandbox lose their executable bit on the Mac. Use `bash script.sh`.
- The Homebrew-installed adb ended up committed. Check `git status` before the first push of
  any new directory.
- Early design assumptions about `ZMESSAGESTATUS`, `ZVCARDSTRING` and plain-text `ZPUSHNAME`
  came from 2018–2022 write-ups and were wrong for current builds. Prefer evidence from the
  target device's own database over documentation — the pristine backup is always available.
