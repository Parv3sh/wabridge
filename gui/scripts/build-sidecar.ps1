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

Write-Host "→ Smoke test …"
$Work = Join-Path ([System.IO.Path]::GetTempPath()) ("wabridge-smoke-" + [System.IO.Path]::GetRandomFileName())
New-Item -ItemType Directory -Force $Work | Out-Null
$Smoke = "{`"id`":`"1`",`"cmd`":`"ping`"}`n{`"id`":`"2`",`"cmd`":`"shutdown`"}`n" | & $Out serve --work $Work 2>&1 | Out-String
if ($Smoke -notmatch '"type": "hello"') {
  Write-Host "   FAILED — the frozen engine did not start:"
  Write-Host ($Smoke -split "`n" | Select-Object -First 20)
  exit 1
}
Write-Host "   engine answers: ok"
Write-Host "sidecar → $Out ($([math]::Round((Get-Item $Out).Length / 1MB)) MB)"
