"""Native Windows window enumeration and graceful close requests.

Only visible top-level user application windows are considered.  Windows
belonging to TALCON, the shell infrastructure (desktop, taskbar), and critical
system processes are always skipped.  Closing sends ``WM_CLOSE`` so every
application keeps its normal save/discard prompts; nothing is force-terminated
and no process is killed with taskkill or TerminateProcess.
"""
from __future__ import annotations

import ctypes
import ctypes.wintypes as wintypes
import logging
import os
import time
from dataclasses import dataclass, field

log = logging.getLogger("talcon.windows_control")

WM_CLOSE = 0x0010
GWL_EXSTYLE = -20
GW_OWNER = 4
WS_EX_TOOLWINDOW = 0x00000080
DWMWA_CLOAKED = 14
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000

# Shell infrastructure classes: desktop, taskbar and their hosts.  These must
# never receive WM_CLOSE, otherwise the desktop or taskbar would disappear.
SHELL_CLASSES = {
    "progman",
    "workerw",
    "shell_traywnd",
    "shell_secondarytraywnd",
    "multitaskingviewframe",
    "tasklistthumbnailwnd",
    "windows.ui.core.corewindow",
}

# Critical processes that must never be asked to close, even if they somehow
# own a visible window.
SYSTEM_PROCESSES = {
    "csrss.exe", "wininit.exe", "winlogon.exe", "services.exe", "lsass.exe",
    "smss.exe", "svchost.exe", "dwm.exe", "sihost.exe", "taskhostw.exe",
    "fontdrvhost.exe", "ctfmon.exe", "runtimebroker.exe", "searchhost.exe",
    "searchapp.exe", "startmenuexperiencehost.exe", "shellexperiencehost.exe",
    "textinputhost.exe", "lockapp.exe", "securityhealthservice.exe",
    "securityhealthsystray.exe", "msmpeng.exe", "nissrv.exe", "wudfhost.exe",
    "system", "registry", "memory compression",
    # Third-party antivirus / security suites: never ask them to close.
    "avastui.exe", "avastsvc.exe", "avgui.exe", "avgsvc.exe", "avp.exe",
    "avpui.exe", "kavfs.exe", "mcshield.exe", "mcuicnt.exe", "nortonsecurity.exe",
    "ns.exe", "bdagent.exe", "bdservicehost.exe", "mbam.exe", "mbamtray.exe",
    "savservice.exe", "sophosui.exe", "zemana.exe", "hitmanpro.exe",
}

#: Processes that may never be closed, even when they own a visible window.
PROTECTED_PROCESSES = SYSTEM_PROCESSES | {"explorer.exe", "pythonw.exe", "python.exe"}


def is_protected_process(process: str) -> bool:
    """True for shell, security, TALON-runtime and critical Windows processes."""
    return str(process or "").strip().lower() in PROTECTED_PROCESSES


MAX_TITLE = 512
MAX_CLASS = 256
PROCESS_TERMINATE = 0x0001


@dataclass
class WindowInfo:
    hwnd: int
    title: str
    class_name: str
    pid: int
    process: str = ""
    minimized: bool = False

    @property
    def label(self) -> str:
        state = " (minimised)" if self.minimized else ""
        owner = f" · {self.process}" if self.process else ""
        return f"{self.title}{state}{owner}"

    def to_dict(self) -> dict:
        return {
            "hwnd": self.hwnd,
            "title": self.title,
            "class_name": self.class_name,
            "pid": self.pid,
            "process": self.process,
            "minimized": self.minimized,
            "label": self.label,
        }


@dataclass
class CloseResult:
    asked: list[WindowInfo] = field(default_factory=list)
    closed: list[WindowInfo] = field(default_factory=list)
    refused: list[WindowInfo] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return bool(self.closed) and not self.errors

    def summary(self) -> str:
        if not self.asked:
            return "No open application windows were found."
        parts = [f"Asked {len(self.asked)} application window(s) to close"]
        if self.closed:
            parts.append(f"{len(self.closed)} closed")
        if self.refused:
            parts.append(f"{len(self.refused)} still open")
        if self.errors:
            parts.append(f"{len(self.errors)} error(s)")
        return "; ".join(parts) + "."


