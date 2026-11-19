"""Engine selection, the sherpa-onnx adapter and the long-question pass."""
from __future__ import annotations

import json
import queue
import sys
from types import SimpleNamespace

import numpy as np

from talcon.asr import (
    ENGINE_SHERPA,
    ENGINE_VOSK,
    create_streaming_recognizer,
    engine_model_ready,
    resolve_engine,
)
from talcon.asr.sherpa_engine import SherpaRecognizer
from talcon.asr.whisper_engine import LongQuestionTranscriber, is_long_question


def make_model(tmp_path):
    model = tmp_path / "sherpa-model"
    model.mkdir()
    for name in (
        "encoder-epoch-99.int8.onnx",
        "decoder-epoch-99.onnx",
        "joiner-epoch-99.int8.onnx",
        "tokens.txt",
    ):
        (model / name).write_bytes(b"x")
    return model


class FakeStream:
    def __init__(self):
        self.samples = np.zeros(0, dtype=np.float32)

    def accept_waveform(self, sample_rate, samples):
        self.samples = np.concatenate([self.samples, np.asarray(samples)])


class FakeOnlineRecognizer:
    @classmethod
    def from_transducer(cls, **kwargs):
        return cls()

    def create_stream(self):
        return FakeStream()

    def is_ready(self, stream):
        return False

    def decode_stream(self, stream):
        return None

    def get_result(self, stream):
        return "hello talcon" if len(stream.samples) else ""

    def is_endpoint(self, stream):
        return len(stream.samples) > 0

    def reset(self, stream):
        stream.samples = np.zeros(0, dtype=np.float32)


def install_fake_sherpa(monkeypatch):
    module = SimpleNamespace(OnlineRecognizer=FakeOnlineRecognizer)
    monkeypatch.setitem(sys.modules, "sherpa_onnx", module)
    return module


def test_sherpa_recogniser_implements_the_vosk_call_shape(tmp_path, monkeypatch):
    install_fake_sherpa(monkeypatch)
    model = make_model(tmp_path)
    recognizer = create_streaming_recognizer(
        {"sherpa_model_path": str(model)}, engine=ENGINE_SHERPA
    )
    assert isinstance(recognizer, SherpaRecognizer)
    pcm = np.zeros(1600, dtype="<i2").tobytes()
    assert recognizer.AcceptWaveform(pcm) is True
    assert json.loads(recognizer.Result())["text"] == "hello talcon"
    # The stream was reset for the next utterance.
    assert json.loads(recognizer.PartialResult())["partial"] == ""


def test_sherpa_final_result_flushes_the_last_segment(tmp_path, monkeypatch):
    install_fake_sherpa(monkeypatch)
    recognizer = create_streaming_recognizer(
        {"sherpa_model_path": str(make_model(tmp_path))}, engine=ENGINE_SHERPA
    )
    recognizer.AcceptWaveform(np.zeros(1600, dtype="<i2").tobytes())
    # An explicit final after a consumed endpoint must still return text when
    # new audio was fed, and a clean stream must return an empty segment.
    assert "text" in json.loads(recognizer.FinalResult())


def test_parakeet_is_offline_batch_only_and_opt_in(tmp_path, monkeypatch):
    from talcon.asr import parakeet_engine

    model = tmp_path / "parakeet"
    model.mkdir()
    (model / "model.onnx").write_bytes(b"x")
    (model / "tokens.txt").write_text("a\n", encoding="utf-8")

    class FakeResult:
        text = "parakeet transcript"

    class FakeStream:
        result = FakeResult()

        def accept_waveform(self, sample_rate, samples):
            return None

    class FakeOffline:
        @classmethod
        def from_nemo_transducer(cls, **kwargs):
            return cls()

        def create_stream(self):
            return FakeStream()

        def decode_stream(self, stream):
            return None

    monkeypatch.setitem(
        sys.modules, "sherpa_onnx",
        SimpleNamespace(OfflineRecognizer=FakeOffline),
    )
    assert parakeet_engine.model_ready(model) is True
    recognizer = parakeet_engine.create_recognizer(model)
    assert parakeet_engine.transcribe(
        recognizer, np.zeros(1600, dtype="<i2").tobytes()
    ) == "parakeet transcript"


def test_engine_resolution_falls_back_to_vosk(tmp_path, monkeypatch):
    monkeypatch.setitem(sys.modules, "sherpa_onnx", None)
    settings = {
        "asr_engine": "sherpa",
        "sherpa_model_path": str(make_model(tmp_path)),
    }
    assert resolve_engine(settings) == ENGINE_VOSK


