"""Command-first routing, spoken approvals, persistence and voice hooks."""
from __future__ import annotations

import queue
import threading
import time
from types import SimpleNamespace

import pytest

import talcon.voice as voice_module
from talcon.assistant import AssistantController
from talcon.commands import CommandRouter, build_builtin_registry
from talcon.config import DEFAULT_SETTINGS, normalize_settings
from talcon.hermes_client import HermesClient
from talcon.runtime import PENDING_LABELS
from tests.test_assistant import FakeClient

from tests.test_commands import FakeModes


class FakeSpeech:
    def __init__(self):
        self.spoken: list[str] = []
        self.enabled = True
        self._lock = threading.Lock()

    def say(self, text):
        with self._lock:
            self.spoken.append(text)


# ---------------------------------------------------------------- helpers
class FakeApp:
    """The slice of TalconRuntime that CommandRouter touches."""

    def __init__(self, assistant=None):
        self.spoken_approval = []
        self.assistant = assistant
        self.pending = SimpleNamespace(
            current=lambda: self._current(),
            request=lambda kind, timeout=None: self._arm(kind),
            cancel=lambda kind=None: self._disarm(kind),
        )
        self._kind = ""
        self.events = []

    def _current(self):
        return self._kind or None

    def _arm(self, kind):
        self._kind = kind
        return True

    def _disarm(self, kind=None):
        if kind in (None, self._kind):
            self._kind = ""
        return True

    def commands_paused(self):
        return False

    def set_muted(self, value, announce=False):
        self.events.append(("mute", value))

    def set_wake_enabled(self, value):
        self.events.append(("wake", value))

    def show_settings(self):
        self.events.append(("settings",))

    def set_face_visible(self, value):
        self.events.append(("face", value))

    def hide_to_tray(self):
        self.events.append(("hide",))

    def set_commands_paused(self, value):
        self.events.append(("commands_paused", bool(value)))

    def current_mode(self):
        return "normal"

    def activate_mode(self, name):
        self.events.append(("mode", name))
        return True

    def answer_ai_approval(self, approved):
        if self._kind != "ai_approval":
            return False
        self._kind = ""
        self.spoken_approval.append(bool(approved))
        if self.assistant is not None:
            return bool(self.assistant.answer_approval(approved))
        return True


def build_controller(settings=None, client=None):
    # Integration coverage retains the optional agent-run/approval workflow;
    # production voice questions use bounded direct chat unless opted in.
    merged = {
        "ai_enabled": True,
        "ai_use_agent_runs": True,
        **dict(settings or {}),
    }
    speech = FakeSpeech()
    fake = client if client is not None else FakeClient()
    controller = AssistantController(
        settings=lambda: merged, speech=speech,
        client_factory=lambda **kw: fake,
    )
    return controller, merged, speech, fake


def wait_until(predicate, timeout=4.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.02)
    return False


# ------------------------------------------------- router: local-first order
def test_local_commands_win_even_with_the_ai_enabled():
    controller, _m, speech, fake = build_controller()
    router = CommandRouter(FakeModes(), speech, FakeApp(assistant=controller))
    router.natural_handler = controller.handle_phrase
    for phrase in (
        "mute", "unmute", "open settings", "what mode",
        "start study mode", "go to sleep", "stop listening",
    ):
        action = router.handle(phrase, announce_unknown=False)
        assert action not in ("assistant", "assistant_cancel"), phrase
    assert fake.submitted == []


def test_ai_answers_only_what_local_parsers_rejected():
    controller, _m, speech, fake = build_controller()
    router = CommandRouter(FakeModes(), speech, FakeApp(assistant=controller))
    router.natural_handler = controller.handle_phrase
    action = router.handle("what is the capital of japan")
    assert action == "assistant"
    assert wait_until(lambda: not controller.is_busy())
    assert fake.submitted == ["what is the capital of japan"]


def test_disabled_ai_leaves_unknown_handling_unchanged():
    controller, merged, speech, fake = build_controller({"ai_enabled": False})
    router = CommandRouter(FakeModes(), speech, FakeApp(assistant=controller))
    router.natural_handler = controller.handle_phrase
    action = router.handle("what is the capital of japan",
                           announce_unknown=True)
    assert action == "unknown"
    assert fake.submitted == []
    assert any("didn't catch" in text for text in speech.spoken)


def test_ineligible_speech_never_reaches_hermes():
    controller, _m, speech, fake = build_controller()
    router = CommandRouter(FakeModes(), speech, FakeApp(assistant=controller))
    router.natural_handler = controller.handle_phrase
    action = router.handle("banana", announce_unknown=True)
    assert action == "unknown" and fake.submitted == []


def test_cancel_phrase_stops_the_assistant():
    fake = FakeClient(hold=True)
    controller, _m, speech, _f = build_controller(client=fake)
    router = CommandRouter(FakeModes(), speech, FakeApp(assistant=controller))
    router.natural_handler = controller.handle_phrase
    router.control_handler = controller.handle_control
    assert router.handle("explain quantum tunnelling in detail") == "assistant"
    # Wait until the run is genuinely in flight, not merely queued.
    assert wait_until(lambda: bool(fake.submitted))
    assert wait_until(lambda: controller.state()["busy"])
    action = router.handle("stop that")
    assert action == "assistant_cancel"
    assert wait_until(lambda: not controller.is_busy())
    assert fake.stopped and "run-1" in fake.stopped


def test_cancel_phrase_is_silent_when_nothing_runs():
    controller, _m, speech, _f = build_controller()
    app = FakeApp(assistant=controller)
    router = CommandRouter(FakeModes(), speech, app)
    router.natural_handler = controller.handle_phrase
    assert router.handle("stop that") == "none"
    assert speech.spoken == []