class WindowApi:
    """Thin ctypes wrapper so tests can substitute a fake implementation.

    Every prototype is declared explicitly: handles are pointer-sized
    (``HWND``/``HANDLE``), so a 64-bit handle can never be truncated to a
    32-bit ``int`` by ctypes' default conversion.
    """

    def __init__(self):
        self._user32 = ctypes.WinDLL("user32", use_last_error=True)
        self._kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        try:
            self._dwmapi = ctypes.WinDLL("dwmapi", use_last_error=True)
        except OSError:
            self._dwmapi = None
        # Pointer-sized window-long accessor: on 64-bit Windows the W variant
        # truncates indexes/returns that live in the upper half of a pointer.
        self._get_window_long = getattr(self._user32, "GetWindowLongPtrW", None)
        if ctypes.sizeof(ctypes.c_void_p) == 8:
            # GetWindowLongPtrW is a C macro alias on 32-bit and may not be an
            # exported symbol; on 64-bit it is the required pointer-safe API.
            self._get_window_long = getattr(
                self._user32, "GetWindowLongPtrW", self._user32.GetWindowLongW
            )
            self._get_window_long.argtypes = [wintypes.HWND, ctypes.c_int]
            self._get_window_long.restype = ctypes.c_ssize_t
        else:  # 32-bit Windows uses the classic export.
            self._get_window_long = self._user32.GetWindowLongW
            self._get_window_long.argtypes = [wintypes.HWND, ctypes.c_int]
            self._get_window_long.restype = ctypes.c_long
        self._enum_proc_type = ctypes.WINFUNCTYPE(
            wintypes.BOOL, wintypes.HWND, wintypes.LPARAM
        )
        self._declare_prototypes()

    def _declare_prototypes(self) -> None:
        user32 = self._user32
        kernel32 = self._kernel32

        # -- enumeration ---------------------------------------------------
        user32.EnumWindows.argtypes = [
            self._enum_proc_type, wintypes.LPARAM,
        ]
        user32.EnumWindows.restype = wintypes.BOOL

        # -- visibility and title -------------------------------------------
        user32.IsWindowVisible.argtypes = [wintypes.HWND]
        user32.IsWindowVisible.restype = wintypes.BOOL
        user32.IsIconic.argtypes = [wintypes.HWND]
        user32.IsIconic.restype = wintypes.BOOL
        user32.GetWindowTextLengthW.argtypes = [wintypes.HWND]
        user32.GetWindowTextLengthW.restype = ctypes.c_int
        user32.GetWindowTextW.argtypes = [
            wintypes.HWND, wintypes.LPWSTR, ctypes.c_int,
        ]
        user32.GetWindowTextW.restype = ctypes.c_int

        # -- class name ------------------------------------------------------
        user32.GetClassNameW.argtypes = [
            wintypes.HWND, wintypes.LPWSTR, ctypes.c_int,
        ]
        user32.GetClassNameW.restype = ctypes.c_int

        # -- window to process -----------------------------------------------
        user32.GetWindowThreadProcessId.argtypes = [
            wintypes.HWND, ctypes.POINTER(wintypes.DWORD),
        ]
        user32.GetWindowThreadProcessId.restype = wintypes.DWORD
        user32.GetWindow.argtypes = [wintypes.HWND, wintypes.UINT]
        user32.GetWindow.restype = wintypes.HWND

        # -- closing and existence -------------------------------------------
        user32.PostMessageW.argtypes = [
            wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM,
        ]
        user32.PostMessageW.restype = wintypes.BOOL
        user32.IsWindow.argtypes = [wintypes.HWND]
        user32.IsWindow.restype = wintypes.BOOL

        # -- process handles --------------------------------------------------
        kernel32.OpenProcess.argtypes = [
            wintypes.DWORD, wintypes.BOOL, wintypes.DWORD,
        ]
        kernel32.OpenProcess.restype = wintypes.HANDLE
        kernel32.QueryFullProcessImageNameW.argtypes = [
            wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR,
            ctypes.POINTER(wintypes.DWORD),
        ]
        kernel32.QueryFullProcessImageNameW.restype = wintypes.BOOL
        kernel32.TerminateProcess.argtypes = [wintypes.HANDLE, wintypes.UINT]
        kernel32.TerminateProcess.restype = wintypes.BOOL
        kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
        kernel32.CloseHandle.restype = wintypes.BOOL

        if self._dwmapi is not None:
            self._dwmapi.DwmGetWindowAttribute.argtypes = [
                wintypes.HWND, wintypes.DWORD, wintypes.LPVOID,
                wintypes.DWORD,
            ]
            self._dwmapi.DwmGetWindowAttribute.restype = ctypes.c_long

    # -- enumeration -------------------------------------------------------
    def windows(self) -> list[int]:
        handles: list[int] = []

        def callback(hwnd, _lparam):
            handles.append(int(hwnd or 0))
            return True

        callback_ref = self._enum_proc_type(callback)
        self._user32.EnumWindows(callback_ref, 0)
        return handles

    def is_visible(self, hwnd: int) -> bool:
        return bool(self._user32.IsWindowVisible(hwnd))

    def is_minimized(self, hwnd: int) -> bool:
        return bool(self._user32.IsIconic(hwnd))

    def title(self, hwnd: int) -> str:
        length = self._user32.GetWindowTextLengthW(hwnd)
        if length <= 0:
            return ""
        buffer = ctypes.create_unicode_buffer(min(length + 1, MAX_TITLE))
        self._user32.GetWindowTextW(hwnd, buffer, len(buffer))
        return buffer.value.strip()

    def class_name(self, hwnd: int) -> str:
        buffer = ctypes.create_unicode_buffer(MAX_CLASS)
        self._user32.GetClassNameW(hwnd, buffer, MAX_CLASS)
        return buffer.value.strip()

    def pid(self, hwnd: int) -> int:
        value = ctypes.c_ulong(0)
        self._user32.GetWindowThreadProcessId(hwnd, ctypes.byref(value))
        return int(value.value)

    def owner(self, hwnd: int) -> int:
        return int(self._user32.GetWindow(hwnd, GW_OWNER) or 0)

    def ex_style(self, hwnd: int) -> int:
        return int(self._get_window_long(hwnd, GWL_EXSTYLE) or 0)

    def is_cloaked(self, hwnd: int) -> bool:
        if self._dwmapi is None:
            return False
        value = ctypes.c_int(0)
        try:
            result = self._dwmapi.DwmGetWindowAttribute(
                hwnd, DWMWA_CLOAKED, ctypes.byref(value), ctypes.sizeof(value)
            )
        except Exception:
            return False
        return result == 0 and bool(value.value)

    def process_name(self, pid: int) -> str:
        handle = self._kernel32.OpenProcess(
            PROCESS_QUERY_LIMITED_INFORMATION, False, int(pid)
        )
        if not handle:
            return ""
        try:
            buffer = ctypes.create_unicode_buffer(1024)
            size = ctypes.c_ulong(len(buffer))
            if not self._kernel32.QueryFullProcessImageNameW(
                handle, 0, buffer, ctypes.byref(size)
            ):
                return ""
            return os.path.basename(buffer.value)
        finally:
            self._kernel32.CloseHandle(handle)

    # -- closing -----------------------------------------------------------
    def post_close(self, hwnd: int) -> bool:
        return bool(self._user32.PostMessageW(hwnd, WM_CLOSE, 0, 0))

    def is_window(self, hwnd: int) -> bool:
        return bool(self._user32.IsWindow(hwnd))

    def terminate_process(self, pid: int) -> bool:
        """Last-resort force termination; only reached after confirmation."""
        handle = self._kernel32.OpenProcess(
            PROCESS_TERMINATE | PROCESS_QUERY_LIMITED_INFORMATION, False, int(pid)
        )
        if not handle:
            return False
        try:
            return bool(self._kernel32.TerminateProcess(handle, 1))
        finally:
            self._kernel32.CloseHandle(handle)

    def is_process_alive(self, pid: int) -> bool:
        if int(pid) in (0, 4):
            return True
        handle = self._kernel32.OpenProcess(
            PROCESS_QUERY_LIMITED_INFORMATION, False, int(pid)
        )
        if not handle:
            return False
        self._kernel32.CloseHandle(handle)
        return True