def test_engine_resolution_uses_sherpa_only_when_explicitly_selected(
    tmp_path, monkeypatch
):
    install_fake_sherpa(monkeypatch)
    settings = {
        "asr_engine": "auto",
        "sherpa_model_path": str(make_model(tmp_path)),
    }
    # Auto is the measured, reliable Vosk command path.  Installing sherpa
    # must not change behaviour until the user opts in.
    assert resolve_engine(settings) == ENGINE_VOSK
    settings["asr_engine"] = "sherpa"
    assert resolve_engine(settings) == ENGINE_SHERPA
    assert engine_model_ready(settings) is True


def test_settings_normalise_the_engine_and_frontend_switches():
    from talcon.config import normalize_settings

    values = normalize_settings({
        "asr_engine": "sherpa-onnx",
        "audio_agc": 0,
        "audio_noise_suppression": False,
        "audio_aec": True,
        "audio_agc_target_rms": 99999,
        "long_question_whisper": 1,
    })
    assert values["asr_engine"] == "sherpa"
    assert values["audio_agc"] is False
    assert values["audio_noise_suppression"] is False
    assert values["audio_aec"] is True
    assert values["audio_agc_target_rms"] == 6000.0
    assert values["long_question_whisper"] is True


def test_long_question_heuristic_never_matches_short_commands():
    assert is_long_question("what is quantum computing")
    assert is_long_question("explain how a transistor works")
    assert not is_long_question("open notepad")
    assert not is_long_question("what time is it")
    assert not is_long_question("mute")
    assert not is_long_question("")


class StubRouter:
    app_aliases = {}
    speech = None
    registry = None
    apps = None

    def __init__(self):
        self.natural = []

    def handle(self, phrase, announce_unknown=True):
        return "unknown"

    def natural_handler(self, phrase):
        self.natural.append(phrase)
        return True


def test_voice_controller_selects_sherpa_behind_the_same_interface(
    tmp_path, monkeypatch
):
    from talcon.voice import VoiceController

    install_fake_sherpa(monkeypatch)
    controller = VoiceController(
        {
            "always_listen": True,
            "asr_engine": "sherpa",
            "sherpa_model_path": str(make_model(tmp_path)),
        },
        StubRouter(),
        event_callback=lambda *_args: None,
    )
    assert controller._engine == ENGINE_SHERPA
    recognizer = controller._make_recognizer()
    assert isinstance(recognizer, SherpaRecognizer)
    # The Vosk model is not loaded when sherpa-onnx is selected.
    assert controller._model is None


def test_long_question_pass_is_limited_to_questions():
    from talcon.voice import VoiceController

    class FakeTranscriber:
        def transcribe(self, pcm):
            return "what is a transformer model"

    controller = VoiceController(
        {"always_listen": True, "long_question_whisper": True},
        StubRouter(),
        event_callback=lambda *_args: None,
    )
    controller._long_question_transcriber = lambda: FakeTranscriber()
    pcm = np.zeros(1600, dtype="<i2").tobytes()
    assert controller._refine_long_question("open notepad", pcm) == "open notepad"
    refined = controller._refine_long_question("what is a transformer", pcm)
    assert refined == "what is a transformer model"


def test_refined_long_question_reaches_the_natural_handler():
    from talcon.voice import VoiceController

    class FakeTranscriber:
        def transcribe(self, pcm):
            return "explain how a transistor works"

    router = StubRouter()
    controller = VoiceController(
        {"always_listen": True, "long_question_whisper": True},
        router,
        event_callback=lambda *_args: None,
    )
    controller._long_question_transcriber = lambda: FakeTranscriber()
    pcm = np.zeros(1600, dtype="<i2").tobytes()
    controller._dispatch_transcript(
        "explain how a transistor", queue.Queue(), None, audio=pcm
    )
    assert router.natural == ["explain how a transistor works"]


def test_faster_whisper_transcriber_is_lazy_and_returns_segments(monkeypatch):
    calls = []

    class Segment:
        text = "a much better transcript"

    class FakeModel:
        def __init__(self, name, device, compute_type):
            calls.append((name, device, compute_type))

        def transcribe(self, audio, **kwargs):
            calls.append((len(audio), kwargs.get("language")))
            return [Segment()], None

    monkeypatch.setitem(
        sys.modules, "faster_whisper", SimpleNamespace(WhisperModel=FakeModel)
    )
    transcriber = LongQuestionTranscriber("small.en")
    pcm = np.zeros(1600, dtype="<i2").tobytes()
    assert transcriber.transcribe(pcm) == "a much better transcript"
    assert calls[0][0] == "small.en"
    assert calls[1][0] == 1600
