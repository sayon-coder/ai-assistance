"""Application services: speech, wake word, coding monitors and shared state."""
from __future__ import annotations

import logging
import threading
from pathlib import Path

from PySide6.QtCore import QObject

from . import startup
from .bridge import UiBridge
from .commands import CommandRouter
from .config import (
    ROOT, load_modes, load_settings, save_modes as persist_modes,
    save_settings as persist_settings,
)
from .model_setup import download as download_model, model_is_ready, resolve_model
from .modes import ModeManager
from .monitors.codex import CodexMonitor
from .monitors.opencode import OpenCodeMonitor
from .speech import SpeechOutput
from .voice import VoiceController, list_microphones

log = logging.getLogger("nova.runtime")


class NovaRuntime(QObject):
    def __init__(self):
        super().__init__()
        self.bridge = UiBridge()
        self.settings = load_settings()
        self.modes_data = load_modes()
        self.muted = not self.settings.get("voice_enabled", True)

        self.speech = SpeechOutput(
            enabled=not self.muted,
            on_speaking=self._on_speech_state,
            on_error=self._on_speech_error,
        )
        self.modes = ModeManager(self.modes_data, self.speech, self._notify)
        self.commands = CommandRouter(self.modes, self.speech, self)

        self.voice: VoiceController | None = None
        self.opencode: OpenCodeMonitor | None = None
        self.codex: CodexMonitor | None = None
        self.voices: list[str] = []
        self._downloading = False
        self._stopped = False
        self._face_state = "idle"

        self.status = {
            "voice": "Starting voice services…",
            "speech": "Muted" if self.muted else "Windows speech ready",
            "opencode": "OpenCode starting…",
            "codex": "Codex starting…",
            "last_event": "NOVA is ready, sir.",
        }

    # -- state helpers -----------------------------------------------------
    def status_snapshot(self) -> dict:
        return dict(self.status)

    def _set_status(self, key: str, value: str) -> None:
        if self.status.get(key) == value:
            return
        self.status[key] = value
        try:
            self.bridge.status_changed.emit(self.status_snapshot())
        except Exception:
            log.exception("Status emit failed")

    def _set_face(self, state: str) -> None:
        self._face_state = state
        try:
            self.bridge.face_state_changed.emit(state)
        except Exception:
            log.exception("Face state emit failed")

    def _notify(self, title: str, body: str) -> None:
        try:
            self.bridge.notification.emit(str(title), str(body))
        except Exception:
            log.exception("Notification emit failed")

    def _on_speech_state(self, speaking: bool) -> None:
        self.bridge.speech_state.emit(bool(speaking))
        if speaking:
            if self._face_state in {"idle", "speaking"}:
                self._set_face("speaking")
        elif self._face_state == "speaking":
            self._set_face("idle")

    def _on_speech_error(self, message: str) -> None:
        self._set_status("speech", message)
        log.warning("Speech problem: %s", message)

    # -- lifecycle ---------------------------------------------------------
    def start(self) -> None:
        self.speech.start()
        threading.Thread(target=self._load_voices, name="NOVA-Voices", daemon=True).start()
        self._start_voice()
        self._start_opencode()
        self._start_codex()
        self._set_status("last_event", "NOVA is ready, sir.")
        log.info("Runtime started")

    def _load_voices(self) -> None:
        voices = self.speech.voices()
        self.voices = voices
        if voices:
            chosen = self.settings.get("speech_voice") or ""
            if chosen not in voices:
                preferred = next(
                    (v for v in voices if any(n in v for n in ("Aria", "Zira", "Guy", "David"))),
                    voices[0],
                )
                chosen = preferred
                self.settings["speech_voice"] = chosen
                persist_settings(self.settings)
            self.speech.set_voice(chosen)
            self._set_status("speech", "Muted" if self.muted else f"Windows voice · {chosen}")
        else:
            self._set_status("speech", "Windows speech unavailable")
        self.bridge.voices_available.emit(list(voices))
        self.speech.set_rate(int(self.settings.get("speech_rate", 0)))
        self.speech.set_volume(int(self.settings.get("speech_volume", 90)))

    def _start_voice(self) -> None:
        if self.voice is not None:
            self.voice.stop()
            self.voice = None
        if not self.settings.get("wake_enabled", True):
            self._set_status("voice", "Wake word off")
            return
        model = resolve_model(self.settings.get("vosk_model_path"))
        if not model_is_ready(model):
            self._set_status("voice", "Speech model missing · download it in Voice settings")
            log.info("Wake word not started: Vosk model missing at %s", model)
            return
        self.voice = VoiceController(
            self.settings, self.commands, self._voice_status, self._set_face
        )
        self.voice.start()

    def _voice_status(self, text: str) -> None:
        self._set_status("voice", text)

    def _start_opencode(self) -> None:
        if self.opencode is not None:
            self.opencode.stop()
            self.opencode = None
        if not self.settings.get("opencode_enabled", True):
            self._set_status("opencode", "Monitoring disabled")
            return
        self._set_status("opencode", "OpenCode connecting…")
        self.opencode = OpenCodeMonitor(
            self.settings.get("opencode_base_url", ""),
            lambda kind, message, session: self._coding_event("OpenCode", kind, message, session),
            lambda text: self._set_status("opencode", text),
        )
        self.opencode.start()

    def _start_codex(self) -> None:
        if self.codex is not None:
            self.codex.stop()
            self.codex = None
        if not self.settings.get("codex_enabled", True):
            self._set_status("codex", "Monitoring disabled")
            return
        self._set_status("codex", "Codex starting…")
        self.codex = CodexMonitor(
            lambda kind, message, session: self._coding_event("Codex", kind, message, session),
            lambda text: self._set_status("codex", text),
        )
        self.codex.start()

    # -- events ------------------------------------------------------------
    def _coding_event(self, source: str, kind: str, message: str, session: str = "") -> None:
        log.info("%s coding event: %s | %s | %s", source, kind, message, session)
        if kind == "working":
            self._set_face("working")
            self._set_status("last_event", f"{source} is working on a task.")
        elif kind == "success":
            self._set_face("completed")
            self.speech.say("Sir, " + message)
            self._notify(f"{source} task completed", message)
            self._set_status("last_event", f"{source}: {message}")
        elif kind == "failure":
            self._set_face("error")
            self.speech.say("Sir, " + message)
            self._notify(f"{source} task needs attention", message)
            self._set_status("last_event", f"{source}: {message}")
        elif kind == "interrupted":
            self._set_face("idle")
            if self.settings.get("announce_interruptions", False):
                self.speech.say("Sir, " + message)
            self._set_status("last_event", f"{source}: {message}")

    # -- commands callable from the service layer -------------------------
    def activate_mode(self, name: str) -> bool:
        ok = self.modes.activate(name)
        self._set_face("completed" if ok else "error")
        if ok:
            self._set_status("last_event", f"{name.title()} mode activated.")
        return ok

    def show_settings(self) -> None:
        self.bridge.open_settings.emit("dashboard")

    def hide_to_tray(self) -> None:
        self.bridge.hide_to_tray.emit()

    def set_face_visible(self, visible: bool) -> None:
        self.bridge.face_visibility_requested.emit(bool(visible))

    def exit_completely(self) -> None:
        self.bridge.exit_requested.emit()

    def set_face_position(self, x: int, y: int, screen: str = "") -> None:
        self.settings["face_x"] = int(x)
        self.settings["face_y"] = int(y)
        self.settings["face_screen"] = screen
        try:
            self.settings = persist_settings(self.settings)
        except Exception:
            log.exception("Could not persist the face position")

    def set_muted(self, muted: bool) -> None:
        muted = bool(muted)
        self.muted = muted
        self.settings["voice_enabled"] = not muted
        self.speech.set_enabled(not muted)
        try:
            self.settings = persist_settings(self.settings)
        except Exception:
            log.exception("Could not persist mute state")
        self._set_status("speech", "Muted" if muted else "Windows speech ready")
        self._notify("NOVA voice", "Voice muted." if muted else "Voice enabled.")
        log.info("Speech %s", "muted" if muted else "enabled")

    def set_wake_enabled(self, enabled: bool) -> None:
        enabled = bool(enabled)
        if self.settings.get("wake_enabled") == enabled:
            return
        self.settings["wake_enabled"] = enabled
        try:
            self.settings = persist_settings(self.settings)
        except Exception:
            log.exception("Could not persist wake setting")
        self._start_voice()
        self.bridge.settings_changed.emit(dict(self.settings))
        self._notify("NOVA listening", "Wake word enabled." if enabled else "Wake word disabled.")

    def listen_now(self) -> None:
        if not model_is_ready(resolve_model(self.settings.get("vosk_model_path"))):
            self._notify("Voice setup", "Download the offline speech model first (Voice settings).")
            return
        if self.voice is None:
            self.voice = VoiceController(
                self.settings, self.commands, self._voice_status, self._set_face
            )
            self.voice.start()
        self.voice.request_manual_listen()

    def test_voice(self) -> None:
        if self.muted:
            self.set_muted(False)
        self.speech.say("This is how I sound, sir. All speech is generated locally by Windows.")

    def microphones(self) -> list[tuple[int | None, str]]:
        return list_microphones()

    def refresh_voices(self) -> None:
        threading.Thread(target=self._load_voices, name="NOVA-Voices", daemon=True).start()

    def download_speech_model(self) -> None:
        if self._downloading:
            return
        self._downloading = True

        def progress(percent: int, text: str) -> None:
            self.bridge.model_progress.emit(int(percent), str(text))

        def work() -> None:
            try:
                path = download_model(progress)
                try:
                    stored = str(path.relative_to(ROOT))
                except ValueError:
                    stored = str(path)
                self.settings["vosk_model_path"] = stored
                self.settings = persist_settings(self.settings)
                self.bridge.model_progress.emit(100, "Speech model installed. Starting the listener…")
                self._start_voice()
                self._notify("Voice ready", "The offline speech model is installed.")
            except Exception as exc:
                log.exception("Speech model download failed")
                self.bridge.model_progress.emit(-1, f"Download failed: {exc}")
                self._notify("Voice setup", f"Speech model download failed: {exc}")
            finally:
                self._downloading = False

        threading.Thread(target=work, name="NOVA-ModelDownload", daemon=True).start()

    # -- settings ----------------------------------------------------------
    def save_settings(self, values: dict) -> dict:
        old = dict(self.settings)
        merged = dict(self.settings)
        merged.update(values or {})
        if "voice_enabled" in merged:
            self.muted = not bool(merged["voice_enabled"])
            merged["voice_enabled"] = not self.muted
        normalized = persist_settings(merged)
        self.settings = normalized
        self.speech.set_enabled(not self.muted)
        self.speech.set_voice(normalized.get("speech_voice", ""))
        self.speech.set_rate(int(normalized.get("speech_rate", 0)))
        self.speech.set_volume(int(normalized.get("speech_volume", 90)))

        voice_keys = {"vosk_model_path", "microphone_device", "wake_enabled"}
        if any(old.get(key) != normalized.get(key) for key in voice_keys):
            self._start_voice()
        if any(old.get(key) != normalized.get(key) for key in {"opencode_enabled", "opencode_base_url"}):
            self._start_opencode()
        if old.get("codex_enabled") != normalized.get("codex_enabled"):
            self._start_codex()

        if "start_with_windows" in (values or {}):
            # Compare against the real shortcut, not just the stored flag, so a
            # missing or externally changed startup entry is repaired either way.
            desired = bool(normalized.get("start_with_windows"))
            if desired != startup.is_enabled():
                ok, message = startup.enable() if desired else startup.disable()
                if not ok:
                    normalized["start_with_windows"] = startup.is_enabled()
                    self._notify("Startup setting", message)
                else:
                    log.info("Startup setting: %s", message)
            normalized["start_with_windows"] = startup.is_enabled()
            self.settings = normalized
            normalized = persist_settings(normalized)

        self._set_status("speech", "Muted" if self.muted else "Windows speech ready")
        self.bridge.settings_changed.emit(dict(self.settings))
        return self.settings

    def save_modes(self, modes: dict) -> dict:
        cleaned = persist_modes(modes)
        self.modes_data = cleaned
        self.modes.reload(cleaned)
        self.bridge.settings_changed.emit(dict(self.settings))
        return cleaned

    # -- shutdown ----------------------------------------------------------
    def shutdown(self) -> None:
        if self._stopped:
            return
        self._stopped = True
        log.info("Stopping NOVA services")
        for service in (self.voice, self.opencode, self.codex):
            if service is not None:
                try:
                    service.stop()
                except Exception:
                    log.exception("Service stop failed")
        self.voice = None
        self.opencode = None
        self.codex = None
        try:
            self.speech.stop()
        except Exception:
            log.exception("Speech shutdown failed")
        log.info("NOVA services stopped")
