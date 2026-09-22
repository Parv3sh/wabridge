@echo off
REM WaBridge one-shot launcher for Windows. Installs uv, Python 3.12, adb and wabridge into this folder (no admin), then runs the wizard.
REM You still need iTunes (Microsoft Store) installed once, for the iPhone USB driver.
cd /d "%~dp0"
where uv >nul 2>nul || (echo Installing uv ... && powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex")
set "PATH=%USERPROFILE%\.local\bin;%PATH%"
if not exist ".venv\Scripts\python.exe" uv venv --python 3.12 .venv
if not exist "tools\platform-tools\adb.exe" (
  echo Downloading Android platform-tools ...
  mkdir tools 2>nul
  powershell -c "Invoke-WebRequest https://dl.google.com/android/repository/platform-tools-latest-windows.zip -OutFile tools\pt.zip; Expand-Archive tools\pt.zip tools -Force; Remove-Item tools\pt.zip"
)
set "PATH=%CD%\tools\platform-tools;%PATH%"
uv pip install --python .venv\Scripts\python.exe -q -e ".[ios]"
if not exist wabridge-work mkdir wabridge-work
.venv\Scripts\wabridge.exe wizard --work "%CD%\wabridge-work"
pause
