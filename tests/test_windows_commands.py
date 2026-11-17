"""Exact command-registry wiring for media, close-all and shutdown."""
from __future__ import annotations

from talcon.commands import CommandRouter, build_builtin_registry


class FakeSpeech:
    def __init__(self):
        self.spoken = []
        self.enabled = True

    def say(self, text):
        self.spoken.append(text)


class FakeModes:
    current_mode = "normal"

    def activate(self, name):
        return True


class FakeApp:
    def __init__(self):
        self.calls = []
        self.pending = None

    def media_toggle(self):
        self.calls.append("media")
        return True, "ok"

    def request_close_all(self):
        self.calls.append("request_close_all")
        return True, ""

    def confirm_close_all(self):
        self.calls.append("confirm_close_all")
        return True, ""

    def cancel_close_all(self):
        self.calls.append("cancel_close_all")
        return True, ""

    def request_shutdown(self):
        self.calls.append("request_shutdown")
        return True, ""

    def confirm_shutdown(self):
        self.calls.append("confirm_shutdown")
        return True, ""

    def cancel_shutdown(self):
        self.calls.append("cancel_shutdown")
        return True, ""

    def exit_completely(self):
        self.calls.append("exit")


def build():
    speech = FakeSpeech()
    app = FakeApp()
    return CommandRouter(FakeModes(), speech, app), speech, app


def test_media_aliases_are_exact_and_send_one_key():
    router, _speech, app = build()
    for phrase in (
        "pause video", "pause YouTube", "pause playback", "resume video",
        "play video", "resume playback", "stop video",
    ):
        assert router.handle(phrase) == "media_toggle", phrase
    assert app.calls == ["media"] * 7


def test_close_all_requires_a_separate_confirmation_phrase():
    router, _speech, app = build()
    for phrase in (
        "close everything", "close everything on my desktop",
        "close all applications", "close all apps", "close all open windows",
    ):
        assert router.handle(phrase) == "close_all_pending"
    assert "request_close_all" in app.calls
    assert "confirm_close_all" not in app.calls

    assert router.handle("confirm close everything") == "close_all"
    assert app.calls[-1] == "confirm_close_all"

    assert router.handle("cancel close everything") == "close_all_cancelled"
    assert app.calls[-1] == "cancel_close_all"


def test_shutdown_requires_a_separate_confirmation_phrase():
    router, _speech, app = build()
    for phrase in (
        "shutdown", "shut down computer", "shutdown my PC",
        "turn off my computer",
    ):
        assert router.handle(phrase) == "shutdown_pending"
    assert "request_shutdown" in app.calls
    assert "confirm_shutdown" not in app.calls

    assert router.handle("confirm shutdown") == "shutdown"
    assert app.calls[-1] == "confirm_shutdown"

    assert router.handle("cancel shutdown") == "shutdown_cancelled"
    assert app.calls[-1] == "cancel_shutdown"


class _FakePending:
    def __init__(self, kind):
        self.kind = kind

    def current(self):
        return self.kind


def test_spoken_confirmation_variants_are_recognised():
    registry = build_builtin_registry()
    for phrase in ("confirm shut down", "yes shutdown", "yes shut down"):
        assert registry.match(phrase).action == "confirm_shutdown", phrase
    for phrase in ("cancel shut down", "no shut down", "do not shut down"):
        assert registry.match(phrase).action == "cancel_shutdown", phrase


def test_short_yes_confirms_a_pending_shutdown():
    router, _speech, app = build()
    app.pending = _FakePending("shutdown")
    assert router.handle("yes") == "shutdown"
    assert app.calls[-1] == "confirm_shutdown"


def test_short_no_cancels_a_pending_shutdown():
    router, _speech, app = build()
    app.pending = _FakePending("shutdown")
    assert router.handle("no") == "shutdown_cancelled"
    assert app.calls[-1] == "cancel_shutdown"


def test_short_yes_does_nothing_without_a_pending_action():
    router, _speech, app = build()
    assert router.handle("yes") == "unknown"
    assert app.calls == []


def test_shut_down_talcon_still_only_exits_talcon():
    registry = build_builtin_registry()
    assert registry.match("shut down talcon").action == "exit"
    assert registry.match("shutdown").action == "shutdown"
    assert registry.match("shut down computer").action == "shutdown"
    router, _speech, app = build()
    assert router.handle("shut down talcon") == "exit"
    assert app.calls == ["exit"]


def test_random_transcripts_do_not_arm_or_confirm():
    router, _speech, app = build()
    for phrase in (
        "close everything now", "shutdown now please", "confirm",
        "confirm everything", "close all my tabs",
    ):
        assert router.handle(phrase) == "unknown", phrase
    assert app.calls == []


def test_new_phrases_are_reserved_for_custom_commands():
    router, _speech, _app = build()
    reserved = router.reserved_phrases()
    for phrase in (
        "close everything", "confirm close everything", "cancel close everything",
        "shutdown", "confirm shutdown", "cancel shutdown", "pause video",
    ):
        assert phrase in reserved, phrase
