"""Fully offline wake-word ("NOVA") and command capture using Vosk.

The wake interaction is deliberately silent: say "NOVA", hear one short
confirmation beep, then speak the command.  Microphone input is gated while
the beep plays, stale audio is discarded, and recognition pauses whenever
Windows speech output is actually speaking so NOVA never hears itself.

No Picovoice key, cloud service or extra model is used.  The existing small
English Vosk model is loaded once per process.
"""
from __future__ import annotations

import array
import json
import logging
import math
import queue
import re
import threading
import time

from .model_setup import model_is_ready, resolve_model

log = logging.getLogger("nova.voice")

SAMPLE_RATE = 16000
BLOCK_SIZE = 4000  # 0.25 s at 16 kHz
WAKE_WORD = "nova"
WAKE_GRAMMAR = '["nova", "[unk]"]'
WAKE_DEBOUNCE = 1.5          # seconds; one wake can never start two sessions
BEEP_GATE_SECONDS = 0.45     # microphone is ignored while the beep plays
SETTLE_SECONDS = 0.30        # discard residual audio after the beep
COMMAND_START_TIMEOUT = 6.0  # wait this long for speech to begin
COMMAND_SILENCE = 1.1        # end the command after this much trailing silence
COMMAND_MAX_SECONDS = 12.0   # hard upper bound for one command
SPEECH_RMS_THRESHOLD = 300   # lightweight VAD, no extra model
MIN_SPEECH_BLOCKS = 2
N_ALTERNATIVES = 5           # Vosk's own hypotheses, exact-matched (never fuzzy)
DESTRUCTIVE_ALTERNATIVES = 2  # exit/quit may only come from the best two
_TOKEN = re.compile(r"[a-z']+")

_MODEL_CACHE: dict[str, object] = {}


def _load_model(path: str):
    from vosk import Model, SetLogLevel

    if path not in _MODEL_CACHE:
        SetLogLevel(-1)
        log.info("Loading Vosk model from %s", path)
        _MODEL_CACHE[path] = Model(path)
    return _MODEL_CACHE[path]


def tokens(text: str) -> list[str]:
    return _TOKEN.findall(str(text or "").lower())


def is_wake_phrase(text: str) -> bool:
    """Exact token match only: "NOVA" (or "hey NOVA"), never "novak"."""
    return WAKE_WORD in tokens(text)


def _rms(pcm: bytes) -> float:
    """Root-mean-square level of 16-bit mono PCM without a heavy dependency."""
    if not pcm:
        return 0.0
    usable = len(pcm) - (len(pcm) % 2)
    if usable <= 0:
        return 0.0
    samples = array.array("h")
    samples.frombytes(pcm[:usable])
    if not samples:
        return 0.0
    total = 0
    for sample in samples:
        total += sample * sample
    return math.sqrt(total / len(samples))


def _play_beep() -> None:
    try:
        import winsound

        winsound.PlaySound("SystemAsterisk", winsound.SND_ALIAS | winsound.SND_ASYNC)
    except Exception:
        log.debug("Confirmation beep unavailable", exc_info=True)


def list_microphones() -> list[tuple[int | None, str]]:
    result: list[tuple[int | None, str]] = [(None, "System default microphone")]
    try:
        import sounddevice as sd

        seen = set()
        for index, device in enumerate(sd.query_devices()):
            if int(device.get("max_input_channels", 0)) <= 0:
                continue
            name = str(device.get("name", "Microphone")).strip()
            key = name.lower()
            if key in seen:
                continue
            seen.add(key)
            result.append((index, name))
    except Exception:
        log.exception("Could not enumerate microphones")
    return result


def measure_input(seconds: float = 1.2, device: int | None = None) -> dict:
    """Record a short sample (never stored) and report its level for setup tests."""
    try:
        import numpy as np
        import sounddevice as sd

        frames = max(1, int(seconds * SAMPLE_RATE))
        recording = sd.rec(frames, samplerate=SAMPLE_RATE, channels=1, dtype="int16", device=device)
        sd.wait()
        data = np.abs(recording.astype("int32"))
        peak = int(data.max()) if data.size else 0
        rms = int(np.sqrt(float((data.astype("float64") ** 2).mean()))) if data.size else 0
        return {"ok": True, "peak": peak, "rms": rms, "error": ""}
    except Exception as exc:
        return {"ok": False, "peak": 0, "rms": 0, "error": str(exc)}


