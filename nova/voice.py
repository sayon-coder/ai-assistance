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

from .commands import build_recognition_grammar, is_command_like, normalize_phrase
from .model_setup import model_is_ready, resolve_model
from .phrase import PHRASE_TIMEOUT, PhraseAssembler

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
MIN_FINAL_BLOCKS = 1         # voiced blocks required before a final can run
N_ALTERNATIVES = 5           # Vosk's own hypotheses, exact-matched (never fuzzy)
DESTRUCTIVE_ALTERNATIVES = 2  # exit/quit may only come from the best two
COMMAND_DEBOUNCE = 2.0       # the same command cannot run twice this quickly
REPEAT_COOLDOWN = 15.0       # never nag more than once per this window
REPEAT_MESSAGE = "Sorry, I didn't catch that command. Please repeat."
DESTRUCTIVE_ACTIONS = {"exit"}
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


def _has_unknown(text: str) -> bool:
    return "[unk]" in str(text or "").lower()


def _result_texts(payload) -> list[str]:
    """Return the primary text and Vosk's own alternatives, best first."""
    if not isinstance(payload, dict):
        return []
    texts: list[str] = []
    primary = str(payload.get("text", "") or "").strip()
    if primary:
        texts.append(primary)
    for alternative in payload.get("alternatives", []) or []:
        if not isinstance(alternative, dict):
            continue
        text = str(alternative.get("text", "") or "").strip()
        if text and text not in texts:
            texts.append(text)
    return texts


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
                 face_callback=None, event_callback=None, beep=None,
                 grammar_provider=None, phrase_timeout=None):
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
        self._last_executed: tuple[str, float] = ("", 0.0)
        self._last_repeat_at = 0.0

        # -- dynamic grammar -------------------------------------------------
        self._grammar_provider = grammar_provider
        self._grammar_lock = threading.Lock()
        self._grammar_json = ""
        self._grammar_phrases: set[str] = set()
        self._grammar_dirty = threading.Event()
        timeout = phrase_timeout
        if timeout is None:
            try:
                timeout = float(self.settings.get("phrase_timeout", PHRASE_TIMEOUT))
            except (TypeError, ValueError):
                timeout = PHRASE_TIMEOUT
        timeout = max(2.0, min(8.0, float(timeout)))
        self._assembler = PhraseAssembler(
            vocabulary=self._vocabulary, timeout=timeout
        )
        self.refresh_grammar()

    # -- vocabulary and grammar -------------------------------------------
    def _vocabulary(self) -> set[str] | None:
        with self._grammar_lock:
            if self._grammar_phrases:
                return set(self._grammar_phrases)
        registry = getattr(self.command_router, "registry", None)
        if registry is None:
            # Lightweight routers (for example in tests) have no registry; let
            # the assembler fall back to trying every exact candidate.
            return None
        try:
            return set(registry.phrases())
        except Exception:
            log.debug("Could not read registry phrases", exc_info=True)
            return None

    def _grammar_source(self) -> set[str]:
        if self._grammar_provider is not None:
            try:
                return {str(item) for item in self._grammar_provider() if str(item).strip()}
            except Exception:
                log.exception("Grammar provider failed")
        registry = getattr(self.command_router, "registry", None)
        if registry is not None:
            try:
                return set(registry.phrases())
            except Exception:
                log.debug("Could not read registry phrases", exc_info=True)
        return set()

    def refresh_grammar(self) -> None:
        """Rebuild the command grammar and schedule a thread-safe application.

        The Vosk model is never reloaded and the microphone is not restarted;
        the listener thread applies the new grammar between audio blocks.
        """
        phrases = self._grammar_source()
        grammar = build_recognition_grammar(phrases)
        with self._grammar_lock:
            self._grammar_phrases = {str(p) for p in phrases}
            self._grammar_json = grammar
        self._grammar_dirty.set()

    def _current_grammar(self) -> str:
        with self._grammar_lock:
            return self._grammar_json

    def _apply_grammar(self, recognizer):
        """Swap in a recogniser built from the latest command vocabulary.

        This runs in the listener thread and never restarts the microphone or
        reloads the Vosk model.  A fresh recogniser is used instead of
        SetGrammar because SetGrammar drops words that are missing from the
        model's word list (for example "unmute").
        """
        if not self._grammar_dirty.is_set():
            return recognizer
        self._grammar_dirty.clear()
        if not self._current_grammar() or self._model is None:
            return recognizer
        try:
            fresh = self._make_recognizer()
            log.info("Recognition grammar refreshed (%d phrases)", len(self._grammar_phrases))
            return fresh
        except Exception:
            log.exception("Could not refresh the recognition grammar; keeping the previous one")
            return recognizer

    def _make_recognizer(self, wake: bool = False):
        from vosk import KaldiRecognizer

        grammar = WAKE_GRAMMAR if wake else self._current_grammar()
        try:
            recognizer = (
                KaldiRecognizer(self._model, SAMPLE_RATE, grammar) if grammar
                else KaldiRecognizer(self._model, SAMPLE_RATE)
            )
        except Exception:
            log.exception("Grammar recogniser could not be created; using generic recognition")
            recognizer = KaldiRecognizer(self._model, SAMPLE_RATE)
        if not wake:
            try:
                recognizer.SetMaxAlternatives(N_ALTERNATIVES)
            except Exception:
                log.debug("Recognition alternatives are not supported", exc_info=True)
        return recognizer

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
                recognizer = self._make_recognizer(wake=not self.always_listen)
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

        Final segments are accumulated for a short, bounded window so a long
        command split by Vosk is still matched exactly.  Only exact registered
        phrases run; everything else is ignored (or asks for one repeat when it
        clearly sounds like a command attempt).
        """
        self._continuous = True
        self._assembler.reset()
        voiced_blocks = 0
        voiced_peak = 0.0
        try:
            while not self._stop.is_set():
                # Grammar updates are applied here, in the listener thread, so
                # the microphone and model are never touched from the UI thread.
                updated = self._apply_grammar(recognizer)
                if updated is not recognizer:
                    recognizer = updated
                    voiced_blocks = 0
                    voiced_peak = 0.0

                # Never listen while NOVA is speaking, otherwise it hears itself.
                if self._speech_is_speaking():
                    self._drain(audio_q)
                    self._reset(recognizer)
                    voiced_blocks = 0
                    voiced_peak = 0.0
                    self._stop.wait(0.05)
                    continue

                try:
                    pcm = audio_q.get(timeout=0.25)
                except queue.Empty:
                    if self._assembler.expire():
                        self._set_status("Listening continuously")
                    continue

                # Clear a stale pending phrase even while audio keeps arriving.
                if self._assembler.expire():
                    self._set_status("Listening continuously")

                if self._speech_is_speaking():
                    self._drain(audio_q)
                    self._reset(recognizer)
                    voiced_blocks = 0
                    voiced_peak = 0.0
                    continue

                level = _rms(pcm)
                if level >= SPEECH_RMS_THRESHOLD:
                    voiced_blocks += 1
                    voiced_peak = max(voiced_peak, level)

                try:
                    if not recognizer.AcceptWaveform(pcm):
                        continue
                    texts = _result_texts(json.loads(recognizer.Result()))
                except Exception:
                    log.exception("Continuous recognizer failed")
                    return
                # Never treat an unknown-word marker as a command.
                texts = [item for item in texts if not _has_unknown(item)]
                if not texts:
                    voiced_blocks = 0
                    voiced_peak = 0.0
                    continue
                # A final with no voiced audio is noise or silence decoded as a
                # short command; it must never execute.
                if voiced_blocks < MIN_FINAL_BLOCKS:
                    log.info(
                        "Ignored a final without voiced audio: %s (level=%.0f)",
                        " | ".join(texts), voiced_peak,
                    )
                    self._reset(recognizer)
                    voiced_blocks = 0
                    voiced_peak = 0.0
                    continue
                log.info(
                    "Heard: %s (level=%.0f, voiced=%d)",
                    " | ".join(texts), voiced_peak, voiced_blocks,
                )
                action = self._dispatch_transcript(
                    texts[0], audio_q, recognizer, alternatives=texts[1:]
                )
                voiced_blocks = 0
                voiced_peak = 0.0
                if action not in (None, "none", "unknown"):
                    # A command ran: drop any audio from its spoken reply.
                    if not self._wait_for_speech_end(20.0):
                        log.warning("Speech output stayed busy; resuming listening")
                    self._drain(audio_q)
                    self._reset(recognizer)
                    self.face_callback("idle")
                    self._set_status("Listening continuously")
        finally:
            self._continuous = False

    def _dispatch_transcript(self, text: str, audio_q: queue.Queue, recognizer,
                             alternatives=None) -> str | None:
        """Match a final transcript through the bounded phrase assembler.

        Vosk may finalise a command in several segments, and its own alternative
        hypotheses are exact-matched too.  No similarity scoring is used.
        """
        now = time.monotonic()
        texts = [item for item in [text, *(alternatives or [])] if not _has_unknown(item)]
        if not texts:
            return None
        assembly = self._assembler.add(texts, now=now)
        if assembly.heard:
            self._emit("last_heard", assembly.heard)

        if assembly.pending:
            self._set_status(f"Heard: {assembly.heard} …")
            return None

        weak = {
            normalize_phrase(item)
            for item in (alternatives or [])[max(0, DESTRUCTIVE_ALTERNATIVES - 1):]
        }
        attempts = [assembly.matched] if assembly.matched else list(assembly.candidates)
        for index, candidate in enumerate(attempts):
            if not candidate:
                continue
            if self._is_destructive(candidate) and candidate in weak:
                log.info("Ignored a weak destructive hypothesis: %s", candidate)
                continue
            if (candidate == self._last_executed[0]
                    and now - self._last_executed[1] < COMMAND_DEBOUNCE):
                self._assembler.reset()
                return None
            action = self._execute_phrase(candidate, primary=index == 0)
            if action not in (None, "none", "unknown"):
                self._assembler.reset()
                self._last_executed = (candidate, now)
                return action
        if assembly.heard:
            self._handle_unintelligible(assembly.heard, audio_q)
        return None

    def _is_destructive(self, phrase: str) -> bool:
        registry = getattr(self.command_router, "registry", None)
        matcher = getattr(registry, "match", None)
        if not callable(matcher):
            return False
        try:
            command = matcher(phrase)
        except Exception:
            return False
        return command is not None and getattr(command, "action", "") in DESTRUCTIVE_ACTIONS

    def _execute_phrase(self, phrase: str, primary: bool = True) -> str:
        try:
            try:
                action = self.command_router.handle(phrase, announce_unknown=False)
            except TypeError:
                action = self.command_router.handle(phrase)
        except Exception as exc:
            # The phrase matched but the action itself failed.  This is an
            # execution failure, not a recognition failure.
            log.exception("Command handling failed")
            self._emit("last_match", phrase)
            self._emit("last_command", phrase)
            self._emit("last_result", f"Action failed: {exc}")
            return "failed"
        if action in (None, "none", "unknown"):
            log.info("Ignored unrecognized speech: %s", phrase)
            return "unknown"
        self._emit("last_match", phrase)
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
                recognizer = self._make_recognizer()

            phrase, heard_speech, aborted = self._capture(audio_q, recognizer)
            if self._stop.is_set() or aborted:
                return
            if not heard_speech:
                # CASE A: no actual speech. Stay completely silent.
                self._emit("last_result", "No speech detected")
                return
            if not phrase:
                # CASE B: speech but nothing intelligible. Never execute.
                self._emit("last_failure", "Speech heard but no words recognised")
                return

            # CASE C: a transcript exists; the deterministic router decides.
            self._emit("last_heard", phrase)
            self._set_status(f"Heard: {phrase}")
            action = self._execute_phrase(phrase)
            if action in (None, "none", "unknown"):
                self._handle_unintelligible(phrase, audio_q)
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

    def _handle_unintelligible(self, text: str, audio_q: queue.Queue) -> None:
        """Ask for one repeat only when the utterance sounded like a command.

        Unrelated background conversation is ignored silently, and a cooldown
        prevents an apology loop.  Nothing is executed here.
        """
        phrase = normalize_phrase(text)
        if not phrase:
            self._drain(audio_q)
            return
        if not is_command_like(phrase, self._vocabulary() or ()):
            # Ordinary chatter is not a recognition failure, so leave the
            # diagnostics untouched and stay silent.
            self._drain(audio_q)
            return
        self._emit("last_failure", "No matching registered command")
        self._set_status("Speech detected · no matching command")
        now = time.monotonic()
        if now - self._last_repeat_at < REPEAT_COOLDOWN:
            self._drain(audio_q)
            return
        self._last_repeat_at = now
        speech = self._speech
        if speech is not None and getattr(speech, "enabled", False):
            try:
                say_and_wait = getattr(speech, "say_and_wait", None)
                if callable(say_and_wait):
                    say_and_wait(REPEAT_MESSAGE, 15.0)
                else:
                    speech.say(REPEAT_MESSAGE)
                    self._wait_for_speech_end(15.0)
            except Exception:
                log.exception("Could not speak the repeat request")
        self._wait_for_speech_end(15.0)
        self._drain(audio_q)

    def _capture(self, audio_q: queue.Queue, recognizer) -> tuple[str, bool, bool]:
        """Return (text, heard_speech, aborted).

        Every final segment is accumulated until the user stops speaking, so a
        command is not truncated when Vosk finalises its first short segment.
        Silence is detected with a small RMS threshold, and the phrase is only
        returned after the trailing-silence window has elapsed.
        """
        started = time.monotonic()
        last_voice = started
        speech_blocks = 0
        heard_speech = False
        finals: list[str] = []

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
                    for item in _result_texts(json.loads(recognizer.Result())):
                        if _has_unknown(item):
                            continue
                        if not finals or item != finals[-1]:
                            finals.append(item)
                        break
                    if finals:
                        heard_speech = True
            except Exception:
                log.exception("Command recognition failed")
                return "", heard_speech, False

            if heard_speech and time.monotonic() - last_voice > COMMAND_SILENCE:
                break

        try:
            for item in _result_texts(json.loads(recognizer.FinalResult())):
                if _has_unknown(item):
                    continue
                if not finals or item != finals[-1]:
                    finals.append(item)
                break
        except Exception:
            log.debug("Could not read the final recognition result", exc_info=True)
        phrase = normalize_phrase(" ".join(finals))
        if phrase:
            heard_speech = True
            log.info("Voice command recognized: %s", phrase)
        return phrase, heard_speech, False

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
