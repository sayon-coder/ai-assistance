"""Single-use, expiring confirmation state for dangerous actions."""
from __future__ import annotations

from talcon.pending_actions import DEFAULT_TIMEOUT, PendingActions


class Clock:
    def __init__(self, now: float = 100.0):
        self.now = now

    def __call__(self) -> float:
        return self.now


def test_request_and_confirm_once():
    clock = Clock()
    pending = PendingActions(clock=clock)
    assert pending.request("close_all") is True
    assert pending.current() == "close_all"
    assert pending.remaining() == DEFAULT_TIMEOUT
    assert pending.confirm("shutdown") is False
    assert pending.confirm("close_all") is True
    assert pending.confirm("close_all") is False, "a pending action can only run once"
    assert pending.current() is None


def test_confirmation_expires_after_fifteen_seconds():
    clock = Clock()
    pending = PendingActions(clock=clock)
    pending.request("shutdown")
    clock.now += 14.9
    assert pending.current() == "shutdown"
    clock.now += 0.2
    assert pending.current() is None
    assert pending.confirm("shutdown") is False


def test_cancel_discards_the_pending_action():
    pending = PendingActions(clock=Clock())
    pending.request("close_all")
    assert pending.cancel("shutdown") is False
    assert pending.cancel("close_all") is True
    assert pending.current() is None
    assert pending.cancel("close_all") is False


def test_a_new_request_replaces_the_previous_one():
    pending = PendingActions(clock=Clock())
    pending.request("close_all")
    pending.request("shutdown")
    assert pending.current() == "shutdown"
    assert pending.confirm("close_all") is False
    assert pending.confirm("shutdown") is True


def test_random_transcripts_cannot_confirm_without_a_request():
    pending = PendingActions(clock=Clock())
    assert pending.confirm("close_all") is False
    assert pending.confirm("shutdown") is False
