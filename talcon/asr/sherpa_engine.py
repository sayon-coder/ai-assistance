"""sherpa-onnx streaming recogniser (optional dependency).

sherpa-onnx ships small int8 streaming transducers that beat the Vosk *small*
model on free-form speech while still running on a 4-core CPU.  This adapter
keeps Vosk's call shape so the voice pipeline does not change::

    if recognizer.AcceptWaveform(pcm):
        payload = json.loads(recognizer.Result())
    payload = json.loads(recognizer.FinalResult())

Streaming transducers have no phrase-list grammar and do not expose word
confidence.  The voice layer already treats a missing confidence as "unknown"
and only executes an exact registered command, so commands keep working while
natural speech is decoded more accurately.
"""
from __future__ import annotations

import json
import logging
import os
from pathlib import Path

from .base import AsrSegment, BaseRecognizer, pcm_to_float32

log = logging.getLogger("talcon.asr.sherpa")

SAMPLE_RATE = 16000


def package_available() -> bool:
    try:
        import sherpa_onnx  # noqa: F401
    except Exception:
        return False
    return True


def model_ready(model_dir: str | os.PathLike) -> bool:
    """A transducer directory needs encoder, decoder, joiner and tokens."""
    path = Path(model_dir)
    if not path.is_dir():
        return False
    names = {item.name for item in path.iterdir() if item.is_file()}
    return (
        any(n.startswith("encoder") and n.endswith(".onnx") for n in names)
        and any("decoder" in n and n.endswith(".onnx") for n in names)
        and any("joiner" in n and n.endswith(".onnx") for n in names)
        and "tokens.txt" in names
    )


def resolve_model_path(value: str | os.PathLike | None = None) -> Path:
    """Resolve the configured model folder (relative paths live beside TALCON)."""
    from ..config import MODELS_DIR, resolve_path

    if value:
        return resolve_path(value)
    return MODELS_DIR / "sherpa-onnx-streaming-zipformer-en-2023-06-26"


def _pick(names: list[str], prefix: str) -> str | None:
    candidates = [name for name in names if name.startswith(prefix) and name.endswith(".onnx")]
    if not candidates:
        return None
    # Prefer the quantised weights: they are the only practical CPU choice.
    candidates.sort(key=lambda name: (0 if "int8" in name else 1, len(name), name))
    return candidates[0]


def model_files(model_dir: str | os.PathLike) -> dict[str, str]:
    path = Path(model_dir)
    names = [item.name for item in path.iterdir() if item.is_file()]
    encoder = _pick(names, "encoder")
    decoder = _pick(names, "decoder")
    joiner = _pick(names, "joiner")
    tokens = "tokens.txt" if "tokens.txt" in names else None
    if not all((encoder, decoder, joiner, tokens)):
        raise FileNotFoundError(
            f"sherpa-onnx transducer files are incomplete in {path}"
        )
    return {
        "encoder": str(path / encoder),
        "decoder": str(path / decoder),
        "joiner": str(path / joiner),
        "tokens": str(path / tokens),
    }


def describe_model(model_dir: str | os.PathLike) -> str:
    path = Path(model_dir)
    if not model_ready(path):
        return f"not installed at {path}"
    return path.name


