"""Pluggable speech-recognition engines.

TALCON's command pipeline talks to a Vosk-shaped recogniser::

    if recognizer.AcceptWaveform(pcm):   # True when one segment is final
        payload = json.loads(recognizer.Result())
    payload = json.loads(recognizer.FinalResult())

Any engine that implements those three calls (plus ``Reset``) can replace
Vosk without touching the voice, grammar or command layers.  Streaming engines
report segments as they settle; batch engines buffer the utterance and answer
in ``FinalResult``.
"""
from __future__ import annotations

import array
import json
from dataclasses import dataclass, field


@dataclass
class AsrSegment:
    """One decoded utterance, independent of the engine that produced it."""

    text: str = ""
    confidence: float | None = None
    alternatives: list[str] = field(default_factory=list)
    words: list[str] = field(default_factory=list)

    def to_payload(self) -> dict:
        """Vosk-compatible JSON shape consumed by ``talcon.voice``."""
        payload: dict = {"text": self.text}
        if self.confidence is not None:
            payload["confidence"] = float(self.confidence)
        if self.alternatives:
            payload["alternatives"] = [
                {"text": item, "confidence": self.confidence}
                for item in self.alternatives
            ]
        return payload

    def to_json(self) -> str:
        return json.dumps(self.to_payload())


class BaseRecognizer:
    """The duck-typed interface the voice pipeline already expects."""

    name = "base"
    streaming = False

    def AcceptWaveform(self, pcm: bytes) -> bool:  # noqa: N802 - Vosk API
        raise NotImplementedError

    def Result(self) -> str:  # noqa: N802 - Vosk API
        return AsrSegment().to_json()

    def PartialResult(self) -> str:  # noqa: N802 - Vosk API
        return json.dumps({"partial": ""})

    def FinalResult(self) -> str:  # noqa: N802 - Vosk API
        return AsrSegment().to_json()

    def Reset(self) -> None:  # noqa: N802 - Vosk API
        pass

    # Optional Vosk configuration calls; ignored by engines that lack them.
    def SetMaxAlternatives(self, count: int) -> None:  # noqa: N802
        del count

    def SetWords(self, enabled: bool) -> None:  # noqa: N802
        del enabled

    def SetGrammar(self, grammar: str) -> None:  # noqa: N802
        del grammar


def pcm_to_float32(pcm: bytes):
    """Convert 16-bit mono PCM to the float32 range sherpa/Whisper expect."""
    import numpy as np

    usable = len(pcm) - (len(pcm) % 2)
    if usable <= 0:
        return np.zeros(0, dtype=np.float32)
    # ``frombuffer`` is zero-copy; the division materialises a writable array.
    return np.frombuffer(pcm[:usable], dtype="<i2").astype(np.float32) / 32768.0


def pcm_to_int16_list(pcm: bytes) -> list[int]:
    """Small helper for engines that want plain Python samples."""
    samples = array.array("h")
    samples.frombytes(pcm[: len(pcm) - (len(pcm) % 2)])
    return list(samples)
