#!/bin/sh
# Freeze the Python engine into a single executable Tauri can bundle as a sidecar.
# Output: gui/src-tauri/binaries/wabridge-<target-triple>
set -e
cd "$(dirname "$0")/.."
ROOT="$(cd .. && pwd)"
TRIPLE="${1:-$(rustc -vV | sed -n 's|host: ||p')}"
PY="$ROOT/.venv/bin/python"
[ -x "$PY" ] || PY="$(command -v python3)"

echo "→ Installing PyInstaller into the venv …"
if command -v uv >/dev/null 2>&1; then
  uv pip install --python "$PY" -q pyinstaller
else
  "$PY" -m pip install -q pyinstaller
fi

echo "→ Freezing the engine for $TRIPLE …"
rm -rf build/pyi build/sidecar
"$PY" -m PyInstaller --noconfirm --clean --onefile --name wabridge \
  --paths "$ROOT/src" \
  --collect-submodules wabridge \
  --collect-all pymobiledevice3 \
  --hidden-import cryptography.hazmat.primitives.ciphers.aead \
  --exclude-module tkinter --exclude-module matplotlib --exclude-module IPython \
  --distpath build/sidecar --workpath build/pyi --specpath build \
  "$ROOT/src/wabridge/__main__.py"

mkdir -p src-tauri/binaries
OUT="src-tauri/binaries/wabridge-engine-$TRIPLE"
cp "build/sidecar/wabridge" "$OUT"
chmod +x "$OUT"

echo "→ Smoke test …"
SMOKE="$(printf '{"id":"1","cmd":"ping"}\n{"id":"2","cmd":"shutdown"}\n' | "$OUT" serve --work "$(mktemp -d)" 2>&1 || true)"
case "$SMOKE" in
  *'"type": "hello"'*) echo "   engine answers: ok" ;;
  *) echo "   FAILED — the frozen engine did not start:"; echo "$SMOKE" | head -20; exit 1 ;;
esac
echo "sidecar → $OUT ($(du -h "$OUT" | cut -f1))"
