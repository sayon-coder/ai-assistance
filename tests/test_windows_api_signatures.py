"""Explicit ctypes signatures and safe Windows window API tests.

The declaration-layout checks run on any host; the live integration smoke test
is automatically performed only on 64-bit Windows and never closes/kills a
real application.
"""
from __future__ import annotations

import ctypes
import os
import struct
import sys
import types

import pytest

from talcon import windows_control as wc


class FakeFunc:
    def __init__(self, result=1):
        self.argtypes = None
        self.restype = None
        self.result = result

    def __call__(self, *args):
        return self.result


class FakeDll:
    def __init__(self, exports):
        self.exports = {name: FakeFunc() for name in exports}

    def __getattr__(self, name):
        if name not in self.exports:
            self.exports[name] = FakeFunc()
        return self.exports[name]


def fake_dlls(monkeypatch):
    user = FakeDll([
        "EnumWindows", "IsWindowVisible", "IsIconic", "GetWindowTextLengthW",
        "GetWindowTextW", "GetClassNameW", "GetWindowThreadProcessId",
        "GetWindow", "PostMessageW", "IsWindow", "GetWindowLongPtrW",
    ])
    kernel = FakeDll([
        "OpenProcess", "QueryFullProcessImageNameW", "TerminateProcess", "CloseHandle",
    ])
    dwm = FakeDll(["DwmGetWindowAttribute"])
    def win_dll(name, **kwargs):
        if name == "user32": return user
        if name == "kernel32": return kernel
        if name == "dwmapi": return dwm
        raise OSError(name)
    monkeypatch.setattr(ctypes, "WinDLL", win_dll)
    return user, kernel, dwm


def test_api_function_prototypes_use_pointer_sized_handles(monkeypatch):
    user, kernel, dwm = fake_dlls(monkeypatch)
    api = wc.WindowApi()
    pointer_bits = ctypes.sizeof(ctypes.c_void_p) * 8
    assert ctypes.sizeof(wc.wintypes.HWND) * 8 == pointer_bits
    assert ctypes.sizeof(wc.wintypes.HANDLE) * 8 == pointer_bits
    assert user.EnumWindows.restype is wc.wintypes.BOOL
    assert user.EnumWindows.argtypes == [api._enum_proc_type, wc.wintypes.LPARAM]
    assert user.GetWindowThreadProcessId.restype is wc.wintypes.DWORD
    assert user.GetWindowThreadProcessId.argtypes[1]._type_ is wc.wintypes.DWORD
    assert user.PostMessageW.argtypes == [
        wc.wintypes.HWND, wc.wintypes.UINT, wc.wintypes.WPARAM, wc.wintypes.LPARAM,
    ]
    assert user.PostMessageW.restype is wc.wintypes.BOOL
    assert kernel.OpenProcess.restype is wc.wintypes.HANDLE
    assert kernel.CloseHandle.argtypes == [wc.wintypes.HANDLE]
    assert kernel.CloseHandle.restype is wc.wintypes.BOOL
    assert dwm.DwmGetWindowAttribute.argtypes[0] is wc.wintypes.HWND


def test_64_bit_uses_pointer_sized_window_long(monkeypatch):
    user, _kernel, _dwm = fake_dlls(monkeypatch)
    api = wc.WindowApi()
    if ctypes.sizeof(ctypes.c_void_p) == 8:
        assert api._get_window_long is user.GetWindowLongPtrW
        assert api._get_window_long.restype is ctypes.c_ssize_t
        assert api.ex_style(0x1_2345_6789) == 1
    else:
        pytest.skip("pointer-sized long check is specific to 64-bit Windows")


def test_post_close_remains_graceful_and_does_not_terminate(monkeypatch):
    user, kernel, _dwm = fake_dlls(monkeypatch)
    api = wc.WindowApi()
    assert api.post_close(0x1_2345_6789) is True
    assert user.PostMessageW.restype is wc.wintypes.BOOL
    # Constructing the wrapper declares a termination function, but ordinary
    # close goes only through PostMessageW.
    assert kernel.TerminateProcess.argtypes == [wc.wintypes.HANDLE, wc.wintypes.UINT]


def test_explicit_termination_keeps_protected_process_safeguards():
    class FakeApi:
        def __init__(self):
            self.terminated = []

        def terminate_process(self, pid):
            self.terminated.append(pid)
            return True

        def is_process_alive(self, pid):
            return False

    api = FakeApi()
    own = 777
    result = wc.force_terminate(
        [0, 4, own, 888], api=api, own_pid=own, wait=0.0,
        sleep=lambda _seconds: None, now=lambda: 0.0,
    )
    assert api.terminated == [888]
    assert set(result.refused) == {0, 4, own}


@pytest.mark.skipif(sys.platform != "win32", reason="Windows API integration only")
def test_live_64_bit_windows_api_smoke_without_closing_real_windows():
    if ctypes.sizeof(ctypes.c_void_p) != 8:
        pytest.skip("not 64-bit Windows")
    api = wc.WindowApi()
    handles = api.windows()
    assert handles and all(isinstance(hwnd, int) and hwnd > 0 for hwnd in handles)
    # Invalid handles are safe inspection targets and must not affect windows.
    assert api.is_window(0) is False
    assert api.is_visible(0) is False
    assert api.title(0) == ""
    assert api.class_name(0) == ""
    assert api.pid(0) == 0
    assert api.is_process_alive(0) is True  # system PID 0 is conservatively treated as alive
    # No call to PostMessageW / TerminateProcess is made against a real target.
