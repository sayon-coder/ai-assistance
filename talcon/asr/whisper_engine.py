"""Optional faster-whisper pass for long, natural-language questions only.

Streaming models answer commands quickly; a 0.6 s-to-1 s second pass is
acceptable when the user clearly asked a long question ("explain…",
"what is…").  Commands never take this path, so recognition latency for
"open notepad" is unchanged.
"""
from __future__ import annotations

import logging
import re
import threading
from pathlib import Path

from .base import pcm_to_float32

log = logging.getLogger("talcon.asr.whisper")

#: A question must start with one of these to justify the extra pass.
LONG_QUESTION_PREFIXES = (
    "explain", "describe", "summarise", "summarize", "compare",
    "what is", "what's", "whats", "what are", "who is", "who was",
    "why is", "why does", "why do", "how does", "how do", "how can",
    "tell me about", "give me", "walk me through",
)
MIN_WORDS = 4
MAX_SECONDS = 30.0


def package_available() -> bool:
    try:
        import faster_whisper  # noqa: F401
    except Exception:
        return False
    return True


def resolve_model(value: str | None) -> str:
    """Return a local model folder when it exists, otherwise the model name."""
    text = str(value or "").strip()
    if not text:
        text = "small.en"
    path = Path(text).expanduser()
    if path.is_dir():
        return str(path)
    from ..config import resolve_path

    candidate = resolve_path(text)
    if candidate.is_dir():
        return str(candidate)
    return text


def model_ready(value: str | None) -> bool:
    """True when an explicit local model folder is present.

    A bare model name (for example ``small.en``) is still usable: the
    ``faster-whisper`` package downloads it on first use.  That is reported as
    "not installed locally" so the UI can stay honest about the network.
    """
    text = str(value or "").strip()
    if not text:
        return False
    path = Path(text).expanduser()
    if path.is_dir():
        return any(path.glob("*.bin")) or any(path.glob("*.ctranslate2"))
    from ..config import resolve_path

    candidate = resolve_path(text)
    return candidate.is_dir()


def is_long_question(text: str) -> bool:
    """Conservative prefix check; short commands are never eligible."""
    phrase = " ".join(re.findall(r"[a-z0-9']+", str(text or "").lower()))
    if not phrase:
        return False
    words = phrase.split()
    if len(words) < MIN_WORDS:
        return False
    return any(phrase == prefix or phrase.startswith(prefix + " ")
               for prefix in LONG_QUESTION_PREFIXES)


class LongQuestionTranscriber:
    """Lazily loaded faster-whisper model, intended for one-off long questions."""

    def __init__(self, model: str = "small.en", compute_type: str = "int8",
                 language: str = "en", beam_size: int = 1):
        self.model_name = resolve_model(model)
        self.compute_type = str(compute_type or "int8")
        self.language = str(language or "en")
        self.beam_size = max(1, int(beam_size))
        self._model = None
        self._lock = threading.Lock()

    @property
    def unavailable_reason(self) -> str:
        if not package_available():
            return "faster-whisper is not installed"
        if not model_ready(self.model_name) and Path(self.model_name).suffix == "":
            return f"{self.model_name} will download on first use"
        return ""

    def available(self) -> bool:
        return package_available()

    def transcribe(self, pcm: bytes) -> str:
        if not pcm or not package_available():
            return ""
        try:
            with self._lock:
                if self._model is None:
                    from faster_whisper import WhisperModel

                    log.info("Loading faster-whisper model %s (%s)",
                             self.model_name, self.compute_type)
                    self._model = WhisperModel(
                        self.model_name, device="cpu", compute_type=self.compute_type
                    )
                audio = pcm_to_float32(pcm)
                segments, _info = self._model.transcribe(
                    audio,
                    language=self.language,
                    beam_size=self.beam_size,
                    vad_filter=False,
                    condition_on_previous_text=False,
                )
                return " ".join(
                    str(getattr(segment, "text", "") or "").strip()
                    for segment in segments
                ).strip()
        except Exception:
            log.exception("faster-whisper long-question pass failed")
            self._model = None
            return ""
