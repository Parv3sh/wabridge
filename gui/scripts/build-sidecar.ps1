# Windows equivalent of build-sidecar.sh. Run from PowerShell in the gui/ folder (or anywhere).
$ErrorActionPreference = "Stop"
Set-Location (Join-Path $PSScriptRoot "..")
$Root = (Resolve-Path "..").Path
$Triple = (rustc -vV | Select-String "host: (.*)").Matches[0].Groups[1].Value
$Py = Join-Path $Root ".venv\Scripts\python.exe"
if (-not (Test-Path $Py)) { $Py = "python" }

Write-Host "→ Installing PyInstaller …"
if (Get-Command uv -ErrorAction SilentlyContinue) { uv pip install --python $Py -q pyinstaller } else { & $Py -m pip install -q pyinstaller }

Write-Host "→ Freezing the engine for $Triple …"
Remove-Item -Recurse -Force build\pyi, build\sidecar -ErrorAction SilentlyContinue
& $Py -m PyInstaller --noconfirm --clean --onefile --name wabridge `
  --paths "$Root\src" --collect-submodules wabridge --collect-all pymobiledevice3 `
  --hidden-import cryptography.hazmat.primitives.ciphers.aead `
  --exclude-module tkinter --exclude-module matplotlib --exclude-module IPython `
  --distpath build\sidecar --workpath build\pyi --specpath build `
  "$Root\src\wabridge\__main__.py"

New-Item -ItemType Directory -Force src-tauri\binaries | Out-Null
$Out = "src-tauri\binaries\wabridge-engine-$Triple.exe"
Copy-Item build\sidecar\wabridge.exe $Out -Force
Write-Host "sidecar → $Out"
