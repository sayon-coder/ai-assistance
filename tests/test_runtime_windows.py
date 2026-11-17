"""Runtime integration for confirmations and dangerous-action wiring."""
from __future__ import annotations

from types import SimpleNamespace

import pytest

import talcon.runtime as runtime_module
from talcon.pending_actions import PendingActions
from talcon.runtime import TalconRuntime


class Clock:
    def __init__(self, now=100.0):
        self.now = now

    def __call__(self):
        return self.now


class FakeSpeech:
    def __init__(self):
        self.spoken = []
        self.enabled = True

    def say(self, text):
        self.spoken.append(text)


class FakeSignal:
    def __init__(self):
        self.events = []

    def emit(self, *args):
        self.events.append(args)


def make_runtime(clock=None):
    runtime = TalconRuntime.__new__(TalconRuntime)
    runtime.pending = PendingActions(clock=clock or Clock())
    runtime.speech = FakeSpeech()
    runtime.muted = False
    runtime.status = {}
    runtime.bridge = SimpleNamespace(
        status_changed=FakeSignal(), notification=FakeSignal()
    )
    runtime._last_close_result = "No close-all run yet."
    runtime._last_closed = []
    runtime._last_refused = []
    runtime._shutdown_scheduled = False
    runtime._shutdown_deadline = 0.0
    runtime._close_preview = []
    return runtime


def test_close_all_is_not_executed_without_confirmation():
    runtime = make_runtime()
    calls = []
    runtime.close_all_now = lambda: calls.append(True) or (True, "closed")

    assert runtime.confirm_close_all()[0] is False
    assert calls == []
    assert "no close-all request" in runtime.speech.spoken[-1].lower()


def test_close_all_runs_once_after_confirmation():
    runtime = make_runtime()
    calls = []
    runtime.close_all_now = lambda: calls.append(True) or (True, "closed")

    runtime.request_close_all()
    assert calls == []
    assert runtime.confirm_close_all()[0] is True
    assert calls == [True]
    assert runtime.confirm_close_all()[0] is False
    assert calls == [True]


def test_expired_close_all_confirmation_does_nothing():
    clock = Clock()
    runtime = make_runtime(clock)
    calls = []
    runtime.close_all_now = lambda: calls.append(True) or (True, "closed")

    runtime.request_close_all()
    clock.now += 16.0
    assert runtime.confirm_close_all()[0] is False
    assert calls == []


def test_cancelled_close_all_does_nothing():
    runtime = make_runtime()
    calls = []
    runtime.close_all_now = lambda: calls.append(True) or (True, "closed")

    runtime.request_close_all()
    assert runtime.cancel_close_all()[0] is True
    assert runtime.confirm_close_all()[0] is False
    assert calls == []


def test_shutdown_requires_confirmation(monkeypatch):
    runtime = make_runtime()
    calls = []

    def fake_shutdown():
        calls.append(True)
        return True, "scheduled"

    monkeypatch.setattr(runtime_module.windows_shutdown, "shutdown_computer", fake_shutdown)
    assert runtime.confirm_shutdown()[0] is False
    assert calls == []

    runtime.request_shutdown()
    assert calls == []
    ok, message = runtime.confirm_shutdown()
    assert ok is True
    assert "now" in message.lower(), "the reply must not promise a 30-second wait"
    assert calls == [True]
    assert runtime._shutdown_scheduled is True


def test_expired_shutdown_confirmation_does_nothing(monkeypatch):
    clock = Clock()
    runtime = make_runtime(clock)
    calls = []
    monkeypatch.setattr(
        runtime_module.windows_shutdown, "shutdown_computer",
        lambda: calls.append(True) or (True, "scheduled"),
    )
    runtime.request_shutdown()
    clock.now += 16.0
    assert runtime.confirm_shutdown()[0] is False
    assert calls == []


def test_cancel_shutdown_aborts_a_counting_down_shutdown(monkeypatch):
    runtime = make_runtime()
    aborted = []
    monkeypatch.setattr(
        runtime_module.windows_shutdown, "abort_shutdown",
        lambda: aborted.append(True) or (True, "aborted"),
    )
    runtime._shutdown_scheduled = True
    assert runtime.cancel_shutdown()[0] is True
    assert aborted == [True]
    assert runtime._shutdown_scheduled is False


def test_media_toggle_is_silent(monkeypatch):
    runtime = make_runtime()
    monkeypatch.setattr(runtime_module.windows_media, "available", lambda: True)
    monkeypatch.setattr(
        runtime_module.windows_media, "send_play_pause",
        lambda: (True, "Sent the Windows play/pause media key."),
    )
    ok, message = runtime.media_toggle()
    assert ok is True
    assert "toggle" in message.lower()
    assert runtime.speech.spoken == []


def test_media_failure_is_reported(monkeypatch):
    runtime = make_runtime()
    monkeypatch.setattr(runtime_module.windows_media, "available", lambda: True)
    monkeypatch.setattr(
        runtime_module.windows_media, "send_play_pause",
        lambda: (False, "Windows media key failed: SendInput"),
    )
    ok, message = runtime.media_toggle()
    assert ok is False
    assert "failed" in message.lower()
    assert runtime.speech.spoken == []
    assert not runtime.bridge.notification.events


def test_shutdown_request_speaks_the_confirmation():
    runtime = make_runtime()
    runtime.request_shutdown()
    assert runtime.speech.spoken
    assert "confirm" in runtime.speech.spoken[-1].lower()


def test_close_all_request_speaks_the_confirmation():
    runtime = make_runtime()
    runtime.request_close_all()
    assert runtime.speech.spoken
    assert "confirm" in runtime.speech.spoken[-1].lower()


def test_set_commands_paused_persists_and_updates_status(monkeypatch):
    runtime = make_runtime()
    runtime.settings = {"commands_paused": False}
    runtime.bridge.settings_changed = FakeSignal()
    monkeypatch.setattr(runtime_module, "persist_settings", lambda s: dict(s))

    runtime.set_commands_paused(True)
    assert runtime.settings["commands_paused"] is True
    assert runtime.status["commands_state"] == "Commands paused"
    assert runtime.bridge.settings_changed.events

    runtime.set_commands_paused(False)
    assert runtime.settings["commands_paused"] is False
    assert runtime.status["commands_state"] == "Listening"
