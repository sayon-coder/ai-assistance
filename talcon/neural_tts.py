"""Offline neural text-to-speech through Kokoro-82M (ONNX).

Hardware decision
-----------------
TALCON targets a Ryzen 5 3550H with 16 GB RAM, a GTX 1650 (4 GB) and no CUDA
toolkit.  Measured on that machine, the quantised Kokoro int8 model loads in
about 1.5 s.  A single synthesis call runs at only about half of realtime,
which is slower than playback; three calls at once reach roughly 0.85×, which
is enough to keep a queue of sentences ahead of the speaker.  That is why the
synthesiser is a small worker pool and why the speech layer submits several
sentences before playback starts.

DirectML (the GPU path) rejects this model's ``ConvTranspose`` node, so the CPU
provider is used deliberately.  Everything stays local: no API key, no cloud
service and no account.

Kokoro is the only speech engine TALCON uses.  If the model files or the
``kokoro-onnx`` package are missing, :func:`available` returns ``False`` and
:func:`unavailable_reason` explains what to install; there is no robotic
Windows fallback.
"""
from __future__ import annotations

import io
import logging
import os
import queue
import re
import threading
import time
import wave
from pathlib import Path

import numpy as np

from .config import MODELS_DIR

log = logging.getLogger("talcon.neural_tts")

#: Folder containing ``kokoro-v1.0.int8.onnx`` and ``voices-v1.0.bin``.
KOKORO_DIR = MODELS_DIR / "kokoro"
KOKORO_MODEL_NAMES = ("kokoro-v1.0.int8.onnx", "kokoro-v1.0.onnx")
KOKORO_VOICES_NAME = "voices-v1.0.bin"

#: Kokoro-82M is licensed Apache-2.0; the files come from the publisher.
KOKORO_LICENSE = "Apache License 2.0"
KOKORO_HOMEPAGE = "https://github.com/thewh1teagle/kokoro-onnx"
KOKORO_SIZE_MB = 118

#: Default voice: a calm British male, closest to a butler-style assistant.
DEFAULT_VOICE = "bm_george"
DEFAULT_LANGUAGE = "en-gb"

#: Voices offered in the settings list, with a friendly label.  Every other
#: voice in the model file is still selectable by id.
KNOWN_VOICES = (
    ("bm_george", "George · British male"),
    ("bm_lewis", "Lewis · British male"),
    ("bm_fable", "Fable · British male"),
    ("am_michael", "Michael · American male"),
    ("am_fenrir", "Fenrir · American male"),
    ("am_puck", "Puck · American male"),
    ("af_heart", "Heart · American female"),
    ("af_bella", "Bella · American female"),
    ("bf_emma", "Emma · British female"),
    ("bf_isabella", "Isabella · British female"),
)

#: Release the model after this long without speech.  A reload costs about
#: 2.4 s on the target laptop, so keep it warm for a few minutes; short idle
#: gaps between follow-up questions should not be paid for twice.
IDLE_UNLOAD_SECONDS = 300.0

_SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+")
#: Long sentences are split at clauses.  Smaller chunks let the first words
#: start playing sooner; the pipelined synthesiser hides most of the rest.
_MAX_CHUNK = 200

#: Silence after a full stop when more speech is already queued.  Synthesis
#: runs ahead of playback, so without this the sentences would run together;
#: one second leaves the natural beat a speaker would take.
DEFAULT_SENTENCE_PAUSE = 1.0

#: Concurrent synthesis calls.  Measured on the target Ryzen 5, one Kokoro
#: call at a time runs at about 0.5x realtime, which cannot keep up with
#: playback; three concurrent calls reach about 0.85x and stay ahead.  The
#: phonemiser is serialised because espeak-ng keeps global state.
SYNTH_WORKERS = 3


def _ends_sentence(text: str) -> bool:
    """True when ``text`` finishes a thought (full stop, question or bang)."""
    tail = str(text or "").rstrip().rstrip("\"'”’)]")
    return tail.endswith((".", "!", "?"))


def model_paths() -> tuple[Path | None, Path]:
    """Return ``(model, voices)`` paths, choosing the smallest available model."""
    voices = KOKORO_DIR / KOKORO_VOICES_NAME
    for name in KOKORO_MODEL_NAMES:
        candidate = KOKORO_DIR / name
        if candidate.is_file():
            return candidate, voices
    return None, voices


