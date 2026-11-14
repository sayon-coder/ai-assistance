"""Deterministic command-registry tests."""
from __future__ import annotations

import pytest

from nova.commands import (
    CommandConflictError, CommandRegistry, CommandRouter, build_builtin_registry,
    normalize_phrase,
)


class FakeSpeech:
    def __init__(self):
        self.spoken = []
        self.enabled = True

    def say(self, text):
        self.spoken.append(text)


class FakeModes:
    current_mode = "normal"

    def __init__(self):
        self.activated = []

    def activate(self, name):
        self.activated.append(name)
        return True


class FakeApp:
    def __init__(self):
        self.events = []
        self.muted = False
        self.wake = True

    def activate_mode(self, name):
        self.events.append(("mode", name))

    def set_muted(self, value):
        self.muted = value
        self.events.append(("mute", value))

    def set_wake_enabled(self, value):
        self.wake = value
        self.events.append(("wake", value))

    def show_settings(self):
        self.events.append(("settings",))

    def hide_to_tray(self):
        self.events.append(("hide",))

    def set_face_visible(self, value):
        self.events.append(("face", value))

    def listen_now(self):
        self.events.append(("listen",))

    def exit_completely(self):
        self.events.append(("exit",))


def build():
    speech = FakeSpeech()
    modes = FakeModes()
    app = FakeApp()
    return CommandRouter(modes, speech, app), speech, modes, app


def test_normalisation_handles_punctuation_and_wake_prefix():
    assert normalize_phrase("NOVA, activate my study mode.") == "activate my study mode"
    assert normalize_phrase("Hey Nova — mute!") == "mute"
    assert normalize_phrase("  NOVA...  what time is it?  ") == "what time is it"
    assert normalize_phrase("") == ""


def test_study_variations_are_all_exact_aliases():
    router, _speech, _modes, app = build()
    for phrase in (
        "NOVA, study mode.",
        "activate study mode",
        "start studying",
        "prepare my study environment",
        "get me ready for study",
    ):
        assert router.handle(phrase) == "study"
    assert app.events == [("mode", "study")] * 5


def test_entertainment_variations_are_all_exact_aliases():
    router, _speech, _modes, app = build()
    for phrase in (
        "entertainment mode",
        "activate entertainment mode",
        "start entertainment",
        "prepare my entertainment environment",
    ):
        assert router.handle(phrase) == "entertainment"
    assert app.events == [("mode", "entertainment")] * 4


def test_token_boundaries_prevent_substring_matches():
    router, _speech, _modes, app = build()
    assert router.handle("commute") == "unknown"
    assert router.handle("mute the television") == "unknown"
    assert app.events == []


def test_polite_lead_ins_are_removed_deterministically():
    router, _speech, _modes, app = build()
    for phrase in (
        "study mode please",
        "please study mode",
        "can you open settings",
        "could you please mute",
        "kindly hide nova",
    ):
        assert router.handle(phrase) != "unknown", phrase
    assert ("mode", "study") in app.events
    assert ("settings",) in app.events
    assert ("mute", True) in app.events


def test_information_and_interface_commands():
    router, speech, _modes, app = build()
    assert router.handle("what time is it") == "time"
    assert router.handle("what mode is active") == "status"
    assert router.handle("help") == "help"
    assert router.handle("open settings") == "settings"
    assert router.handle("hide nova") == "hide"
    assert router.handle("show nova") == "show"
    assert router.handle("listen now") == "listen_now"
    assert router.handle("exit nova") == "exit"
    assert ("settings",) in app.events
    assert ("hide",) in app.events
    assert ("face", True) in app.events
    assert ("listen",) in app.events
    assert ("exit",) in app.events
    assert speech.spoken


def test_unknown_command_does_not_execute_anything():
    router, speech, _modes, app = build()
    assert router.handle("make me a sandwich") == "unknown"
    assert app.events == []
    assert "didn't catch" in speech.spoken[-1]


def test_unknown_can_be_silent_for_the_voice_layer():
    router, speech, _modes, app = build()
    assert router.handle("make me a sandwich", announce_unknown=False) == "unknown"
    assert app.events == []
    assert speech.spoken == []


def test_custom_handler_runs_only_after_builtins():
    router, _speech, _modes, app = build()
    calls = []

    def custom(text):
        calls.append(text)
        return "custom:1"

    router.custom_handler = custom
    assert router.handle("study mode") == "study"
    assert calls == []
    assert router.handle("open my notes") == "custom:1"
    assert calls == ["open my notes"]


def test_unmute_spoken_forms_are_exact_aliases():
    router, _speech, _modes, app = build()
    assert router.handle("unmute") == "unmute"
    assert router.handle("un mute") == "unmute"
    assert router.handle("un mute voice") == "unmute"
    assert app.events == [("mute", False)] * 3


def test_registry_rejects_duplicate_phrases():
    registry = CommandRegistry()
    registry.register("one", ("do the thing",))
    with pytest.raises(CommandConflictError):
        registry.register("two", ("do the thing",))


def test_registry_rejects_wake_word_as_a_command():
    registry = CommandRegistry()
    with pytest.raises(ValueError):
        registry.register("bad", ("nova",))


def test_reserved_phrases_include_builtins_and_wake_words():
    router, _speech, _modes, _app = build()
    reserved = router.reserved_phrases()
    assert "study mode" in reserved
    assert "open settings" in reserved
    assert "nova" in reserved
    assert "hey nova" in reserved


def test_builtin_registry_has_no_duplicate_phrases():
    registry = build_builtin_registry()
    assert len(registry.phrases()) == sum(len(command.phrases) for command in registry.commands.values())
