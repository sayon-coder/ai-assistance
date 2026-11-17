"""Vosk endpointer configuration tests.

The installed Vosk 0.3.45 exposes neither ``EndpointerMode`` nor
``SetEndpointerMode``/``SetEndpointerDelays`` (verified against the
``libvosk.dll`` export table).  These tests prove that the configuration is
honest: it uses the three-argument delays API when a build provides it and
clearly reports "unsupported" when it does not.
"""
from __future__ import annotations

import json

from talcon.voice import VoiceController


class FakeSpeech:
    def __init__(self):
        self.said = []

    def say(self, text):
        self.said.append(text)


class FakeRouter:
    def __init__(self, speech):
        self.speech = speech

    def handle(self, phrase, announce_unknown=True):
        return "unknown"


def make_controller():
    speech = FakeSpeech()
    router = FakeRouter(speech)
    controller = VoiceController(
        {"always_listen": False}, router, beep=lambda: None,
        event_callback=lambda kind, text: None,
    )
    return controller


class SupportsDelays:
    """A stand-in for a Vosk build that exposes the delays API."""

    def __init__(self):
        self.calls = []

    def SetEndpointerDelays(self, t_start, t_delta, t_end):
        self.calls.append((t_start, t_delta, t_end))


class BrokenDelays:
    def SetEndpointerDelays(self, *args):
        raise OSError("endpoint configuration rejected")


def test_unsupported_build_is_reported_not_claimed_configured():
    controller = make_controller()
    status = controller._configure_endpointer(object())
    assert status == "unsupported"
    assert controller._endpointer_status == "unsupported"


def test_delays_are_configured_with_three_settings_derived_values():
    controller = make_controller()
    controller.apply_settings({
        "speech_trailing_silence": 1.6,
        "speech_max_utterance": 20.0,
    })
    recognizer = SupportsDelays()
    status = controller._configure_endpointer(recognizer)
    assert status == "configured"
    assert controller._endpointer_status == "configured"
    (t_start, t_delta, t_end) = recognizer.calls[0]
    assert len(recognizer.calls) == 1, "the delays are set exactly once"
    assert 0.3 <= t_start <= 2.0
    assert t_delta == 1.6, "trailing silence comes from the configured setting"
    assert t_end == 20.0, "the maximum comes from the configured setting"
    assert all(isinstance(value, float) for value in (t_start, t_delta, t_end))


def test_configuration_failure_is_reported():
    controller = make_controller()
    status = controller._configure_endpointer(BrokenDelays())
    assert status == "failed"
    assert controller._endpointer_status == "failed"


def test_one_reliable_strategy_no_mode_call():
    class SupportsBoth(SupportsDelays):
        def __init__(self):
            super().__init__()
            self.mode_calls = 0

        def SetEndpointerMode(self, mode):  # pragma: no cover - must not run
            self.mode_calls += 1

    controller = make_controller()
    recognizer = SupportsBoth()
    status = controller._configure_endpointer(recognizer)
    assert status == "configured"
    assert recognizer.mode_calls == 0, "delays alone define the rules; a mode "
    "call after them could reset the configuration"


def test_wake_recognizers_skip_endpoint_configuration():
    controller = make_controller()
    assert controller._configure_endpointer(SupportsDelays(), wake=True) == "skipped"
