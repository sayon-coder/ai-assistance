@echo off
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  echo TALCON is not installed yet. Double-click "Install TALCON.bat" first.
  pause
  exit /b 1
)
start "TALCON" ".venv\Scripts\pythonw.exe" main.py
