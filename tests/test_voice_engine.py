"""Voice-engine tests with mocked microphone frames and speech output."""
from __future__ import annotations

import json
import queue
import threading
import time

import nova.voice as voice
from nova.voice import VoiceController, is_wake_phrase, tokens


SILENT = b"\x00\x00" * 2000
LOUD = (2000).to_bytes(2, "little", signed=True) * 2000


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


class FakeRouter:
    def __init__(self, speech):
        self.speech = speech
        self.handled = []
        self.known = {"study mode", "study", "open settings", "mute"}

    def handle(self, phrase, announce_unknown=True):
        self.handled.append((phrase, announce_unknown))
        return "study" if phrase in self.known else "unknown"


class FakeRecognizer:
    def __init__(self, text=""):
        self.text = text
        self.final_calls = 0

    def AcceptWaveform(self, data):
        return bool(self.text)

    def Result(self):
        return json.dumps({"text": self.text})

    def PartialResult(self):
        return json.dumps({"partial": ""})

    def FinalResult(self):
        self.final_calls += 1
        return json.dumps({"text": self.text})

    def Reset(self):
        pass


def make_controller(settings=None):
    speech = FakeSpeech()
    router = FakeRouter(speech)
    controller = VoiceController(
        settings if settings is not None else {"always_listen": False},
        router, beep=lambda: None, event_callback=lambda kind, text: None,
    )
    return controller, router, speech


def fast(monkeypatch):
    monkeypatch.setattr(voice, "COMMAND_START_TIMEOUT", 0.15)
    monkeypatch.setattr(voice, "COMMAND_MAX_SECONDS", 0.4)
    monkeypatch.setattr(voice, "COMMAND_SILENCE", 0.05)
    monkeypatch.setattr(voice, "MIN_SPEECH_BLOCKS", 1)
    monkeypatch.setattr(voice, "BEEP_GATE_SECONDS", 0.01)
    monkeypatch.setattr(voice, "SETTLE_SECONDS", 0.01)


# -- wake word --------------------------------------------------------------
def test_wake_word_is_an_exact_token():
    assert is_wake_phrase("nova")
    assert is_wake_phrase("NOVA")
    assert is_wake_phrase("hey nova")
    assert is_wake_phrase("Nova, study mode.")
    assert tokens("NOVA, study mode.") == ["nova", "study", "mode"]


def test_false_wake_words_are_rejected():
    for text in ("novak", "november", "[unk]", "no va", "unova", ""):
        assert not is_wake_phrase(text), text


# -- microphone hygiene -----------------------------------------------------
def test_beep_gating_clears_stale_frames(monkeypatch):
    fast(monkeypatch)
    beeps = []
    controller, _router, _speech = make_controller()
    controller._beep = lambda: beeps.append("beep")
    audio_q: queue.Queue = queue.Queue()
    for _ in range(5):
        audio_q.put(SILENT)
    controller._beep_and_settle(audio_q)
    assert beeps == ["beep"]
    assert audio_q.empty(), "stale microphone frames must be discarded"


def test_queue_is_bounded_by_dropping_oldest_frames():
    audio_q: queue.Queue = queue.Queue(maxsize=2)
    audio_q.put(SILENT)
    audio_q.put(SILENT)
    try:
        audio_q.put_nowait(LOUD)
    except queue.Full:
        audio_q.get_nowait()
        audio_q.put_nowait(LOUD)
    assert audio_q.qsize() == 2


def test_continuous_mode_ignores_a_final_without_voiced_audio():
    controller, router, _speech = make_controller({"always_listen": True})
    controller._model = None
    controller._grammar_dirty.clear()
    audio_q: queue.Queue = queue.Queue()
    audio_q.put(SILENT)
    thread = threading.Thread(
        target=controller._listen_loop, args=(audio_q, FakeRecognizer("hide")), daemon=True
    )
    thread.start()
    time.sleep(0.5)
    controller._stop.set()
    thread.join(timeout=2.0)
    assert router.handled == [], "noise must never execute a short command"


def test_continuous_mode_accepts_a_final_after_voiced_audio():
    controller, router, _speech = make_controller({"always_listen": True})
    controller._model = None
    controller._grammar_dirty.clear()
    audio_q: queue.Queue = queue.Queue()
    audio_q.put(LOUD)
    thread = threading.Thread(
        target=controller._listen_loop, args=(audio_q, FakeRecognizer("hide")), daemon=True
    )
    thread.start()
    time.sleep(0.5)
    controller._stop.set()
    thread.join(timeout=2.0)
    assert router.handled and router.handled[0][0] == "hide"


def test_capture_reports_silence_as_case_a(monkeypatch):
    fast(monkeypatch)
    controller, _router, _speech = make_controller()
    audio_q: queue.Queue = queue.Queue()
    audio_q.put(SILENT)
    audio_q.put(SILENT)
    text, heard, aborted = controller._capture(audio_q, FakeRecognizer(""))
    assert text == ""
    assert heard is False
    assert aborted is False


