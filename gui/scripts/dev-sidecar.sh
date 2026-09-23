#!/bin/sh
# Dev-mode sidecar: a tiny wrapper that runs the engine from the repo's Python venv, so
# `npm run tauri dev` picks up Python changes instantly without a PyInstaller build.
set -e
cd "$(dirname "$0")/.."
ROOT="$(cd .. && pwd)"
TRIPLE="$(rustc -vV | sed -n 's|host: ||p')"
PY="$ROOT/.venv/bin/python"
if [ ! -x "$PY" ]; then
  echo "No Python venv at $ROOT/.venv. Run  ./start.sh --setup-only  in the repo root first." >&2
  exit 1
fi
mkdir -p src-tauri/binaries
OUT="src-tauri/binaries/wabridge-engine-$TRIPLE"
cat > "$OUT" <<WRAP
#!/bin/sh
exec "$PY" -m wabridge "\$@"
WRAP
chmod +x "$OUT"
echo "dev sidecar → $OUT"