def test_cancel_phrases_are_in_the_recognition_grammar():
    phrases = build_builtin_registry().phrases()
    assert "stop that" in phrases and "never mind" in phrases


# ------------------------------------------------- approval over voice
def test_yes_no_words_answer_a_pending_ai_approval():
    app = FakeApp(assistant=None)          # router wiring only, no controller
    speech = FakeSpeech()
    router = CommandRouter(FakeModes(), speech, app)
    # The runtime arms ai_approval through the shared pending store; simulate
    # exactly that state and then speak the confirmation words.
    app.pending.request("ai_approval")
    assert router.handle("yes do it") == "ai_approved"
    # FakeApp routes through answer_ai_approval, which the real router calls:
    assert app.spoken_approval and app.spoken_approval[0] is True
    app.pending.request("ai_approval")
    router.handle("no")
    assert app.spoken_approval[-1] is False


def test_approval_pending_label_exists_for_the_ui():
    assert "ai_approval" in PENDING_LABELS


def test_end_to_end_spoken_approval_via_runtime_bridge():
    controller, _m, speech, fake = build_controller()
    app = FakeApp(assistant=controller)
    router = CommandRouter(FakeModes(), speech, app)
    controller._on_approval_wait = lambda: app.pending.request("ai_approval")
    controller._on_approval_done = lambda: app.pending.cancel("ai_approval")
    controller._on_state = lambda event: None
    fake.events = [
        {"event": "approval.requested", "approval_id": "z9",
         "description": "empty the recycle bin"},
        {"event": "run.completed", "output": "done"},
    ]
    controller.handle_phrase("can you empty the recycle bin please")
    assert wait_until(lambda: bool(app._kind))
    assert any("needs your approval" in text for text in speech.spoken)
    router.handle("yes")
    assert wait_until(lambda: not controller.is_busy())
    assert fake.approvals and fake.approvals[0][0] is True
    assert app._kind == ""  # pending cleared either way


# ------------------------------------------------- voice-layer hook
def make_voice_controller(natural_handler):
    speech = FakeSpeech()
    router = SimpleNamespace(
        speech=speech, natural_handler=natural_handler,
        registry=None, app_aliases={},
    )
    from talcon.voice import VoiceController
    controller = VoiceController({"always_listen": False}, router,
                                 beep=lambda: None,
                                 event_callback=lambda k, t: None)
    return controller, speech


def test_unintelligible_questions_go_to_the_assistant_not_the_apology():
    calls = []

    def accept(phrase):
        calls.append(phrase)
        return True

    controller, speech = make_voice_controller(accept)
    controller._handle_unintelligible(
        "what is the weather like tomorrow", queue.Queue()
    )
    assert calls == ["what is the weather like tomorrow"]
    assert speech.spoken == []  # no "please repeat" apology


def test_rejected_phrases_keep_the_repeat_apology():
    controller, speech = make_voice_controller(lambda phrase: False)
    controller._handle_unintelligible("open ze braave", queue.Queue())
    assert any("repeat" in text.lower() for text in speech.spoken)


def test_chat_like_chatter_still_ignored_without_ai_acceptance():
    controller, speech = make_voice_controller(lambda phrase: False)
    controller._handle_unintelligible("i wonder what mum said", queue.Queue())
    assert speech.spoken == []


# ------------------------------------------------- configuration
def test_ai_defaults_are_on_and_loopback():
    # Enabled out of the box so the assistant answers without a hidden toggle;
    # it can still be switched off in Voice Settings.
    assert DEFAULT_SETTINGS["ai_enabled"] is True
    assert DEFAULT_SETTINGS["hermes_host"] == "127.0.0.1"
    assert DEFAULT_SETTINGS["hermes_port"] == 8642
    s = normalize_settings({})
    assert s["ai_enabled"] is True
    assert s["hermes_model"] == "hermes-agent"
    assert normalize_settings({"ai_enabled": False})["ai_enabled"] is False


def test_ai_setting_survives_normalization_roundtrip():
    s = normalize_settings({"ai_enabled": True, "hermes_port": "1",
                            "hermes_host": "127.0.0.1"})
    assert s["ai_enabled"] is True
    assert s["hermes_port"] == 1
    again = normalize_settings(s)
    assert again["ai_enabled"] is True and again["hermes_port"] == 1
    junk = normalize_settings({"hermes_port": "banana"})
    assert junk["hermes_port"] == 8642


def test_client_accepts_settings_shaped_values_only_over_loopback():
    s = normalize_settings({"hermes_host": "attacker.example",
                            "hermes_port": 9999})
    with pytest.raises(Exception):
        HermesClient(host=s["hermes_host"], port=s["hermes_port"],
                     api_key="x" * 32)


def test_no_new_runtime_import_cycles_and_bridge_signal_exists():
    from talcon.bridge import UiBridge
    assert hasattr(UiBridge, "assistant_state")


# ------------------------------------------------- privacy guardrails
def test_privacy_note_mentions_local_ai():
    from pathlib import Path
    source = (
        Path(__file__).resolve().parent.parent
        / "talcon" / "ui" / "main_window.py"
    ).read_text(encoding="utf-8")
    assert "127.0.0.1" in source
    assert "in memory" in source.lower()


def test_audio_bytes_never_enter_the_assistant_surface():
    # The controller API takes strings; feeding it bytes would be the only
    # way raw audio could leak, and str() normalizes anything else.
    controller, _m, _speech, fake = build_controller()
    assert controller.handle_phrase(b"what is this") is False
    assert fake.submitted == []
