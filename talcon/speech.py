"""Speech output through the local Kokoro-82M neural voice.

TALCON uses exactly one synthesis engine: the free, Apache-2.0 Kokoro-82M
model in ``models/kokoro``, run on the CPU.  Microsoft SAPI and Windows
Runtime voices are not used at all.  If the model or the optional
``kokoro-onnx`` package is missing, TALCON reports that honestly instead of
falling back to a robotic voice.
"""
from __future__ import annotations

import logging
import queue
import threading
import time
from dataclasses import dataclass

from . import neural_tts
from .neural_tts import DEFAULT_SENTENCE_PAUSE, model_paths

log = logging.getLogger("talcon.speech")

#: There is a single engine.  ``auto`` is kept as an accepted saved value.
BACKEND_AUTO = "kokoro"
BACKEND_KOKORO = "kokoro"

SPEECH_BACKEND_OPTIONS = (
    (BACKEND_KOKORO, "Kokoro Neural Voice (offline, on-device)"),
)
SPEECH_BACKEND_LABELS = dict(SPEECH_BACKEND_OPTIONS)

ENGINE_KOKORO = "kokoro"
ENGINE_LABELS = {ENGINE_KOKORO: "Kokoro Neural Voice"}

NATURAL_PREVIEW = (
    "Hello, sir. I'm TALCON, your personal desktop assistant. "
    "How can I help you today?"
)

SPEECH_MAX_CHARS = 700


def normalize_backend(value) -> str:
    """There is one engine; every saved value maps to it."""
    return BACKEND_KOKORO


def create_backend(kind: str = BACKEND_KOKORO, on_error=None):
    """Create the Kokoro backend (kept as a seam for tests)."""
    return neural_tts.create_backend(kind, on_error=on_error)


@dataclass(frozen=True)
class VoiceInfo:
    """Truthful description of one installed Kokoro voice."""

    id: str
    name: str
    engine: str = ENGINE_KOKORO
    language: str = "en-GB"
    gender: str = ""
    description: str = ""
    backend: str = BACKEND_KOKORO
    voice_type: str = "Neural"
    available: bool = True
    source: str = "kokoro"
    ssml: bool = False

    @property
    def natural(self) -> bool:
        return True

    @property
    def offline(self) -> bool:
        return True

    @property
    def engine_label(self) -> str:
        return "Kokoro Neural Voice"

    @property
    def backend_label(self) -> str:
        return "Kokoro (offline)"

    @property
    def language_label(self) -> str:
        return language_label(self.language)

    @property
    def label(self) -> str:
        pieces = [self.engine_label]
        if self.language:
            pieces.append(self.language_label)
        if self.gender:
            pieces.append(self.gender)
        return " - ".join(pieces)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "engine": self.engine,
            "engine_label": self.engine_label,
            "backend": self.backend,
            "backend_label": self.backend_label,
            "language": self.language,
            "language_label": self.language_label,
            "gender": self.gender,
            "natural": self.natural,
            "offline": self.offline,
            "description": self.description,
            "voice_type": self.voice_type,
            "available": self.available,
            "source": self.source,
            "ssml": self.ssml,
            "label": self.label,
        }


def language_label(tag: str) -> str:
    """Turn a BCP-47 tag into a friendly label."""
    raw = str(tag or "").strip()
    if not raw:
        return "Unknown language"
    names = {"en": "English", "hi": "Hindi", "es": "Spanish", "fr": "French"}
    regions = {"IN": "India", "US": "United States", "GB": "United Kingdom"}
    parts = raw.replace("_", "-").split("-")
    base = names.get(parts[0].lower(), parts[0].upper())
    if len(parts) > 1:
        region = parts[-1].upper()
        return f"{base} ({regions.get(region, region)})"
    return base


def spoken_excerpt(text: str, limit: int = SPEECH_MAX_CHARS) -> str:
    """Trim to a sentence boundary so playback always ends on a whole thought."""
    text = str(text or "")
    if len(text) <= limit:
        return text
    window = text[:limit]
    floor = int(limit * 0.45)
    for separator in (". ", "! ", "? ", "; ", ", "):
        index = window.rfind(separator)
        if index >= floor:
            return window[: index + 1].strip()
    return window.rsplit(" ", 1)[0].strip() or window.strip()


