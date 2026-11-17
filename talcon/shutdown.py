"""Real Windows shutdown through InitiateSystemShutdownExW.

Applications are never force-closed: bForceAppsClosed is always FALSE, so
Windows shows its normal save prompts and can still cancel the shutdown.
The shutdown privilege is enabled for the current process only.
"""
from __future__ import annotations

import ctypes
import logging

log = logging.getLogger("talcon.shutdown")

SHUTDOWN_TIMEOUT = 0
SHTDN_REASON_MAJOR_APPLICATION = 0x00040000
SHTDN_REASON_FLAG_PLANNED = 0x80000000
SHUTDOWN_REASON = SHTDN_REASON_MAJOR_APPLICATION | SHTDN_REASON_FLAG_PLANNED
WARNING_MESSAGE = "TALCON is shutting down this computer now."

TOKEN_ADJUST_PRIVILEGES = 0x0020
TOKEN_QUERY = 0x0008
SE_PRIVILEGE_ENABLED = 0x00000002


class ShutdownApiError(RuntimeError):
    pass


class WindowsShutdownApi:
    """Small ctypes wrapper, replaceable in tests."""

    def __init__(self):
        from ctypes import wintypes

        self._advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)
        self._kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        self._user32 = ctypes.WinDLL("user32", use_last_error=True)
        self._wintypes = wintypes

        # 64-bit safety: without these signatures ctypes truncates the
        # pseudo-handle returned by GetCurrentProcess() to 32 bits, and
        # OpenProcessToken fails with "The handle is invalid".
        self._kernel32.GetCurrentProcess.argtypes = []
        self._kernel32.GetCurrentProcess.restype = wintypes.HANDLE
        self._advapi32.OpenProcessToken.argtypes = [
            wintypes.HANDLE, wintypes.DWORD, ctypes.POINTER(wintypes.HANDLE),
        ]
        self._advapi32.OpenProcessToken.restype = wintypes.BOOL
        self._advapi32.LookupPrivilegeValueW.argtypes = [
            wintypes.LPCWSTR, wintypes.LPCWSTR, ctypes.c_void_p,
        ]
        self._advapi32.LookupPrivilegeValueW.restype = wintypes.BOOL
        self._advapi32.AdjustTokenPrivileges.argtypes = [
            wintypes.HANDLE, wintypes.BOOL, ctypes.c_void_p, wintypes.DWORD,
            ctypes.c_void_p, ctypes.c_void_p,
        ]
        self._advapi32.AdjustTokenPrivileges.restype = wintypes.BOOL
        self._advapi32.InitiateSystemShutdownExW.argtypes = [
            wintypes.LPWSTR, wintypes.LPWSTR, wintypes.DWORD, wintypes.BOOL,
            wintypes.BOOL, wintypes.DWORD,
        ]
        self._advapi32.InitiateSystemShutdownExW.restype = wintypes.BOOL
        self._advapi32.AbortSystemShutdownW.argtypes = [wintypes.LPWSTR]
        self._advapi32.AbortSystemShutdownW.restype = wintypes.BOOL
        self._kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
        self._kernel32.CloseHandle.restype = wintypes.BOOL

    # -- privilege ---------------------------------------------------------
    def enable_privilege(self) -> tuple[bool, str]:
        wintypes = self._wintypes

        class LUID(ctypes.Structure):
            _fields_ = [("LowPart", wintypes.DWORD), ("HighPart", wintypes.LONG)]

        class LUID_AND_ATTRIBUTES(ctypes.Structure):
            _fields_ = [("Luid", LUID), ("Attributes", wintypes.DWORD)]

        class TOKEN_PRIVILEGES(ctypes.Structure):
            _fields_ = [("PrivilegeCount", wintypes.DWORD),
                        ("Privileges", LUID_AND_ATTRIBUTES * 1)]

        token = wintypes.HANDLE()
        if not self._advapi32.OpenProcessToken(
            self._kernel32.GetCurrentProcess(),
            TOKEN_ADJUST_PRIVILEGES | TOKEN_QUERY,
            ctypes.byref(token),
        ):
            return False, _win_error("OpenProcessToken")
        try:
            luid = LUID()
            if not self._advapi32.LookupPrivilegeValueW(
                None, "SeShutdownPrivilege", ctypes.byref(luid)
            ):
                return False, _win_error("LookupPrivilegeValue")
            privileges = TOKEN_PRIVILEGES()
            privileges.PrivilegeCount = 1
            privileges.Privileges[0].Luid = luid
            privileges.Privileges[0].Attributes = SE_PRIVILEGE_ENABLED
            if not self._advapi32.AdjustTokenPrivileges(
                token, False, ctypes.byref(privileges), 0, None, None
            ):
                return False, _win_error("AdjustTokenPrivileges")
            return True, "Shutdown privilege enabled."
        finally:
            self._kernel32.CloseHandle(token)

    # -- operations --------------------------------------------------------
    def initiate(self, machine, message: str, timeout: int,
                 force: bool, reboot: bool, reason: int) -> tuple[bool, str]:
        result = self._advapi32.InitiateSystemShutdownExW(
            machine, message, int(timeout), bool(force), bool(reboot), int(reason)
        )
        if not result:
            return False, _win_error("InitiateSystemShutdownEx")
        return True, "Windows shutdown scheduled."

    def abort(self, machine) -> tuple[bool, str]:
        result = self._advapi32.AbortSystemShutdownW(machine)
        if not result:
            return False, _win_error("AbortSystemShutdown")
        return True, "Windows shutdown cancelled."


def _win_error(operation: str) -> str:
    try:
        code = ctypes.get_last_error()
    except Exception:
        code = 0
    try:
        text = ctypes.FormatError(code).strip()
    except Exception:
        text = ""
    return f"{operation} failed (Windows error {code}{': ' + text if text else ''})"


def shutdown_computer(message: str = WARNING_MESSAGE, timeout: int = SHUTDOWN_TIMEOUT,
                      api=None) -> tuple[bool, str]:
    """Start a real Windows shutdown immediately, without a countdown."""
    api = api or WindowsShutdownApi()
    try:
        ok, detail = api.enable_privilege()
    except Exception as exc:
        log.exception("Could not enable the shutdown privilege")
        return False, f"Could not enable the shutdown privilege: {exc}"
    if not ok:
        log.error("Shutdown privilege refused: %s", detail)
        return False, detail
    try:
        ok, detail = api.initiate(None, message, int(timeout), False, False, SHUTDOWN_REASON)
    except Exception as exc:
        log.exception("InitiateSystemShutdownEx failed")
        return False, f"Windows shutdown failed: {exc}"
    if ok:
        if int(timeout) > 0:
            log.info("Windows shutdown scheduled in %s seconds", timeout)
        else:
            log.info("Windows shutdown started immediately")
    else:
        log.error("Windows shutdown refused: %s", detail)
    return ok, detail


def abort_shutdown(api=None) -> tuple[bool, str]:
    """Cancel a shutdown that is still counting down."""
    api = api or WindowsShutdownApi()
    try:
        ok, detail = api.abort(None)
    except Exception as exc:
        log.exception("AbortSystemShutdown failed")
        return False, f"Could not cancel the shutdown: {exc}"
    if ok:
        log.info("Windows shutdown cancelled")
    else:
        log.error("Windows shutdown cancellation refused: %s", detail)
    return ok, detail
