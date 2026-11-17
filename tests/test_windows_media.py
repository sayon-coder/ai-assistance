"""Global media-key dispatch tests.  No real key is sent."""
from __future__ import annotations

from talcon import windows_media


def test_media_key_is_the_play_pause_virtual_key():
    assert windows_media.VK_MEDIA_PLAY_PAUSE == 0xB3
    assert windows_media.VK_MEDIA_PLAY_PAUSE != 0x20, "the Space key must never be used"


def test_media_event_is_dispatched_exactly_once():
    sent = []
    ok, message = windows_media.send_play_pause(sender=lambda vk: sent.append(vk))
    assert ok is True
    assert sent == [windows_media.VK_MEDIA_PLAY_PAUSE]
    assert "toggle" not in message.lower()  # the caller adds the limitation note


def test_platform_errors_are_reported():
    def broken(_vk):
        raise OSError("SendInput failed")

    ok, message = windows_media.send_play_pause(sender=broken)
    assert ok is False
    assert "failed" in message.lower()


def test_media_control_is_only_claimed_on_windows(monkeypatch):
    monkeypatch.setattr(windows_media.os, "name", "posix")
    assert windows_media.available() is False
    monkeypatch.setattr(windows_media.os, "name", "nt")
    assert windows_media.available() is True


def test_sender_only_receives_a_virtual_key_not_text():
    calls = []

    def sender(value):
        calls.append(value)

    windows_media.send_play_pause(sender=sender)
    assert calls == [0xB3]
    assert all(isinstance(value, int) for value in calls)
