"""Per-user Windows startup integration.

Two mechanisms are used, both for the current user and both without admin
rights:

* a **logon Scheduled Task** (primary).  It waits 30 seconds, then runs a small
  launcher stored in ``%LOCALAPPDATA%\\TALCON`` so a slow OneDrive mount cannot
  prevent TALCON from starting;
* the classic **Startup-folder shortcut** as a fallback when task creation is
  not permitted.

The launcher retries for up to five minutes and writes a local startup log, so
a boot-time failure can be diagnosed instead of disappearing silently.
"""
from __future__ import annotations

import logging
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

from .config import ASSETS_DIR, ROOT

log = logging.getLogger("talcon.startup")
SHORTCUT_NAME = "TALCON.lnk"
LEGACY_SHORTCUT_NAME = "TALCON.lnk"
TASK_NAME = "TALCON_Assistant"
RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
RUN_VALUE = "TALCON"


def startup_folder() -> Path:
    appdata = os.environ.get("APPDATA")
    if appdata:
        return Path(appdata) / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Startup"
    return Path.home() / "AppData" / "Roaming" / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Startup"


def shortcut_path() -> Path:
    return startup_folder() / SHORTCUT_NAME


def legacy_shortcut_path() -> Path:
    return startup_folder() / LEGACY_SHORTCUT_NAME


def launcher_path() -> Path:
    local = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
    return Path(local) / "TALCON" / "startup.ps1"


def launch_command() -> tuple[Path, str]:
    """Return (executable, argument string) used for autostart."""
    main_py = ROOT / "main.py"
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve(), "--startup"
    pythonw = ROOT / ".venv" / "Scripts" / "pythonw.exe"
    if not pythonw.exists():
        pythonw = Path(sys.executable).with_name("pythonw.exe")
    if not pythonw.exists():
        pythonw = Path(sys.executable).resolve()
    return pythonw.resolve(), f'"{main_py}" --startup'


# ---------------------------------------------------------------------------
# state
# ---------------------------------------------------------------------------
#: Cached result of the slow ``schtasks.exe`` probe (link/registry hit first).
_CACHE_LOCK = threading.Lock()
_CACHE: tuple[float, bool] | None = None
CACHE_SECONDS = 5.0


def scheduled_task_exists() -> bool:
    try:
        result = subprocess.run(
            ["schtasks.exe", "/Query", "/TN", TASK_NAME],
            capture_output=True, text=True, timeout=15,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        return result.returncode == 0
    except Exception:
        return False


def run_key_value() -> str:
    try:
        import winreg
        key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_READ)
    except OSError:
        return ""
    try:
        value, _kind = winreg.QueryValueEx(key, RUN_VALUE)
        return str(value)
    except OSError:
        return ""
    finally:
        winreg.CloseKey(key)


def run_key_exists() -> bool:
    return bool(run_key_value())


def is_enabled(use_cache: bool = True) -> bool:
    """True when TALCON starts with Windows.

    The Startup-folder shortcut and the HKCU Run value are cheap to read.  Only
    ``schtasks.exe`` costs ~90 ms (it spawns a process), so its answer is
    cached briefly: the window calls this on every show and the tray menu on
    every status change.
    """
    if shortcut_path().exists() or legacy_shortcut_path().exists():
        return True
    if run_key_exists():
        return True
    if not use_cache:
        value = scheduled_task_exists()
        _store_cache(value)
        return value
    now = time.monotonic()
    with _CACHE_LOCK:
        cached = _CACHE
        if cached is not None and now - cached[0] < CACHE_SECONDS:
            return cached[1]
    value = scheduled_task_exists()
    _store_cache(value)
    return value


def _store_cache(value: bool) -> None:
    global _CACHE
    with _CACHE_LOCK:
        _CACHE = (time.monotonic(), bool(value))


def invalidate_cache() -> None:
    """Forget the cached startup state (after an enable/disable)."""
    global _CACHE
    with _CACHE_LOCK:
        _CACHE = None


