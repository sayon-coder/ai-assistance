"""Per-user Windows startup integration through a Startup-folder shortcut.

No administrator rights are needed and no registry Run key is used.
"""
from __future__ import annotations

import logging
import os
import subprocess
import sys
from pathlib import Path

from .config import ASSETS_DIR, ROOT

log = logging.getLogger("nova.startup")
SHORTCUT_NAME = "NOVA.lnk"


def startup_folder() -> Path:
    appdata = os.environ.get("APPDATA")
    if appdata:
        return Path(appdata) / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Startup"
    return Path.home() / "AppData" / "Roaming" / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Startup"


def shortcut_path() -> Path:
    return startup_folder() / SHORTCUT_NAME


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


def is_enabled() -> bool:
    return shortcut_path().exists()


def _powershell(code: str) -> subprocess.CompletedProcess:
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    return subprocess.run(
        ["powershell.exe", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-Command", code],
        capture_output=True, text=True, timeout=45, creationflags=flags,
    )


def _ps_quote(value: str) -> str:
    return "'" + str(value).replace("'", "''") + "'"


def enable() -> tuple[bool, str]:
    try:
        folder = startup_folder()
        folder.mkdir(parents=True, exist_ok=True)
        target, arguments = launch_command()
        icon = ASSETS_DIR / "nova_icon.png"
        code = (
            "$ErrorActionPreference='Stop';"
            "$w=New-Object -ComObject WScript.Shell;"
            f"$s=$w.CreateShortcut({_ps_quote(str(shortcut_path()))});"
            f"$s.TargetPath={_ps_quote(str(target))};"
            f"$s.Arguments={_ps_quote(arguments)};"
            f"$s.WorkingDirectory={_ps_quote(str(ROOT))};"
            "$s.Description='NOVA local desktop assistant';"
            + (f"$s.IconLocation={_ps_quote(str(icon) + ',0')};" if icon.exists() else "")
            + "$s.Save()"
        )
        result = _powershell(code)
        if result.returncode != 0:
            raise RuntimeError((result.stderr or result.stdout or "shortcut creation failed").strip())
        log.info("Startup shortcut created at %s", shortcut_path())
        return True, "NOVA will start quietly when you sign in to Windows."
    except Exception as exc:
        log.exception("Could not enable startup")
        return False, f"Could not enable startup: {exc}"


def disable() -> tuple[bool, str]:
    try:
        path = shortcut_path()
        if path.exists():
            path.unlink()
        log.info("Startup shortcut removed")
        return True, "NOVA will no longer start automatically."
    except Exception as exc:
        log.exception("Could not disable startup")
        return False, f"Could not disable startup: {exc}"
