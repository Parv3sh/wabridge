# WaBridge — full hand-off (sessions of 22–23 September 2026, three sessions)

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

### Day 3 (23 Sept, evening, Claude Code on the Mac) — first real runs of the desktop app

12. **`bash build-app.sh dev` opens the window and boots the engine.** Proof without a screenshot:
    the engine's log gets `engine 0.1.0a1 started`, `← 1 state`, `→ 1 result` — the GUI's
    `refreshState()` after `hello`, which is exactly what flips it to "Engine ready". (The work
    folder also already held `tools/platform-tools` downloaded at 12:32, so the owner had clicked
    "Install Android tools" in the app earlier that day.) No screenshot of the real window was
    possible: from Claude Code inside VS Code, `screencapture` fails ("could not create image from
    display", Screen Recording permission) and `osascript … System Events` hangs on the Automation
    prompt. See §9.
13. **Engine audit trail** (`serve.py`, logger `wabridge.rpc`): one line per request and answer in
    `engine.log` — command names and error codes only, never args (they carry the key), `devices`
    polling excluded, not echoed to the GUI console. Two tests; the error line deliberately omits
    the message because messages can name devices ("Device R5C… not connected").
14. **Browser mock + screenshot suite**, built because no window capture and no phones were
    available: `gui/src/engine.ts` now spawns the Tauri sidecar when `__TAURI_INTERNALS__` exists
    and otherwise (dev builds only) loads `gui/src/dev/mockEngine.ts`, a scripted engine speaking
    the same protocol from a world chosen by URL flags (`?mock=android,iphone`, `resume-iphone`,
    `find-my`, `crash`, `speed=0`, …). `gui/scripts/screenshots.mjs` (`npm run screenshots`,
    Playwright devDependency) drives 35 scenes through headless Chromium into `gui/screenshots/`
    (git-ignored), with `.full.png` variants for screens taller than the 660 px window. All 35 pass.
15. **Two frontend bugs found by the suite and fixed.** (a) An engine that exits before `hello`
    left the app on "Starting the engine…" for the full 30 s timeout; `onClose` now fails the boot
    immediately with "The engine stopped unexpectedly (exit code N)". (b) Vite HMR remounts
    `AppProvider`, and every remount spawned a new `wabridge serve` without stopping the old one
    (three were running at one point); `store.tsx` now stops the engine on unmount.
16. **Production build works.** `bash build-app.sh`: PyInstaller froze the engine (41.7 MB, the
    script's smoke test printed `engine answers: ok`; the warn file lists only optional modules),
    `tauri build` took 2 m 15 s and produced `WaBridge.app` (44 MB, ad-hoc linker-signed) and
    `WaBridge_0.1.0_aarch64.dmg`; the DMG script ran without an Automation prompt. Opening the
    `.app` spawned the frozen engine (two processes — PyInstaller `--onefile` bootloader + child)
    and reached "Engine ready" within 3 s. The standalone frozen binary also answered `state` and
    `devices` (pymobiledevice3's usbmux path works frozen). Note: `shutdown` right after other
    requests exits the main loop while their threads are still running, so their results are
    dropped — the smoke test only relies on `hello`, which is why it passes.
17. Housekeeping: ruff had six import-order/unused-import errors (CI would have failed); fixed.
    `.omc/` (a Claude Code plugin's state) and `gui/screenshots/` git-ignored. Versions still
    differ: `pyproject`/`__version__` say `0.1.0a1`, Tauri/npm/Cargo say `0.1.0` — align before tagging.
18. **Six-lens code review with adversarial verification** (31 agents: protocol contract,
    frontend, engine, build/release, docs, privacy; every high/medium finding re-checked by a
    skeptic). 23 confirmed, 2 refuted, 20 low-severity unverified. Fixed the same evening:
    - `pipeline.ios_backup` copied the pristine backup *before* checking for WhatsApp, so a backup
      taken before WhatsApp was installed became the permanent rollback point and every later
      `convert` failed with the generic code `backup`. Now checks first, replaces an unusable
      pristine, cleans up a half-copied one, and `classify()` maps the BackupError to
      `no_whatsapp_ios`. Regression test added.
    - `android.check` ran `du -sk` over the whole Media tree on every 3 s poll and the GUI
      discarded in-flight answers, so on a full phone the Android screen could sit on "checking
      WhatsApp" forever. Engine only measures media once a backup exists; the screen keeps one check
      in flight and never drops its answer.
    - An engine crash after boot only reached the console; now it returns to the boot screen with
      "Try again". A failed initial `state` no longer reports success. `start()`/`stop()` can no
      longer overlap (an unmount during spawn used to orphan the engine).
    - "Run the media pass" reopened Transfer with "Chats only" preselected (dead `key` trick).
    - Disk-space precheck budgets a second full copy where APFS clonefile is unavailable
      (non-macOS: 2.1×). `serve.main()` closes its log handler (Windows CI could not delete the
      temp dir). The pymobiledevice3 CLI fallback no longer writes to the protocol pipe and redacts
      the backup password from its error. `clean` empties `engine.log`. `iphone_dropped` was
      unreachable behind `locked`. `critical` log records map to `error`.
    - release.yml: `macos-13` runner is retired → `macos-15-intel`; a manual `workflow_dispatch`
      would have created a release named `main` → only tags publish. `.deb` depended on the
      non-existent `android-tools-adb` → `adb`. `build-sidecar.ps1` gained the smoke test.
      Tauri capability now allows only `serve --work <dir>` instead of any sidecar args.
    - Docs: USER_GUIDE told users to `pip install wabridge[ios]` (not on PyPI) → clone + `start.sh`;
      Find My and the media caveat added to guide and README; DESIGN.md's "no network code",
      `--json`, sidecar name and directory mode statements corrected; macOS 15 removed
      right-click › Open, so the unsigned-app guidance now says Open Anyway / `xattr`.
19. **Screenshot design critique** (three lenses + a judge, see `gui/DESIGN.md` → "Screenshot
    review"). Fifteen ranked items, all implemented: sticky action rows and disclosures so no
    forward button is below the fold, reordered Android/iPhone/Transfer/Done screens, a "Find My
    iPhone is off" confirmation before the transfer, legible disabled buttons distinct from busy
    ones, dark-mode contrast, engine-voice text stripped from labels, and copy fixes throughout.
    The screenshot scenes were updated to the new copy; 36 scenes pass.
20. Observed once, not reproduced: a `bash build-app.sh dev` run exited silently (code 0) a few
    seconds after the engine started, right after the watcher had restarted the app because
    `dev-sidecar.sh` rewrote the wrapper. The next run stayed up. If it recurs, suspect the
    src-tauri watcher reacting to the wrapper rewrite; running `sh gui/scripts/dev-sidecar.sh`
    before `npm run tauri dev` in two steps would separate the two.
21. **First run with both phones through the GUI** (owner at the keyboard, engine.log watched from
    the session). Android: `android.check` 2.5 s, `android.fetch` (pull + decrypt + contacts) 9 s,
    one LID-only chat warning as on day 1. iPhone: the first "Back up iPhone" failed after 10 s as
    `iphone_dropped` — pymobiledevice3 aborts the TLS handshake to the backup service after 10 s
    ("SSL handshake is taking longer than 10 seconds"). Two fixes: `serve.py` serialises the 3 s
    `devices` probe with iPhone actions (`iphone_probe` lock) and `device._service` retries the
    connect once after 1.5 s. On the retry run engine.log shows exactly that: first connect fails at
    +20 s, retry succeeds, the phone's passcode prompt is forwarded as a WARNING (the app shows it
    under the progress bar), backup of ~41 GB used → 16,889 files finishes 5 m 40 s later, pristine
    copy present, `state.json` updated. The owner stopped before Transfer (a restore).
22. The 64-digit key is not recoverable from this Mac (never persisted; work folder deleted on day 1;
    shell history, Trash, session memory and Claude Code transcripts searched). The owner had it.
23. **Stale-close race, found by the screenshot suite.** After `stop()` + `start()` on one
    `EngineClient` (React StrictMode's double mount, Fast Refresh of `store.tsx`), the `close`
    event of the *old* process arrives asynchronously once the *new* engine is already spawned;
    the shared `onClose` then failed the new boot ("stopped unexpectedly") and `doStart`'s cleanup
    killed the new engine — every mock scene after the first showed the boot-error screen, and in
    the Tauri dev app the engine vanished after a hot reload. Two fixes in `engine.ts`: each spawn
    is stamped with a generation and close events from earlier generations are ignored; and
    `stop()` detaches an in-flight `start()` so the next `start()` waits for the stop and spawns
    afresh instead of joining a boot that is about to be torn down. Production never hit it (one
    mount, no reload), but `retryBoot` after a crash would have. `engine.log` now also records
    why an engine stopped ("shutdown requested" vs "stdin closed"); a dev boot reads
    `started → stopping: shutdown requested → started → ← 1 state` within ~100 ms (StrictMode's
    mount → unmount → mount), and that is the expected shape.

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
  defaults before coding. First seen rendered on day 3 through the browser mock (§2 item 14); the
  screenshot-based critique and its outcomes are recorded there and in §7 item 1.

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
| Engine `serve` protocol | 33 offline tests green |
| Desktop app | dev app and built `.app` open and boot the engine (23 Sept); all 36 screens rendered via the browser mock; **Android and iPhone steps run with real phones through the GUI (23 Sept evening)**; Transfer step not run with phones |
| Production build (`bash build-app.sh`, PyInstaller) | **works on macOS arm64** (23 Sept): `WaBridge.app` 44 MB + `.dmg`; frozen engine boots inside the app |
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

1. **Transfer step with phones** — i.e. a chats-only restore through the GUI (the terminal wizard
   did this successfully on day 1; the GUI path convert → inject → restore → "can you see your
   chats?" has not). Android and iPhone steps ran with real phones on day 3 (§2 item 22). Still
   unobserved on hardware: the encrypted-backup (password) path, the disk-space warning with real
   numbers, `android-unauthorized`, `no_whatsapp`.
2. **Release**: the macOS production build works (day 3). Remaining: align the version strings
   (`0.1.0a1` vs `0.1.0`), tag `v0.2.0` and check the draft release the workflow produces (never
   run; the Intel leg now targets `macos-15-intel`); Windows/Linux builds (`build-sidecar.ps1`
   has a smoke test but is unrun). Unsigned builds: macOS users need Open Anyway or `xattr`;
   signing/notarisation later via `bundle.macOS.signingIdentity` and `APPLE_*` secrets.
   Low-severity leftovers from the day-3 review: `shutdown` is honoured mid-restore when the
   window closes (consider refusing while a heavy action holds the lock, and a close confirmation);
   `wadecrypt_fallback` passes the key on the command line; the Windows-only `usbmuxd` story.
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
- After any GUI change run `cd gui && npm run screenshots` and look at the PNGs; keep
  `gui/src/dev/mockEngine.ts` in step with the protocol (it only needs what the screens read).

## 9. Environment on the owner's Mac (as of 23 Sept 2026)

Apple-silicon Mac, macOS 27-era, Rust from Homebrew (`brew install rust`, so no rustup — fine
for host-target builds), Node 22, Python via `uv` in `.venv` (editable install of the package),
adb in `tools/platform-tools` (git-ignored) plus whatever `start.sh` finds. The terminal
migration used `./wabridge-work` (deleted afterwards); the desktop app uses
`~/Library/Application Support/dev.wabridge.desktop/work`. GitHub remote is
`https://github.com/parvesh-rm/wabridge.git`; the owner's local git identity is a different
account, which is fine — attribution is by name in `pyproject.toml`, `LICENSE`, `CITATION.cff`.

Added day 3: PyInstaller is installed in `.venv`; Playwright (devDependency) and its Chromium are
installed (`npx playwright install chromium`); the built app is at
`gui/src-tauri/target/release/bundle/macos/WaBridge.app`. Visual Studio Code does **not** have
Screen Recording or Automation permission, so an unattended session cannot capture the real window
or drive System Events — grant them in System Settings › Privacy & Security if a real-window
screenshot is ever needed; until then `npm run screenshots` and `engine.log` are the evidence.

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
- Day 3: a `sleep`/poll or an `osascript` call is not a way to see the GUI from an unattended
  session; the audit log and the headless suite are. Vite HMR is not a no-op for a sidecar app:
  every remount must release the previous process. A boot that only waits for `hello` needs a
  second exit path for "the process died".
