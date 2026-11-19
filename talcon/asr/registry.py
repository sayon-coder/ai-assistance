"""Engine selection and fallback rules for TALCON's speech recognition.

Selection is intentionally boring:

* ``auto`` uses sherpa-onnx only when the package *and* the model folder are
  both present; otherwise Vosk.
* ``sherpa`` asks for sherpa-onnx and quietly falls back to Vosk (with a
  reason) when it cannot run.
* ``vosk`` never touches sherpa-onnx.

The configured engine is read from settings, so the microphone pipeline can be
switched without changing the ``VoiceController`` interface.
"""
from __future__ import annotations

import logging
from pathlib import Path

log = logging.getLogger("talcon.asr.registry")

ENGINE_AUTO = "auto"
ENGINE_VOSK = "vosk"
ENGINE_SHERPA = "sherpa"
ENGINE_LABELS = {
    ENGINE_AUTO: "Automatic (Vosk; select sherpa-onnx explicitly)",
    ENGINE_VOSK: "Vosk (small English model)",
    ENGINE_SHERPA: "sherpa-onnx streaming transformer (opt-in)",
}
ENGINE_CHOICES = (ENGINE_AUTO, ENGINE_VOSK, ENGINE_SHERPA)


def normalize_engine(value) -> str:
    text = str(value or "").strip().lower().replace("_", "-")
    if text in {"sherpa", "sherpa-onnx", "onnx"}:
        return ENGINE_SHERPA
    if text in {"vosk", "kaldi"}:
        return ENGINE_VOSK
    return ENGINE_AUTO


def sherpa_model_path(settings: dict | None = None) -> Path:
    from . import sherpa_engine

    values = dict(settings or {})
    return sherpa_engine.resolve_model_path(values.get("sherpa_model_path"))


def engine_model_ready(settings: dict | None) -> bool:
    """True when the *resolved* engine has a usable model on this PC."""
    from . import sherpa_engine

    values = dict(settings or {})
    engine = resolve_engine(values)
    if engine == ENGINE_SHERPA:
        return sherpa_engine.package_available() and sherpa_engine.model_ready(
            sherpa_model_path(values)
        )
    try:
        from ..model_setup import model_is_ready, resolve_model

        return model_is_ready(resolve_model(values.get("vosk_model_path")))
    except Exception:
        return False


def engine_reason(settings: dict | None, engine: str | None = None) -> str:
    """Human-readable explanation of the current selection."""
    from . import sherpa_engine

    values = dict(settings or {})
    requested = normalize_engine(values.get("asr_engine"))
    engine = engine or resolve_engine(values)
    if engine == ENGINE_SHERPA:
        if not sherpa_engine.package_available():
            return "sherpa-onnx package is not installed"
        path = sherpa_model_path(values)
        if not sherpa_engine.model_ready(path):
            return f"sherpa-onnx model is missing at {path}"
        return f"sherpa-onnx · {path.name}"
    if requested == ENGINE_SHERPA:
        if not sherpa_engine.package_available():
            return "sherpa-onnx unavailable; using Vosk"
        path = sherpa_model_path(values)
        if not sherpa_engine.model_ready(path):
            return f"sherpa-onnx model missing at {path}; using Vosk"
    return "Vosk · small English model"


def resolve_engine(settings: dict | None) -> str:
    """Return ``vosk`` or ``sherpa`` for this process."""
    from . import sherpa_engine

    values = dict(settings or {})
    requested = normalize_engine(values.get("asr_engine"))
    if requested in (ENGINE_VOSK, ENGINE_AUTO):
        # Automatic means the measured, reliable command path.  Installing
        # sherpa-onnx must never silently change command accuracy; the user
        # opts in with asr_engine="sherpa" after reading the benchmark.
        return ENGINE_VOSK
    available = sherpa_engine.package_available()
    path = sherpa_model_path(values)
    ready = available and sherpa_engine.model_ready(path)
    if ready:
        return ENGINE_SHERPA
    if requested == ENGINE_SHERPA:
        reasons = []
        if not available:
            reasons.append("sherpa-onnx package not installed")
        if not sherpa_engine.model_ready(path):
            reasons.append(f"model missing at {path}")
        log.warning("sherpa-onnx requested but unavailable (%s); using Vosk",
                    ", ".join(reasons) or "unknown reason")
    return ENGINE_VOSK


def available_engines(settings: dict | None = None) -> dict[str, bool]:
    from . import parakeet_engine, sherpa_engine

    values = dict(settings or {})
    path = sherpa_model_path(values)
    return {
        ENGINE_VOSK: True,
        ENGINE_SHERPA: sherpa_engine.package_available()
        and sherpa_engine.model_ready(path),
        "parakeet": parakeet_engine.package_available(),
    }


def create_streaming_recognizer(settings: dict | None, *, engine: str,
                                trailing_silence: float = 1.0,
                                max_utterance: float = 20.0):
    """Build a Vosk-shaped recogniser for a non-Vosk engine."""
    if engine != ENGINE_SHERPA:
        raise ValueError(f"Unsupported streaming engine: {engine}")
    from . import sherpa_engine

    recognizer = sherpa_engine.create_recognizer(
        sherpa_model_path(settings),
        trailing_silence=trailing_silence,
        max_utterance=max_utterance,
    )
    return sherpa_engine.SherpaRecognizer(recognizer)
