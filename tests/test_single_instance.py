"""Single-instance guard: a racing duplicate must never reach settings code."""
from __future__ import annotations

import ctypes
import sys

import pytest

from talcon import app as app_module


@pytest.fixture(autouse=True)
def _release_lock_between_tests():
    # The kernel releases a mutex when its process exits; within the test
    # session we simply hand ownership back between cases.
    saved = app_module._instance_mutex
    app_module._instance_mutex = None
    yield
    app_module._instance_mutex = saved


def test_mutex_is_released_by_the_previous_instance(monkeypatch, qapp):
    """Two launches in the same instant: only the mutex winner starts."""
    if sys.platform != "win32":
        pytest.skip("kernel mutex is Windows-only")
    created = {}
    closed = []

    class FakeKernel32:
        def CreateMutexW(self, _sa, _initial, name):
            if name in created:
                ctypes.set_last_error(183)  # ERROR_ALREADY_EXISTS
                return 999                 # real API still hands back a handle
            handle = object()
            created[name] = handle
            ctypes.set_last_error(0)
            return id(handle)

        def GetLastError(self):
            return ctypes.get_last_error()

        def CloseHandle(self, handle):
            closed.append(handle)
            return True

    monkeypatch.setattr(ctypes.windll, "kernel32", FakeKernel32(), raising=False)
    # First claim wins, second sees ERROR_ALREADY_EXISTS and must refuse.
    assert app_module._claim_instance_mutex() is True
    assert app_module._claim_instance_mutex() is False
    # Only the loser's duplicate handle is closed; the winner keeps the lock.
    assert closed == [999]


def test_duplicate_launch_closes_its_extra_handle(monkeypatch, qapp):
    if sys.platform != "win32":
        pytest.skip("kernel mutex is Windows-only")
    closed = []

    class FakeKernel32:
        def CreateMutexW(self, _sa, _initial, _name):
            ctypes.set_last_error(183)
            return 1234

        def GetLastError(self):
            return ctypes.get_last_error()

        def CloseHandle(self, handle):
            closed.append(handle)
            return True

    monkeypatch.setattr(ctypes.windll, "kernel32", FakeKernel32(), raising=False)
    app_module._instance_mutex = None
    assert app_module._claim_instance_mutex() is False
    assert closed == [1234]           # the loser must not leak its handle
    assert app_module._instance_mutex is None


def test_claim_single_instance_refuses_when_mutex_taken(monkeypatch, qapp):
    monkeypatch.setattr(app_module, "_claim_instance_mutex", lambda: False)
    notified = []
    monkeypatch.setattr(app_module, "_notify_running_instance", lambda: notified.append(True))
    assert app_module._claim_single_instance(qapp) is False
    assert notified == [True]


def test_stale_socket_name_does_not_start_a_competitor(monkeypatch, qapp):
    """A failed first listen is cleared and retried; the winner still starts."""
    monkeypatch.setattr(app_module, "_claim_instance_mutex", lambda: True)
    attempts = []

    class FakeServer:
        def __init__(self, _parent):
            self._listening = False

        def listen(self, _name):
            attempts.append(1)
            if len(attempts) == 1:
                return False           # stale name from a crashed instance
            self._listening = True
            return True

        def errorString(self):
            return "stale"

    removed = []
    monkeypatch.setattr(app_module, "QLocalServer", FakeServer)
    monkeypatch.setattr(
        app_module.QLocalServer, "removeServer",
        staticmethod(lambda _name: removed.append(_name)), raising=False,
    )
    assert app_module._claim_single_instance(qapp) is True
    assert removed == [app_module.SERVER_NAME]