def is_current() -> bool:
    """True when a reliable autostart mechanism is present."""
    return is_enabled()


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------
def _powershell(code: str) -> subprocess.CompletedProcess:
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    return subprocess.run(
        ["powershell.exe", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-Command", code],
        capture_output=True, text=True, timeout=60, creationflags=flags,
    )


def _ps_quote(value: str) -> str:
    return "'" + str(value).replace("'", "''") + "'"


def _ps_string(value: str) -> str:
    return str(value).replace("'", "''")


def _clear_startup_approval() -> None:
    """Remove a user-disabled Startup-folder flag for TALCON/TALCON."""
    try:
        import winreg
        key = winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            r"Software\Microsoft\Windows\CurrentVersion\Explorer\StartupApproved\StartupFolder",
            0, winreg.KEY_SET_VALUE,
        )
    except OSError:
        return
    try:
        for name in (SHORTCUT_NAME, LEGACY_SHORTCUT_NAME):
            try:
                winreg.DeleteValue(key, name)
            except OSError:
                pass
    finally:
        winreg.CloseKey(key)


def _write_launcher() -> Path:
    """Write a launcher outside OneDrive that waits for the project at logon."""
    target, _arguments = launch_command()
    main_py = ROOT / "main.py"
    path = launcher_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    body = f"""# TALCON logon launcher (generated). Retries while OneDrive is still starting.
$ErrorActionPreference = "SilentlyContinue"
$log = Join-Path $env:LOCALAPPDATA "TALCON\\startup.log"
function Write-Log($message) {{
    try {{ Add-Content -Path $log -Value ("{{0}} {{1}}" -f (Get-Date -Format o), $message) }} catch {{}}
}}
$python = '{_ps_string(target)}'
$main = '{_ps_string(main_py)}'
$work = '{_ps_string(ROOT)}'
for ($attempt = 1; $attempt -le 60; $attempt++) {{
    if ((Test-Path $python) -and (Test-Path $main)) {{
        try {{
            Start-Process -FilePath $python -ArgumentList @('{_ps_string(main_py)}', '--startup') -WorkingDirectory $work
            Write-Log "TALCON started (attempt $attempt)"
            exit 0
        }} catch {{
            Write-Log ("Start failed: " + $_.Exception.Message)
        }}
    }}
    Start-Sleep -Seconds 5
}}
Write-Log "TALCON files were not available after 5 minutes"
exit 1
"""
    path.write_text(body, encoding="utf-8")
    return path


def _create_scheduled_task() -> tuple[bool, str]:
    launcher = launcher_path()
    script = (
        "$ErrorActionPreference='Stop';"
        "$action=New-ScheduledTaskAction -Execute 'powershell.exe' "
        "-Argument '-NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass "
        f"-File \"{launcher}\"';"
        "$trigger=New-ScheduledTaskTrigger -AtLogOn;"
        "$trigger.Delay='PT30S';"
        "$settings=New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries "
        "-DontStopIfGoingOnBatteries -StartWhenAvailable;"
        f"Register-ScheduledTask -TaskName '{TASK_NAME}' -Action $action "
        "-Trigger $trigger -Settings $settings "
        "-Description 'TALCON local desktop assistant' -Force | Out-Null"
    )
    result = _powershell(script)
    if result.returncode != 0:
        return False, (result.stderr or result.stdout or "scheduled task creation failed").strip()
    log.info("Logon task created: %s", TASK_NAME)
    return True, "TALCON logon task created."


def _delete_scheduled_task() -> None:
    try:
        subprocess.run(
            ["schtasks.exe", "/Delete", "/TN", TASK_NAME, "/F"],
            capture_output=True, text=True, timeout=15,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except Exception:
        log.debug("Could not delete the logon task", exc_info=True)


def _run_command() -> str:
    powershell = (
        Path(os.environ.get("SystemRoot", r"C:\Windows"))
        / "System32" / "WindowsPowerShell" / "v1.0" / "powershell.exe"
    )
    return (
        f'"{powershell}" -NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass '
        f'-File "{launcher_path()}"'
    )


def _create_run_value() -> tuple[bool, str]:
    """HKCU Run entry; needs no admin rights and starts the retry launcher."""
    import winreg
    command = _run_command()
    key = winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE)
    try:
        winreg.SetValueEx(key, RUN_VALUE, 0, winreg.REG_SZ, command)
    finally:
        winreg.CloseKey(key)
    log.info("Startup Run value created for %s", RUN_VALUE)
    return True, "TALCON will start quietly when you sign in to Windows."


def _delete_run_value() -> None:
    try:
        import winreg
        key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE)
    except OSError:
        return
    try:
        try:
            winreg.DeleteValue(key, RUN_VALUE)
        except OSError:
            pass
    finally:
        winreg.CloseKey(key)