def test_capture_detects_speech_and_returns_transcript(monkeypatch):
    fast(monkeypatch)
    controller, _router, _speech = make_controller()
    audio_q: queue.Queue = queue.Queue()
    audio_q.put(LOUD)
    text, heard, aborted = controller._capture(audio_q, FakeRecognizer("study mode"))
    assert text == "study mode"
    assert heard is True
    assert aborted is False


def test_capture_aborts_when_nova_is_speaking(monkeypatch):
    fast(monkeypatch)
    controller, _router, speech = make_controller()
    speech.is_speaking = True
    audio_q: queue.Queue = queue.Queue()
    audio_q.put(LOUD)
    text, heard, aborted = controller._capture(audio_q, FakeRecognizer("study mode"))
    assert text == ""
    assert aborted is True, "NOVA must never capture its own speech"


# -- session behaviour ------------------------------------------------------
def test_valid_command_executes_exactly_once(monkeypatch):
    controller, router, _speech = make_controller()
    controller._beep_and_settle = lambda audio_q: None
    controller._capture = lambda audio_q, recognizer: ("study mode", True, False)
    controller._run_command_session(queue.Queue(), FakeRecognizer())
    assert router.handled == [("study mode", False)]


def test_unintelligible_speech_is_ignored_without_nagging():
    controller, router, speech = make_controller()
    controller._beep_and_settle = lambda audio_q: None
    controller._capture = lambda audio_q, recognizer: ("", True, False)
    controller._run_command_session(queue.Queue(), FakeRecognizer())
    assert router.handled == []
    assert speech.said == [], "NOVA must not repeat a 'did not catch that' prompt"


def test_case_a_stays_silent(monkeypatch):
    controller, router, speech = make_controller()
    controller._beep_and_settle = lambda audio_q: None
    controller._capture = lambda audio_q, recognizer: ("", False, False)
    controller._run_command_session(queue.Queue(), FakeRecognizer())
    assert router.handled == []
    assert speech.said == []


# -- continuous listening ---------------------------------------------------
def continuous_controller():
    speech = FakeSpeech()
    router = FakeRouter(speech)
    controller = VoiceController(
        {"always_listen": True}, router, beep=lambda: None,
        event_callback=lambda kind, text: None,
    )
    return controller, router, speech


def test_continuous_mode_executes_a_command_without_a_wake_word():
    controller, router, _speech = continuous_controller()
    audio_q: queue.Queue = queue.Queue()
    action = controller._dispatch_transcript("study mode", audio_q, FakeRecognizer())
    assert action == "study"
    assert router.handled == [("study mode", False)]


def test_continuous_mode_ignores_unknown_speech_silently():
    controller, router, speech = continuous_controller()
    action = controller._dispatch_transcript(
        "random background conversation", queue.Queue(), FakeRecognizer()
    )
    assert action is None
    assert speech.said == []
    assert router.handled[-1][0] == "random background conversation"


def test_continuous_mode_joins_short_fragments():
    controller, router, _speech = continuous_controller()
    audio_q: queue.Queue = queue.Queue()
    assert controller._dispatch_transcript("open", audio_q, FakeRecognizer()) is None
    assert controller._dispatch_transcript("settings", audio_q, FakeRecognizer()) == "study"
    assert ("open settings", False) in router.handled


def test_continuous_mode_debounces_the_same_command():
    controller, router, _speech = continuous_controller()
    audio_q: queue.Queue = queue.Queue()
    assert controller._dispatch_transcript("mute", audio_q, FakeRecognizer()) == "study"
    assert controller._dispatch_transcript("mute", audio_q, FakeRecognizer()) is None
    assert router.handled.count(("mute", False)) == 1


def test_one_wake_produces_one_session_during_debounce(monkeypatch):
    controller, _router, _speech = make_controller()
    calls = []
    controller._run_command_session = lambda audio_q, recognizer=None: calls.append(time.monotonic())
    audio_q: queue.Queue = queue.Queue()
    recognizer = FakeRecognizer("nova")

    thread = threading.Thread(target=controller._wake_loop, args=(audio_q, recognizer))
    thread.start()
    try:
        audio_q.put(LOUD)
        time.sleep(0.12)
        audio_q.put(LOUD)
        time.sleep(0.12)
    finally:
        controller._stop.set()
        thread.join(timeout=2.0)
    assert len(calls) == 1, "a second wake inside the debounce window must be ignored"


def test_manual_listen_requests_one_session():
    controller, _router, _speech = make_controller()
    gate = threading.Event()
    controller._thread = threading.Thread(target=gate.wait)
    controller._thread.start()
    try:
        controller.request_manual_listen()
        assert controller._manual.is_set()
    finally:
        gate.set()
        controller._thread.join(timeout=1.0)
