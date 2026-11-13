"""Fully offline wake-word ("NOVA") and command capture using Vosk.

No Picovoice key, no cloud speech service and no audio ever leaves the machine.
The small English Vosk model (~40 MB, Apache-2.0) is used for both wake-word
spotting and command recognition. CPU use is modest because recognition runs on
0.25 s audio blocks and the model is loaded once per process.
"""
from __future__ import annotations

import json
import logging
import queue
import threading
import time

from .model_setup import model_is_ready, resolve_model

log = logging.getLogger("nova.voice")

SAMPLE_RATE = 16000
BLOCK_SIZE = 4000  # 0.25 s at 16 kHz
WAKE_VARIANTS = ("nova", "no va", "nov a", "novak", "hey nova")
_MODEL_CACHE: dict[str, object] = {}


def _load_model(path: str):
    from vosk import Model, SetLogLevel

    if path not in _MODEL_CACHE:
        SetLogLevel(-1)
        log.info("Loading Vosk model from %s", path)
        _MODEL_CACHE[path] = Model(path)
    return _MODEL_CACHE[path]


def _play_beep() -> None:
    try:
        import winsound

        winsound.PlaySound("SystemAsterisk", winsound.SND_ALIAS | winsound.SND_ASYNC)
    except Exception:
        pass


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
    def __init__(self, settings: dict, command_router, status_callback=None, face_callback=None):
        self.settings = dict(settings or {})
        self.command_router = command_router
        self.status_callback = status_callback or (lambda text: None)
        self.face_callback = face_callback or (lambda state: None)
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._manual = threading.Event()
        self._status = ""
        self._model = None
        self._last_manual_thread: threading.Thread | None = None

    # -- helpers -----------------------------------------------------------
    def _set_status(self, text: str) -> None:
        if text == self._status:
            return
        self._status = text
        try:
            self.status_callback(text)
        except Exception:
            log.exception("Voice status callback failed")

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
        self._thread = None

    def request_manual_listen(self) -> None:
        """Capture one command now, even without saying the wake word."""
        if self._thread and self._thread.is_alive():
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
                    pass

            try:
                recognizer = KaldiRecognizer(self._model, SAMPLE_RATE, '["nova", "[unk]"]')
                with sd.RawInputStream(
                    samplerate=SAMPLE_RATE,
                    blocksize=BLOCK_SIZE,
                    dtype="int16",
                    channels=1,
                    callback=callback,
                    device=device,
                ):
                    self._set_status("Listening for NOVA")
                    log.info("Wake-word listener started on device %s", device if device is not None else "default")
                    self._wake_loop(audio_q, recognizer)
            except Exception as exc:
                if self._stop.is_set():
                    break
                log.warning("Microphone stream failed: %s", exc)
                self._set_status("Microphone unavailable · retrying")
                self.face_callback("idle")
                self._stop.wait(2.5)
                if device is not None:
                    # Configured device may have been unplugged; fall back once.
                    device = None

        self._set_status("Wake word off")
        self.face_callback("idle")

    def _wake_loop(self, audio_q: queue.Queue, recognizer) -> None:
        while not self._stop.is_set():
            if self._manual.is_set():
                self._manual.clear()
                self._acknowledge_and_capture(audio_q)
                try:
                    recognizer.Reset()
                except Exception:
                    log.debug("Wake recognizer could not be reset")
            try:
                pcm = audio_q.get(timeout=0.3)
            except queue.Empty:
                continue
            try:
                if recognizer.AcceptWaveform(pcm):
                    text = json.loads(recognizer.Result()).get("text", "")
                else:
                    text = json.loads(recognizer.PartialResult()).get("partial", "")
            except Exception:
                log.exception("Wake recognizer failed")
                return
            if self._is_wake(text):
                log.info("Wake word detected: %s", text)
                self._acknowledge_and_capture(audio_q)
                try:
                    recognizer.Reset()
                except Exception:
                    pass

    @staticmethod
    def _is_wake(text: str) -> bool:
        normalized = " ".join(str(text or "").lower().split())
        return any(variant in normalized for variant in WAKE_VARIANTS)

    def _acknowledge_and_capture(self, audio_q: queue.Queue) -> None:
        from vosk import KaldiRecognizer

        self.face_callback("listening")
        self._set_status("Listening for your command…")
        _play_beep()
        speech = getattr(self.command_router, "speech", None)
        if speech is not None and getattr(speech, "enabled", False):
            speech.say("Yes, sir?")
            time.sleep(0.55)
        phrase = self._capture(audio_q, KaldiRecognizer(self._model, SAMPLE_RATE))
        if phrase and not self._stop.is_set():
            self._set_status(f"Heard: {phrase}")
            try:
                self.command_router.handle(phrase)
            except Exception:
                log.exception("Command handling failed")
        else:
            self._set_status("Listening for NOVA")
        self.face_callback("idle")

    def _capture(self, audio_q: queue.Queue, recognizer, timeout: float = 8.0) -> str:
        started = time.monotonic()
        heard_voice = False
        last_voice = started
        while time.monotonic() - started < timeout and not self._stop.is_set():
            try:
                data = audio_q.get(timeout=0.3)
            except queue.Empty:
                continue
            try:
                if recognizer.AcceptWaveform(data):
                    text = json.loads(recognizer.Result()).get("text", "").strip()
                    if text:
                        log.info("Voice command recognized: %s", text)
                        return text
                else:
                    partial = json.loads(recognizer.PartialResult()).get("partial", "").strip()
                    if partial:
                        heard_voice = True
                        last_voice = time.monotonic()
            except Exception:
                log.exception("Command recognition failed")
                return ""
            if heard_voice and time.monotonic() - last_voice > 1.6:
                break
        try:
            return json.loads(recognizer.FinalResult()).get("text", "").strip()
        except Exception:
            return ""

    def _manual_session(self) -> None:
        """One-off listening when the continuous wake loop is not running."""
        from vosk import KaldiRecognizer

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
                pass

        self._set_status("Listening for your command…")
        self.face_callback("listening")
        try:
            with sd.RawInputStream(
                samplerate=SAMPLE_RATE, blocksize=BLOCK_SIZE, dtype="int16",
                channels=1, callback=callback, device=self._device(),
            ):
                _play_beep()
                time.sleep(0.35)
                phrase = self._capture(audio_q, KaldiRecognizer(self._model, SAMPLE_RATE))
        except Exception as exc:
            log.warning("Manual listen failed: %s", exc)
            self._set_status(f"Microphone unavailable · {exc}")
            self.face_callback("idle")
            return
        if phrase:
            self._set_status(f"Heard: {phrase}")
            try:
                self.command_router.handle(phrase)
            except Exception:
                log.exception("Command handling failed")
        self.face_callback("idle")
        self._set_status("Wake word off")
