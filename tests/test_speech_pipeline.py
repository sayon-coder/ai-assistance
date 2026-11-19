"""Pipelined Kokoro speech: no multi-second silence at a full stop.

The streamed answer is spoken sentence by sentence.  These tests pin the two
behaviours that make that feel natural: the next sentence is synthesised while
the current one is still playing, and a one-second beat is left at the full
stop that separates them.
"""
from __future__ import annotations

import threading
import time

import numpy as np

from talcon import neural_tts
from talcon.neural_tts import DEFAULT_SENTENCE_PAUSE, KokoroBackend, _ends_sentence
from talcon.speech import SpeechOutput


class FakeEngine:
    """Kokoro stand-in that records what is synthesised, and when."""

    def __init__(self, sample_rate: int = 24000, frames: int = 4800):
        self.sample_rate = sample_rate
        self.frames = frames
        self.calls: list[str] = []
        self.second_started = threading.Event()

    def create(self, text, voice=None, speed=1.0, lang=None):
        self.calls.append(text)
        if len(self.calls) >= 2:
            self.second_started.set()
        return np.zeros(self.frames, dtype=np.float32), self.sample_rate


def make_backend(monkeypatch):
    backend = KokoroBackend()
    engine = FakeEngine()
    monkeypatch.setattr(backend, "ensure_started", lambda timeout=25.0: True)
    backend._engine = engine
    return backend, engine


def test_ends_sentence_handles_terminal_punctuation():
    assert _ends_sentence("Done.")
    assert _ends_sentence("Really?")
    assert _ends_sentence('He said "stop!"')
    assert not _ends_sentence("a comma,")
    assert not _ends_sentence("")


def test_next_sentence_is_synthesised_while_the_first_plays(monkeypatch):
    backend, engine = make_backend(monkeypatch)
    played: list[bytes] = []

    def fake_play(data: bytes) -> bool:
        played.append(data)
        # Playback of the first sentence must overlap synthesis of the second.
        assert engine.second_started.wait(3.0), "second sentence never synthesised"
        return True

    monkeypatch.setattr(backend, "_play", fake_play)
    try:
        first = backend.submit("First sentence.")
        second = backend.submit("Second sentence.")
        assert first is not None and second is not None
        assert backend.play(first) is True
        assert sorted(engine.calls) == ["First sentence.", "Second sentence."]
    finally:
        backend.stop()


def test_full_stop_gets_one_second_before_the_next_sentence(monkeypatch):
    backend, _engine = make_backend(monkeypatch)
    sequence: list[object] = []
    monkeypatch.setattr(backend, "_play", lambda data: sequence.append(data) or True)
    monkeypatch.setattr(
        backend, "_silence", lambda rate, seconds: sequence.append(("pause", seconds))
    )
    try:
        first = backend.submit("First sentence.")
        second = backend.submit("Second sentence.")
        assert backend.play(first) is True
        assert backend.play(second) is True
        assert ("pause", DEFAULT_SENTENCE_PAUSE) in sequence
        # The final sentence is not followed by a pointless pause.
        assert sequence[-1] != ("pause", DEFAULT_SENTENCE_PAUSE)
    finally:
        backend.stop()


def test_intra_sentence_chunks_do_not_get_a_full_stop_pause(monkeypatch):
    backend, _engine = make_backend(monkeypatch)
    pauses: list[float] = []
    monkeypatch.setattr(backend, "_play", lambda data: True)
    monkeypatch.setattr(
        backend, "_silence",
        lambda rate, seconds: pauses.append(seconds) or b"",
    )
    # A comma-split long sentence must not pause for a full second mid-thought.
    long_sentence = ("word " * 60).strip() + "."
    job = backend.submit(long_sentence)
    try:
        assert job is not None and len(job.chunks) > 1
        assert backend.play(job) is True
        assert pauses == []
    finally:
        backend.stop()


def test_sentence_pause_is_clamped():
    backend = KokoroBackend()
    backend.set_sentence_pause(9.0)
    assert backend._sentence_pause == 3.0
    backend.set_sentence_pause(-1)
    assert backend._sentence_pause == 0.0
    backend.set_sentence_pause("not a number")
    assert backend._sentence_pause == DEFAULT_SENTENCE_PAUSE


class FakeJob:
    def __init__(self, text, on_start=None):
        self.text = text
        self.on_start = on_start


class PipelineBackend:
    """Backend-shaped object that plays instantly but records the order."""

    supports_pipelining = True
    supports_playback_start = True
    last_error = ""

    def __init__(self):
        self.submitted: list[str] = []
        self.played: list[str] = []

    def submit(self, text, on_start=None):
        self.submitted.append(text)
        return FakeJob(text, on_start)

    def play(self, job):
        self.played.append(job.text)
        if job.on_start:
            job.on_start()
        if len(self.played) == 1:
            # Submission of the next sentence must happen without waiting for
            # this playback to finish; otherwise the gap reappears.
            deadline = time.monotonic() + 3.0
            while len(self.submitted) < 2 and time.monotonic() < deadline:
                time.sleep(0.02)
            assert len(self.submitted) == 2, "next sentence was not submitted ahead"
        return True

    def discard(self, job):
        pass

    def stop_speaking(self):
        pass

    def stop(self):
        pass


def test_speech_output_submits_the_next_sentence_while_playing():
    backend = PipelineBackend()
    seen: list[str] = []
    output = SpeechOutput(enabled=True, backend=backend, on_utterance=seen.append)
    try:
        output.say("One.")
        output.say("Two.")
        assert output.wait_until_quiet(5.0)
        assert backend.played == ["One.", "Two."]
        assert seen == ["One.", "Two."]
        assert output.is_busy is False
    finally:
        output.stop()


class PauseAwareBackend(PipelineBackend):
    def __init__(self):
        super().__init__()
        self.pauses: list[float] = []

    def set_sentence_pause(self, seconds):
        self.pauses.append(seconds)


def test_speech_output_forwards_the_sentence_pause():
    backend = PauseAwareBackend()
    output = SpeechOutput(enabled=True, backend=backend)
    try:
        output.set_sentence_pause(0.5)
        assert backend.pauses[-1] == 0.5
        output.set_sentence_pause(7.0)
        assert backend.pauses[-1] == 3.0
    finally:
        output.stop()


def test_multi_sentence_utterance_is_spoken_in_sentences_announced_once():
    backend = PipelineBackend()
    seen: list[str] = []
    output = SpeechOutput(enabled=True, backend=backend, on_utterance=seen.append)
    try:
        output.say("One thing happened. Another followed.")
        assert output.wait_until_quiet(5.0)
        assert backend.played == ["One thing happened.", "Another followed."]
        # History/preview still get one complete entry, not one per sentence.
        assert seen == ["One thing happened. Another followed."]
    finally:
        output.stop()


def test_pipeline_flag_only_set_when_the_engine_supports_it():
    assert neural_tts.KokoroBackend.supports_pipelining is True
    assert SpeechOutput._pipeline_ready(KokoroBackend()) is True
    assert SpeechOutput._pipeline_ready(object()) is False