def package_available() -> bool:
    """True when the optional Python packages can be imported."""
    try:
        import kokoro_onnx  # noqa: F401
    except Exception:
        return False
    return True


def model_ready() -> bool:
    model, voices = model_paths()
    return bool(model and model.is_file() and voices.is_file())


def available() -> bool:
    """True when Kokoro can actually be used on this PC right now."""
    return package_available() and model_ready()


def unavailable_reason() -> str:
    """A short, honest explanation for the settings page."""
    if not model_ready():
        model, voices = model_paths()
        missing = []
        if model is None:
            missing.append("kokoro-v1.0.int8.onnx")
        if not voices.is_file():
            missing.append(KOKORO_VOICES_NAME)
        return (
            "Kokoro voice model not installed ("
            + ", ".join(missing)
            + "). Place it in models/kokoro or run "
            "`python -m talcon.model_setup --kokoro`."
        )
    if not package_available():
        return (
            "The optional kokoro-onnx package is not installed. Run "
            "`pip install kokoro-onnx onnxruntime`."
        )
    return ""


def _split_long(sentence: str, limit: int) -> list[str]:
    """Break one over-long sentence at a clause boundary."""
    pieces: list[str] = []
    sentence = sentence.strip()
    while len(sentence) > limit:
        window = sentence[:limit]
        cut = max(
            window.rfind(", "),
            window.rfind("; "),
            window.rfind(": "),
            window.rfind(" "),
        )
        if cut < limit // 2:
            cut = limit
        piece = sentence[:cut].strip()
        if piece:
            pieces.append(piece)
        sentence = sentence[cut:].strip()
    if sentence:
        pieces.append(sentence)
    return pieces or [""]


def split_for_speech(text: str, limit: int = _MAX_CHUNK) -> list[str]:
    """Split text into short, sentence-sized chunks for low-latency playback.

    One sentence per chunk means the first words can be synthesised and played
    while later sentences are still being generated.  Only unusually long
    sentences are split further, at a comma or clause boundary when possible.
    """
    raw = " ".join(str(text or "").split())
    if not raw:
        return []
    sentences = [part.strip() for part in _SENTENCE_SPLIT.split(raw) if part.strip()]
    chunks: list[str] = []
    for index, sentence in enumerate(sentences):
        # Keep the first chunk short so speech starts sooner.
        chunks.extend(_split_long(sentence, 90 if index == 0 else limit))
    return [chunk for chunk in chunks if chunk]


def voice_label(voice_id: str) -> str:
    """A friendly name; the engine is already shown as an engine label."""
    for key, label in KNOWN_VOICES:
        if key == voice_id:
            return label
    return str(voice_id or "").replace("_", " ").strip().title()


def wav_bytes(samples, sample_rate: int, volume: float = 1.0) -> bytes:
    """Convert float samples to PCM WAV bytes, applying volume safely."""
    audio = np.asarray(samples, dtype=np.float32).reshape(-1)
    gain = max(0.0, min(1.0, float(volume)))
    if gain < 0.999:
        audio = audio * gain
    pcm = np.clip(audio, -1.0, 1.0)
    pcm = (pcm * 32767.0).astype(np.int16)
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(int(sample_rate))
        handle.writeframes(pcm.tobytes())
    return buffer.getvalue()


def _inference_threads() -> int:
    """Pick a CPU thread count that is fast without starving the UI/mic.

    Measured on the target laptop's Ryzen 5 (4 cores / 8 logical), a 4.6 s
    sentence took 8.2 s with four intra-op threads and 7.4 s with six.  More
    than six was slower again, and using every logical core competed with the
    microphone and UI threads.  Six threads is the sweet spot for eight
    logical cores; smaller machines get proportionally fewer.
    """
    logical = os.cpu_count() or 4
    if logical >= 8:
        return 6
    return max(2, min(6, logical - 1))