def create_recognizer(model_dir: str | os.PathLike, *, num_threads: int = 2,
                      trailing_silence: float = 1.0,
                      max_utterance: float = 20.0):
    """Build a sherpa-onnx ``OnlineRecognizer`` from a transducer folder."""
    import sherpa_onnx

    files = model_files(model_dir)
    kwargs = {
        "tokens": files["tokens"],
        "encoder": files["encoder"],
        "decoder": files["decoder"],
        "joiner": files["joiner"],
        "num_threads": max(1, int(num_threads)),
        "sample_rate": SAMPLE_RATE,
        "feature_dim": 80,
        "decoding_method": "greedy_search",
        "enable_endpoint_detection": True,
        "rule1_min_trailing_silence": max(0.3, min(5.0, float(trailing_silence))),
        "rule2_min_trailing_silence": max(0.2, min(3.0, float(trailing_silence) * 0.5)),
        "rule3_min_utterance_length": max(5.0, min(60.0, float(max_utterance))),
    }
    try:
        return sherpa_onnx.OnlineRecognizer.from_transducer(**kwargs)
    except TypeError:
        # Older sherpa-onnx releases did not accept endpoint options here.
        for key in (
            "enable_endpoint_detection", "rule1_min_trailing_silence",
            "rule2_min_trailing_silence", "rule3_min_utterance_length",
        ):
            kwargs.pop(key, None)
        recognizer = sherpa_onnx.OnlineRecognizer.from_transducer(**kwargs)
        for attribute, value in (
            ("enable_endpoint_detection", True),
            ("rule1_min_trailing_silence", float(trailing_silence)),
            ("rule2_min_trailing_silence", float(trailing_silence) * 0.5),
            ("rule3_min_utterance_length", float(max_utterance)),
        ):
            try:
                setattr(recognizer, attribute, value)
            except Exception:
                log.debug("sherpa-onnx endpoint setting %s is unsupported", attribute)
        return recognizer


class SherpaRecognizer(BaseRecognizer):
    """Vosk-shaped adapter around an ``OnlineRecognizer`` + single stream."""

    name = "sherpa"
    streaming = True

    def __init__(self, recognizer, sample_rate: int = SAMPLE_RATE):
        self._recognizer = recognizer
        self.sample_rate = int(sample_rate)
        self._stream = recognizer.create_stream()
        self._pending: AsrSegment | None = None
        self._last_text = ""

    # -- helpers -----------------------------------------------------------
    def _decode(self) -> None:
        ready = getattr(self._recognizer, "is_ready", None)
        decoder = getattr(self._recognizer, "decode_stream", None)
        if not callable(ready) or not callable(decoder):
            return
        guard = 0
        while ready(self._stream) and guard < 1000:
            decoder(self._stream)
            guard += 1

    def _text(self) -> str:
        getter = getattr(self._recognizer, "get_result", None)
        if not callable(getter):
            return ""
        try:
            result = getter(self._stream)
        except Exception:
            log.debug("sherpa-onnx get_result failed", exc_info=True)
            return ""
        if isinstance(result, str):
            return result.strip()
        return str(getattr(result, "text", "") or "").strip()

    def _endpoint(self) -> bool:
        check = getattr(self._recognizer, "is_endpoint", None)
        if not callable(check):
            return False
        try:
            return bool(check(self._stream))
        except Exception:
            return False

    def _new_stream(self) -> None:
        self._stream = self._recognizer.create_stream()

    # -- Vosk API ----------------------------------------------------------
    def AcceptWaveform(self, pcm: bytes) -> bool:  # noqa: N802 - Vosk API
        samples = pcm_to_float32(pcm)
        if not len(samples):
            return False
        self._stream.accept_waveform(self.sample_rate, samples)
        self._decode()
        if not self._endpoint():
            return False
        text = self._text()
        if not text:
            # Endpoint on leading silence: keep the stream.  Resetting here
            # drops the first syllable of the command that follows.
            return False
        self._new_stream()
        self._last_text = text
        self._pending = AsrSegment(text=text)
        return True

    def Result(self) -> str:  # noqa: N802 - Vosk API
        segment = self._pending or AsrSegment(text=self._last_text)
        self._pending = None
        return segment.to_json()

    def PartialResult(self) -> str:  # noqa: N802 - Vosk API
        self._decode()
        return json.dumps({"partial": self._text()})

    def FinalResult(self) -> str:  # noqa: N802 - Vosk API
        pending = self._pending
        self._pending = None
        if pending is not None and pending.text:
            self._new_stream()
            self._last_text = pending.text
            return pending.to_json()
        # Do not append artificial silence: streaming transducers already
        # decode the available frames, and padding silence made this model
        # hallucinate extra words after a short command.
        self._decode()
        text = self._text()
        self._new_stream()
        self._last_text = text
        return AsrSegment(text=text).to_json()

    def Reset(self) -> None:  # noqa: N802 - Vosk API
        self._pending = None
        self._last_text = ""
        self._new_stream()
