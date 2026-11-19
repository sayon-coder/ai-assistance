"""System-wide hotkey (default Ctrl+Alt+Space) to start listening.

TALCON is a voice assistant; having to find the tray icon first defeats the
point.  The hotkey is delivered through Qt's native event filter, so it works
while another application has focus.  A hotkey already owned by another
program must never stop TALCON from starting, so failure is silent.
"""
from __future__ import annotations

import ctypes
import logging
import sys

from PySide6.QtCore import QAbstractNativeEventFilter

log = logging.getLogger("talcon.hotkey")

WM_HOTKEY = 0x0312
MOD_ALT = 0x0001
MOD_CONTROL = 0x0002
MOD_NOREPEAT = 0x4000
VK_SPACE = 0x20
HOTKEY_ID = 0x7A21


class _MSG(ctypes.Structure):
    _fields_ = [
        ("hWnd", ctypes.c_void_p),
        ("message", ctypes.c_uint),
        ("wParam", ctypes.c_size_t),
        ("lParam", ctypes.c_ssize_t),
    ]


class _HotkeyFilter(QAbstractNativeEventFilter):
    def __init__(self, callback):
        super().__init__()
        self._callback = callback

    def nativeEventFilter(self, event_type, message):
        try:
            if event_type in (b"windows_generic_MSG", b"windows_dispatcher_MSG"):
                msg = ctypes.cast(int(message), ctypes.POINTER(_MSG)).contents
                if msg.message == WM_HOTKEY and int(msg.wParam) == HOTKEY_ID:
                    self._callback()
        except Exception:
            log.debug("Hotkey event could not be decoded", exc_info=True)
        return False, 0


class GlobalHotkey:
    """Owns one RegisterHotKey registration for the application's lifetime."""

    def __init__(self, app, callback):
        self._app = app
        self._filter = _HotkeyFilter(callback)
        self._registered = False

    def register(self) -> bool:
        if sys.platform != "win32":
            return False
        try:
            user32 = ctypes.windll.user32
            self._registered = bool(user32.RegisterHotKey(
                None, HOTKEY_ID,
                MOD_CONTROL | MOD_ALT | MOD_NOREPEAT, VK_SPACE,
            ))
            if self._registered:
                self._app.installNativeEventFilter(self._filter)
                log.info("Global listen hotkey registered (Ctrl+Alt+Space)")
            else:
                log.info("Ctrl+Alt+Space is already used by another program")
        except Exception:
            log.debug("Could not register the global hotkey", exc_info=True)
        return self._registered

    def unregister(self) -> None:
        if not self._registered:
            return
        try:
            ctypes.windll.user32.UnregisterHotKey(None, HOTKEY_ID)
        except Exception:
            pass
        try:
            self._app.removeNativeEventFilter(self._filter)
        except Exception:
            pass
        self._registered = False


def install(app, callback) -> GlobalHotkey:
    """Register the hotkey and return the object that owns it."""
    hotkey = GlobalHotkey(app, callback)
    hotkey.register()
    try:
        app.aboutToQuit.connect(hotkey.unregister)
    except Exception:
        log.debug("Could not hook hotkey cleanup", exc_info=True)
    return hotkey