def humanize_for_speech(text: str) -> str:
    """Turn written shorthand into words a speaker would actually say.

    This is deterministic and keeps the meaning; the complete original text is
    still what the response preview and History show.
    """
    import re

    clean = str(text or "")
    if not clean:
        return ""
    replacements = (
        (r"\*\*(.+?)\*\*", r"\1"),
        (r"`([^`]*)`", r"\1"),
        (r"\[([^\]]+)\]\([^)]*\)", r"\1"),
        (r"(?i)\be\.g\.\s*", "for example, "),
        (r"(?i)\bi\.e\.\s*", "that is, "),
        (r"(?i)\bvs\.?\s*", "versus "),
        (r"(?i)\betc\.?", "et cetera"),
        (r"(?i)\bapprox\.?\s*", "approximately "),
        (r"\s*&\s*", " and "),
        (r"\s*[|]\s*", ", "),
        (r"[*_#]+", " "),
    )
    for pattern, replacement in replacements:
        clean = re.sub(pattern, replacement, clean)
    return " ".join(clean.split()).strip()


def select_voice(voices, voice_id: str = "", voice_name: str = ""):
    """Choose a voice by id, then name, else the first curated voice."""
    voices = list(voices or [])
    if not voices:
        return None, "No Kokoro voices are available; install the voice model."
    wanted_id = str(voice_id or "").strip()
    if wanted_id:
        for voice in voices:
            if voice.id == wanted_id:
                return voice, ""
    wanted_name = str(voice_name or "").strip().lower()
    if wanted_name:
        for voice in voices:
            if voice.name.lower() == wanted_name or voice.id.lower() == wanted_name:
                return voice, ""
    if wanted_id or wanted_name:
        missing = wanted_id or voice_name
        return voices[0], f"The saved voice “{missing}” is not installed."
    return voices[0], ""


def _backend_voice_list(backend) -> list[VoiceInfo]:
    try:
        lister = getattr(backend, "voice_details", None)
        if callable(lister):
            return list(lister())
        return [
            VoiceInfo(id=str(name), name=str(name))
            for name in backend.voices()
        ]
    except Exception:
        log.exception("Could not list Kokoro voices")
        return []


