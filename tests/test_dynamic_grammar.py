"""Dynamic Vosk grammar tests: built-ins, custom triggers and safe fallback."""
from __future__ import annotations

import json
from types import SimpleNamespace

from nova.commands import (
    build_builtin_registry, build_recognition_grammar, spoken_form,
)
from nova.custom import CustomStore
from nova.runtime import NovaRuntime
from nova.voice import VoiceController


class FakeSpeech:
    enabled = True
    is_speaking = False

    def say(self, text):
        pass

    def wait_until_idle(self, timeout=None):
        return True


class FakeRouter:
    def __init__(self):
        self.speech = FakeSpeech()
        self.registry = build_builtin_registry()

    def handle(self, phrase, announce_unknown=True):
        command = self.registry.match(phrase)
        return command.action if command is not None else "unknown"


def runtime_with(store, builtin=("study mode",)):
    runtime = NovaRuntime.__new__(NovaRuntime)
    runtime.commands = SimpleNamespace(recognition_phrases=lambda: set(builtin))
    runtime.custom = store
    return runtime


def command(trigger="open notes"):
    return {
        "name": "Open notes",
        "triggers": [trigger],
        "action": "open_app",
        "params": {"path": r"C:\Tools\notes.exe"},
    }


# -- grammar content -------------------------------------------------------
def test_all_enabled_builtin_phrases_are_in_the_grammar():
    registry = build_builtin_registry()
    grammar = json.loads(build_recognition_grammar(registry.phrases()))
    assert "[unk]" in grammar
    for phrase in registry.phrases():
        assert spoken_form(phrase) in grammar


def test_grammar_normalises_phrases_consistently():
    grammar = json.loads(build_recognition_grammar({"Study Mod", "open settings"}))
    assert "study mode" in grammar
    assert "open settings" in grammar


def test_grammar_uses_spoken_forms_for_words_missing_from_the_model():
    grammar = json.loads(build_recognition_grammar({"unmute", "unmute voice"}))
    # The small model has no "unmute" token, so the grammar must use the
    # spoken form that the registry also registers as an alias.
    assert "unmute" not in grammar
    assert "un mute" in grammar
    assert "un mute voice" in grammar


def test_every_builtin_phrase_has_a_grammar_form():
    registry = build_builtin_registry()
    grammar = set(json.loads(build_recognition_grammar(registry.phrases())))
    for phrase in registry.phrases():
        assert spoken_form(phrase) in grammar, phrase


def test_custom_triggers_are_included_only_while_enabled(tmp_path):
    store = CustomStore(tmp_path / "custom_commands.json", reserved_phrases={"study mode"})
    runtime = runtime_with(store)
    saved = store.save_command(command("open notes"))
    assert "open notes" in runtime.recognition_phrases()

    store.set_enabled("commands", saved["id"], False)
    assert "open notes" not in runtime.recognition_phrases()

    store.set_enabled("commands", saved["id"], True)
    assert "open notes" in runtime.recognition_phrases()


def test_automation_triggers_are_included(tmp_path):
    store = CustomStore(tmp_path / "custom_commands.json", reserved_phrases={"study mode"})
    runtime = runtime_with(store)
    store.save_automation({
        "name": "Study setup",
        "triggers": ["prepare my session"],
        "steps": [{"action": "activate_mode", "params": {"mode": "study"}}],
    })
    assert "prepare my session" in runtime.recognition_phrases()


def test_deleted_custom_trigger_is_removed(tmp_path):
    store = CustomStore(tmp_path / "custom_commands.json", reserved_phrases={"study mode"})
    runtime = runtime_with(store)
    saved = store.save_command(command("open notes"))
    assert "open notes" in runtime.recognition_phrases()
    store.delete("commands", saved["id"])
    assert "open notes" not in runtime.recognition_phrases()


# -- live refresh ----------------------------------------------------------
def test_refresh_updates_the_active_grammar_without_reloading_the_model():
    phrases = {"study mode"}
    voice = VoiceController(
        {"always_listen": True}, FakeRouter(), beep=lambda: None,
        grammar_provider=lambda: set(phrases),
    )
    assert "study mode" in voice._current_grammar()

    phrases.add("open notes")
    voice.refresh_grammar()
    assert "open notes" in voice._current_grammar()
    assert "open notes" in voice._grammar_phrases


def test_removing_a_phrase_updates_the_active_grammar():
    phrases = {"study mode", "open notes"}
    voice = VoiceController(
        {"always_listen": True}, FakeRouter(), beep=lambda: None,
        grammar_provider=lambda: set(phrases),
    )
    phrases.discard("open notes")
    voice.refresh_grammar()
    assert "open notes" not in voice._current_grammar()


def test_listener_swaps_the_recogniser_instead_of_restarting_the_microphone():
    voice = VoiceController(
        {"always_listen": True}, FakeRouter(), beep=lambda: None,
        grammar_provider=lambda: {"study mode"},
    )
    voice._model = object()
    replacement = object()
    voice._make_recognizer = lambda: replacement
    voice._grammar_dirty.set()
    original_thread = voice._thread
    assert voice._apply_grammar(object()) is replacement
    assert voice._thread is original_thread, "the microphone must not be restarted"


def test_grammar_failure_falls_back_to_the_previous_recogniser():
    voice = VoiceController(
        {"always_listen": True}, FakeRouter(), beep=lambda: None,
        grammar_provider=lambda: {"study mode"},
    )
    voice._model = object()

    def boom():
        raise RuntimeError("grammar unavailable")

    voice._make_recognizer = boom
    voice._grammar_dirty.set()
    original = object()
    assert voice._apply_grammar(original) is original
