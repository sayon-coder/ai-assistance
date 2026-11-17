"""Start-menu shortcut and taskbar identity for TALCON.

Windows pins a pythonw.exe program with the Python icon unless a Start-menu
shortcut with the same AppUserModelID and a real .ico exists.  This module
creates that shortcut once and repairs it on every launch.
"""
from __future__ import annotations

import logging
import os
import subprocess
from pathlib import Path

from .config import ASSETS_DIR, ROOT

log = logging.getLogger("talcon.shortcut")
APP_USER_MODEL_ID = "TALCON.LocalAssistant"
SHORTCUT_NAME = "TALCON.lnk"


def start_menu_path() -> Path:
    appdata = os.environ.get("APPDATA") or str(Path.home() / "AppData" / "Roaming")
    return (
        Path(appdata) / "Microsoft" / "Windows" / "Start Menu" / "Programs"
        / SHORTCUT_NAME
    )


def icon_path() -> Path:
    return ASSETS_DIR / "talcon.ico"


def _pythonw() -> Path:
    candidate = ROOT / ".venv" / "Scripts" / "pythonw.exe"
    if candidate.exists():
        return candidate.resolve()
    import sys
    return Path(sys.executable).with_name("pythonw.exe").resolve()


def _ensure_icon() -> Path:
    """Create a multi-size .ico from the PNG when it is missing."""
    target = icon_path()
    if target.exists():
        return target
    png = ASSETS_DIR / "talcon_icon.png"
    if not png.exists():
        return target
    try:
        from PIL import Image

        image = Image.open(png)
        image.save(
            target,
            sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64),
                   (128, 128), (256, 256)],
        )
    except Exception:
        log.debug("Could not build the .ico with Pillow", exc_info=True)
    return target


_AUMID_CS = r"""
using System;
using System.Runtime.InteropServices;

public static class TalconShortcut {
    [StructLayout(LayoutKind.Sequential, Pack = 4)]
    public struct PropertyKey { public Guid fmtid; public int pid; }

    [StructLayout(LayoutKind.Explicit)]
    public struct PropVariant {
        [FieldOffset(0)] public short vt;
        [FieldOffset(8)] public IntPtr pointerValue;
    }

    [ComImport, Guid("886D8EEB-8CF2-4446-8D02-CDBA1DBDCF99"),
     InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
    interface IPropertyStore {
        void GetCount(out uint count);
        void GetAt(uint index, out PropertyKey key);
        void GetValue(ref PropertyKey key, out PropVariant value);
        void SetValue(ref PropertyKey key, ref PropVariant value);
        void Commit();
    }

    [DllImport("shell32.dll", CharSet = CharSet.Unicode, PreserveSig = false)]
    static extern void SHGetPropertyStoreFromParsingName(
        string path, IntPtr parent, uint flags, ref Guid iid,
        [MarshalAs(UnmanagedType.Interface)] out IPropertyStore store);

    [DllImport("ole32.dll")]
    static extern int PropVariantClear(ref PropVariant value);

    public static void SetAppId(string path, string appId) {
        var iid = new Guid("886D8EEB-8CF2-4446-8D02-CDBA1DBDCF99");
        IPropertyStore store;
        SHGetPropertyStoreFromParsingName(path, IntPtr.Zero, 2, ref iid, out store);
        var key = new PropertyKey {
            fmtid = new Guid("9F4C2855-9F79-4B39-A8D0-E1D42DE1D5F3"),
            pid = 5
        };
        var value = new PropVariant {
            vt = 31,
            pointerValue = Marshal.StringToCoTaskMemUni(appId)
        };
        store.SetValue(ref key, ref value);
        store.Commit();
        PropVariantClear(ref value);
        Marshal.ReleaseComObject(store);
    }
}
"""


def _ps_quote(value: str) -> str:
    return "'" + str(value).replace("'", "''") + "'"


def _powershell(code: str) -> subprocess.CompletedProcess:
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    return subprocess.run(
        ["powershell.exe", "-NoProfile", "-NonInteractive",
         "-ExecutionPolicy", "Bypass", "-Command", code],
        capture_output=True, text=True, timeout=60, creationflags=flags,
    )


def _write_shortcut() -> None:
    target = _pythonw()
    main_py = ROOT / "main.py"
    icon = _ensure_icon()
    link = start_menu_path()
    link.parent.mkdir(parents=True, exist_ok=True)
    script = (
        "$ErrorActionPreference='Stop';"
        "$w=New-Object -ComObject WScript.Shell;"
        f"$s=$w.CreateShortcut({_ps_quote(str(link))});"
        f"$s.TargetPath={_ps_quote(str(target))};"
        f"$s.Arguments={_ps_quote(chr(34) + str(main_py) + chr(34))};"
        f"$s.WorkingDirectory={_ps_quote(str(ROOT))};"
        "$s.Description='TALCON local desktop assistant';"
        + (f"$s.IconLocation={_ps_quote(str(icon) + ',0')};" if icon.exists() else "")
        + "$s.Save();"
        f"Add-Type -TypeDefinition {_ps_quote(_AUMID_CS)};"
        f"[TalconShortcut]::SetAppId({_ps_quote(str(link))}, "
        f"{_ps_quote(APP_USER_MODEL_ID)})"
    )
    result = _powershell(script)
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "shortcut creation failed").strip()
        raise RuntimeError(detail)


def ensure_shortcut() -> bool:
    """Create or repair the Start-menu shortcut used for taskbar pinning."""
    if os.name != "nt":
        return False
    try:
        import sys
        if getattr(sys, "frozen", False):
            return False
        link = start_menu_path()
        if link.exists():
            try:
                # The .lnk stores its target as UTF-16; repair a stale path.
                if str(ROOT).encode("utf-16-le") in link.read_bytes():
                    return True
            except Exception:
                pass
        _write_shortcut()
        log.info("Start-menu shortcut ready: %s", start_menu_path())
        return True
    except Exception:
        log.debug("Could not prepare the Start-menu shortcut", exc_info=True)
        return False