def _create_shortcut() -> tuple[bool, str]:
    folder = startup_folder()
    folder.mkdir(parents=True, exist_ok=True)
    target = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32" / "WindowsPowerShell" / "v1.0" / "powershell.exe"
    arguments = (
        f'-NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass '
        f'-File "{launcher_path()}"'
    )
    icon = ASSETS_DIR / "talcon_icon.png"
    code = (
        "$ErrorActionPreference='Stop';"
        "$w=New-Object -ComObject WScript.Shell;"
        f"$s=$w.CreateShortcut({_ps_quote(str(shortcut_path()))});"
        f"$s.TargetPath={_ps_quote(str(target))};"
        f"$s.Arguments={_ps_quote(arguments)};"
        f"$s.WorkingDirectory={_ps_quote(str(ROOT))};"
        "$s.Description='TALCON local desktop assistant';"
        + (f"$s.IconLocation={_ps_quote(str(icon) + ',0')};" if icon.exists() else "")
        + "$s.Save()"
    )
    result = _powershell(code)
    if result.returncode != 0:
        raise RuntimeError((result.stderr or result.stdout or "shortcut creation failed").strip())
    return True, str(shortcut_path())


# ---------------------------------------------------------------------------
# public API
# ---------------------------------------------------------------------------
def ensure() -> tuple[bool, str]:
    """Repair startup at each launch when the setting is enabled."""
    _clear_startup_approval()
    try:
        # Always refresh: the project folder may have been renamed/moved.
        _write_launcher()
    except Exception:
        log.exception("Could not write the startup launcher")
    if scheduled_task_exists():
        return True, "TALCON logon task is present."
    if run_key_exists():
        if run_key_value() != _run_command():
            try:
                _create_run_value()
            except Exception:
                log.exception("Could not refresh the startup Run entry")
        return True, "TALCON logon entry is present."
    try:
        _write_launcher()
        ok, detail = _create_scheduled_task()
    except Exception as exc:
        ok, detail = False, str(exc)
    if ok:
        return True, detail
    log.warning("Logon task could not be created (%s); using the Run entry", detail)
    if run_key_exists():
        if run_key_value() != _run_command():
            try:
                _create_run_value()
            except Exception:
                log.exception("Could not refresh the startup Run entry")
        return True, "TALCON logon entry is present."
    return enable()


def enable() -> tuple[bool, str]:
    try:
        return _enable()
    finally:
        invalidate_cache()


def _enable() -> tuple[bool, str]:
    try:
        _clear_startup_approval()
        _write_launcher()
        task_ok, task_detail = _create_scheduled_task()
        if task_ok:
            # The scheduled task is the reliable path.  Remove older Startup
            # entries so Windows cannot launch a second copy.
            for path in (shortcut_path(), legacy_shortcut_path()):
                try:
                    if path.exists():
                        path.unlink()
                except OSError:
                    log.debug("Could not remove %s", path, exc_info=True)
            return True, "TALCON will start quietly when you sign in to Windows."
        try:
            run_ok, run_detail = _create_run_value()
        except Exception as exc:
            log.warning("Run entry could not be created: %s", exc)
            run_ok, run_detail = False, str(exc)
        if run_ok:
            for path in (shortcut_path(), legacy_shortcut_path()):
                try:
                    if path.exists():
                        path.unlink()
                except OSError:
                    log.debug("Could not remove %s", path, exc_info=True)
            return True, run_detail
        _create_shortcut()
        legacy = legacy_shortcut_path()
        if legacy.exists():
            try:
                legacy.unlink()
            except OSError:
                log.warning("Could not remove the old TALCON startup shortcut")
        log.info("Startup shortcut created at %s", shortcut_path())
        return True, "TALCON will start quietly when you sign in to Windows."
    except Exception as exc:
        log.exception("Could not enable startup")
        return False, f"Could not enable startup: {exc}"


def disable() -> tuple[bool, str]:
    try:
        return _disable()
    finally:
        invalidate_cache()


def _disable() -> tuple[bool, str]:
    try:
        _delete_scheduled_task()
        _delete_run_value()
        for path in (shortcut_path(), legacy_shortcut_path()):
            if path.exists():
                path.unlink()
        log.info("Startup task and shortcuts removed")
        return True, "TALCON will no longer start automatically."
    except Exception as exc:
        log.exception("Could not disable startup")
        return False, f"Could not disable startup: {exc}"
