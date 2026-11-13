@echo off
setlocal
cd /d "%~dp0"
title NOVA Uninstaller

echo ============================================================
echo    NOVA Assistant - uninstaller
echo ============================================================
echo.
echo Your Windows startup entry is always removed.
echo.

REM ---- 1. remove the per-user startup shortcut (no admin needed) ----
echo [1/4] Removing Windows startup entry...
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\uninstall_startup.ps1"
echo.

REM ---- 2. stop a running NOVA (face, voice, monitors) ----
echo [2/4] Stopping NOVA if it is running...
powershell -NoProfile -ExecutionPolicy Bypass -Command "Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -like '*%~dp0main.py*' } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }"
powershell -NoProfile -Command "Start-Sleep -Seconds 2"
echo.

REM ---- 3. optional data removal ----
set "CLEANDATA="
set /p CLEANDATA="Delete the downloaded speech model, settings and logs? (Y/N): "
if /i "%CLEANDATA%"=="Y" (
  echo [3/4] Deleting speech model, settings and logs...
  if exist "models" rmdir /s /q "models"
  if exist "config\settings.json" del /f /q "config\settings.json"
  if exist "logs" rmdir /s /q "logs"
) else (
  echo [3/4] Keeping your speech model, settings and logs.
)
echo.

REM ---- 4. optional program removal ----
set "CLEANVENV="
set /p CLEANVENV="Delete the Python environment (.venv) too? (Y/N): "
if /i "%CLEANVENV%"=="Y" (
  echo [4/4] Deleting the Python environment...
  if exist ".venv" rmdir /s /q ".venv"
) else (
  echo [4/4] Keeping the Python environment.
)
echo.
echo ============================================================
echo    NOVA has been uninstalled.
echo    To finish, delete this NOVA_Assistant folder yourself.
echo ============================================================
echo.
pause
exit /b 0
