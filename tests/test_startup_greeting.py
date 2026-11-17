"""Startup greeting behaviour: once, muted-aware, ready-aware and silent manually."""
from __future__ import annotations

import threading
from types import SimpleNamespace

from talcon.app import TalconApplication
from talcon.runtime import TalconRuntime, STARTUP_GREETING
from talcon.speech import SpeechOutput


class FakeSpeech:
    def __init__(self, enabled=True, ready=True, error=None):
        self.enabled = enabled
        self.ready = ready
        self.error = error
        self.spoken = []
        self.ready_called = 0
        self.ready_event = threading.Event()
        self.done = threading.Event()

    def ensure_ready(self, timeout=25.0):
        self.ready_called += 1
        self.ready_event.set()
        if self.error is not None:
            raise self.error
        return self.ready

    def say_and_wait(self, text, timeout=30.0):
        self.spoken.append(text)
        self.done.set()
        return True

    def say(self, text):
        self.spoken.append(text)


class FakeSignal:
    def __init__(self):
        self.events = []

    def emit(self, *args):
        self.events.append(args)


def make_runtime(speech, muted=False):
    runtime = TalconRuntime.__new__(TalconRuntime)
    runtime.muted = muted
    runtime._startup_greeting_spoken = False
    runtime.status = {}
    runtime.bridge = SimpleNamespace(status_changed=FakeSignal())
    runtime.speech = speech
    return runtime


def test_startup_greeting_is_spoken_exactly_once():
    speech = FakeSpeech()
    runtime = make_runtime(speech)
    assert runtime.speak_startup_greeting() is True
    assert speech.done.wait(2.0)
    assert speech.spoken == [STARTUP_GREETING]
    assert speech.ready_called == 1

    assert runtime.speak_startup_greeting() is False
    assert speech.spoken == [STARTUP_GREETING]


def test_muted_startup_greeting_stays_silent():
    speech = FakeSpeech()
    runtime = make_runtime(speech, muted=True)
    assert runtime.speak_startup_greeting() is False
    assert speech.ready_called == 0
    assert speech.spoken == []


def test_disabled_speech_does_not_speak():
    speech = FakeSpeech(enabled=False)
    runtime = make_runtime(speech)
    assert runtime.speak_startup_greeting() is False
    assert speech.spoken == []


def test_speech_readiness_is_checked_before_speaking():
    speech = FakeSpeech(ready=False)
    runtime = make_runtime(speech)
    assert runtime.speak_startup_greeting() is True
    assert speech.ready_event.wait(2.0)
    speech.done.wait(0.2)
    assert speech.spoken == []


def test_speech_errors_do_not_crash_the_greeting_thread():
    speech = FakeSpeech(error=RuntimeError("Kokoro unavailable"))
    runtime = make_runtime(speech)
    assert runtime.speak_startup_greeting() is True
    assert speech.ready_event.wait(2.0)
    speech.done.wait(0.2)
    assert speech.spoken == []


def test_greeting_is_queued_so_the_microphone_sees_speech_busy():
    class BlockingBackend:
        def __init__(self):
            self.started = threading.Event()
            self.release = threading.Event()

        def ensure_started(self, timeout=25.0):
            return True

        def say(self, text):
            self.started.set()
            self.release.wait(2.0)

        def stop(self):
            self.release.set()

    backend = BlockingBackend()
    output = SpeechOutput(enabled=True, backend=backend)
    try:
        output.say(STARTUP_GREETING)
        assert output.is_busy is True
        assert backend.started.wait(2.0)
        assert output.is_busy is True, "the voice loop must ignore TALCON's own greeting"
        backend.release.set()
        assert output.wait_until_quiet(2.0) is True
    finally:
        output.stop()


class _FakeRuntime:
    def __init__(self):
        self.settings = {}
        self.started = False
        self.greeting = False

    def start(self):
        self.started = True

    def speak_startup_greeting(self):
        self.greeting = True


class _FakeQt:
    def exec(self):
        return 0


def run_application(startup_mode: bool):
    runtime = _FakeRuntime()
    shown = []
    fake = SimpleNamespace(
        startup_mode=startup_mode,
        runtime=runtime,
        _apply_face_settings=lambda settings: None,
        _refresh_menu=lambda: None,
        show_settings=lambda: shown.append(True),
        show_wizard=lambda: None,
        qt=_FakeQt(),
    )
    result = TalconApplication.run(fake)
    return result, runtime, shown


def test_automatic_startup_speaks_and_stays_in_the_background():
    result, runtime, shown = run_application(True)
    assert result == 0
    assert runtime.started is True
    assert runtime.greeting is True
    assert shown == [], "automatic startup must not open the Control Center"


def test_manual_launch_does_not_speak_the_greeting():
    result, runtime, shown = run_application(False)
    assert result == 0
    assert runtime.started is True
    assert runtime.greeting is False
    assert shown == [True], "a manual launch opens the Control Center"
