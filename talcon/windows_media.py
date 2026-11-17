"""Global Windows media transport control through the media key.

The Windows media key ``VK_MEDIA_PLAY_PAUSE`` is delivered to whichever
application currently owns the system media session, so YouTube or a music
player does not need to be the foreground window and no browser extension,
DevTools session or extra process is involved.

The key is a *toggle*: Windows exposes no supported API here that reports
whether the session is currently playing, so "pause" and "resume" both send
the same key and the actual state depends on the media application.
"""
from __future__ import annotations

import ctypes
import logging
import os

log = logging.getLogger("talcon.media")

VK_MEDIA_PLAY_PAUSE = 0xB3
KEYEVENTF_KEYUP = 0x0002
INPUT_KEYBOARD = 1

TOGGLE_NOTE = (
    "Windows media keys are a play/pause toggle; Windows does not report the "
    "current playback state to TALCON."
)


def available() -> bool:
    """True when the Windows media key can be sent on this platform."""
    return os.name == "nt"


def _send_media_key(vk: int) -> None:
    """Send one key press and release through SendInput."""
    from ctypes import wintypes

    ulong_ptr = ctypes.c_size_t

    class KEYBDINPUT(ctypes.Structure):
        _fields_ = [
            ("wVk", wintypes.WORD),
            ("wScan", wintypes.WORD),
            ("dwFlags", wintypes.DWORD),
            ("time", wintypes.DWORD),
            ("dwExtraInfo", ulong_ptr),
        ]

    class MOUSEINPUT(ctypes.Structure):
        _fields_ = [
            ("dx", wintypes.LONG),
            ("dy", wintypes.LONG),
            ("mouseData", wintypes.DWORD),
            ("dwFlags", wintypes.DWORD),
            ("time", wintypes.DWORD),
            ("dwExtraInfo", ulong_ptr),
        ]

    class HARDWAREINPUT(ctypes.Structure):
        _fields_ = [
            ("uMsg", wintypes.DWORD),
            ("wParamL", wintypes.WORD),
            ("wParamH", wintypes.WORD),
        ]

    class _InputUnion(ctypes.Union):
        _fields_ = [("ki", KEYBDINPUT), ("mi", MOUSEINPUT), ("hi", HARDWAREINPUT)]

    class INPUT(ctypes.Structure):
        _fields_ = [("type", wintypes.DWORD), ("union", _InputUnion)]

    user32 = ctypes.WinDLL("user32", use_last_error=True)
    down = INPUT(type=INPUT_KEYBOARD, union=_InputUnion(ki=KEYBDINPUT(vk, 0, 0, 0, 0)))
    up = INPUT(type=INPUT_KEYBOARD, union=_InputUnion(ki=KEYBDINPUT(vk, 0, KEYEVENTF_KEYUP, 0, 0)))
    inputs = (INPUT * 2)(down, up)
    sent = user32.SendInput(2, inputs, ctypes.sizeof(INPUT))
    if sent != 2:
        raise ctypes.WinError(ctypes.get_last_error())


def send_play_pause(sender=None) -> tuple[bool, str]:
    """Send the global play/pause media key exactly once."""
    sender = sender or _send_media_key
    try:
        sender(VK_MEDIA_PLAY_PAUSE)
    except Exception as exc:
        log.exception("Could not send the Windows media key")
        return False, f"Windows media key failed: {exc}"
    log.info("Sent VK_MEDIA_PLAY_PAUSE (0x%02X)", VK_MEDIA_PLAY_PAUSE)
    return True, "Sent the Windows play/pause media key."