def enumerate_windows(api=None, own_pid: int | None = None) -> list[WindowInfo]:
    """Return visible top-level user application windows, including minimised."""
    api = api or WindowApi()
    own_pid = os.getpid() if own_pid is None else int(own_pid)
    result: list[WindowInfo] = []
    try:
        handles = api.windows()
    except Exception:
        log.exception("Could not enumerate windows")
        return result

    for hwnd in handles:
        try:
            if not api.is_visible(hwnd):
                continue
            title = api.title(hwnd)
            if not title:
                continue
            class_name = api.class_name(hwnd)
            if class_name.lower() in SHELL_CLASSES:
                continue
            pid = api.pid(hwnd)
            if pid in (0, 4) or pid == own_pid:
                continue
            if api.owner(hwnd):
                continue
            if api.ex_style(hwnd) & WS_EX_TOOLWINDOW:
                continue
            if api.is_cloaked(hwnd):
                continue
            process = api.process_name(pid)
            if process.lower() in SYSTEM_PROCESSES:
                continue
            result.append(WindowInfo(
                hwnd=hwnd,
                title=title,
                class_name=class_name,
                pid=pid,
                process=process,
                minimized=api.is_minimized(hwnd),
            ))
        except Exception:
            log.debug("Skipped a window that could not be inspected", exc_info=True)
    return result


