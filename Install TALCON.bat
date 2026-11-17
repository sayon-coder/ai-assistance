@echo off
setlocal
cd /d "%~dp0"
title TALCON Installer
color 0B

echo ============================================================
echo    TALCON Assistant - one-click installer
echo ============================================================
echo.
echo This installs TALCON locally. Everything runs offline on this
echo PC: no paid APIs, no cloud speech, no account needed.
echo.

REM ---- locate a supported Python (3.11 or newer, 64-bit) ----
set "PYVER="
for %%V in (3.14 3.13 3.12 3.11) do (
  if not defined PYVER (
    py -%%V --version >nul 2>&1 && set "PYVER=%%V"
  )
)
if not defined PYVER (
  python --version >nul 2>&1 && set "PYVER=python"
)
if not defined PYVER (
  echo [ERROR] Python 3.11 or newer was not found.
  echo.
  echo Install Python from https://www.python.org/downloads/
  echo and tick "Add py.exe to PATH" during setup, then run
  echo this installer again.
  echo.
  pause
  exit /b 1
)
echo [1/4] Python found: %PYVER%
echo.

REM ---- create the virtual environment and install dependencies ----
echo [2/4] Installing dependencies (first run takes a few minutes)...
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0setup.ps1" -SkipStartupPrompt
if errorlevel 1 (
  echo.
  echo [ERROR] Dependency installation failed. Read the messages above.
  pause
  exit /b 1
)
echo.

REM ---- download the free offline models (Vosk + Kokoro speech) ----
echo [3/4] Preparing the offline speech models (free, Apache 2.0)...
".venv\Scripts\python.exe" -m talcon.model_setup
if errorlevel 1 (
  echo.
  echo [WARNING] The Vosk recognition model could not be downloaded now.
  echo You can retry later from TALCON - Voice tab - "Download model".
)
".venv\Scripts\python.exe" -m talcon.model_setup --kokoro
if errorlevel 1 (
  echo.
  echo [WARNING] The Kokoro voice model could not be downloaded now.
  echo TALCON needs it: retry with  python -m talcon.model_setup --kokoro
)
echo.

REM ---- done ----
echo [4/4] Installation finished.
echo.
echo ============================================================
echo    NEXT STEP: double-click  "Launch TALCON.bat"
echo ============================================================
echo.
echo  - First launch opens the TALCON control center.
echo  - Say "TALCON" to wake it, then a command such as
echo    "Activate my study mode".
echo  - To remove TALCON later, double-click "Uninstall TALCON.bat".
echo.
pause
exit /b 0
