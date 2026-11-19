"""Global hotkey plumbing tests (no real key is registered)."""
from __future__ import annotations

from talcon.hotkey import GlobalHotkey, _HotkeyFilter


def test_filter_ignores_unrelated_native_messages():
    calls: list[int] = []
    event_filter = _HotkeyFilter(lambda: calls.append(1))
    assert event_filter.nativeEventFilter(b"windows_generic_MSG", 0) == (False, 0)
    assert calls == []


def test_unregister_is_safe_before_any_registration(qapp):
    hotkey = GlobalHotkey(qapp, lambda: None)
    hotkey.unregister()          # must not raise or touch user32
    assert hotkey._registered is False