class SpeechOutput:
    """Thread-safe, muting-aware queue in front of the Kokoro engine."""

    def __init__(self, enabled: bool = True, on_speaking=None, on_error=None,
                 backend=None, backend_kind: str = BACKEND_AUTO,
                 on_utterance=None):
        self.enabled = bool(enabled)
        self._on_speaking = on_speaking or (lambda speaking: None)
        self._on_error = on_error or (lambda message: None)
        #: Called with the *complete* text of each utterance as playback
        #: starts.  The engine may receive a shortened, humanised form.
        self._on_utterance = on_utterance or (lambda text: None)
        self._backend_lock = threading.RLock()
        self._init_lock = threading.Lock()
        self._requested_backend = normalize_backend(backend_kind)
        self._backend = backend
        self._active_backend = "custom" if backend is not None else ""
        self._auto_reason = ""
        self._natural_available = False
        self._pending_rate = 0
        self._pending_volume = 90
        self._pending_voice = ""
        self._pending_pause = DEFAULT_SENTENCE_PAUSE
        self._last_reported_error = ""
        self._last_spoken = ""

        self._queue: queue.Queue = queue.Queue(maxsize=32)
        #: Jobs already synthesising, waiting their turn to play.  Keeping one
        #: or two ahead of playback is what removes the long gap after a
        #: streamed sentence: the next sentence becomes audio while the
        #: current one is still being spoken.
        self._playback: queue.Queue = queue.Queue(maxsize=8)
        self._player_thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._speaking = False
        self._idle = threading.Event()
        self._idle.set()
        self._pending = 0
        self._pending_lock = threading.Lock()
        self._thread = threading.Thread(target=self._worker, name="TALCON-TTS", daemon=True)
        self._thread.start()

    # -- backend lifecycle -------------------------------------------------
    @property
    def backend(self):
        return self._backend

    @property
    def requested_backend(self) -> str:
        return self._requested_backend

    @property
    def active_backend(self) -> str:
        return self._active_backend

    @property
    def natural_available(self) -> bool:
        return self._natural_available

    @property
    def last_spoken(self) -> str:
        return self._last_spoken

    @last_spoken.setter
    def last_spoken(self, value: str) -> None:
        self._last_spoken = str(value or "")

    @property
    def last_synthesis(self) -> dict:
        backend = self._backend
        if backend is None:
            return {}
        try:
            return dict(getattr(backend, "last_synthesis", {}) or {})
        except Exception:
            return {}

    def synthesis_summary(self) -> str:
        """Human-readable account of the last real synthesis call."""
        used = self.last_synthesis
        if not used:
            return ""
        voice = str(used.get("voice_name") or used.get("voice_id") or "").strip()
        mode = str(used.get("mode") or "").strip()
        mode_label = {"kokoro": "Kokoro neural voice"}.get(
            mode, mode or "Kokoro neural voice"
        )
        return f"{voice} · {mode_label}" if voice else mode_label

    def _new_backend(self, kind: str):
        return create_backend(kind, on_error=self._handle_backend_error)

    def _build_backend(self, requested: str) -> tuple:
        """Create the Kokoro backend and report whether it is usable."""
        backend = self._new_backend(BACKEND_KOKORO)
        try:
            ready = bool(neural_tts.available())
            reason = "" if ready else neural_tts.unavailable_reason()
        except Exception as exc:  # pragma: no cover - defensive
            ready = False
            reason = f"Kokoro could not be checked: {exc}"
        if ready:
            note = (
                "Kokoro-82M neural voice is installed and runs offline on "
                "this PC's CPU."
            )
        else:
            note = reason or "Kokoro neural voice is unavailable."
        return backend, BACKEND_KOKORO, ready, note

    def _ensure_backend(self):
        while True:
            with self._backend_lock:
                if self._backend is not None:
                    return self._backend
            with self._init_lock:
                with self._backend_lock:
                    if self._backend is not None:
                        return self._backend
                    requested = self._requested_backend
                backend, active, natural, reason = self._build_backend(requested)
                with self._backend_lock:
                    stale = self._requested_backend != requested
                    if not stale:
                        self._backend = backend
                        self._active_backend = active
                        self._natural_available = natural
                        self._auto_reason = reason
                if stale:
                    try:
                        backend.stop()
                    except Exception:
                        log.exception("Could not stop a stale speech backend")
                    continue
                try:
                    backend.set_rate(self._pending_rate)
                    backend.set_volume(self._pending_volume)
                    pause = getattr(backend, "set_sentence_pause", None)
                    if callable(pause):
                        pause(self._pending_pause)
                    if self._pending_voice:
                        try:
                            backend.set_voice(self._pending_voice, wait=True)
                        except TypeError:
                            backend.set_voice(self._pending_voice)
                except Exception:
                    log.exception("Could not apply speech settings to the backend")
                return backend

    def set_backend(self, kind: str) -> None:
        """Reset the engine; it restarts lazily on the next request."""
        normalized = normalize_backend(kind)
        with self._backend_lock:
            if normalized == self._requested_backend and self._backend is not None:
                return
            old = self._backend
            self._requested_backend = normalized
            self._backend = None
            self._active_backend = ""
            self._natural_available = False
            self._auto_reason = ""
            self._last_reported_error = ""
            self._pending_voice = ""
        if old is not None:
            try:
                old.stop()
            except Exception:
                log.exception("Could not stop the previous speech backend")

    def engine_info(self) -> dict:
        with self._backend_lock:
            requested = self._requested_backend
            active = self._active_backend
            reason = self._auto_reason
            natural = self._natural_available
        if active == BACKEND_KOKORO:
            active_label = (
                "Kokoro Neural Voice" if natural else "Kokoro unavailable"
            )
        else:
            active_label = "Not started yet"
        if active and not reason:
            reason = "Kokoro-82M neural voice, generated on this PC."
        return {
            "requested": requested,
            "requested_label": SPEECH_BACKEND_LABELS.get(requested, requested),
            "active": active,
            "active_label": active_label,
            "natural_available": natural,
            "note": reason,
        }

    # -- state -------------------------------------------------------------
    def _handle_backend_error(self, message: str) -> None:
        if self._speaking:
            self._set_speaking(False)
        self._report_error(str(message))

    def _report_error(self, message: str) -> None:
        message = str(message or "").strip()
        if not message or message == self._last_reported_error:
            return
        self._last_reported_error = message
        try:
            self._on_error(message)
        except Exception:
            log.exception("Speech error callback failed")

    def _set_speaking(self, value: bool) -> None:
        if self._speaking == value:
            return
        self._speaking = value
        if value:
            self._idle.clear()
        else:
            self._idle.set()
        try:
            self._on_speaking(value)
        except Exception:
            log.exception("Speaking callback failed")

    @property
    def is_speaking(self) -> bool:
        return self._speaking

    @property
    def is_busy(self) -> bool:
        with self._pending_lock:
            return self._speaking or self._pending > 0

    def wait_until_idle(self, timeout: float | None = None) -> bool:
        """Block until the currently playing utterance has finished."""
        return self._idle.wait(timeout)

    def wait_until_quiet(self, timeout: float | None = None) -> bool:
        """Wait for queued speech to start and finish, not just current playback."""
        deadline = None if timeout is None else time.monotonic() + timeout
        while True:
            with self._pending_lock:
                busy = self._speaking or self._pending > 0
            if not busy:
                return True
            if deadline is not None and time.monotonic() >= deadline:
                return False
            time.sleep(0.02)

    # -- public API --------------------------------------------------------
    def start(self) -> None:
        # Warm the engine in the background so the first announcement is quick.
        threading.Thread(target=self._warm, name="TALCON-TTS-Warm", daemon=True).start()

    def ensure_ready(self, timeout: float = 25.0) -> bool:
        """Block until the Kokoro engine has loaded."""
        try:
            return bool(self._ensure_backend().ensure_started(timeout))
        except Exception:
            log.exception("Could not start the Kokoro speech backend")
            return False

    def _warm(self) -> None:
        try:
            self._ensure_backend().ensure_started()
        except Exception:
            log.exception("Speech warm-up failed")

    def voices(self) -> list[str]:
        return [voice.name for voice in self.voice_details()]

    def voice_details(self) -> list[VoiceInfo]:
        try:
            backend = self._ensure_backend()
            details = _backend_voice_list(backend)
            with self._backend_lock:
                self._natural_available = any(voice.natural for voice in details)
            return details
        except Exception:
            log.exception("Could not list Kokoro voices")
            return []

    def set_enabled(self, enabled: bool) -> None:
        self.enabled = bool(enabled)
        if not self.enabled:
            self.clear()
            with self._backend_lock:
                backend = self._backend
            if backend is not None:
                backend.stop_speaking()
            self._set_speaking(False)

    def clear(self) -> None:
        try:
            while True:
                self._queue.get_nowait()
        except queue.Empty:
            pass
        while True:
            try:
                entry = self._playback.get_nowait()
            except queue.Empty:
                break
            if entry is None:
                continue
            backend, job = entry[0], entry[1]
            discard = getattr(backend, "discard", None)
            if callable(discard):
                try:
                    discard(job)
                except Exception:
                    log.debug("Could not discard queued speech", exc_info=True)
        with self._pending_lock:
            self._pending = 0

    def stop_speaking(self) -> None:
        """Drop queued speech and interrupt whatever is playing."""
        self.clear()
        with self._backend_lock:
            backend = self._backend
        if backend is not None:
            backend.stop_speaking()
        self._set_speaking(False)

    def say(self, text: str, announce: bool = True) -> None:
        if not self.enabled or not text:
            return
        full_text = str(text)
        speech_text = spoken_excerpt(humanize_for_speech(full_text))
        if not speech_text:
            return
        # One sentence per queue item: the pipelined engine then prepares the
        # next sentence while this one plays, instead of synthesising a whole
        # paragraph in one blocking call.  Only the first item announces, so
        # History and the preview still show the complete answer exactly once.
        try:
            chunks = neural_tts.split_for_speech(speech_text)
        except Exception:
            log.debug("Could not split speech into sentences", exc_info=True)
            chunks = [speech_text]
        first = True
        for chunk in chunks or [speech_text]:
            # Count the item before it becomes visible to the worker.  If the
            # count were incremented after the put, a very fast utterance could
            # finish first and leave the busy counter stuck above zero forever.
            with self._pending_lock:
                self._pending += 1
            try:
                self._queue.put_nowait(
                    (chunk, full_text, bool(announce) and first)
                )
            except queue.Full:
                with self._pending_lock:
                    self._pending = max(0, self._pending - 1)
                log.warning("Speech queue full; dropping announcement")
                continue
            first = False

    def announce(self, text: str) -> None:
        """Publish a completed answer to History/preview without speaking it.

        Used when an answer was already spoken sentence by sentence while it
        streamed in: the transcript must still appear exactly once.
        """
        text = str(text or "").strip()
        if not text:
            return
        try:
            self._on_utterance(text)
        except Exception:
            log.exception("Utterance callback failed")
        self._last_spoken = text

    def say_and_wait(self, text: str, timeout: float = 30.0) -> bool:
        """Queue an announcement and block until playback has really finished."""
        if not self.enabled or not text:
            return True
        self.say(text)
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            with self._pending_lock:
                pending = self._pending
            if pending <= 0 and not self._speaking:
                return True
            time.sleep(0.02)
        return False

    def set_voice(self, name: str) -> None:
        candidate = str(name or "").strip()
        if not candidate:
            return
        with self._backend_lock:
            backend = self._backend
        if backend is not None:
            try:
                result = backend.set_voice(candidate, wait=False)
            except TypeError:
                result = backend.set_voice(candidate)
            except Exception:
                log.exception("Could not select the speech voice")
                result = False
            if result is False:
                self._report_error(
                    getattr(backend, "last_error", "")
                    or f"Voice is not available: {candidate}"
                )
                return
        self._pending_voice = candidate

    def verify_voice(self, timeout: float = 3.0) -> tuple[bool, str, str]:
        """Ask the active backend whether the requested voice is really active."""
        with self._backend_lock:
            backend = self._backend
        if backend is None:
            return True, "", ""
        waiter = getattr(backend, "wait_voice", None)
        if not callable(waiter):
            return True, "", ""
        try:
            return waiter(timeout)
        except Exception as exc:
            log.exception("Could not verify the selected speech voice")
            return False, "", str(exc)

    def set_rate(self, rate: int) -> None:
        self._pending_rate = max(-10, min(10, int(rate)))
        with self._backend_lock:
            backend = self._backend
        if backend is not None:
            backend.set_rate(self._pending_rate)

    def set_volume(self, volume: int) -> None:
        # Kokoro applies volume while writing the WAV; 100 is full scale.
        self._pending_volume = max(0, min(100, int(volume)))
        with self._backend_lock:
            backend = self._backend
        if backend is not None:
            backend.set_volume(self._pending_volume)

    def set_sentence_pause(self, seconds: float) -> None:
        """Set the beat of silence TALCON leaves after each full stop."""
        try:
            value = max(0.0, min(3.0, float(seconds)))
        except (TypeError, ValueError):
            value = DEFAULT_SENTENCE_PAUSE
        self._pending_pause = value
        with self._backend_lock:
            backend = self._backend
        setter = getattr(backend, "set_sentence_pause", None)
        if callable(setter):
            setter(value)

    def stop(self) -> None:
        self._stop.set()
        self.clear()
        with self._backend_lock:
            backend = self._backend
        if backend is not None:
            backend.stop()
        for thread in (self._thread, self._player_thread):
            if (thread is not None and thread.is_alive()
                    and threading.current_thread() is not thread):
                thread.join(timeout=2.0)
        self._set_speaking(False)

    # -- pipelined playback -------------------------------------------------
    @staticmethod
    def _pipeline_ready(backend) -> bool:
        return (
            bool(getattr(backend, "supports_pipelining", False))
            and callable(getattr(backend, "submit", None))
            and callable(getattr(backend, "play", None))
        )

    def _ensure_player(self) -> None:
        thread = self._player_thread
        if thread is not None and thread.is_alive():
            return
        thread = threading.Thread(
            target=self._player, name="TALCON-TTS-Play", daemon=True
        )
        self._player_thread = thread
        thread.start()

    def _player(self) -> None:
        """Play submitted jobs in order while the feeder keeps synthesising."""
        while not self._stop.is_set():
            try:
                entry = self._playback.get(timeout=0.3)
            except queue.Empty:
                continue
            if entry is None:
                break
            backend, job, full_text = entry
            try:
                play = getattr(backend, "play", None)
                ok = play(job) if callable(play) else False
                if ok is False:
                    self._report_error(
                        getattr(backend, "last_error", "")
                        or "Speech synthesis failed."
                    )
                else:
                    self._last_spoken = full_text
            except Exception as exc:
                log.exception("Speech playback failed")
                self._report_error(f"Speech output failed: {exc}")
            finally:
                self._finish_item()

    def _finish_item(self) -> None:
        with self._pending_lock:
            self._pending = max(0, self._pending - 1)
            idle = self._pending == 0
        if idle and self._queue.empty() and self._playback.empty():
            self._set_speaking(False)

    def _announcer(self, full_text: str):
        if not full_text:
            return None

        def announce() -> None:
            try:
                self._on_utterance(full_text)
            except Exception:
                log.exception("Utterance callback failed")

        return announce

    @staticmethod
    def _unpack(item):
        if isinstance(item, tuple):
            if len(item) >= 3:
                return item[0], item[1], bool(item[2])
            speech_text, full_text = item
            return speech_text, full_text, True
        # Tolerate a custom queue item for older callers/tests.
        return item, item, True

    @staticmethod
    def _say_now(backend, speech_text: str, announce) -> bool:
        if getattr(backend, "supports_playback_start", False):
            return backend.say(speech_text, on_start=announce)
        if callable(announce):
            announce()
        return backend.say(speech_text)

    def _worker(self) -> None:
        from .win_threads import lower_current_thread

        lower_current_thread()
        while not self._stop.is_set():
            try:
                item = self._queue.get(timeout=0.3)
            except queue.Empty:
                continue
            if item is None:
                break
            speech_text, full_text, announce_item = self._unpack(item)
            handled_by_player = False
            try:
                if not self.enabled:
                    continue
                self._set_speaking(True)
                backend = self._ensure_backend()
                announce = self._announcer(full_text if announce_item else "")
                if self._pipeline_ready(backend):
                    job = backend.submit(speech_text, on_start=announce)
                    if job is None:
                        if not self._stop.is_set():
                            self._report_error(
                                getattr(backend, "last_error", "")
                                or "Speech synthesis failed."
                            )
                        continue
                    # The player thread finishes this item, not the worker.
                    handled_by_player = True
                    self._ensure_player()
                    self._playback.put((backend, job, full_text))
                    continue
                ok = self._say_now(backend, speech_text, announce)
                if ok is False:
                    self._report_error(
                        getattr(backend, "last_error", "") or "Speech synthesis failed."
                    )
                else:
                    self._last_spoken = full_text
            except Exception as exc:
                log.exception("Speech output failed")
                self._report_error(f"Speech output failed: {exc}")
            finally:
                if not handled_by_player:
                    self._finish_item()


