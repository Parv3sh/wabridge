#!/bin/bash
# WaBridge one-shot launcher for macOS / Linux.
# Installs everything it needs *without sudo* into this folder, then runs the guided wizard.
#   - uv (tiny Python manager) -> ~/.local/bin
#   - Python 3.12 (standalone, managed by uv)
#   - Android platform-tools (adb) -> ./tools/platform-tools
#   - the wabridge package + pymobiledevice3 -> ./.venv
# Re-running is safe; everything is skipped if already present.

set -euo pipefail
cd "$(dirname "$0")"

say() { printf '\n\033[1m%s\033[0m\n' "$*"; }

OS="$(uname -s)"
case "$OS" in
  Darwin) PT_URL="https://dl.google.com/android/repository/platform-tools-latest-darwin.zip" ;;
  Linux)  PT_URL="https://dl.google.com/android/repository/platform-tools-latest-linux.zip" ;;
  *) echo "Unsupported OS: $OS (use start.bat on Windows)"; exit 1 ;;
esac

# ---------------------------------------------------------------- uv
export PATH="$HOME/.local/bin:$HOME/.cargo/bin:$PATH"
if ! command -v uv >/dev/null 2>&1; then
  say "Installing uv (Python manager, no admin rights needed) …"
  curl -LsSf https://astral.sh/uv/install.sh | sh
  export PATH="$HOME/.local/bin:$PATH"
fi

# ---------------------------------------------------------------- Python 3.12 + venv
if [ ! -x ".venv/bin/python" ]; then
  say "Setting up Python 3.12 …"
  uv venv --python 3.12 .venv
fi

# ---------------------------------------------------------------- adb
if ! command -v adb >/dev/null 2>&1 && [ ! -x "tools/platform-tools/adb" ]; then
  say "Downloading Android platform-tools (adb) …"
  mkdir -p tools
  curl -L --progress-bar -o tools/platform-tools.zip "$PT_URL"
  unzip -qo tools/platform-tools.zip -d tools
  rm -f tools/platform-tools.zip
fi
export PATH="$PWD/tools/platform-tools:$PATH"

# ---------------------------------------------------------------- Linux only: usbmuxd for the iPhone
if [ "$OS" = "Linux" ] && ! command -v usbmuxd >/dev/null 2>&1; then
  echo "NOTE: the iPhone needs 'usbmuxd'. Install it with your package manager (e.g. sudo apt install usbmuxd)."
fi

# ---------------------------------------------------------------- wabridge
say "Installing WaBridge …"
uv pip install --python .venv/bin/python -q -e ".[ios]"

# ---------------------------------------------------------------- go
mkdir -p wabridge-work
say "Starting the wizard. Log: $PWD/wabridge-work/wizard.log"
exec .venv/bin/wabridge wizard --work "$PWD/wabridge-work"
