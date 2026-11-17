"""The optional offline Kokoro neural voice, tested without loading the model."""
from __future__ import annotations

import io
import wave

from talcon import neural_tts, speech
from talcon.neural_tts import (
    KokoroBackend, model_ready, split_for_speech, unavailable_reason, wav_bytes,
)
from talcon.speech import (
    BACKEND_KOKORO, ENGINE_KOKORO, SpeechOutput, humanize_for_speech,
    normalize_backend,
)


def test_split_for_speech_keeps_sentences_whole_and_bounded():
    chunks = split_for_speech("One. Two. Three.")
    assert chunks[0] == "One."
    assert " ".join(chunks) == "One. Two. Three."
    long_text = " ".join(["This sentence is here."] * 40)
    chunks = split_for_speech(long_text)
    assert all(len(chunk) <= 240 for chunk in chunks)
    assert " ".join(chunks).split() == long_text.split()


def test_wav_bytes_is_valid_pcm_and_applies_volume():
    data = wav_bytes([0.5, -0.5, 1.5], 24000, volume=0.5)
    with wave.open(io.BytesIO(data), "rb") as handle:
        assert handle.getnchannels() == 1
        assert handle.getsampwidth() == 2
        assert handle.getframerate() == 24000
        frames = handle.readframes(handle.getnframes())
    assert len(frames) == 6
    # 0.5 * 0.5 * 32767, truncated to PCM.
    assert frames[:2] == (8191).to_bytes(2, "little", signed=True)


def test_model_ready_is_false_without_files(tmp_path, monkeypatch):
    monkeypatch.setattr(neural_tts, "KOKORO_DIR", tmp_path)
    assert model_ready() is False
    assert "not installed" in unavailable_reason()


def test_kokoro_voice_details_are_natural_and_offline():
    backend = KokoroBackend()
    details = backend.voice_details()
    assert details
    assert all(voice.engine == ENGINE_KOKORO for voice in details)
    assert all(voice.natural and voice.offline for voice in details)
    assert all(voice.backend == BACKEND_KOKORO for voice in details)
    assert backend.request_voice("bm_george") is True
    assert backend.wait_voice()[0] is True


def test_normalize_backend_and_factory_recognise_kokoro():
    assert normalize_backend("kokoro") == BACKEND_KOKORO
    assert normalize_backend("neural") == BACKEND_KOKORO
    assert isinstance(speech.create_backend(BACKEND_KOKORO), KokoroBackend)


def test_humanize_turns_written_shorthand_into_spoken_words():
    text = humanize_for_speech(
        "**Done** — e.g. one thing & another, i.e. this, etc."
    )
    assert "*" not in text
    assert "for example" in text
    assert " and " in text
    assert "that is" in text
    assert "et cetera" in text


class _FakeBackend:
    def __init__(self):
        self.said = []
        self.stopped = 0
        self.last_error = ""

    def say(self, text, timeout=None):
        self.said.append(text)
        return True

    def set_rate(self, rate):
        pass

    def set_volume(self, volume):
        pass

    def stop_speaking(self):
        self.stopped += 1

    def stop(self):
        self.stopped += 1


def test_speech_output_humanizes_text_but_keeps_the_full_response():
    seen = []
    backend = _FakeBackend()
    output = SpeechOutput(
        enabled=True, backend=backend, on_utterance=seen.append
    )
    try:
        output.say("**Ready** & waiting.")
        assert output.wait_until_quiet(5.0)
        assert backend.said == ["Ready and waiting."]
        assert seen == ["**Ready** & waiting."]
        assert output.last_spoken == "**Ready** & waiting."
    finally:
        output.stop()


def test_stop_speaking_drops_the_queue_and_interrupts_the_backend():
    backend = _FakeBackend()
    output = SpeechOutput(enabled=False, backend=backend)
    try:
        output.say("A sentence that will never play.")
        output.stop_speaking()
        assert backend.stopped == 1
        assert output.is_speaking is False
    finally:
        output.stop()


def test_auto_prefers_kokoro_when_the_model_is_installed(monkeypatch):
    class FakeWindows(_FakeBackend):
        def voice_details(self, timeout=15.0):
            return []

    created = []

    def fake_create(kind, on_error=None):
        backend = KokoroBackend(on_error) if kind == BACKEND_KOKORO else FakeWindows()
        created.append(kind)
        return backend

    monkeypatch.setattr(speech, "create_backend", fake_create)
    monkeypatch.setattr(neural_tts, "available", lambda: True)
    output = SpeechOutput(enabled=False, backend_kind=speech.BACKEND_AUTO)
    try:
        backend, active, natural, note = output._build_backend(speech.BACKEND_AUTO)
        assert isinstance(backend, KokoroBackend)
        assert active == BACKEND_KOKORO
        assert natural is True
        assert "Kokoro" in note
        assert BACKEND_KOKORO in created
    finally:
        output.stop()