# -- diagnostic ------------------------------------------------------------
def _format_voice(voice: VoiceInfo) -> str:
    return (
        f"  {voice.name} | id={voice.id!r} | engine={voice.engine} "
        f"| language={voice.language or '?'} | gender={voice.gender or '?'}"
    )


def voice_diagnostic(audible: bool = False, voice_id: str = "") -> str:
    """Report the real Kokoro speech path; optionally speak a sentence."""
    lines = ["TALCON speech diagnostic", "Engine: Kokoro-82M (offline, CPU)"]
    model, voices = model_paths()
    lines.append(f"Model folder: {neural_tts.KOKORO_DIR}")
    if model is not None:
        size_mb = model.stat().st_size / (1024 * 1024)
        lines.append(f"Model: {model.name} ({size_mb:.1f} MB)")
    else:
        lines.append("Model: missing")
    lines.append(f"Voice file: {voices.name} ({'present' if voices.is_file() else 'missing'})")
    package = neural_tts.package_available()
    lines.append(f"kokoro-onnx package: {'installed' if package else 'missing'}")
    if not neural_tts.available():
        lines.append(f"Status: unavailable — {neural_tts.unavailable_reason()}")
        return "\n".join(lines)

    backend = create_backend()
    try:
        details = _backend_voice_list(backend)
        lines.append(f"Installed voices: {len(details)}")
        for voice in details[:20]:
            lines.append(_format_voice(voice))
        if len(details) > 20:
            lines.append(f"  … and {len(details) - 20} more")
        failed = ""
        if voice_id:
            if not backend.set_voice(voice_id):
                failed = f"Voice rejected: {voice_id}"
            else:
                lines.append(f"Selected voice: {voice_id}")
        if failed:
            lines.append(f"Status: {failed}")
            return "\n".join(lines)
        if audible:
            ok = backend.say(NATURAL_PREVIEW)
            lines.append(
                "Audible test: sentence synthesised and played."
                if ok else "Audible test: failed to synthesise or play."
            )
        lines.append(
            "Status: ready"
            if not audible else "Status: synthesis completed"
        )
    except Exception as exc:
        lines.append(f"Status: failed — {exc}")
    finally:
        try:
            backend.stop()
        except Exception:
            pass
    return "\n".join(lines)
