# WaBridge desktop app

A Tauri 2 shell around the Python engine. The Rust layer is ~15 lines; the frontend is React +
TypeScript; all migration logic stays in `../src/wabridge`, which the app runs as a *sidecar*
(`wabridge serve`) and talks to over stdin/stdout with JSON lines.

```
gui/
  src/                 React frontend
    engine.ts          sidecar client (spawn, request/response, event stream)
    store.tsx          app state: engine state, device polling, console, current action
    components/        Shell (transit-line rail + console drawer), ui primitives
    steps/             Start, Android, IPhone, Transfer, Done
    dev/mockEngine.ts  browser stand-in for the engine (development builds only, see below)
    styles.css         design tokens — see DESIGN.md
  src-tauri/           Rust shell, tauri.conf.json, capabilities, icons, binaries/ (sidecar)
  scripts/             dev-sidecar.sh (venv wrapper), build-sidecar.sh/.ps1 (PyInstaller freeze),
                       screenshots.mjs (renders every screen headlessly)
  screenshots/         output of `npm run screenshots` (git-ignored)
```

## Run it in development

From the repo root, one command (installs npm packages, points the sidecar at the repo venv,
starts Tauri with hot reload):

```bash
bash build-app.sh dev
```

Python changes take effect on the next app launch; frontend changes reload instantly.

Verified on the owner's Mac on 23 September 2026: the window opens, the engine is spawned and the
Start screen shows "Engine … ready". The quickest proof without looking at the window is the audit
trail in the engine's log: `~/Library/Application Support/dev.wabridge.desktop/work/engine.log`
gets `engine … started`, `← 1 state`, `→ 1 result` within a second of launch.

## Preview in a browser, without phones

The frontend also runs in a plain browser. With the dev server up (`bash build-app.sh dev`, or just
`npm run dev`), open <http://localhost:1420/> — with no Tauri window around it, development builds
load `src/dev/mockEngine.ts`, a scripted stand-in that speaks the engine protocol from a chosen
world. Pick the scene with URL flags:

```
http://localhost:1420/?mock=android,iphone            both phones connected and ready
http://localhost:1420/?mock=resume-iphone,iphone      earlier session already backed up → Transfer
http://localhost:1420/?mock=no-adb                    Start offers to install Android tools
http://localhost:1420/?mock=crash                     engine dies at startup
```

The full flag list is at the top of `src/dev/mockEngine.ts`; `&speed=0` makes progress instant.
The console drawer says "Browser preview" so a mock session cannot be mistaken for a real one.
Production builds never load the mock — outside Tauri they show a "run the desktop app" error.

## Screenshots of every screen

```bash
npx playwright install chromium     # once; Playwright itself is a devDependency
npm run screenshots                 # → gui/screenshots/NN-scene.png (+ .full.png for tall screens)
```

36 scenes cover every state of every step in light and dark schemes and at the minimum window
size. Look at the PNGs after any change to a screen or to `styles.css`; they are how the design was
reviewed, since the real window cannot be captured from an unattended session.

The README's gallery is a hand-picked subset downscaled to 1× in `docs/img/`. After a visible
change, refresh it (macOS):

```bash
for s in 02-start:start 10-android-key:android-key 14-android-fetched:android-fetched \
         21-iphone-backup-progress:iphone-backup 23-transfer-options:transfer 28-done:done 31-start-dark:start-dark; do
  sips -Z 980 "screenshots/${s%%:*}.png" --out "../docs/img/${s##*:}.png" >/dev/null
done
```

## Build installers

```bash
bash build-app.sh
# → gui/src-tauri/target/release/bundle/dmg/WaBridge_0.1.0_aarch64.dmg   (macOS)
#   …/bundle/msi/  …/bundle/nsis/   (Windows)   …/bundle/deb/  …/bundle/appimage/ (Linux)
```

Prerequisites: Rust (`rustup`), Node 20+, and the Python venv (`bash start.sh --setup-only` creates
it). Windows additionally needs iTunes or Apple Mobile Device Support for the iPhone USB driver.

Verified on macOS (Apple silicon) on 23 September 2026: `bash build-app.sh` froze the engine
(PyInstaller, smoke test passed), built the release shell and produced `WaBridge.app` (44 MB) and
`WaBridge_0.1.0_aarch64.dmg`; opening the `.app` spawned the frozen engine and reached the Start
screen. The Windows and Linux paths above are what Tauri produces; neither has been run yet.

Size: the Tauri shell itself is a few MB; the frozen Python engine (pymobiledevice3 and its
dependencies) adds roughly 40–70 MB, so expect an installer in that range — still far below an
Electron app with the same engine.

Signing: builds are unsigned and not notarised, so macOS blocks the first launch of a downloaded
copy ("damaged" or "cannot be opened"). Either open System Settings › Privacy & Security and
click **Open Anyway** after the blocked attempt, or clear the quarantine flag once:
`xattr -dr com.apple.quarantine /Applications/WaBridge.app` (the right-click › Open shortcut no
longer works on macOS 15+). A locally built `.app` has no quarantine flag and opens directly.
Signing and notarisation can be added later through Tauri's `bundle.macOS.signingIdentity` and
the `APPLE_*` secrets in `.github/workflows/release.yml`.

## How the pieces talk

The engine protocol is documented at the top of `src/wabridge/serve.py`; the TypeScript mirror
is `src/types.ts`. Every long action streams `log` and `progress` events; the rail's span between
the current and next station doubles as the progress bar. `state` is re-read after every action
so the app can resume a half-finished migration after a restart.

Secrets (the 64-digit key, a backup password) travel only in request arguments over stdin and are
never logged or persisted by the engine. `engine.log` in the work folder keeps an audit trail of
command names and error codes (never arguments) so a failed run can be understood after the fact.

## Changing the design

Read `DESIGN.md` first — it records the palette, type and layout decisions and the defaults they
deliberately avoid. Tokens live in `src/styles.css`.
