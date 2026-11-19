"""Best-effort Windows thread priority helper.

TALCON keeps the UI and the microphone hot; neural speech, recognition and
file monitors are background work.  Lowering those threads one step means a
misbehaving background task cannot make the window stutter on a small laptop.
"""
from __future__ import annotations

import ctypes
import logging
import sys

log = logging.getLogger("talcon.threads")

THREAD_PRIORITY_BELOW_NORMAL = -1


def lower_current_thread() -> bool:
    """Drop the calling thread to below-normal priority (Windows only)."""
    if sys.platform != "win32":
        return False
    try:
        kernel32 = ctypes.windll.kernel32
        kernel32.SetThreadPriority.argtypes = [ctypes.c_void_p, ctypes.c_int]
        kernel32.SetThreadPriority.restype = ctypes.c_int
        return bool(
            kernel32.SetThreadPriority(
                kernel32.GetCurrentThread(), THREAD_PRIORITY_BELOW_NORMAL
            )
        )
    except Exception:
        log.debug("Could not lower the worker thread priority", exc_info=True)
        return False
