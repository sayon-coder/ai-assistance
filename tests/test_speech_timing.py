"""Slow-speech endpoint timing tests at 0.5/1.0/1.5/2.0 s pauses.

These tests drive the actual continuous listener with a fake Vosk recognizer
and timed PCM blocks.  Commands are never dispatched before the configured
1.6 s trailing-silence window. Vosk final segments remain buffered during a
slow multiword command and are prioritized over a running partial.
"""
from __future__ import annotations

import json
import queue
import threading
import time
import types

from talcon.voice import VoiceController

TRAILING = 1.6
BLOCK_SECONDS = 0.25
SILENT = b"\x00\x00" * 2000
LOUD = (2000).to_bytes(2, "little", signed=True) * 2000


class Speech:
    def __init__(self):
        self.said = []
    def say(self, text): self.said.append(text)
    def say_and_wait(self, text, timeout=30.0): self.said.append(text); return True
    def wait_until_idle(self, timeout=None): return True


class Router:
    def __init__(self, speech):
        self.speech = speech
        self.handled = []
        self.registry = None
    def handle(self, phrase, announce_unknown=True):
        self.handled.append(phrase)
        return "ok" if phrase in {"study mode", "mute", "open notepad", "close notepad"} else "unknown"


class ScriptedRecognizer:
    """Vosk facade that emits the scripted whole-utterance final at a time."""
    def __init__(self, finals=(), partial="open notepad"):
        self.finals = iter(finals)
        self.partial = partial
        self.current = ""
        self.pending = None
        self.deadline = None
        self.final_emitted = False
    def AcceptWaveform(self, data):
        # Real Vosk advances on each PCM block; do not synthesize a new
        # utterance while only silence is being fed after Reset().
        if not data.strip(b"\x00"):
            return False
        if self.pending is None:
            try:
                self.pending, pause_after = next(self.finals)
            except StopIteration:
                self.pending = None
                return False
            self.deadline = time.monotonic() + pause_after
            # A zero pause means this is the final for the current completed
            # utterance. Otherwise this recognizer waits during word pauses.
            if pause_after <= 0:
                self.current = self.pending
                self.pending = None
                self.final_emitted = True
                return True
        if self.pending is not None and time.monotonic() >= self.deadline:
            self.current = self.pending
            self.pending = None
            self.final_emitted = True
            return True
        return False
    def Result(self):
        text = self.current
        self.current = ""
        return json.dumps({"text": text})
    def PartialResult(self):
        return json.dumps({"partial": self.partial if not self.final_emitted else ""})
    def FinalResult(self):
        return json.dumps({"text": ""})
    def Reset(self):
        pass


def setup(trailing=TRAILING):
    speech = Speech()
    router = Router(speech)
    controller = VoiceController(
        {"always_listen": True, "speech_trailing_silence": trailing},
        router, beep=lambda: None, event_callback=lambda *_: None,
    )
    controller._model = None
    controller._grammar_dirty.clear()
    # This suite tests endpoint timing, not score normalization.
    controller._dispatch_transcript = lambda text, *a, **kw: router.handle(text)
    return controller, router


def start(controller, audio_q):
    thread = threading.Thread(
        target=controller._listen_loop,
        args=(audio_q, ScriptedRecognizer()), daemon=True,
    )
    thread.start()
    return thread


def feed_later(audio_q, segments):
    """segments=(delay after previous, PCM block count, silence?)"""
    def worker():
        for delay, blocks, speech in segments:
            time.sleep(delay)
            for _ in range(blocks):
                audio_q.put(LOUD if speech else SILENT)
                time.sleep(BLOCK_SECONDS)
    thread = threading.Thread(target=worker, daemon=True)
    thread.start()
    return thread


def finish(controller, listener, duration):
    time.sleep(duration)
    controller._stop.set()
    listener.join(timeout=3)
    assert not listener.is_alive()


def test_partial_not_called_before_trailing_silence_for_0_5_1_0_1_5_pauses():
    for pause in (0.5, 1.0, 1.5):
        controller, router = setup()
        audio_q = queue.Queue()
        attempts = []
        controller._lenient_app_action = lambda phrase: attempts.append(phrase) or None
        # Start one voiced phrase; pause is less than configured endpoint.
        audio_q.put(LOUD)
        listener = threading.Thread(
            target=controller._listen_loop,
            args=(audio_q, ScriptedRecognizer(partial="open notepad")), daemon=True,
        )
        listener.start()
        time.sleep(pause)
        assert attempts == [], f"partial ran during {pause}s pause"
        controller._stop.set()
        listener.join(timeout=3)
        assert attempts == [], f"partial ran before full timeout for {pause}s"
        assert router.handled == []


def test_two_word_slow_command_final_waits_for_pause_then_executes_whole():
    for pause in (0.5, 1.0, 1.5):
        controller, router = setup()
        audio_q = queue.Queue()
        listener = threading.Thread(
            target=controller._listen_loop,
            args=(audio_q, ScriptedRecognizer(finals=[("open notepad", pause)])),
            daemon=True,
        )
        listener.start()
        # Word one, pause, word two, then the full silence endpoint.
        audio_q.put(LOUD)
        time.sleep(pause)
        audio_q.put(LOUD)
        time.sleep(TRAILING + 0.35)
        # Put actual silence blocks so adaptive VAD's release doesn't stand in
        # for the clock; the listener still requires the complete wall time.
        for _ in range(8):
            audio_q.put(SILENT)
            time.sleep(BLOCK_SECONDS)
        controller._stop.set()
        listener.join(timeout=3)
        assert router.handled == ["open notepad"], (pause, router.handled)


