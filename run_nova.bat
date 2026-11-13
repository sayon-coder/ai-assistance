@echo off
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  echo NOVA is not installed yet. Double-click "Install NOVA.bat" first.
  pause
  exit /b 1
)
start "NOVA" ".venv\Scripts\pythonw.exe" main.py
