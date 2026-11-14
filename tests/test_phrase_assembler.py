"""Bounded phrase assembly tests: multiple finals, expiry and alternatives."""
from __future__ import annotations

import queue

from nova.commands import build_builtin_registry
from nova.phrase import PhraseAssembler
from nova.voice import REPEAT_MESSAGE, VoiceController

VOCABULARY = {
    "open my code editor",
    "study mode",
    "what time is it",
    "help",
    "exit nova",
}
EXTRA = {"open my code editor"}


class FakeSpeech:
    def __init__(self):
        self.said = []
        self.enabled = True
        self.is_speaking = False

    def say(self, text):
        self.said.append(text)

    def say_and_wait(self, text, timeout=30.0):
        self.said.append(text)
        return True

    def wait_until_idle(self, timeout=None):
        return True


class RegistryRouter:
    """Minimal router backed by the real command registry."""

    def __init__(self):
        self.registry = build_builtin_registry()
        self.speech = FakeSpeech()
        self.handled = []

    def handle(self, phrase, announce_unknown=True):
        self.handled.append(phrase)
        if phrase in EXTRA:
            return "custom"
        command = self.registry.match(phrase)
        return command.action if command is not None else "unknown"


def assembler(vocabulary=VOCABULARY, timeout=4.0):
    return PhraseAssembler(vocabulary=vocabulary, timeout=timeout)


def controller(events=None, router=None):
    events = events if events is not None else []
    router = router or RegistryRouter()
    voice = VoiceController(
        {"always_listen": True}, router, beep=lambda: None,
        event_callback=lambda kind, text: events.append((kind, text)),
        grammar_provider=lambda: router.registry.phrases() | EXTRA,
    )
    return voice, router, events


# -- capture ---------------------------------------------------------------
def test_full_phrase_is_assembled_from_multiple_finals():
    voice, router, _events = controller()
    audio_q: queue.Queue = queue.Queue()
    assert voice._dispatch_transcript("open my", audio_q, None) is None
    assert voice._dispatch_transcript("code", audio_q, None) is None
    assert voice._dispatch_transcript("editor", audio_q, None) == "custom"
    assert router.handled[-1] == "open my code editor"


def test_long_phrase_over_five_words_is_assembled():
    phrase = "open the visual studio code editor now"
    assembler_ = PhraseAssembler(vocabulary={phrase})
    for part in ("open", "the", "visual", "studio", "code", "editor"):
        assert assembler_.add(part).pending
    assert assembler_.add("now").matched == phrase


def test_short_pauses_between_finals_are_tolerated():
    voice, _router, _events = controller()
    voice._assembler.reset()
    now = 100.0
    assert voice._assembler.add("open", now=now).pending
    assert voice._assembler.add("my", now=now + 1.0).pending
    assert voice._assembler.add("code", now=now + 2.0).pending
    assert voice._assembler.add("editor", now=now + 3.0).matched == "open my code editor"


def test_incomplete_prefix_expires_and_never_executes():
    voice, router, _events = controller()
    assembler_ = assembler()
    assert assembler_.add("open", now=10.0).pending
    assert assembler_.expire(now=14.1) is True
    assert assembler_.add("editor", now=15.0).matched is None
    assert router.handled == []


def test_stale_fragments_are_cleared_before_joining():
    assembler_ = assembler()
    assembler_.add("open", now=1.0)
    result = assembler_.add("study mode", now=10.0)
    assert result.matched == "study mode"
    assert result.heard == "study mode"


def test_unrelated_speech_is_not_combined_into_a_command():
    assembler_ = assembler()
    first = assembler_.add("the weather is nice today", now=1.0)
    assert first.matched is None
    assert first.pending is False
    second = assembler_.add("open my code editor", now=1.2)
    assert second.matched == "open my code editor"


def test_unrelated_prefix_clears_the_buffer():
    assembler_ = assembler()
    assert not assembler_.add("random background conversation", now=1.0).pending
    assert not assembler_.pending()


# -- alternatives ----------------------------------------------------------
def test_alternative_hypothesis_can_match_exactly():
    assembler_ = assembler()
    result = assembler_.add(["sturdy mode", "study mode"], now=1.0)
    assert result.matched == "study mode"


def test_primary_hypothesis_is_preferred_over_a_longer_alternative():
    assembler_ = assembler(vocabulary={"mute voice", "unmute voice"})
    result = assembler_.add(["mute voice", "unmute voice"], now=1.0)
    assert result.matched == "mute voice"