def _load_engine(model: Path, voices: Path):
    """Build a tuned CPU ONNX session, falling back to the stock constructor."""
    from kokoro_onnx import Kokoro

    try:
        import onnxruntime as ort

        options = ort.SessionOptions()
        options.intra_op_num_threads = _inference_threads()
        options.inter_op_num_threads = 1
        options.execution_mode = ort.ExecutionMode.ORT_SEQUENTIAL
        options.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
        session = ort.InferenceSession(
            str(model), sess_options=options, providers=["CPUExecutionProvider"]
        )
        log.info(
            "Kokoro ONNX session ready (%d intra-op threads)",
            options.intra_op_num_threads,
        )
        return Kokoro.from_session(session, str(voices))
    except Exception:
        log.debug("Tuned Kokoro session failed; using the default", exc_info=True)
        return Kokoro(str(model), str(voices))


class _SpeechJob:
    """One utterance, synthesised ahead of playback.

    ``audio`` receives ``((index, wav, seconds, total, rate), pause_after)``
    tuples as the synthesis thread produces them, then a single ``None``.
    """

    def __init__(self, text, chunks, engine, voice, language, speed, volume,
                 epoch, on_start=None):
        self.text = str(text)
        self.chunks = list(chunks)
        self.engine = engine
        self.voice = voice
        self.language = language
        self.speed = speed
        self.volume = volume
        self.epoch = int(epoch)
        self.on_start = on_start
        self.audio: queue.Queue = queue.Queue()
        self.cancel = threading.Event()
        self.error = ""
        self.finished = False