class VoiceController:
    def __init__(self, settings: dict, command_router, status_callback=None,
                 face_callback=None, event_callback=None, beep=None):
        self.settings = dict(settings or {})
        self.command_router = command_router
        self.status_callback = status_callback or (lambda text: None)
        self.face_callback = face_callback or (lambda state: None)
        self.event_callback = event_callback or (lambda kind, text: None)
        self._beep = beep or _play_beep
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._manual = threading.Event()
        self._session_lock = threading.Lock()
        self._status = ""
        self._model = None
        self._last_manual_thread: threading.Thread | None = None
        self._next_wake_at = 0.0
        self._continuous = False
        self.always_listen = bool(self.settings.get("always_listen", True))
        self._recent: list[tuple[str, float]] = []
        self._last_executed: tuple[str, float] = ("", 0.0)

    # -- helpers -----------------------------------------------------------
    def _set_status(self, text: str) -> None:
        if text == self._status:
            return
        self._status = text
        try:
            self.status_callback(text)
        except Exception:
            log.exception("Voice status callback failed")

    def _emit(self, kind: str, text: str) -> None:
        try:
            self.event_callback(str(kind), str(text))
        except Exception:
            log.exception("Voice event callback failed")

    def _device(self):
        value = self.settings.get("microphone_device")
        if value in (None, ""):
            return None
        try:
            return int(value)
        except (TypeError, ValueError):
            return None

    def _model_path(self):
        return resolve_model(self.settings.get("vosk_model_path"))

    @property
    def _speech(self):
        return getattr(self.command_router, "speech", None)

    def _speech_is_speaking(self) -> bool:
        speech = self._speech
        if speech is None:
            return False
        busy = getattr(speech, "is_busy", None)
        if busy is not None:
            return bool(busy)
        return bool(getattr(speech, "is_speaking", False))

    def _wait_for_speech_end(self, timeout: float) -> bool:
        waiter = getattr(self._speech, "wait_until_quiet", None)
        if not callable(waiter):
            waiter = getattr(self._speech, "wait_until_idle", None)
        if not callable(waiter):
            return True
        try:
            return bool(waiter(timeout))
        except Exception:
            log.exception("Could not wait for speech output")
            return False

    @staticmethod
    def _drain(audio_q: queue.Queue) -> int:
        dropped = 0
        while True:
            try:
                audio_q.get_nowait()
                dropped += 1
            except queue.Empty:
                return dropped

    def _gate_microphone(self, audio_q: queue.Queue, seconds: float) -> None:
        deadline = time.monotonic() + max(0.0, seconds)
        while time.monotonic() < deadline and not self._stop.is_set():
            try:
                audio_q.get(timeout=min(0.05, max(0.005, deadline - time.monotonic())))
            except queue.Empty:
                pass
        self._drain(audio_q)

    @staticmethod
    def _reset(recognizer) -> None:
        try:
            recognizer.Reset()
        except Exception:
            log.debug("Recogniser reset was not available", exc_info=True)

    # -- lifecycle ---------------------------------------------------------
    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="NOVA-Voice", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread and self._thread.is_alive() and self._thread is not threading.current_thread():
            self._thread.join(timeout=2.5)
        if (self._last_manual_thread and self._last_manual_thread.is_alive()
                and self._last_manual_thread is not threading.current_thread()):
            self._last_manual_thread.join(timeout=1.5)
        self._thread = None

    def request_manual_listen(self) -> None:
        """Capture one command now, even without saying the wake word."""
        if self._thread and self._thread.is_alive():
            if self.always_listen:
                self._set_status("Listening continuously")
                return
            self._manual.set()
            self._set_status("Listening for your command…")
            return
        if self._last_manual_thread and self._last_manual_thread.is_alive():
            return
        self._last_manual_thread = threading.Thread(
            target=self._manual_session, name="NOVA-Voice-Manual", daemon=True
        )
        self._last_manual_thread.start()

    # -- main loop ---------------------------------------------------------
    def _run(self) -> None:
        path = self._model_path()
        if not model_is_ready(path):
            self._set_status("Speech model missing · download it in Voice settings")
            log.warning("Vosk model not found at %s", path)
            return
        try:
            import sounddevice as sd
            from vosk import KaldiRecognizer
        except Exception as exc:
            self._set_status(f"Speech recognition unavailable · {exc}")
            log.exception("Vosk/sounddevice import failed")
            return

        try:
            self._model = _load_model(str(path))
        except Exception as exc:
            self._set_status(f"Speech model could not be loaded · {exc}")
            log.exception("Could not load the Vosk model")
            return

        device = self._device()
        while not self._stop.is_set():
            audio_q: queue.Queue = queue.Queue(maxsize=16)

            def callback(indata, frames, time_info, status, _q=audio_q):
                if status:
                    log.debug("Microphone status: %s", status)
                try:
                    _q.put_nowait(bytes(indata))
                except queue.Full:
                    # Drop the oldest block so the queue stays bounded.
                    try:
                        _q.get_nowait()
                        _q.put_nowait(bytes(indata))
                    except queue.Empty:
                        pass

            try:
                if self.always_listen:
                    recognizer = KaldiRecognizer(self._model, SAMPLE_RATE)
                else:
                    recognizer = KaldiRecognizer(self._model, SAMPLE_RATE, WAKE_GRAMMAR)
                with sd.RawInputStream(
                    samplerate=SAMPLE_RATE,
                    blocksize=BLOCK_SIZE,
                    dtype="int16",
                    channels=1,
                    callback=callback,
                    device=device,
                ):
                    self._drain(audio_q)
                    self._reset(recognizer)
                    if self.always_listen:
                        self._set_status("Listening continuously")
                        log.info("Continuous listener started on device %s",
                                 device if device is not None else "default")
                    else:
                        self._set_status("Listening for NOVA")
                        log.info("Wake-word listener started on device %s",
                                 device if device is not None else "default")
                    self._emit("mic_error", "")
                    if self.always_listen:
                        self._listen_loop(audio_q, recognizer)
                    else:
                        self._wake_loop(audio_q, recognizer)
            except Exception as exc:
                if self._stop.is_set():
                    break
                log.warning("Microphone stream failed: %s", exc)
                self._set_status("Microphone unavailable · retrying")
                self._emit("mic_error", str(exc))
                self.face_callback("idle")
                self._stop.wait(2.5)
                if device is not None:
                    # Configured device may have been unplugged; fall back once.
                    device = None

        self._continuous = False
        self._set_status("Listening stopped")
        self.face_callback("idle")

    def _listen_loop(self, audio_q: queue.Queue, recognizer) -> None:
        """Always-on command recognition: no wake word and no beep.

        Only exact, known command phrases are executed.  Anything else is
        ignored silently, so background noise can never trigger the old
        "Sorry, I didn't catch that" loop.
        """
        self._continuous = True
        self._recent.clear()
        try:
            while not self._stop.is_set():
                # Never listen while NOVA is speaking, otherwise it hears itself.
                if self._speech_is_speaking():
                    self._drain(audio_q)
                    self._reset(recognizer)
                    self._stop.wait(0.05)
                    continue

                try:
                    pcm = audio_q.get(timeout=0.25)
                except queue.Empty:
                    continue

                if self._speech_is_speaking():
                    self._drain(audio_q)
                    self._reset(recognizer)
                    continue

                try:
                    if not recognizer.AcceptWaveform(pcm):
                        continue
                    text = json.loads(recognizer.Result()).get("text", "").strip()
                except Exception:
                    log.exception("Continuous recognizer failed")
                    return
                if not text:
                    continue
                if self._dispatch_transcript(text, audio_q, recognizer) not in (
                    None, "none", "unknown",
                ):
                    # A command ran: drop any audio from its spoken reply.
                    self._wait_for_speech_end(20.0)
                    self._drain(audio_q)
                    self._reset(recognizer)
                    self.face_callback("idle")
                    self._set_status("Listening continuously")
        finally:
            self._continuous = False

    def _dispatch_transcript(self, text: str, audio_q: queue.Queue, recognizer) -> str | None:
        """Match a final transcript, joining short fragments if needed.

        Vosk sometimes ends a phrase early, so "study" and "mode" can arrive as
        two finals.  The last few fragments are tried longest-first, which makes
        multi-word commands reliable without any fuzzy matching.
        """
        now = time.monotonic()
        self._recent = [(part, stamp) for part, stamp in self._recent if now - stamp <= 2.5]
        self._recent.append((text, now))
        if len(self._recent) > 5:
            del self._recent[:-5]

        candidates: list[str] = []
        for count in (5, 4, 3, 2, 1):
            if len(self._recent) >= count:
                candidate = " ".join(part for part, _ in self._recent[-count:]).strip()
                if candidate and candidate not in candidates:
                    candidates.append(candidate)

        for candidate in candidates:
            if (candidate == self._last_executed[0]
                    and now - self._last_executed[1] < 2.0):
                continue
            action = self._execute_phrase(candidate)
            if action not in (None, "none", "unknown"):
                self._recent.clear()
                self._last_executed = (candidate, now)
                return action
        return None

    def _execute_phrase(self, phrase: str) -> str:
        try:
            action = self.command_router.handle(phrase, announce_unknown=False)
        except TypeError:
            action = self.command_router.handle(phrase)
        except Exception:
            log.exception("Command handling failed")
            return "unknown"
        if action in (None, "none", "unknown"):
            log.info("Ignored unrecognized speech: %s", phrase)
            return "unknown"
        self._emit("last_heard", phrase)
        self._emit("last_command", phrase)
        if not str(action).startswith(("custom", "automation")):
            self._emit("last_result", f"Executed: {action}")
        self.face_callback("listening")
        return str(action)

    def _wake_loop(self, audio_q: queue.Queue, recognizer) -> None:
        self._continuous = True
        try:
            while not self._stop.is_set():
                # While NOVA itself is speaking, discard microphone audio so its
                # own voice can never be recognised as a wake word or command.
                if self._speech_is_speaking():
                    self._drain(audio_q)
                    self._reset(recognizer)
                    self._stop.wait(0.05)
                    continue

                if self._manual.is_set():
                    self._manual.clear()
                    self._run_command_session(audio_q)
                    self._reset(recognizer)
                    self._drain(audio_q)
                    continue

                try:
                    pcm = audio_q.get(timeout=0.25)
                except queue.Empty:
                    continue

                if self._speech_is_speaking():
                    self._drain(audio_q)
                    self._reset(recognizer)
                    continue

                try:
                    if recognizer.AcceptWaveform(pcm):
                        text = json.loads(recognizer.Result()).get("text", "")
                    else:
                        text = json.loads(recognizer.PartialResult()).get("partial", "")
                except Exception:
                    log.exception("Wake recognizer failed")
                    return

                now = time.monotonic()
                if now < self._next_wake_at:
                    continue
                if is_wake_phrase(text):
                    self._next_wake_at = now + WAKE_DEBOUNCE
                    log.info("Wake word detected: %s", text)
                    self._run_command_session(audio_q)
                    self._reset(recognizer)
                    self._drain(audio_q)
        finally:
            self._continuous = False

    # -- one command session ----------------------------------------------
    def _run_command_session(self, audio_q: queue.Queue, recognizer=None) -> None:
        if not self._session_lock.acquire(blocking=False):
            return
        try:
            self.face_callback("listening")
            self._set_status("Listening for your command…")
            # Never start capturing while an announcement is still playing.
            self._wait_for_speech_end(10.0)
            if self._stop.is_set():
                return
            self._beep_and_settle(audio_q)
            if self._stop.is_set():
                return

            if recognizer is None:
                from vosk import KaldiRecognizer

                recognizer = KaldiRecognizer(self._model, SAMPLE_RATE)

            phrase, heard_speech, aborted = self._capture(audio_q, recognizer)
            if self._stop.is_set() or aborted:
                return
            if not heard_speech:
                # CASE A: no actual speech. Stay completely silent.
                self._emit("last_result", "No speech detected")
                return
            if not phrase:
                # CASE B: speech but nothing intelligible.
                self._handle_unintelligible(audio_q)
                return

            # CASE C: a transcript exists; the deterministic router decides.
            self._emit("last_heard", phrase)
            self._set_status(f"Heard: {phrase}")
            try:
                action = self.command_router.handle(phrase, announce_unknown=False)
            except TypeError:
                # Older routers without the keyword argument.
                action = self.command_router.handle(phrase)
            except Exception:
                log.exception("Command handling failed")
                action = "unknown"

            if action in (None, "none", "unknown"):
                self._handle_unintelligible(audio_q)
            else:
                self._emit("last_command", phrase)
                # Custom commands and automations report their own real result.
                if not str(action).startswith(("custom", "automation")):
                    self._emit("last_result", f"Executed: {action}")
            # Wait for any spoken confirmation before reopening the microphone.
            self._wait_for_speech_end(20.0)
            self._drain(audio_q)
        finally:
            self.face_callback("idle")
            self._set_status("Listening for NOVA" if self._continuous else "Listening stopped")
            self._session_lock.release()

    def _beep_and_settle(self, audio_q: queue.Queue) -> None:
        self._drain(audio_q)
        try:
            self._beep()
        except Exception:
            log.debug("Could not play the confirmation beep", exc_info=True)
        self._gate_microphone(audio_q, BEEP_GATE_SECONDS)
        self._gate_microphone(audio_q, SETTLE_SECONDS)
        self._drain(audio_q)

    def _handle_unintelligible(self, audio_q: queue.Queue) -> None:
        """Ignore speech that did not match a command; never nag the user."""
        self._set_status("Speech detected · no matching command")
        self._emit("last_result", "No matching command")
        self._drain(audio_q)

    def _capture(self, audio_q: queue.Queue, recognizer) -> tuple[str, bool, bool]:
        """Return (text, heard_speech, aborted).

        Silence is detected with a small RMS threshold, so a command is not cut
        off and background noise does not create endless listening sessions.
        """
        started = time.monotonic()
        last_voice = started
        speech_blocks = 0
        heard_speech = False

        while time.monotonic() - started < COMMAND_MAX_SECONDS:
            if self._stop.is_set():
                return "", heard_speech, True
            if self._speech_is_speaking():
                self._drain(audio_q)
                return "", heard_speech, True
            try:
                data = audio_q.get(timeout=0.2)
            except queue.Empty:
                if not heard_speech and time.monotonic() - started > COMMAND_START_TIMEOUT:
                    return "", False, False
                if heard_speech and time.monotonic() - last_voice > COMMAND_SILENCE:
                    break
                continue

            if _rms(data) >= SPEECH_RMS_THRESHOLD:
                speech_blocks += 1
                if speech_blocks >= MIN_SPEECH_BLOCKS:
                    heard_speech = True
                last_voice = time.monotonic()

            try:
                if recognizer.AcceptWaveform(data):
                    text = json.loads(recognizer.Result()).get("text", "").strip()
                    if text:
                        log.info("Voice command recognized: %s", text)
                        return text, True, False
            except Exception:
                log.exception("Command recognition failed")
                return "", heard_speech, False

            if heard_speech and time.monotonic() - last_voice > COMMAND_SILENCE:
                break

        try:
            text = json.loads(recognizer.FinalResult()).get("text", "").strip()
        except Exception:
            text = ""
        if text:
            heard_speech = True
        return text, heard_speech, False

    # -- manual listening when the wake loop is not running ----------------
    def _manual_session(self) -> None:
        path = self._model_path()
        if not model_is_ready(path):
            self._set_status("Speech model missing · download it in Voice settings")
            return
        try:
            import sounddevice as sd
        except Exception as exc:
            self._set_status(f"Microphone unavailable · {exc}")
            return
        try:
            if self._model is None:
                self._model = _load_model(str(path))
        except Exception as exc:
            self._set_status(f"Speech model could not be loaded · {exc}")
            return

        audio_q: queue.Queue = queue.Queue(maxsize=16)

        def callback(indata, frames, time_info, status):
            try:
                audio_q.put_nowait(bytes(indata))
            except queue.Full:
                try:
                    audio_q.get_nowait()
                    audio_q.put_nowait(bytes(indata))
                except queue.Empty:
                    pass

        try:
            with sd.RawInputStream(
                samplerate=SAMPLE_RATE, blocksize=BLOCK_SIZE, dtype="int16",
                channels=1, callback=callback, device=self._device(),
            ):
                self._drain(audio_q)
                self._run_command_session(audio_q)
        except Exception as exc:
            log.warning("Manual listen failed: %s", exc)
            self._set_status(f"Microphone unavailable · {exc}")
            self._emit("mic_error", str(exc))
            self.face_callback("idle")
