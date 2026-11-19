"""Experimental Parakeet batch engine through sherpa-onnx (offline models).

Parakeet is not used by the live voice loop.  It is offered only behind the
benchmark's ``--engine parakeet`` flag and an explicitly supplied model folder,
so a missing download can never break normal recognition.
"""
from __future__ import annotations

import logging
import os
from pathlib import Path

from .base import pcm_to_float32

log = logging.getLogger("talcon.asr.parakeet")

SAMPLE_RATE = 16000


def package_available() -> bool:
    try:
        import sherpa_onnx  # noqa: F401
    except Exception:
        return False
    return True


def model_ready(model_path: str | os.PathLike) -> bool:
    path = Path(model_path)
    if path.is_file() and path.suffix == ".onnx":
        return path.stat().st_size > 0
    if not path.is_dir():
        return False
    names = {item.name for item in path.iterdir() if item.is_file()}
    has_model = "model.onnx" in names or any(name.endswith(".onnx") for name in names)
    return has_model and "tokens.txt" in names


def _model_file(model_path: str | os.PathLike) -> str:
    path = Path(model_path)
    if path.is_file():
        return str(path)
    preferred = path / "model.onnx"
    if preferred.is_file():
        return str(preferred)
    candidates = sorted(path.glob("*.onnx"))
    if not candidates:
        raise FileNotFoundError(f"No Parakeet ONNX model in {path}")
    return str(candidates[0])


def create_recognizer(model_path: str | os.PathLike, num_threads: int = 2):
    import sherpa_onnx

    return sherpa_onnx.OfflineRecognizer.from_nemo_transducer(
        model=_model_file(model_path),
        num_threads=max(1, int(num_threads)),
        decoding_method="greedy_search",
    )


def transcribe(recognizer, pcm: bytes) -> str:
    samples = pcm_to_float32(pcm)
    if not len(samples):
        return ""
    stream = recognizer.create_stream()
    stream.accept_waveform(SAMPLE_RATE, samples)
    recognizer.decode_stream(stream)
    result = stream.result
    return str(getattr(result, "text", "") or "").strip()