def test_wrong_alternatives_never_execute():
    assembler_ = assembler()
    result = assembler_.add(["make me a sandwich", "the weather is nice"], now=1.0)
    assert result.matched is None


def test_weak_destructive_alternative_is_ignored():
    voice, router, _events = controller()
    action = voice._dispatch_transcript(
        "unrelated speech", queue.Queue(), None,
        alternatives=["unrelated speech", "another phrase", "exit nova"],
    )
    assert action is None
    assert "exit nova" not in router.handled


def test_destructive_command_from_the_primary_hypothesis_still_runs():
    voice, router, _events = controller()
    action = voice._dispatch_transcript(
        "exit nova", queue.Queue(), None, alternatives=["exit nova"]
    )
    assert action == "exit"
    assert router.handled == ["exit nova"]


# -- unknown-word marker ---------------------------------------------------
def test_unknown_marker_never_executes():
    voice, router, _events = controller()
    assert voice._dispatch_transcript("[unk]", queue.Queue(), None) is None
    assert voice._dispatch_transcript(
        "open", queue.Queue(), None, alternatives=["[unk]"]
    ) is None
    assert router.handled == []


# -- diagnostics -----------------------------------------------------------
def test_success_updates_every_diagnostic_field():
    events = []
    voice, _router, _events = controller(events=events)
    action = voice._dispatch_transcript("study mode", queue.Queue(), None)
    assert action == "study"
    assert ("last_heard", "study mode") in events
    assert ("last_match", "study mode") in events
    assert ("last_command", "study mode") in events
    assert any(kind == "last_result" and "Executed" in text for kind, text in events)


def test_failure_updates_heard_and_failure_but_not_executed_command():
    events = []
    voice, _router, _events = controller(events=events)
    assert voice._dispatch_transcript(
        "open the pod bay doors", queue.Queue(), None
    ) is None
    assert ("last_heard", "open the pod bay doors") in events
    assert ("last_failure", "No matching registered command") in events
    assert not any(kind == "last_command" for kind, _text in events)


def test_background_chatter_does_not_record_a_failure():
    events = []
    voice, _router, _events = controller(events=events)
    assert voice._dispatch_transcript(
        "the weather is nice today", queue.Queue(), None
    ) is None
    assert ("last_heard", "the weather is nice today") in events
    assert not any(kind == "last_failure" for kind, _text in events)


def test_action_failure_is_reported_separately_from_recognition_failure():
    events = []

    class ExplodingRouter:
        def handle(self, phrase, announce_unknown=True):
            raise RuntimeError("mode backend offline")

    voice = VoiceController(
        {"always_listen": True}, ExplodingRouter(), beep=lambda: None,
        event_callback=lambda kind, text: events.append((kind, text)),
        grammar_provider=lambda: {"study mode"},
    )
    assert voice._dispatch_transcript("study mode", queue.Queue(), None) == "failed"
    assert ("last_match", "study mode") in events
    assert ("last_command", "study mode") in events
    assert any(kind == "last_result" and "Action failed" in text
               for kind, text in events)
    assert not any(kind == "last_failure" for kind, _text in events)


# -- repeat handling -------------------------------------------------------
def test_command_like_unknown_asks_for_one_repeat():
    voice, _router, _events = controller()
    voice._handle_unintelligible("open the pod bay doors", queue.Queue())
    assert voice._speech.said == [REPEAT_MESSAGE]


def test_unrelated_background_conversation_stays_silent():
    voice, _router, _events = controller()
    voice._handle_unintelligible("the weather is nice today", queue.Queue())
    voice._handle_unintelligible("what a lovely day", queue.Queue())
    voice._handle_unintelligible("tell me a story", queue.Queue())
    assert voice._speech.said == []


def test_repeat_request_is_rate_limited():
    voice, _router, _events = controller()
    voice._handle_unintelligible("open the pod bay doors", queue.Queue())
    voice._handle_unintelligible("open the pod bay doors", queue.Queue())
    assert voice._speech.said == [REPEAT_MESSAGE]


def test_repeat_never_runs_when_voice_is_muted():
    voice, _router, _events = controller()
    voice._speech.enabled = False
    voice._handle_unintelligible("open the pod bay doors", queue.Queue())
    assert voice._speech.said == []
