#!/bin/bash
# Build the WaBridge desktop app (Tauri shell + frozen Python engine) on macOS or Linux.
# Prerequisites it will check for: Rust (rustup), Node 20+, and the Python venv from ./start.sh.
set -euo pipefail
cd "$(dirname "$0")"
say() { printf '\n\033[1m%s\033[0m\n' "$*"; }

command -v cargo >/dev/null 2>&1 || { echo "Rust is missing. Install it with:  curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh   then re-open the terminal."; exit 1; }
command -v node  >/dev/null 2>&1 || { echo "Node.js 20+ is missing. Install from https://nodejs.org (or: brew install node)."; exit 1; }
[ -x .venv/bin/python ] || ./start.sh --setup-only

MODE="${1:-build}"        # build | dev
cd gui
say "Installing frontend dependencies …"
npm install --no-fund --no-audit
if [ "$MODE" = "dev" ]; then
  sh ./scripts/dev-sidecar.sh
  say "Starting WaBridge in dev mode (hot reload) …"
  exec npm run tauri dev
fi
sh ./scripts/build-sidecar.sh
say "Building the app …"
npm run tauri build
say "Done. Installers are in gui/src-tauri/target/release/bundle/:"
ls -1 src-tauri/target/release/bundle/*/ 2>/dev/null | sed 's/^/  /'