class KokoroBackend:
    """Lazily loaded Kokoro synthesiser with a persistent-host-like interface."""

    backend_kind = "kokoro"
    reader_name = "TALCON-Kokoro"

    def __init__(self, on_error=None):
        self.on_error = on_error or (lambda message: None)
        self._lock = threading.RLock()
        self._engine = None
        self._last_error = ""
        self._voice = DEFAULT_VOICE
        self._language = DEFAULT_LANGUAGE
        self._rate = 0
        self._volume = 90
        self._closed = threading.Event()
        self._unload_timer: threading.Timer | None = None
        self._used: dict = {}
        self._last_spoken = ""
        self._voice_ids: list[str] = []
        # Speech generation: bumping the epoch discards jobs already queued or
        # in flight when the user interrupts TALCON.
        self._epoch = 0
        self._outstanding = 0
        self._outstanding_lock = threading.Lock()
        self._synth_queue: queue.Queue = queue.Queue()
        self._synth_threads: list[threading.Thread] = []
        self._phoneme_lock = threading.Lock()
        self._sentence_pause = DEFAULT_SENTENCE_PAUSE
        self._silence_cache: dict = {}
        self._silence_lock = threading.Lock()

    # -- lifecycle ---------------------------------------------------------
    def ensure_started(self, timeout: float = 25.0) -> bool:
        """Load the model once.  Returns False with a spoken-friendly reason."""
        with self._lock:
            if self._engine is not None:
                return True
            if not available():
                self._last_error = unavailable_reason()
                self.on_error(self._last_error)
                return False
            model, voices = model_paths()
            if model is None:
                self._last_error = unavailable_reason()
                self.on_error(self._last_error)
                return False
            try:
                log.info("Loading the Kokoro neural voice model (int8, CPU)")
                engine = _load_engine(model, voices)
            except Exception as exc:
                log.exception("Could not load the Kokoro model")
                self._last_error = f"Kokoro could not start: {exc}"
                self.on_error(self._last_error)
                return False
            self._engine = engine
            try:
                names = list(engine.get_voices())
            except Exception:
                names = []
            self._voice_ids = names
            if self._voice and names and self._voice not in names:
                self._voice = DEFAULT_VOICE if DEFAULT_VOICE in names else names[0]
            self._schedule_unload()
            return True

    def _schedule_unload(self) -> None:
        timer = self._unload_timer
        if timer is not None:
            timer.cancel()
        timer = threading.Timer(IDLE_UNLOAD_SECONDS, self._unload_if_idle)
        timer.daemon = True
        self._unload_timer = timer
        timer.start()

    def _unload_if_idle(self) -> None:
        with self._lock:
            self._engine = None
            self._voice_ids = []
            self._unload_timer = None
        log.info("Kokoro released after %.0f s idle", IDLE_UNLOAD_SECONDS)

    def stop(self) -> None:
        timer = self._unload_timer
        self._unload_timer = None
        if timer is not None:
            timer.cancel()
        self._closed.set()
        self.stop_speaking()
        for _ in self._synth_threads:
            self._synth_queue.put(None)
        threads = list(self._synth_threads)
        self._synth_threads = []
        for thread in threads:
            if thread is not threading.current_thread():
                thread.join(timeout=1.5)
        with self._lock:
            self._engine = None
            self._voice_ids = []

    def stop_speaking(self) -> None:
        """Interrupt playback immediately (the model stays loaded)."""
        with self._outstanding_lock:
            self._epoch += 1
            self._outstanding = 0
        self._cancel_queued_jobs()
        self._purge_audio()

    def _cancel_queued_jobs(self) -> None:
        try:
            while True:
                job = self._synth_queue.get_nowait()
                if job is not None:
                    job.cancel.set()
        except queue.Empty:
            pass

    @staticmethod
    def _purge_audio() -> None:
        try:
            import winsound

            winsound.PlaySound(None, winsound.SND_PURGE)
        except Exception:
            pass

    # -- settings ----------------------------------------------------------
    def set_rate(self, rate: int) -> None:
        self._rate = max(-10, min(10, int(rate)))

    def set_volume(self, volume: int) -> None:
        self._volume = max(0, min(100, int(volume)))

    def set_sentence_pause(self, seconds: float) -> None:
        """Set the beat of silence after each full stop (0-3 seconds)."""
        try:
            value = float(seconds)
        except (TypeError, ValueError):
            value = DEFAULT_SENTENCE_PAUSE
        self._sentence_pause = max(0.0, min(3.0, value))

    def request_voice(self, name: str) -> bool:
        candidate = str(name or "").strip()
        if not candidate:
            return False
        with self._lock:
            if self._voice_ids and candidate not in self._voice_ids:
                # A friendly label may have been stored instead of the id.
                for key, label in KNOWN_VOICES:
                    if candidate == label:
                        candidate = key
                        break
            if self._voice_ids and candidate not in self._voice_ids:
                self._last_error = f"Voice is not installed: {candidate}"
                return False
            self._voice = candidate
            self._language = "en-gb" if candidate.startswith("b") else "en-us"
        return True

    def set_voice(self, name: str, wait: bool = True) -> bool:
        ok = self.request_voice(name)
        if not ok:
            self.on_error(self._last_error)
        return ok

    def wait_voice(self, timeout: float = 3.0) -> tuple[bool, str, str]:
        return True, self._voice, ""

    def voice_details(self, timeout: float = 15.0) -> list:
        from .speech import ENGINE_KOKORO, VoiceInfo

        ids = list(self._voice_ids)
        if not ids:
            # Even before the model loads, offer the known voices so the
            # settings page is not empty.
            ids = [key for key, _label in KNOWN_VOICES]
        # Put the curated, butler-like voices first, then every remaining id.
        known = [key for key, _label in KNOWN_VOICES]
        ids = [voice_id for voice_id in known if voice_id in ids] + [
            voice_id for voice_id in ids if voice_id not in known
        ]
        details = []
        for voice_id in ids:
            details.append(
                VoiceInfo(
                    id=voice_id,
                    name=voice_label(voice_id),
                    engine=ENGINE_KOKORO,
                    language="en-GB" if voice_id.startswith("b") else "en-US",
                    gender="Male" if voice_id.startswith(("am", "bm")) else "Female",
                    description="Kokoro-82M offline neural voice",
                    backend="kokoro",
                    voice_type="Neural",
                    source="kokoro",
                    ssml=False,
                )
            )
        return details

    def voices(self, timeout: float = 15.0) -> list[str]:
        return [voice.name for voice in self.voice_details(timeout)]

    def has_natural_voice(self, timeout: float = 10.0) -> bool:
        return True

    def state(self, timeout: float = 3.0) -> dict:
        return {
            "engine": "kokoro",
            "voice": self._voice,
            "loaded": self._engine is not None,
        }

    # -- synthesis ---------------------------------------------------------
    @property
    def last_error(self) -> str:
        return self._last_error

    @property
    def last_synthesis(self) -> dict:
        return dict(self._used or {})

    supports_playback_start = True
    #: The speech queue can submit the next sentence while this one plays.
    supports_pipelining = True

    def submit(self, text: str, on_start=None) -> "_SpeechJob | None":
        """Start synthesising ``text`` now and play it later with :meth:`play`.

        This is what removes the long pause after each full stop.  Speech that
        arrives sentence by sentence is handed to the synthesis thread
        immediately, so the next sentence is already audio by the time the
        current one finishes playing.
        """
        if not str(text or "").strip():
            return None
        if self._closed.is_set():
            return None
        self._schedule_unload()
        if not self.ensure_started():
            return None
        chunks = split_for_speech(text)
        if not chunks:
            return None
        with self._lock:
            engine = self._engine
            voice = self._voice
            language = self._language
        if engine is None:
            self._last_error = "The Kokoro model was released; please try again."
            return None
        job = _SpeechJob(
            text=text, chunks=chunks, engine=engine, voice=voice,
            language=language,
            speed=max(0.6, min(1.4, 1.0 + (self._rate * 0.04))),
            volume=self._volume / 100.0,
            epoch=self._epoch, on_start=on_start,
        )
        with self._outstanding_lock:
            self._outstanding += 1
        self._ensure_synth_threads()
        self._synth_queue.put(job)
        return job

    def play(self, job: "_SpeechJob | None") -> bool:
        """Play a submitted job, blocking until its audio has finished."""
        if job is None:
            return False
        return self._play_job(job)

    def discard(self, job: "_SpeechJob | None") -> None:
        """Drop a job that was submitted but will not be played."""
        if job is None:
            return
        job.cancel.set()
        self._finish_job(job)

    def say(self, text: str, timeout: float | None = None,
            on_start=None) -> bool:
        if not str(text or "").strip():
            return True
        self._schedule_unload()
        if not self.ensure_started():
            return False
        job = self.submit(text, on_start)
        if job is None:
            return False
        return self._play_job(job, timeout)

    # -- synthesis threads -------------------------------------------------
    def _ensure_synth_threads(self) -> None:
        with self._lock:
            alive = [t for t in self._synth_threads if t.is_alive()]
            while len(alive) < SYNTH_WORKERS:
                thread = threading.Thread(
                    target=self._synth_worker, name="TALCON-Kokoro-Synth",
                    daemon=True,
                )
                thread.start()
                alive.append(thread)
            self._synth_threads = alive

    def _synth_worker(self) -> None:
        from .win_threads import lower_current_thread

        lower_current_thread()
        while True:
            job = self._synth_queue.get()
            if job is None:
                return
            self._synthesise_job(job)

    def _synthesise_job(self, job: _SpeechJob) -> None:
        total = len(job.chunks)
        try:
            for index, chunk in enumerate(job.chunks):
                if self._interrupted(job):
                    break
                item = self._synthesise(
                    job.engine, chunk, job.speed, index, total,
                    voice=job.voice, language=job.language, volume=job.volume,
                )
                if item is None:
                    job.error = self._last_error
                    break
                pause_after = 0.0
                if index + 1 < total and _ends_sentence(chunk):
                    pause_after = self._sentence_pause
                job.audio.put((item, pause_after))
        except Exception as exc:  # pragma: no cover - defensive
            log.exception("Kokoro synthesis worker failed")
            job.error = f"Kokoro synthesis failed: {exc}"
        finally:
            job.audio.put(None)

    def _create_audio(self, engine, chunk, speed, voice, language):
        """Phonemise under a lock, then infer (ONNX inference is thread-safe).

        espeak-ng keeps global state, so phonemisation must never run on two
        threads at once.  The model itself is happy to serve several calls.
        """
        tokenizer = getattr(engine, "tokenizer", None)
        phonemize = getattr(tokenizer, "phonemize", None)
        if callable(phonemize):
            try:
                with self._phoneme_lock:
                    phonemes = phonemize(chunk, language)
            except Exception:
                phonemes = ""
            if phonemes:
                try:
                    return engine.create(
                        phonemes, voice=voice, speed=speed,
                        lang=language, is_phonemes=True,
                    )
                except TypeError:
                    log.debug("kokoro-onnx does not accept pre-phonemised text")
        with self._phoneme_lock:
            return engine.create(
                chunk, voice=voice, speed=speed, lang=language
            )

    def _synthesise(self, engine, chunk, speed, index, total, voice=None,
                    language=None, volume=None):
        try:
            samples, sample_rate = self._create_audio(
                engine, chunk, speed,
                voice or self._voice, language or self._language,
            )
        except Exception as exc:
            log.exception("Kokoro synthesis failed")
            self._last_error = f"Kokoro synthesis failed: {exc}"
            self.on_error(self._last_error)
            return None
        gain = self._volume / 100.0 if volume is None else volume
        data = wav_bytes(samples, sample_rate, gain)
        seconds = len(samples) / float(sample_rate or 1)
        return (index, data, seconds, total, sample_rate)

    # -- playback ----------------------------------------------------------
    def _interrupted(self, job: _SpeechJob) -> bool:
        return (
            self._closed.is_set()
            or job.cancel.is_set()
            or job.epoch != self._epoch
        )

    def _finish_job(self, job: _SpeechJob) -> bool:
        """Release one job; True when more speech is already waiting."""
        with self._outstanding_lock:
            if not job.finished:
                job.finished = True
                self._outstanding = max(0, self._outstanding - 1)
            return self._outstanding > 0

    def _silence(self, sample_rate: int, seconds: float) -> bytes:
        """Cached PCM silence, so a pause costs no synthesis time."""
        rate = int(sample_rate or 24000)
        millis = int(round(max(0.0, float(seconds)) * 1000))
        key = (rate, millis)
        with self._silence_lock:
            cached = self._silence_cache.get(key)
            if cached is None:
                frames = max(0, int(rate * millis / 1000))
                cached = wav_bytes(
                    np.zeros(frames, dtype=np.float32), rate
                )
                self._silence_cache[key] = cached
            return cached

    def _play_job(self, job: _SpeechJob, timeout: float | None = None) -> bool:
        started = time.monotonic()
        spoken = 0.0
        announced = False
        sample_rate = 0
        while True:
            try:
                item = job.audio.get(timeout=0.2)
            except queue.Empty:
                if timeout is not None and time.monotonic() - started > timeout:
                    job.error = "Kokoro synthesis took too long."
                    job.cancel.set()
                    break
                if self._interrupted(job):
                    break
                continue
            if item is None or self._interrupted(job):
                break
            (index, data, seconds, total, sample_rate), pause_after = item
            if timeout is not None and time.monotonic() - started > timeout:
                job.error = "Kokoro synthesis took too long."
                job.cancel.set()
                break
            spoken += seconds
            self._used = {
                "mode": "kokoro",
                "voice_id": job.voice,
                "voice_name": job.voice,
                "chunk": index + 1,
                "chunks": total,
                "audio_seconds": round(spoken, 2),
            }
            if not announced and callable(job.on_start):
                announced = True
                try:
                    job.on_start()
                except Exception:
                    log.debug("Playback-start callback failed", exc_info=True)
            if not self._play(data):
                job.cancel.set()
                break
            self._schedule_unload()
            if pause_after and not self._interrupted(job):
                self._play(self._silence(sample_rate, pause_after))
        more_speech = self._finish_job(job)
        if (more_speech and sample_rate and not self._interrupted(job)
                and _ends_sentence(job.text)):
            # One natural beat at the full stop, with the next sentence
            # already synthesised and waiting behind it.
            self._play(self._silence(sample_rate, self._sentence_pause))
        if job.error:
            self._last_error = job.error
            self.on_error(self._last_error)
            return False
        if not self._interrupted(job):
            self._last_spoken = str(job.text)
        return True

    def _play(self, data: bytes) -> bool:
        try:
            import winsound

            winsound.PlaySound(data, winsound.SND_MEMORY)
            return True
        except Exception as exc:
            log.exception("Could not play the Kokoro audio")
            self._last_error = f"Audio playback failed: {exc}"
            self.on_error(self._last_error)
            return False


def create_backend(kind: str = "kokoro", on_error=None) -> KokoroBackend:
    return KokoroBackend(on_error=on_error)
