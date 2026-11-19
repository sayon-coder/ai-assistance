"""Pluggable offline speech-recognition engines for TALCON.

The live voice loop only needs the small Vosk-shaped interface in
:mod:`talcon.asr.base`.  :mod:`talcon.asr.registry` chooses Vosk or
sherpa-onnx from settings and falls back safely; optional long-question and
experimental engines live in their own modules.
"""
from .base import AsrSegment, BaseRecognizer, pcm_to_float32
from .registry import (
    ENGINE_AUTO,
    ENGINE_CHOICES,
    ENGINE_LABELS,
    ENGINE_SHERPA,
    ENGINE_VOSK,
    available_engines,
    create_streaming_recognizer,
    engine_model_ready,
    engine_reason,
    normalize_engine,
    resolve_engine,
)

__all__ = [
    "AsrSegment",
    "BaseRecognizer",
    "pcm_to_float32",
    "ENGINE_AUTO",
    "ENGINE_CHOICES",
    "ENGINE_LABELS",
    "ENGINE_SHERPA",
    "ENGINE_VOSK",
    "available_engines",
    "create_streaming_recognizer",
    "engine_model_ready",
    "engine_reason",
    "normalize_engine",
    "resolve_engine",
]
