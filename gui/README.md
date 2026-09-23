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
    styles.css         design tokens — see DESIGN.md
  src-tauri/           Rust shell, tauri.conf.json, capabilities, icons, binaries/ (sidecar)
  scripts/             dev-sidecar.sh (venv wrapper), build-sidecar.sh/.ps1 (PyInstaller freeze)
```

## Run it in development

From the repo root, one command (installs npm packages, points the sidecar at the repo venv,
starts Tauri with hot reload):

```bash
./build-app.sh dev
```

Python changes take effect on the next app launch; frontend changes reload instantly.

## Build installers

```bash
./build-app.sh
# → gui/src-tauri/target/release/bundle/dmg/WaBridge_0.1.0_aarch64.dmg   (macOS)
#   …/bundle/msi/  …/bundle/nsis/   (Windows)   …/bundle/deb/  …/bundle/appimage/ (Linux)
```

Prerequisites: Rust (`rustup`), Node 20+, and the Python venv (`./start.sh --setup-only` creates
it). Windows additionally needs iTunes or Apple Mobile Device Support for the iPhone USB driver.

Size: the Tauri shell itself is a few MB; the frozen Python engine (pymobiledevice3 and its
dependencies) adds roughly 40–70 MB, so expect an installer in that range — still far below an
Electron app with the same engine.

Signing: builds are unsigned. On macOS the first launch needs right-click › Open. Signing and
notarisation can be added later through Tauri's `bundle.macOS.signingIdentity` and the
`APPLE_*` secrets in `.github/workflows/release.yml`.

## How the pieces talk

The engine protocol is documented at the top of `src/wabridge/serve.py`; the TypeScript mirror
is `src/types.ts`. Every long action streams `log` and `progress` events; the rail's span between
the current and next station doubles as the progress bar. `state` is re-read after every action
so the app can resume a half-finished migration after a restart.

Secrets (the 64-digit key, a backup password) travel only in request arguments over stdin and are
never logged or persisted by the engine.

## Changing the design

Read `DESIGN.md` first — it records the palette, type and layout decisions and the defaults they
deliberately avoid. Tokens live in `src/styles.css`.
