@echo off
setlocal
cd /d "%~dp0"

if not exist ".venv\Scripts\pythonw.exe" (
  echo TALCON is not installed yet.
  echo Double-click "Install TALCON.bat" first.
  pause
  exit /b 1
)

if not exist "assets\talcon_face.gif" (
  echo [WARNING] Face asset missing - starting without it may fail.
)

start "TALCON" ".venv\Scripts\pythonw.exe" main.py
exit /b 0