def enumerate_process_windows(processes, api=None, own_pid=None) -> list[WindowInfo]:
    """Top-level windows owned by the given processes, hidden ones included.

    :func:`enumerate_windows` only reports visible windows, so an application
    that hides its main window in the system tray (MechVibes does exactly
    that) looked as though it had already quit and could not be closed.
    """
    names = {str(name).strip().lower() for name in processes or () if str(name).strip()}
    if not names:
        return []
    api = api or WindowApi()
    own_pid = os.getpid() if own_pid is None else int(own_pid)
    try:
        handles = api.windows()
    except Exception:
        log.exception("Could not enumerate windows")
        return []
    result: list[WindowInfo] = []
    for hwnd in handles:
        try:
            title = api.title(hwnd)
            if not title:
                continue
            class_name = api.class_name(hwnd)
            if class_name.lower() in SHELL_CLASSES:
                continue
            pid = api.pid(hwnd)
            if pid in (0, 4) or pid == own_pid:
                continue
            if api.owner(hwnd):
                continue
            if api.ex_style(hwnd) & WS_EX_TOOLWINDOW:
                continue
            if api.is_cloaked(hwnd):
                continue
            process = api.process_name(pid)
            if process.lower() not in names:
                continue
            result.append(WindowInfo(
                hwnd=hwnd,
                title=title,
                class_name=class_name,
                pid=pid,
                process=process,
                minimized=api.is_minimized(hwnd),
            ))
        except Exception:
            log.debug("Skipped a window that could not be inspected", exc_info=True)
    return result


def close_windows(windows, api=None, wait: float = 1.2, sleep=time.sleep,
                  now=time.monotonic) -> CloseResult:
    """Send WM_CLOSE to each window and report which ones remained open."""
    api = api or WindowApi()
    result = CloseResult()
    pending: list[WindowInfo] = []
    for window in windows or ():
        try:
            if api.post_close(window.hwnd):
                result.asked.append(window)
                pending.append(window)
            else:
                result.errors.append(f"Could not ask {window.title} to close")
        except Exception as exc:
            log.exception("Could not post WM_CLOSE to %s", window.title)
            result.errors.append(f"{window.title}: {exc}")

    if pending:
        deadline = now() + max(0.0, float(wait))
        while True:
            pending = [window for window in pending if api.is_window(window.hwnd)]
            if not pending or now() >= deadline:
                break
            sleep(0.05)
        remaining = {window.hwnd for window in pending}
        result.closed = [window for window in result.asked if window.hwnd not in remaining]
        result.refused = list(pending)

    log.info(
        "Close-all: asked=%d closed=%d refused=%d errors=%d",
        len(result.asked), len(result.closed), len(result.refused), len(result.errors),
    )
    return result


def preview(api=None, own_pid: int | None = None) -> list[dict]:
    """List the windows close-all would target, without closing anything."""
    return [window.to_dict() for window in enumerate_windows(api=api, own_pid=own_pid)]


@dataclass
class ForceResult:
    """Outcome of an explicitly confirmed force termination."""

    terminated: list[int] = field(default_factory=list)
    refused: list[int] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return bool(self.terminated) and not self.errors


def force_terminate(pids, api=None, own_pid: int | None = None,
                    skip=(), wait: float = 0.8, sleep=time.sleep,
                    now=time.monotonic) -> ForceResult:
    """Terminate only the supplied, already-confirmed process ids.

    This is never called by ordinary close or close-all.  System, shell,
    security and TALON's own process are refused here as a final safety net.
    """
    api = api or WindowApi()
    own_pid = os.getpid() if own_pid is None else int(own_pid)
    blocked = {0, 4, own_pid, *(int(pid) for pid in (skip or ()))}
    result = ForceResult()
    targets: list[int] = []
    for pid in dict.fromkeys(int(pid) for pid in (pids or ())):
        if pid in blocked:
            result.refused.append(pid)
            continue
        try:
            if api.terminate_process(pid):
                result.terminated.append(pid)
                targets.append(pid)
            else:
                result.refused.append(pid)
        except Exception as exc:
            log.exception("Could not terminate process %s", pid)
            result.errors.append(f"PID {pid}: {exc}")
    if targets:
        deadline = now() + max(0.0, float(wait))
        while True:
            alive = [pid for pid in targets if api.is_process_alive(pid)]
            if not alive or now() >= deadline:
                break
            sleep(0.05)
        result.terminated = [
            pid for pid in result.terminated if not api.is_process_alive(pid)
        ]
        for pid in targets:
            if pid not in result.terminated:
                result.refused.append(pid)
    log.warning("Force terminate: terminated=%s refused=%s errors=%s",
                result.terminated, result.refused, result.errors)
    return result
