"""Routing contract: predefined commands are the cache, the AI is last.

Every local command must answer without building a Hermes client (so no HTTP
request is ever made), and only a phrase that no local parser can place may
reach the AI.  These tests fail loudly if the order ever inverts.
"""
from __future__ import annotations

import pytest

from talcon.apps import AppEntry
from talcon.commands import CommandRouter, build_builtin_registry
from talcon.pending_actions import PendingActions
from tests.test_commands import FakeModes, FakeSpeech


class Catalogue:
    def __init__(self, entries):
        self._entries = list(entries)

    def entries(self):
        return list(self._entries)

    def refresh(self):
        return list(self._entries)

    def refresh_async(self):
        pass

    def invalidate(self, entry):
        pass


class RecordingAssistant:
    """Stands in for AssistantController and records every consultation."""

    def __init__(self, settings=None):
        self.settings = dict(settings or {"ai_enabled": True})
        self.consulted = []
        self.busy = False

    def enabled(self):
        return bool(self.settings.get("ai_enabled", True))

    def is_busy(self):
        return self.busy

    def handle_phrase(self, phrase):
        self.consulted.append(phrase)
        self.busy = True
        return True

    def handle_control(self, phrase):
        if self.busy and phrase in {"stop that", "never mind"}:
            self.busy = False
            return True
        return False

    def cancel(self):
        self.busy = False

    def answer_approval(self, approved):
        return False


class LocalApp:
    """Permissive stand-in for TalconRuntime; records every local capability.

    Commands return (ok, message) for their confirm/cancel pair, so the fake
    mirrors that shape while keeping everything offline.
    """

    def __init__(self):
        self.pending = PendingActions()
        self.calls = []
        self.assistant = None
        self.commands_paused = False
        self.muted = False
        self.wake = True

    def __getattr__(self, name):
        if name.startswith("_") or name == "pending":
            raise AttributeError(name)

        def recorder(*args, **kwargs):
            self.calls.append((name, args))
            if name in {"request_close_all", "request_shutdown"}:
                self.pending.request(name.replace("request_", ""))
                return True
            if name in {"cancel_close_all", "confirm_close_all",
                        "cancel_shutdown", "confirm_shutdown"}:
                if "cancel" in name:
                    self.pending.cancel(name.replace("cancel_", ""))
                return True, "ok"
            if name == "media_toggle":
                return True, "ok"
            return True

        return recorder


def entry(name):
    return AppEntry(name=name, kind="executable", path=f"C:/Apps/{name}.exe",
                    source="start_menu")


def build(assistant=None, entries=None):
    speech = FakeSpeech()
    app = LocalApp()
    assistant = assistant or RecordingAssistant()
    app.assistant = assistant
    router = CommandRouter(FakeModes(), speech, app)
    router.set_application_library(
        Catalogue(entries if entries is not None
                  else [entry("DeepSeek"), entry("Brave")])
    )
    launches = []
    router.app_launcher = lambda e: launches.append(e.name)
    router.natural_handler = assistant.handle_phrase
    router.control_handler = assistant.handle_control
    return router, speech, app, assistant, launches


# ------------------------------------------------------- local fast path
LOCAL_PHRASES = [
    "mute",
    "unmute",
    "what mode",
    "open settings",
    "hide talcon",
    "show talcon",
    "introduce yourself",
    "activate my study mode",
    "start study mode",
    "get ready for my entertainment mode",
    "stop listening",
    "listen now",
    "go to sleep",
    "what is running",
    "refresh apps",
    "help",
]


@pytest.mark.parametrize("phrase", LOCAL_PHRASES)
def test_local_commands_never_consult_the_ai(phrase):
    router, _speech, _app, assistant, _launches = build()
    action = router.handle(phrase, announce_unknown=False)
    assert action != "assistant", phrase
    assert assistant.consulted == [], f"{phrase!r} reached the assistant"


def test_every_registered_phrase_resolves_locally():
    """The whole built-in grammar is answered without the AI."""
    registry = build_builtin_registry()
    phrases = sorted(registry.phrases())
    assert len(phrases) > 100
    router, _speech, _app, assistant, _launches = build()
    reached = []
    for phrase in phrases:
        action = router.handle(phrase, announce_unknown=False)
        if action == "assistant":
            reached.append(phrase)
    assert reached == [], f"{len(reached)} built-in phrases went to the AI"