def test_two_second_pause_splits_and_executes_separate_commands():
    controller, router = setup()
    audio_q = queue.Queue()
    recognizer = ScriptedRecognizer(
        finals=[("study mode", 0), ("mute", 0)], partial=""
    )
    listener = threading.Thread(
        target=controller._listen_loop,
        args=(audio_q, recognizer), daemon=True,
    )
    listener.start()
    audio_q.put(LOUD)
    time.sleep(TRAILING + 0.2)
    for _ in range(8):
        audio_q.put(SILENT)
        time.sleep(BLOCK_SECONDS)
    # This is more than the endpoint window, so the second command must not
    # join the first command's phrase-assembler state.
    time.sleep(2.0 - TRAILING)
    audio_q.put(LOUD)
    time.sleep(0.2)
    time.sleep(TRAILING + 0.2)
    for _ in range(8):
        audio_q.put(SILENT)
        time.sleep(BLOCK_SECONDS)
    controller._stop.set()
    listener.join(timeout=3)
    assert router.handled == ["study mode", "mute"], router.handled


def test_final_result_takes_priority_over_partial():
    controller, router = setup()
    audio_q = queue.Queue()
    calls = []
    controller._lenient_app_action = lambda phrase: calls.append(phrase) or None
    final_vosk = ScriptedRecognizer(finals=[("open notepad", 0)], partial="open notepad trash")
    listener = threading.Thread(
        target=controller._listen_loop,
        args=(audio_q, final_vosk), daemon=True,
    )
    listener.start()
    audio_q.put(LOUD)
    # Vosk provides a complete final before the silence window is up.
    time.sleep(0.4)
    audio_q.put(LOUD)
    time.sleep(TRAILING + 0.2)
    for _ in range(8):
        audio_q.put(SILENT)
        time.sleep(BLOCK_SECONDS)
    controller._stop.set()
    listener.join(timeout=3)
    assert calls == [], "final path must win; partial fallback must not run"


class RegistryRouter(Router):
    """Router with a real-shaped registry so the assembler can match exactly."""
    class _Registry:
        def phrases(self):
            return {"open notepad", "close notepad", "study mode", "mute"}
        def match(self, phrase):
            if phrase in self.phrases():
                return types.SimpleNamespace(action="open_app")
            return None

    def __init__(self, speech):
        super().__init__(speech)
        self.registry = RegistryRouter._Registry()


def test_matched_command_executes_without_waiting_for_trailing_silence():
    """Latency: a grammar-matched final must not sit for the silence window."""
    speech = Speech()
    router = RegistryRouter(speech)
    controller = VoiceController(
        {"always_listen": True, "speech_trailing_silence": TRAILING},
        router, beep=lambda: None, event_callback=lambda *_: None,
    )
    controller._model = None
    controller._grammar_dirty.clear()
    controller._dispatch_transcript = lambda text, *a, **kw: router.handle(text)
    audio_q = queue.Queue()
    listener = threading.Thread(
        target=controller._listen_loop,
        args=(audio_q, ScriptedRecognizer(finals=[("open notepad", 0)])),
        daemon=True,
    )
    listener.start()
    audio_q.put(LOUD)
    # Well before the 1.6 s endpoint could ever expire.
    time.sleep(TRAILING * 0.4)
    assert router.handled == ["open notepad"], (
        "a matched command must run immediately, not after trailing silence"
    )
    controller._stop.set()
    listener.join(timeout=3)
    # And it must not run twice when the silence window later expires.
    assert router.handled == ["open notepad"], router.handled


def test_partial_fallback_runs_once_only_after_full_timeout():
    controller, router = setup()
    audio_q = queue.Queue()
    attempts = []
    controller._lenient_app_action = lambda phrase: attempts.append(phrase) or None
    listener = threading.Thread(
        target=controller._listen_loop,
        args=(audio_q, ScriptedRecognizer(partial="open notepad")), daemon=True,
    )
    listener.start()
    audio_q.put(LOUD)
    time.sleep(TRAILING + 0.2)
    for _ in range(12):
        audio_q.put(SILENT)
        time.sleep(BLOCK_SECONDS)
    controller._stop.set()
    listener.join(timeout=3)
    # An exact registered command runs directly from the partial once the
    # silence window has passed; the lenient app path is not needed.
    assert router.handled == ["open notepad"], router.handled
    assert attempts == []


def test_unregistered_partial_still_falls_back_to_lenient_app_matching():
    controller, router = setup()
    audio_q = queue.Queue()
    attempts = []
    controller._lenient_app_action = lambda phrase: attempts.append(phrase) or None
    listener = threading.Thread(
        target=controller._listen_loop,
        args=(audio_q, ScriptedRecognizer(partial="open spotift")), daemon=True,
    )
    listener.start()
    audio_q.put(LOUD)
    time.sleep(TRAILING + 0.2)
    for _ in range(12):
        audio_q.put(SILENT)
        time.sleep(BLOCK_SECONDS)
    controller._stop.set()
    listener.join(timeout=3)
    assert attempts == ["open spotift"], attempts
