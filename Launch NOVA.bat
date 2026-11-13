@echo off
setlocal
cd /d "%~dp0"

if not exist ".venv\Scripts\pythonw.exe" (
  echo NOVA is not installed yet.
  echo Double-click "Install NOVA.bat" first.
  pause
  exit /b 1
)

if not exist "assets\nova_face.gif" (
  echo [WARNING] Face asset missing - starting without it may fail.
)

start "NOVA" ".venv\Scripts\pythonw.exe" main.py
exit /b 0