def test_opening_a_known_application_is_local():
    router, speech, _app, assistant, launches = build()
    action = router.handle("open deepseek", announce_unknown=False)
    assert action == "app_opened"
    assert launches == ["DeepSeek"]
    assert assistant.consulted == []
    assert any("opening deepseek" in line.lower() for line in speech.spoken)


def test_closing_a_running_application_is_local():
    router, _speech, _app, assistant, _launches = build()
    router.handle("close deepseek", announce_unknown=False)
    assert assistant.consulted == []


def test_answering_a_pending_question_is_local():
    router, _speech, _app, assistant, launches = build()
    router.handle("open deep speak", announce_unknown=False)
    assert router.handle("yes", announce_unknown=False) == "app_opened"
    assert launches == ["DeepSeek"]
    assert assistant.consulted == []


def test_custom_commands_run_before_the_ai():
    router, speech, app, assistant, _launches = build()

    def custom_handler(text):
        return "custom_ran" if text == "my own phrase" else None

    router.custom_handler = custom_handler
    assert router.handle("my own phrase", announce_unknown=False) == "custom_ran"
    assert assistant.consulted == []


# ------------------------------------------------------- AI fallback only
@pytest.mark.parametrize("phrase", [
    "which is better for writing python deepseek or chatgpt",
    "explain how a heat pump works",
    "why does my laptop fan run loudly after updating windows",
])
def test_unknown_phrases_reach_the_ai_exactly_once(phrase):
    router, _speech, _app, assistant, _launches = build()
    assert router.handle(phrase, announce_unknown=False) == "assistant"
    assert assistant.consulted == [phrase]


def test_ai_consulted_only_after_every_local_parser_failed():
    router, _speech, _app, assistant, launches = build()
    # A local open command with an unknown app is still handled locally: the
    # parser owns it, so the AI is not asked about application names.
    action = router.handle("open zzz unknown app", announce_unknown=False)
    assert action in ("app_not_found", "app_open")
    assert assistant.consulted == []


def test_single_word_and_filler_do_not_reach_the_ai():
    from talcon.assistant import AssistantController

    class GatedAssistant(RecordingAssistant):
        def handle_phrase(self, phrase):
            if not AssistantController.is_eligible(phrase):
                return False
            return super().handle_phrase(phrase)

    router, _speech, _app, assistant, _launches = build(GatedAssistant())
    for phrase in ("banana", "um", "okay so"):
        router.handle(phrase, announce_unknown=False)
    assert assistant.consulted == []


# ------------------------------------------------------- cancellation order
def test_cancel_that_still_cancels_a_pending_dangerous_action():
    router, speech, app, assistant, _launches = build()
    # Arm a close-all confirmation through the router's own command.
    router.handle("close all open windows", announce_unknown=False)
    assert app.pending.current() == "close_all"
    assert router.handle("cancel that", announce_unknown=False) != "assistant"
    assert app.pending.current() is None
    assert assistant.consulted == []


def test_cancel_that_closes_an_open_app_question():
    router, speech, _app, assistant, launches = build()
    router.handle("open deep speak", announce_unknown=False)
    assert router._pending_apps
    assert router.handle("cancel that", announce_unknown=False) == "app_cancelled"
    assert router._pending_apps == ()
    assert launches == []
    assert assistant.consulted == []
    assert any("will not open anything" in line.lower() for line in speech.spoken)


def test_cancel_that_stops_a_running_ai_request():
    router, speech, _app, assistant, _launches = build()
    assistant.busy = True
    assert router.handle("stop that", announce_unknown=False) == "assistant_cancel"
    assert assistant.busy is False
    assert any("stopped that request" in line.lower() for line in speech.spoken)


def test_cancel_that_with_nothing_pending_is_silent():
    router, speech, _app, _assistant, _launches = build()
    assert router.handle("cancel that", announce_unknown=False) == "none"
    assert speech.spoken == []
