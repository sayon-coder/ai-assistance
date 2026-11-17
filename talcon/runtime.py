"""Application services: speech, wake word, coding monitors and shared state."""
from __future__ import annotations

import json
import logging
import os
import threading
import time
from pathlib import Path

from PySide6.QtCore import QObject, QTimer

from . import startup, windows_control, windows_media
from . import shutdown as windows_shutdown
from .app_catalog import extract_app_command, load_aliases, resolve_application
from .apps import ApplicationLibrary, discover_start_apps
from .assistant import APPROVAL_TIMEOUT, AssistantController
from .bridge import UiBridge
from .commands import CommandRouter, build_builtin_registry, normalize_phrase
from .config import (
    BUILTIN_MODES, RESERVED_MODE_KEYS, ROOT, load_modes, load_settings,
    normalize_modes, save_modes as persist_modes,
    save_settings as persist_settings,
)
from .cron import CronScheduler
from .custom import ActionExecutor, AutomationRunner, CustomStore
from .model_setup import download as download_model, model_is_ready, resolve_model
from .modes import ModeManager
from .monitors.codex import CodexMonitor
from .monitors.deepai import DeepAICodeMonitor
from .monitors.opencode import OpenCodeMonitor
from .pending_actions import PendingActions
from .running_apps import close_running_app, enumerate_running_apps
from .speech import (
    BACKEND_AUTO, BACKEND_KOKORO, NATURAL_PREVIEW, SPEECH_BACKEND_LABELS,
    SpeechOutput, VoiceInfo, normalize_backend, select_voice,
)
from .voice import VoiceController, list_microphones

log = logging.getLogger("talcon.runtime")

# Face states that represent a completed outcome and should survive a spoken
# reply, so the user sees the emotional reaction after hearing the sentence.
_RESULT_FACE_STATES = {
    "completed", "success", "excited", "surprised", "curious",
    "confused", "concerned", "error", "failure",
}

STARTUP_GREETING = "Welcome back, sir. How can I help you?"

#: Spoken answers to an ambiguity prompt.  They are always in the grammar so
#: the follow-up "second" is recognised even before a prompt is shown.
FOLLOW_UP_PHRASES = frozenset({
    "first", "first one", "the first one",
    "second", "second one", "the second one",
    "third", "third one", "the third one",
})

#: Bounded application grammar.  The Vosk phrase list must stay small, so only
#: this many catalogue names are turned into "open/launch/start/run <name>"
#: phrases (the resolver itself always searches the full cached catalogue).
APP_GRAMMAR_LIMIT = 100
APP_GRAMMAR_VERBS = ("open", "launch", "start", "run")
APP_CLOSE_VERBS = ("close", "exit", "quit")
MAX_APP_PHRASES = 220
FORCE_GRAMMAR_LIMIT = 12
FULL_VERB_LIMIT = 35
RUNNING_QUERY_LIMIT = 12
MAX_RUNNING_PHRASES = 60

#: Grammar prioritisation only.  Matching still resolves dynamically against
#: the catalogue; these names simply make sure everyday applications are not
#: pushed out of the bounded vocabulary by short unrelated Start-menu entries.
PRIORITY_APP_NAMES = (
    "google chrome", "microsoft edge", "firefox", "brave", "opera",
    "visual studio code", "microsoft store", "spotify", "discord",
    "whatsapp", "chatgpt", "telegram", "notepad", "calculator",
    "file explorer", "settings", "terminal", "command prompt",
    "task manager", "microsoft word", "microsoft excel",
    "microsoft powerpoint", "microsoft outlook", "steam", "vlc",
    "zoom", "microsoft teams", "slack", "photos", "camera", "clock",
    "paint", "snipping tool", "wordpad", "powershell", "onedrive",
    "youtube", "gmail", "google drive", "whatsapp web",
)

PENDING_LABELS = {
    "close_all": "Close all application windows",
    "shutdown": "Shut down the computer",
    "ai_approval": "AI assistant permission request",
}

#: How long after hearing a request a spoken response still counts as its
#: answer.  Command replies are immediate; background monitor announcements
#: arrive later and are not paired with a stale request.
REQUEST_PAIRING_SECONDS = 12.0


class TalconRuntime(QObject):
    def __init__(self):
        super().__init__()
        self.bridge = UiBridge()
        self.settings = load_settings()
        self.modes_data = load_modes()
        self.muted = not self.settings.get("voice_enabled", True)

        # Session-only conversation history.  It lives exclusively in this
        # process's RAM: no file, database or browser storage is ever touched,
        # so a restart (or a crash) always starts with an empty History page.
        self._conversation: list[dict] = []
        self._conversation_lock = threading.Lock()
        self._next_response_id = 1
        self._pending_request: tuple[str, float] | None = None

        # Session-only command log for the Commands page: what was heard, which
        # intent matched, what happened and how long it took.  An in-memory
        # bounded list, never written to disk.
        self._command_log: list[dict] = []
        self._command_log_lock = threading.Lock()
        self._next_command_id = 1

        self.speech = SpeechOutput(
            enabled=not self.muted,
            on_speaking=self._on_speech_state,
            on_error=self._on_speech_error,
            on_utterance=self._on_utterance,
            backend_kind=self.settings.get("speech_backend", BACKEND_AUTO),
        )
        self.modes = ModeManager(self.modes_data, self.speech)
        self.commands = CommandRouter(self.modes, self.speech, self)
        self.commands.register_modes(self.modes_data)
        self.custom = CustomStore(
            reserved_phrases=self.commands.reserved_phrases(),
            mode_names=lambda: tuple(self.modes_data),
        )
        # Only mute/unmute raises a notification (see set_muted).  Modes,
        # actions and automations stay silent.
        self.executor = ActionExecutor(self)
        self.automations = AutomationRunner(
            self.executor, status=self._automation_status
        )
        self.commands.custom_handler = self.handle_custom_phrase

        # Windows control services.  The window API is injectable so tests can
        # substitute a fake and never touch real windows.
        self.apps = ApplicationLibrary(packaged_loader=discover_start_apps)
        self.commands.set_application_library(self.apps, load_aliases())
        self.cron = CronScheduler(self)
        self.pending = PendingActions()
        # AI (Hermes) assistant.  Disabled unless the user turns it on; the
        # router consults it only after every local parser has failed.
        self.assistant = AssistantController(
            settings=lambda: self.settings,
            speech=self.speech,
            on_state=self._assistant_state,
            on_approval_wait=lambda: self.pending.request(
                "ai_approval", timeout=APPROVAL_TIMEOUT + 2.0
            ),
            on_approval_done=lambda: self.pending.cancel("ai_approval"),
        )
        self.commands.natural_handler = self.assistant.handle_phrase
        self.commands.control_handler = self.assistant.handle_control
        self.window_api = None
        self._close_preview: list[dict] = []
        self._last_close_result = "No close-all run yet."
        self._last_closed: list[str] = []
        self._last_refused: list[str] = []
        self._shutdown_scheduled = False
        self._shutdown_deadline = 0.0
        self._startup_greeting_spoken = False

        self.voice: VoiceController | None = None
        self.opencode: OpenCodeMonitor | None = None
        self.codex: CodexMonitor | None = None
        self.deepai: DeepAICodeMonitor | None = None
        self.voices: list[str] = []
        self.voice_details: list[VoiceInfo] = []
        self._active_voice: VoiceInfo | None = None
        self._speech_note = ""
        self._voice_load_lock = threading.Lock()
        self._downloading = False
        self._stopped = False
        self._face_state = "idle"
        self._pre_speech_result = ""
        self._speaking_face = False

        self.status = {
            "running": "TALCON is running",
            "voice": "Starting voice services…",
            "wake": "Starting…",
            "microphone": self._microphone_label(),
            "speech": "Muted" if self.muted else "Kokoro voice ready",
            "speech_engine": "Checking the Kokoro voice…",
            "speech_engine_active": "",
            "speech_engine_note": "",
            "speech_voice_name": "",
            "speech_voice_id": "",
            "speech_voice_language": "",
            "speech_voice_gender": "",
            "speech_voice_confirmed": "",
            "speech_synthesis": "",
            "speech_offline": "On-device",
            "opencode": "OpenCode starting…",
            "codex": "Codex starting…",
            "deepai": "DeepAI Code starting…",
            "cron": "Cron Jobs ready",
            "assistant": "Idle",
            "active_mode": "normal",
            "last_heard": "",
            "last_match": "",
            "last_command": "",
            "last_result": "No command yet.",
            "last_failure": "",
            "mic_error": "",
            "speech_noise": "—",
            "speech_rms": "—",
            "speech_threshold": "—",
            "speech_clipping": "Not detected",
            "speech_confidence": "—",
            "speech_endpoint": "—",
            "speech_reject_reason": "No recognition attempt yet.",
            "recent_activity": [],
            "last_event": "TALCON is ready, sir.",
            "media_control": "Available" if windows_media.available() else "Unavailable",
            "pending_action": "None",
            "pending_remaining": "",
            "shutdown_state": "Not scheduled",
            "apps": "Discovering installed applications…",
            "last_close_result": self._last_close_result,
            "last_closed": [],
            "last_refused": [],
        }

        self._dangerous_timer = QTimer(self)
        self._dangerous_timer.setInterval(1000)
        self._dangerous_timer.timeout.connect(self._tick_dangerous_state)
        self._dangerous_timer.start()

    # -- state helpers -----------------------------------------------------
    def status_snapshot(self) -> dict:
        snapshot = dict(self.status)
        snapshot.update(self._dangerous_snapshot())
        return snapshot

    def _dangerous_snapshot(self) -> dict:
        pending = None
        remaining = 0.0
        try:
            pending = self.pending.current()
            remaining = self.pending.remaining()
        except Exception:
            log.debug("Could not read the pending action", exc_info=True)
        snapshot = {
            "media_control": "Available" if windows_media.available() else "Unavailable",
            "pending_action": PENDING_LABELS.get(pending, "None"),
            "pending_remaining": f"{remaining:.0f} s" if pending else "",
            "last_close_result": getattr(self, "_last_close_result", "No close-all run yet."),
            "last_closed": list(getattr(self, "_last_closed", [])),
            "last_refused": list(getattr(self, "_last_refused", [])),
        }
        if getattr(self, "_shutdown_scheduled", False):
            left = max(0, int(round(self._shutdown_deadline - time.monotonic())))
            snapshot["shutdown_state"] = (
                f"Shutdown in {left} s" if left else "Shutdown in progress"
            )
        else:
            snapshot["shutdown_state"] = "Not scheduled"
        return snapshot

    def _tick_dangerous_state(self) -> None:
        for key, value in self._dangerous_snapshot().items():
            self._set_status(key, value)

    def _microphone_label(self) -> str:
        selected = self.settings.get("microphone_device")
        if selected in (None, ""):
            return "System default microphone"
        try:
            for index, name in list_microphones():
                if index == selected:
                    return str(name)
        except Exception:
            log.debug("Could not read the selected microphone name", exc_info=True)
        return f"Microphone device {selected}"

    def _record_activity(self, text: str) -> None:
        text = str(text or "").strip()
        if not text:
            return
        activity = list(self.status.get("recent_activity", []))
        activity.insert(0, f"{time.strftime('%H:%M')} · {text}")
        del activity[8:]
        self._set_status("recent_activity", activity)

    # -- command log -------------------------------------------------------
    def _record_command(self, payload: str) -> None:
        """Store one completed command for the Commands page.

        The voice layer emits this after the action has run, so the duration is
        real execution time.  ``last_heard`` is the transcript that produced the
        match, which makes a misunderstanding visible ("what I heard" vs "what
        ran").
        """
        try:
            data = json.loads(payload)
        except Exception:
            log.debug("Ignoring a malformed command log payload", exc_info=True)
            return
        if not isinstance(data, dict):
            return
        with self._command_log_lock:
            entry = {
                "id": self._next_command_id,
                "time": time.strftime("%H:%M:%S"),
                "heard": str(self.status.get("last_heard", "") or "").strip(),
                "phrase": str(data.get("phrase", "") or "").strip(),
                "action": str(data.get("action", "") or "").strip(),
                "ms": int(data.get("ms", 0) or 0),
                "confidence": data.get("confidence"),
            }
            self._next_command_id += 1
            self._command_log.append(entry)
            del self._command_log[:-100]
            snapshot = [dict(item) for item in self._command_log]
        try:
            self.bridge.command_log_changed.emit(snapshot)
        except Exception:
            log.exception("Could not publish the command log")

    def command_log(self) -> list[dict]:
        """A copy of this session's command log, oldest first."""
        with self._command_log_lock:
            return [dict(item) for item in self._command_log]

    def clear_command_log(self) -> None:
        with self._command_log_lock:
            self._command_log.clear()
            snapshot: list[dict] = []
        try:
            self.bridge.command_log_changed.emit(snapshot)
        except Exception:
            log.exception("Could not publish the cleared command log")

    def _set_status(self, key: str, value) -> None:
        if self.status.get(key) == value:
            return
        self.status[key] = value
        try:
            self.bridge.status_changed.emit(self.status_snapshot())
        except Exception:
            log.exception("Status emit failed")

    def _set_face(self, state: str) -> None:
        if state != self._face_state:
            log.info("Face expression: %s", state)
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
            self._set_status("speech", "Speaking…")
            # Show the speaking eyes, but remember a fresh result so it can
            # return after the sentence instead of being swallowed.
            if self._face_state in _RESULT_FACE_STATES:
                self._pre_speech_result = self._face_state
            self._speaking_face = True
            self._set_face("speaking")
            if self._pre_speech_result:
                # Lower priority than speaking, so the controller keeps it
                # pending and promotes it when speech ends.
                self._set_face(self._pre_speech_result)
        else:
            try:
                self._set_status("speech_synthesis", self.speech.synthesis_summary())
            except Exception:
                log.exception("Could not read the last synthesis result")
            self._set_status("speech", self._speech_status_text())
            if self._speaking_face:
                self._speaking_face = False
                self._pre_speech_result = ""
                self._set_face("idle")

    def _on_speech_error(self, message: str) -> None:
        self._set_status("speech", message)
        self._set_status("speech_engine_note", str(message))
        log.warning("Speech problem: %s", message)

    def _speech_status_text(self) -> str:
        """Describe the engine that is really active, never a wish."""
        if self.muted:
            return "Muted"
        info = self.speech.engine_info()
        active = info.get("active", "")
        voice = self._active_voice
        if active and not info.get("natural_available", False):
            label = "Kokoro unavailable"
        elif active:
            label = "Kokoro Neural Voice"
        else:
            label = "Kokoro Neural Voice (starting)"
        return f"{label} · {voice.name}" if voice is not None else label

    # -- lifecycle ---------------------------------------------------------
    def start(self) -> None:
        self.speech.start()
        threading.Thread(target=self._load_voices, name="TALCON-Voices", daemon=True).start()
        self._start_voice()
        self._start_opencode()
        self._start_codex()
        self._start_deepai()
        self.cron.start()
        self._set_status("cron", self.cron.status_text())
        # Discover installed applications off the UI/voice thread.  The
        # catalogue is cached and only rebuilt on demand or when it goes stale.
        self.apps.refresh_async(self._on_apps_discovered)
        self._set_status("last_event", "TALCON is ready, sir.")
        log.info("Runtime started")

    def _load_voices(self) -> None:
        """Serialise refreshes so a slow probe cannot publish stale state."""
        with self._voice_load_lock:
            self._load_voices_locked()

    def _load_voices_locked(self) -> None:
        """Load Kokoro, list its real voices and restore the saved choice."""
        try:
            ready = self.speech.ensure_ready(timeout=25.0)
        except Exception:
            log.exception("Kokoro speech backend did not become ready")
            ready = False
        details: list[VoiceInfo] = []
        if ready:
            try:
                details = list(self.speech.voice_details())
            except Exception:
                log.exception("Could not read Kokoro voice details")
        self.voice_details = details
        self.voices = [voice.name for voice in details]
        info = self.speech.engine_info()
        note = str(info.get("note", "") or "")

        saved_ids = dict(self.settings.get("speech_voice_ids") or {})
        preferred_id = saved_ids.get(BACKEND_KOKORO, "")
        if not preferred_id:
            # Legacy setting: only used if it actually matches a listed voice.
            preferred_id = str(self.settings.get("speech_voice_id", "") or "")
        if not preferred_id:
            # The model exposes many voices; start with the calm British male
            # instead of whichever id happens to sort first.
            from .neural_tts import DEFAULT_VOICE

            preferred_id = DEFAULT_VOICE
        preferred_name = str(self.settings.get("speech_voice", "") or "")

        chosen, selection_note = select_voice(details, preferred_id, preferred_name)
        if selection_note:
            note = f"{note} {selection_note}".strip()
        self._active_voice = chosen
        confirmed = ""
        if chosen is not None:
            self.speech.set_voice(chosen.id)
            ok, confirmed_id, error = self.speech.verify_voice(timeout=5.0)
            if ok:
                confirmed = confirmed_id or chosen.id
            else:
                note = f"{note} {error}".strip()
            # Persist the choice only when the stored preference was honoured;
            # a fallback keeps the user's original choice untouched.
            if not selection_note:
                saved_ids = {BACKEND_KOKORO: chosen.id}
                if (
                    self.settings.get("speech_voice_ids") != saved_ids
                    or self.settings.get("speech_voice_id") != chosen.id
                    or self.settings.get("speech_voice") != chosen.name
                ):
                    self.settings["speech_voice_ids"] = saved_ids
                    self.settings["speech_voice_id"] = chosen.id
                    self.settings["speech_voice"] = chosen.name
                    try:
                        self.settings = persist_settings(self.settings)
                    except Exception:
                        log.exception("Could not save the selected speech voice")
        elif not ready:
            note = f"{note} Kokoro speech is unavailable.".strip()

        self.speech.set_rate(int(self.settings.get("speech_rate", 0)))
        self.speech.set_volume(int(self.settings.get("speech_volume", 90)))
        self._publish_speech_state(info, note, confirmed)

    def _publish_speech_state(self, info: dict, note: str = "", confirmed: str = "") -> None:
        self._speech_note = str(note or "")
        voice = self._active_voice
        self._set_status("speech_engine", str(info.get("requested_label", "Auto")))
        self._set_status("speech_engine_active", str(info.get("active_label", "")))
        self._set_status("speech_engine_note", self._speech_note)
        self._set_status("speech_voice_name", voice.name if voice else "")
        self._set_status("speech_voice_id", voice.id if voice else "")
        self._set_status("speech_voice_language", voice.language_label if voice else "")
        self._set_status("speech_voice_gender", voice.gender if voice else "")
        self._set_status("speech_voice_confirmed", str(confirmed or ""))
        try:
            self._set_status("speech_synthesis", self.speech.synthesis_summary())
        except Exception:
            log.exception("Could not read the last synthesis result")
        self._set_status(
            "speech_offline",
            "Yes · generated and played on this PC" if voice and voice.offline else "No voice available",
        )
        self._set_status("speech", self._speech_status_text())
        try:
            self.bridge.voices_available.emit(list(self.voices))
            self.bridge.speech_voices_changed.emit(
                [voice.to_dict() for voice in self.voice_details], dict(info)
            )
        except Exception:
            log.exception("Could not publish the speech voice list")

    def _start_voice(self) -> None:
        if self.voice is not None:
            self.voice.stop()
            self.voice = None
        if not self.settings.get("wake_enabled", True):
            self._set_status("voice", "Listening stopped")
            self._set_status("wake", "Listening stopped")
            return
        model = resolve_model(self.settings.get("vosk_model_path"))
        if not model_is_ready(model):
            self._set_status("voice", "Speech model missing · download it in Voice settings")
            self._set_status("wake", "Speech model missing")
            log.info("Wake word not started: Vosk model missing at %s", model)
            return
        self.voice = VoiceController(
            self.settings, self.commands, self._voice_status, self._set_face,
            event_callback=self._voice_event,
            grammar_provider=self.recognition_phrases,
            phrase_timeout=self.settings.get("phrase_timeout"),
        )
        self.voice.start()

    def _voice_status(self, text: str) -> None:
        self._set_status("voice", text)
        lowered = str(text).lower()
        if "listening continuously" in lowered:
            self._set_status("wake", "Listening continuously")
        elif "listening for talcon" in lowered:
            self._set_status("wake", "Listening for TALCON")
        elif "listening for your command" in lowered or "capturing" in lowered:
            self._set_status("wake", "Capturing a command")
        elif "listening stopped" in lowered:
            self._set_status("wake", "Listening stopped")
        elif "microphone unavailable" in lowered:
            self._set_status("wake", "Microphone unavailable")
        elif "speech model missing" in lowered:
            self._set_status("wake", "Speech model missing")
        if self.commands_paused and "stopped" not in lowered and "missing" not in lowered:
            self._set_status("wake", "Commands paused · say take command")

    def _voice_event(self, kind: str, text: str) -> None:
        if kind == "last_heard":
            self._set_status("last_heard", text)
            self._set_face("heard")
            # Remember what the user just asked so the next spoken response
            # can be paired with its request in the History page.
            phrase = str(text or "").strip()
            if phrase:
                with self._conversation_lock:
                    self._pending_request = (phrase, time.monotonic())
        elif kind == "last_match":
            self._set_status("last_match", text)
        elif kind == "last_command":
            self._set_status("last_command", text)
            self._set_face("command")
        elif kind == "command_done":
            self._record_command(text)
        elif kind == "last_result":
            self._set_status("last_result", text)
            lowered = str(text).lower()
            if "no speech" in lowered or "no command" in lowered:
                pass
            elif "fail" in lowered or "error" in lowered:
                self._set_face("failure")
            else:
                self._set_face("completed")
            self._record_activity(text)
        elif kind == "last_failure":
            self._set_status("last_failure", text)
            self._set_face("failure")
        elif kind == "mic_error":
            self._set_status("mic_error", text)
        elif kind == "recognition_diag":
            try:
                import json as _json
                data = _json.loads(text)
            except Exception:
                return
            if not isinstance(data, dict):
                return
            self._set_status("speech_noise", f"{data.get('noise', 0):g}")
            self._set_status("speech_rms", f"{data.get('rms', 0):g}")
            self._set_status("speech_threshold", f"{data.get('threshold', 0):g}")
            self._set_status(
                "speech_clipping",
                "Clipping detected" if data.get("clipping") else "Not detected",
            )
            confidence = data.get("confidence")
            self._set_status(
                "speech_confidence",
                "not supplied" if confidence is None else f"{float(confidence):.2f}",
            )
            endpoint = data.get("endpoint")
            self._set_status(
                "speech_endpoint",
                "—" if endpoint is None else f"{float(endpoint):.2f} s",
            )
            self._set_status(
                "speech_reject_reason", str(data.get("reason") or "—")
            )

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

    def _start_deepai(self) -> None:
        if self.deepai is not None:
            self.deepai.stop()
            self.deepai = None
        if not self.settings.get("deepai_enabled", True):
            self._set_status("deepai", "Monitoring disabled")
            return
        self._set_status("deepai", "DeepAI Code connecting…")
        self.deepai = DeepAICodeMonitor(
            lambda kind, message, run: self._coding_event(
                "DeepAI Code", kind, message, run
            ),
            lambda text: self._set_status("deepai", text),
        )
        self.deepai.start()

    # -- events ------------------------------------------------------------
    def _coding_event(self, source: str, kind: str, message: str, session: str = "") -> None:
        log.info("%s coding event: %s | %s | %s", source, kind, message, session)
        if kind == "working":
            self._set_face("working")
            self._set_status("last_event", f"{source} is working on a task.")
        elif kind == "success":
            self._set_face("success")
            self.speech.say("Sir, " + message)
            self._set_status("last_event", f"{source}: {message}")
        elif kind == "failure":
            self._set_face("error")
            self.speech.say("Sir, " + message)
            self._set_status("last_event", f"{source}: {message}")
        elif kind == "interrupted":
            self._set_face("idle")
            if self.settings.get("announce_interruptions", False):
                self.speech.say("Sir, " + message)
            self._set_status("last_event", f"{source}: {message}")

    # -- commands callable from the service layer -------------------------
    def mode_title(self, name: str) -> str:
        mode = self.modes_data.get(str(name))
        if isinstance(mode, dict) and mode.get("title"):
            return str(mode["title"])
        return str(name).replace("_", " ").title()

    def mode_names(self) -> list[str]:
        return list(self.modes_data)

    def activate_mode(self, name: str) -> bool:
        ok = self.modes.activate(name)
        self._set_face("completed" if ok else "error")
        if ok:
            title = self.mode_title(name)
            self._set_status("active_mode", title)
            self._set_status("last_event", f"{title} mode activated.")
            self._record_activity(f"{title} mode activated")
        return ok

    def show_settings(self) -> None:
        self.bridge.open_settings.emit("dashboard")

    def hide_to_tray(self) -> None:
        self.bridge.hide_to_tray.emit()

    def set_face_visible(self, visible: bool) -> None:
        self.bridge.face_visibility_requested.emit(bool(visible))

    def set_all_faces_visible(self, visible: bool) -> None:
        """Show or hide both companion surfaces without touching the UI window."""
        value = bool(visible)
        self.save_settings({
            "show_face": value,
            "menu_bar_face_enabled": value,
        })

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

    def set_muted(self, muted: bool, announce: bool = False) -> None:
        muted = bool(muted)
        self.muted = muted
        self.settings["voice_enabled"] = not muted
        self.speech.set_enabled(not muted)
        try:
            self.settings = persist_settings(self.settings)
        except Exception:
            log.exception("Could not persist mute state")
        self._set_status("speech", self._speech_status_text())
        if announce:
            self._notify("TALCON voice", "Voice muted." if muted else "Voice enabled.")
        log.info("Speech %s", "muted" if muted else "enabled")

    @property
    def commands_paused(self) -> bool:
        """True while TALCON ignores every command except the resume phrase."""
        return bool(self.settings.get("commands_paused", False))

    # -- AI (Hermes) assistant ------------------------------------------------
    def _assistant_state(self, event: dict) -> None:
        """Publish assistant progress from the worker thread to the UI."""
        kind = str((event or {}).get("kind") or "")
        text = str((event or {}).get("text") or "")
        payload = {
            "kind": kind,
            "text": text,
            "enabled": bool(self.settings.get("ai_enabled", False)),
            "busy": kind in ("accepted", "working", "progress", "approval")
            or self.assistant.is_busy(),
        }
        if kind == "progress" and text:
            self._set_status("assistant", text)
        elif kind in ("answered", "cancelled", "error"):
            self._set_status("assistant", "Idle")
        elif kind in ("accepted", "working"):
            self._set_status("assistant", "Thinking…")
        try:
            self.bridge.assistant_state.emit(payload)
        except Exception:
            log.debug("Could not publish assistant state", exc_info=True)

    def answer_ai_approval(self, approved: bool) -> bool:
        """Deliver a spoken yes/no to a pending AI permission request."""
        if self.pending.current() != "ai_approval":
            return False
        self.pending.cancel("ai_approval")
        return bool(self.assistant.answer_approval(approved))

    def set_ai_enabled(self, enabled: bool) -> None:
        enabled = bool(enabled)
        if self.settings.get("ai_enabled", False) == enabled:
            return
        self.settings["ai_enabled"] = enabled
        try:
            self.settings = persist_settings(self.settings)
        except Exception:
            log.exception("Could not persist the AI setting")
        if not enabled:
            # Turning the feature off abandons anything still in flight and
            # wipes the request context immediately.
            self.assistant.cancel()
            self.assistant.clear_context()
        self.bridge.settings_changed.emit(dict(self.settings))
        self._set_status("assistant", "Idle")
        log.info("AI assistant %s", "enabled" if enabled else "disabled")

    def assistant_backend_status(self) -> str:
        if not self.settings.get("ai_enabled", False):
            return "off"
        try:
            self.assistant._get_client()
        except Exception:
            return "unconfigured"
        client = getattr(self.assistant, "_client", None)
        if client is None:
            return "unconfigured"
        try:
            health = client.health()
        except Exception:
            return "unreachable"
        return "ready" if health.get("status") == "ok" else "unreachable"

    def ai_cache_stats(self) -> dict:
        """Counters for the in-memory response cache (never persisted)."""
        try:
            return self.assistant.cache_stats()
        except Exception:
            log.debug("Could not read the cache stats", exc_info=True)
            return {}

    def clear_ai_cache(self) -> None:
        try:
            self.assistant.clear_cache()
        except Exception:
            log.debug("Could not clear the response cache", exc_info=True)

    def set_commands_paused(self, paused: bool) -> None:
        paused = bool(paused)
        if self.settings.get("commands_paused", False) == paused:
            return
        self.settings["commands_paused"] = paused
        try:
            self.settings = persist_settings(self.settings)
        except Exception:
            log.exception("Could not persist the command pause state")
        self._set_status(
            "commands_state", "Commands paused" if paused else "Listening"
        )
        if paused:
            self._set_status("wake", "Commands paused · say take command")
        elif self.settings.get("wake_enabled", True):
            self._set_status("wake", "Listening for TALCON")
        else:
            self._set_status("wake", "Listening stopped")
        self.bridge.settings_changed.emit(dict(self.settings))
        log.info("Command listening %s", "paused" if paused else "resumed")

    # -- session-only conversation history ---------------------------------
    def _on_utterance(self, text: str) -> None:
        """Record one assistant response exactly as it is about to be spoken.

        The speech engine calls this from its own worker thread, so the list is
        guarded by a lock.  Emitting a Qt signal from that thread is safe: the
        UI receives it through a queued connection.  Nothing is written to
        disk, a database or browser storage.
        """
        text = str(text or "").strip()
        if not text:
            return
        request = ""
        with self._conversation_lock:
            pending = self._pending_request
            self._pending_request = None
            if pending and time.monotonic() - pending[1] <= REQUEST_PAIRING_SECONDS:
                request = pending[0]
            entry = {
                "id": self._next_response_id,
                "time": time.strftime("%H:%M:%S"),
                "request": request,
                "text": text,
            }
            self._next_response_id += 1
            self._conversation.append(entry)
            snapshot = [dict(item) for item in self._conversation]
        try:
            self.bridge.response_started.emit(dict(entry))
            self.bridge.conversation_changed.emit(snapshot)
        except Exception:
            # The UI may already be tearing down when the last utterance starts.
            log.debug("Could not publish the spoken response", exc_info=True)

    def conversation_history(self) -> list[dict]:
        """Return this session's spoken responses (RAM only, never persisted)."""
        with self._conversation_lock:
            return [dict(item) for item in self._conversation]

    def clear_conversation_history(self) -> None:
        """Forget the session history without touching speech playback."""
        with self._conversation_lock:
            self._conversation.clear()
            self._pending_request = None
        try:
            self.bridge.conversation_changed.emit([])
        except Exception:
            log.debug("Could not publish the cleared history", exc_info=True)

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

    def listen_now(self) -> None:
        if not model_is_ready(resolve_model(self.settings.get("vosk_model_path"))):
            self._set_status("wake", "Speech model missing")
            return
        if self.voice is None:
            self.voice = VoiceController(
                self.settings, self.commands, self._voice_status, self._set_face,
                event_callback=self._voice_event,
                grammar_provider=self.recognition_phrases,
                phrase_timeout=self.settings.get("phrase_timeout"),
            )
            # When the wake word is disabled, capture exactly one command and
            # then release the microphone again instead of starting the wake loop.
            if self.settings.get("wake_enabled", True):
                self.voice.start()
        self.voice.request_manual_listen()

    def test_voice(self) -> None:
        """Preview the selected engine and voice without changing the mute setting."""
        def run() -> None:
            was_muted = self.muted
            if was_muted:
                self.speech.set_enabled(True)
            try:
                self.speech.ensure_ready(timeout=25.0)
                ok, confirmed, error = self.speech.verify_voice(timeout=5.0)
                if not ok:
                    message = error or "Kokoro did not confirm the selected voice."
                    self._set_status("speech_engine_note", message)
                else:
                    self._set_status("speech_voice_confirmed", confirmed)
                finished = self.speech.say_and_wait(NATURAL_PREVIEW, timeout=40.0)
                summary = self.speech.synthesis_summary()
                if summary:
                    self._set_status("speech_synthesis", summary)
                if not finished:
                    self._set_status("speech_engine_note", "The preview did not finish playing.")
            finally:
                if was_muted:
                    self.speech.set_enabled(False)
                self._set_status("speech", self._speech_status_text())

        threading.Thread(target=run, name="TALCON-Voice-Test", daemon=True).start()

    def preview_voice(self) -> None:
        """Alias used by the Speech Engine card."""
        self.test_voice()

    def speech_voice_details(self) -> list[dict]:
        return [voice.to_dict() for voice in self.voice_details]

    def speech_engine_info(self) -> dict:
        try:
            return dict(self.speech.engine_info())
        except Exception:
            log.exception("Could not read speech engine information")
            return {
                "requested": self.settings.get("speech_backend", BACKEND_AUTO),
                "requested_label": SPEECH_BACKEND_LABELS.get(
                    self.settings.get("speech_backend", BACKEND_AUTO),
                    "Kokoro Neural Voice"
                ),
                "active": "",
                "active_label": "Not started yet",
                "natural_available": False,
                "note": "",
            }

    def set_speech_backend(self, kind: str) -> None:
        normalized = normalize_backend(kind)
        if normalized == self.settings.get("speech_backend", BACKEND_AUTO):
            return
        self.save_settings({"speech_backend": normalized})

    def choose_speech_voice(self, voice_id: str, name: str = "") -> None:
        """Persist and apply a voice chosen from the real installed list."""
        voice_id = str(voice_id or "").strip()
        if not voice_id:
            return
        match = next(
            (voice for voice in self.voice_details if voice.id == voice_id), None
        )
        if match is None:
            self._set_status("speech_engine_note", "That voice is not available. Refresh the list.")
            return
        self.settings["speech_voice_id"] = match.id
        self.settings["speech_voice"] = str(name or match.name)
        self.settings["speech_voice_ids"] = {BACKEND_KOKORO: match.id}
        self._active_voice = match
        confirmed = ""
        error = ""
        try:
            self.speech.set_voice(match.id)
            ok, confirmed_id, error = self.speech.verify_voice(timeout=5.0)
            if ok:
                confirmed = confirmed_id or match.id
            else:
                error = error or "Kokoro did not confirm the selected voice."
        except Exception as exc:
            log.exception("Could not select the requested speech voice")
            error = str(exc)
        try:
            self.settings = persist_settings(self.settings)
        except Exception:
            log.exception("Could not persist the selected speech voice")
        if error:
            self._set_status("speech_engine_note", error)
        self._set_status("speech_voice_name", match.name)
        self._set_status("speech_voice_id", match.id)
        self._set_status("speech_voice_language", match.language_label)
        self._set_status("speech_voice_gender", match.gender)
        self._set_status("speech_voice_confirmed", confirmed)
        self._set_status("speech", self._speech_status_text())
        self.bridge.settings_changed.emit(dict(self.settings))

    def microphones(self) -> list[tuple[int | None, str]]:
        return list_microphones()

    def refresh_voices(self) -> None:
        threading.Thread(target=self._load_voices, name="TALCON-Voices", daemon=True).start()

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
            except Exception as exc:
                log.exception("Speech model download failed")
                self.bridge.model_progress.emit(-1, f"Download failed: {exc}")
            finally:
                self._downloading = False

        threading.Thread(target=work, name="TALCON-ModelDownload", daemon=True).start()

    # -- Windows control ---------------------------------------------------
    def media_toggle(self) -> tuple[bool, str]:
        """Send the media key silently; media commands never speak a reply."""
        if not windows_media.available():
            message = "Global media control is only available on Windows."
            self._set_status("last_event", message)
            self._set_status("last_result", message)
            return False, message
        ok, detail = windows_media.send_play_pause()
        if ok:
            message = (
                "Sent the Windows play/pause media key. It is a toggle, so the "
                "player decides whether it pauses or resumes."
            )
        else:
            message = detail
        self._set_status("last_event", message)
        self._set_status("last_result", message)
        self._record_activity(message)
        return ok, message

    def request_close_all(self) -> tuple[bool, str]:
        self.pending.request("close_all")
        message = (
            "Sir, this will close your open application windows, including programs "
            "I did not start. Please confirm."
        )
        self._set_status("pending_action", PENDING_LABELS["close_all"])
        self._set_status("pending_remaining", "15 s")
        self._set_status("last_result", message)
        self._record_activity("Close-all requested; waiting for confirmation")
        self.speech.say(message)
        return True, message

    def confirm_close_all(self) -> tuple[bool, str]:
        if not self.pending.confirm("close_all"):
            message = "There is no close-all request waiting for confirmation, sir."
            self.speech.say(message)
            return False, message
        self._set_status("pending_action", "None")
        self._set_status("pending_remaining", "")
        return self.close_all_now()

    def close_all_now(self) -> tuple[bool, str]:
        """Ask every candidate window to close.  Never force-terminates."""
        try:
            windows = windows_control.enumerate_windows(
                api=self.window_api, own_pid=os.getpid()
            )
            self._close_preview = [window.to_dict() for window in windows]
            result = windows_control.close_windows(windows, api=self.window_api)
        except Exception as exc:
            log.exception("Close-all failed")
            message = f"I could not close the applications: {exc}"
            self._last_close_result = message
            self._set_status("last_close_result", message)
            self._set_status("last_result", message)
            self._set_status("last_event", message)
            self.speech.say(message)
            return False, message

        self._last_close_result = result.summary()
        self._last_closed = [window.label for window in result.closed]
        self._last_refused = [window.label for window in result.refused]
        message = self._last_close_result
        if result.errors:
            message = f"{message} {'; '.join(result.errors[:3])}"
        self._set_status("last_close_result", self._last_close_result)
        self._set_status("last_closed", list(self._last_closed))
        self._set_status("last_refused", list(self._last_refused))
        self._set_status("last_result", message)
        self._set_status("last_event", message)
        self._record_activity(message)
        self.speech.say(message)
        return not result.errors, message

    # -- running applications ---------------------------------------------
    def running_applications(self) -> list:
        """Live list of user applications with visible windows."""
        return enumerate_running_apps(
            self.apps.cached_entries(), api=self.window_api,
            own_pid=os.getpid(), aliases=self.commands.app_aliases,
        )

    def close_application(self, app):
        """Graceful WM_CLOSE of every window belonging to one running app."""
        result = close_running_app(app, api=self.window_api)
        self._last_close_result = result.summary()
        self._last_closed = [window.label for window in result.closed]
        self._last_refused = [window.label for window in result.refused]
        self._set_status("last_close_result", self._last_close_result)
        self._set_status("last_closed", list(self._last_closed))
        self._set_status("last_refused", list(self._last_refused))
        self._set_status("last_result", self._last_close_result)
        self._record_activity(f"Close request for {app.name}: {self._last_close_result}")
        return result

    def force_close_application(self, app):
        """Terminate an already-confirmed running app; safety checks remain."""
        from .windows_control import ForceResult, force_terminate, is_protected_process

        if is_protected_process(getattr(app, "process", "")):
            return ForceResult(refused=list(app.pids))
        result = force_terminate(
            app.pids, api=self.window_api, own_pid=os.getpid(),
        )
        message = (
            f"Force closed {app.name}." if result.terminated
            else f"Could not force close {app.name}."
        )
        self._set_status("last_result", message)
        self._record_activity(message)
        return result

    def preview_close_all(self) -> list[dict]:
        """Return the windows close-all would target, without closing anything."""
        try:
            windows = windows_control.enumerate_windows(
                api=self.window_api, own_pid=os.getpid()
            )
        except Exception as exc:
            log.exception("Window preview failed")
            self._set_status("last_event", f"Could not list open windows: {exc}")
            return []
        self._close_preview = [window.to_dict() for window in windows]
        return list(self._close_preview)

    def cancel_close_all(self) -> tuple[bool, str]:
        cancelled = self.pending.cancel("close_all")
        if cancelled:
            self._set_status("pending_action", "None")
            self._set_status("pending_remaining", "")
            message = "Close-all cancelled, sir. Nothing was closed."
        else:
            message = "There is no pending close-all request, sir."
        self._set_status("last_result", message)
        self.speech.say(message)
        return cancelled, message

    def request_shutdown(self) -> tuple[bool, str]:
        self.pending.request("shutdown")
        message = "Are you sure you want to shut down the computer? Please confirm."
        self._set_status("pending_action", PENDING_LABELS["shutdown"])
        self._set_status("pending_remaining", "15 s")
        self._set_status("last_result", message)
        self._record_activity("Windows shutdown requested; waiting for confirmation")
        self.speech.say(message)
        return True, message

    def confirm_shutdown(self) -> tuple[bool, str]:
        if not self.pending.confirm("shutdown"):
            message = "There is no shutdown request waiting for confirmation, sir."
            self.speech.say(message)
            return False, message
        self._set_status("pending_action", "None")
        self._set_status("pending_remaining", "")
        ok, detail = windows_shutdown.shutdown_computer()
        if ok:
            timeout = int(windows_shutdown.SHUTDOWN_TIMEOUT)
            self._shutdown_scheduled = True
            self._shutdown_deadline = time.monotonic() + timeout
            if timeout > 0:
                message = (
                    f"Shutting down in {timeout} seconds, sir. "
                    "Say cancel shutdown to abort."
                )
                self._set_status("shutdown_state", f"Shutdown in {timeout} s")
            else:
                message = "Shutting down now, sir."
                self._set_status("shutdown_state", "Shutdown in progress")
        else:
            message = f"I could not shut down the computer. {detail}"
            self._set_status("shutdown_state", "Shutdown failed")
        self._set_status("last_result", message)
        self._set_status("last_event", message)
        self._record_activity(message)
        self.speech.say(message)
        return ok, message

    def cancel_shutdown(self) -> tuple[bool, str]:
        if self.pending.cancel("shutdown"):
            self._set_status("pending_action", "None")
            self._set_status("pending_remaining", "")
            message = "Shutdown cancelled, sir."
            self._set_status("last_result", message)
            self.speech.say(message)
            return True, message
        if self._shutdown_scheduled:
            ok, detail = windows_shutdown.abort_shutdown()
            if ok:
                self._shutdown_scheduled = False
                self._shutdown_deadline = 0.0
                self._set_status("shutdown_state", "Cancelled")
                message = "Windows shutdown cancelled, sir."
            else:
                message = f"I could not cancel the shutdown. {detail}"
            self._set_status("last_result", message)
            self._set_status("last_event", message)
            self._record_activity(message)
            self.speech.say(message)
            return ok, message
        message = "There is no shutdown waiting to be cancelled, sir."
        self._set_status("last_result", message)
        self.speech.say(message)
        return False, message

    # -- startup greeting --------------------------------------------------
    def speak_startup_greeting(self) -> bool:
        """Speak the automatic-startup greeting exactly once, if unmuted."""
        if getattr(self, "_startup_greeting_spoken", False):
            return False
        self._startup_greeting_spoken = True
        if self.muted or not getattr(self.speech, "enabled", False):
            self._set_status("last_event", "Startup greeting skipped: voice is muted.")
            log.info("Startup greeting skipped because voice is muted")
            return False

        def work() -> None:
            try:
                if not self.speech.ensure_ready(timeout=20.0):
                    log.warning("Startup greeting skipped: speech backend not ready")
                    self._set_status("last_event", "Startup greeting skipped: speech not ready.")
                    return
                if self.muted or not getattr(self.speech, "enabled", False):
                    return
                # SpeechOutput.is_busy is true while this is queued, so the
                # microphone loop ignores the greeting instead of treating it
                # as a user command.
                self.speech.say_and_wait(STARTUP_GREETING, timeout=25.0)
                log.info("Startup greeting spoken once")
            except Exception:
                log.exception("Startup greeting failed")

        threading.Thread(target=work, name="TALCON-Startup-Greeting", daemon=True).start()
        return True

    # -- settings ----------------------------------------------------------
    def save_settings(self, values: dict) -> dict:
        old = dict(self.settings)
        merged = dict(self.settings)
        merged.update(values or {})
        # The setup wizard only knows display names.  Clearing the stable id
        # lets the next voice load resolve the name the user just picked.
        if "speech_voice" in (values or {}) and "speech_voice_id" not in (values or {}):
            merged["speech_voice_id"] = ""
            # A display-name choice from the setup wizard must not be shadowed
            # by a stale per-engine id from a previous session.
            merged["speech_voice_ids"] = {}
        if "voice_enabled" in merged:
            self.muted = not bool(merged["voice_enabled"])
            merged["voice_enabled"] = not self.muted
        normalized = persist_settings(merged)
        self.settings = normalized
        self.speech.set_enabled(not self.muted)

        backend_changed = old.get("speech_backend") != normalized.get("speech_backend")
        voice_changed = (
            old.get("speech_voice_id") != normalized.get("speech_voice_id")
            or old.get("speech_voice") != normalized.get("speech_voice")
        )
        if backend_changed:
            self.speech.set_backend(normalized.get("speech_backend", BACKEND_AUTO))
            self._active_voice = None
            self.voice_details = []
            self.voices = []
            self._set_status("speech", "Switching speech engine…")
            self._set_status("speech_engine_active", "Starting…")
            threading.Thread(
                target=self._load_voices, name="TALCON-Voices", daemon=True
            ).start()
        elif voice_changed:
            wanted = normalized.get("speech_voice_id") or normalized.get("speech_voice", "")
            self.speech.set_voice(wanted)
            match = next(
                (
                    voice for voice in self.voice_details
                    if voice.id == wanted or voice.name.lower() == str(wanted).lower()
                ),
                None,
            )
            if match is not None:
                self._active_voice = match
            self._set_status("speech", self._speech_status_text())
        self.speech.set_rate(int(normalized.get("speech_rate", 0)))
        self.speech.set_volume(int(normalized.get("speech_volume", 90)))

        voice_keys = {"vosk_model_path", "microphone_device", "wake_enabled", "always_listen"}
        speech_keys = {
            "speech_start_timeout", "speech_trailing_silence",
            "speech_max_utterance", "speech_min_confidence",
            "speech_alt_min_confidence", "speech_endpointer_mode",
            "vad_noise_floor",
        }
        if any(old.get(key) != normalized.get(key) for key in voice_keys):
            self._start_voice()
        elif (any(old.get(key) != normalized.get(key) for key in speech_keys)
              and self.voice is not None):
            apply = getattr(self.voice, "apply_settings", None)
            if callable(apply):
                apply(normalized)
        if old.get("microphone_device") != normalized.get("microphone_device"):
            self._set_status("microphone", self._microphone_label())
        if any(old.get(key) != normalized.get(key) for key in {"opencode_enabled", "opencode_base_url"}):
            self._start_opencode()
        if old.get("codex_enabled") != normalized.get("codex_enabled"):
            self._start_codex()
        if old.get("deepai_enabled") != normalized.get("deepai_enabled"):
            self._start_deepai()

        if "start_with_windows" in (values or {}):
            # Compare against the real shortcut, not just the stored flag, so a
            # missing or externally changed startup entry is repaired either way.
            desired = bool(normalized.get("start_with_windows"))
            if desired and not startup.is_current():
                ok, message = startup.enable()
                if ok:
                    log.info("Startup setting: %s", message)
            elif not desired and startup.is_enabled():
                ok, message = startup.disable()
                if ok:
                    log.info("Startup setting: %s", message)
            normalized["start_with_windows"] = startup.is_enabled()
            self.settings = normalized
            normalized = persist_settings(normalized)

        if not backend_changed:
            self._set_status("speech", self._speech_status_text())
        self.bridge.settings_changed.emit(dict(self.settings))
        return self.settings

    def _validate_modes(self, modes: dict) -> None:
        """Reject custom-mode triggers that cannot be registered safely."""
        probe = build_builtin_registry()
        custom_triggers = set(self.custom.used_triggers())
        for key, mode in (modes or {}).items():
            if key in BUILTIN_MODES or not isinstance(mode, dict):
                continue
            title = str(mode.get("title") or key.replace("_", " ").title())
            if key in RESERVED_MODE_KEYS:
                raise ValueError(
                    f"“{title}” uses a reserved section name. Choose another mode name."
                )
            triggers = mode.get("triggers") or []
            if not triggers:
                raise ValueError(f"Add at least one spoken command for {title} mode.")
            for raw in triggers:
                phrase = normalize_phrase(raw)
                if not phrase:
                    raise ValueError(f"{title} mode has an empty spoken command.")
                if phrase in custom_triggers:
                    raise ValueError(
                        f"“{phrase}” is already used by a custom command or automation."
                    )
                try:
                    probe.register(f"mode__{key}", [phrase], source="mode")
                except ValueError as exc:
                    raise ValueError(f"{title} mode: {exc}") from exc

    def save_modes(self, modes: dict) -> dict:
        cleaned = normalize_modes(modes)
        self._validate_modes(cleaned)
        cleaned = persist_modes(cleaned)
        self.modes_data = cleaned
        self.modes.reload(cleaned)
        if self.modes.current_mode not in cleaned:
            self.modes.current_mode = "normal"
            self._set_status("active_mode", "normal")
        self.commands.register_modes(cleaned)
        self.custom.set_reserved(self.commands.reserved_phrases())
        self._refresh_grammar()
        self.bridge.modes_changed.emit(cleaned)
        return cleaned

    # -- custom commands and automations ----------------------------------
    def recognition_phrases(self) -> set[str]:
        """All enabled phrases for the Vosk grammar (built-ins + custom + apps)."""
        phrases = set(self.commands.recognition_phrases())
        try:
            data = self.custom.snapshot()
        except Exception:
            log.exception("Could not read custom commands for the grammar")
        else:
            for kind in ("commands", "automations"):
                for entry in data.get(kind, []):
                    if entry.get("enabled", True):
                        phrases.update(entry.get("triggers", []))
        phrases.update(self._application_phrases())
        phrases.update(self._running_phrases())
        phrases.update(FOLLOW_UP_PHRASES)
        try:
            phrases.update(self.commands.pending_phrases())
        except Exception:
            log.debug("Could not read pending application phrases", exc_info=True)
        return phrases

    @staticmethod
    def _app_name_forms(name: str) -> list[str]:
        """Full name plus the short forms people actually say ("Chrome")."""
        base = normalize_phrase(name)
        if not base:
            return []
        forms = [base]
        words = base.split()
        if len(words) > 1:
            if words[0] in {"google", "microsoft", "mozilla", "adobe", "the"}:
                forms.append(" ".join(words[1:]))
            last = words[-1]
            if len(last) >= 5 or last in {"word", "code", "store", "edge", "brave", "vlc"}:
                forms.append(last)
        result: list[str] = []
        for form in forms:
            if form and form not in result:
                result.append(form)
        return result

    def _application_phrases(self) -> set[str]:
        """Bounded set of "open <app>" phrases from the cached catalogue.

        The catalogue is never rebuilt here.  Names are capped so the Vosk
        phrase-list grammar stays small even with hundreds of installed apps.
        """
        library = getattr(self, "apps", None)
        if library is None:
            return set()
        try:
            entries = library.cached_entries()
        except Exception:
            log.debug("Could not read cached applications", exc_info=True)
            return set()

        def rank(entry):
            name = normalize_phrase(entry.name)
            try:
                priority = PRIORITY_APP_NAMES.index(name)
            except ValueError:
                # Everyday apps first (in the order above), then taskbar pins,
                # then the shortest names across every source.
                priority = len(PRIORITY_APP_NAMES) + (
                    0 if entry.source == "Taskbar" else 1
                )
            return (priority, len(entry.name), entry.name.lower())

        chosen = []
        seen_names: set[str] = set()
        for entry in sorted(entries, key=rank):
            key = normalize_phrase(entry.name)
            if not key or key in seen_names:
                continue
            seen_names.add(key)
            chosen.append(entry)
            if len(chosen) >= APP_GRAMMAR_LIMIT:
                break
        phrases: set[str] = set()
        # Reserve room for the force/confirm templates before filling the
        # budget with ordinary open/close phrases.
        budget = MAX_APP_PHRASES - FORCE_GRAMMAR_LIMIT * 2 - 8
        names = {
            normalize_phrase(entry.name) for entry in chosen
            if normalize_phrase(entry.name)
        }
        # Alias phrases are reserved first so "open vs code" is never pushed
        # out by the bounded budget.
        aliases = getattr(self.commands, "app_aliases", {}) or {}
        for alias, target in aliases.items():
            if len(phrases) >= budget:
                break
            alias_name = normalize_phrase(alias)
            if alias_name and normalize_phrase(target) in names:
                for verb in ("open", "close", "launch", "exit"):
                    phrases.add(f"{verb} {alias_name}")
                    if len(phrases) >= budget:
                        break
        processed = 0
        for entry in chosen:
            if len(phrases) >= budget:
                break
            name = normalize_phrase(entry.name)
            if not name or len(name.split()) > 6:
                continue
            processed += 1
            verbs = ["open", "close"]
            if processed <= FULL_VERB_LIMIT:
                verbs += ["launch", "start", "run", "exit", "quit"]
            for form in self._app_name_forms(entry.name):
                for verb in verbs:
                    phrase = f"{verb} {form}"
                    if phrase not in phrases:
                        phrases.add(phrase)
                if len(phrases) >= budget:
                    break
        # Force close needs a separate, explicitly confirmed template.  It is
        # only added for the shortest app names to keep the grammar bounded.
        for entry in chosen[:FORCE_GRAMMAR_LIMIT]:
            name = normalize_phrase(entry.name)
            if not name:
                continue
            phrases.add(f"force close {name}")
            phrases.add(f"confirm force close {name}")
            if len(phrases) >= MAX_APP_PHRASES:
                break
        return phrases

    def _running_phrases(self) -> set[str]:
        """Close/query phrases for applications that are open right now."""
        if getattr(self, "voice", None) is None:
            return set()
        try:
            apps = self.running_applications()
        except Exception:
            log.debug("Could not read running applications for the grammar", exc_info=True)
            return set()
        phrases: set[str] = set()
        for app in apps[:RUNNING_QUERY_LIMIT]:
            forms = self._app_name_forms(app.name)
            for name in forms:
                if not name or len(name.split()) > 6:
                    continue
                for verb in APP_CLOSE_VERBS:
                    phrases.add(f"{verb} {name}")
                phrases.add(f"is {name} running")
                phrases.add(f"is {name} open")
                if len(phrases) >= MAX_RUNNING_PHRASES:
                    return phrases
        return phrases

    def _on_apps_discovered(self, entries, error: str = "", announce: bool = False) -> None:
        if error:
            log.warning("Application discovery failed: %s", error)
            self._set_status("apps", f"Application discovery failed: {error}")
            if announce:
                self.speech.say("I could not refresh the application list, sir.")
            return
        count = len(entries or ())
        self._set_status("apps", f"{count} applications discovered")
        log.info("Application catalogue ready: %d entries", count)
        try:
            self._refresh_grammar()
        except Exception:
            log.exception("Could not update the grammar after app discovery")
        if announce:
            self.speech.say(f"I found {count} applications, sir.")

    def refresh_apps(self, announce: bool = True) -> None:
        """Rebuild the application catalogue off the UI/voice thread."""
        self._set_status("apps", "Refreshing installed applications…")
        self.apps.refresh_async(
            lambda entries, error: self._on_apps_discovered(
                entries, error, announce=announce
            )
        )

    def _refresh_grammar(self) -> None:
        """Apply a new command vocabulary without reloading the model."""
        if self.voice is not None:
            try:
                self.voice.refresh_grammar()
            except Exception:
                log.exception("Could not refresh the recognition grammar")

    def test_phrase(self, text: str) -> dict:
        """Report what a typed phrase would match, without executing it."""
        normalized = normalize_phrase(text)
        if not normalized:
            return {"matched": False, "phrase": "", "reason": "Type a phrase to test."}
        command = self.commands.registry.match(normalized)
        if command is not None:
            finder = getattr(self.commands, "mode_for_action", None)
            mode_key = finder(command.action) if callable(finder) else None
            if mode_key is not None:
                title = self.mode_title(mode_key)
                return {
                    "matched": True,
                    "kind": "mode",
                    "phrase": normalized,
                    "name": f"{title} mode",
                    "action": command.action,
                }
            return {
                "matched": True,
                "kind": "builtin",
                "phrase": normalized,
                "name": command.action,
                "action": command.action,
            }
        entry = self.custom.match(normalized)
        if entry is not None:
            return {
                "matched": True,
                "kind": entry.get("kind", "command"),
                "phrase": normalized,
                "name": entry.get("name", "Custom command"),
                "action": entry.get("action", "automation"),
            }
        query = extract_app_command(normalized)
        if query:
            try:
                entries = self.apps.entries()
                result = resolve_application(query, entries, self.commands.app_aliases)
            except Exception:
                log.exception("Could not test an application phrase")
            else:
                if result.status == "match" and result.entry is not None:
                    return {
                        "matched": True,
                        "kind": "app",
                        "phrase": normalized,
                        "name": result.entry.name,
                        "action": "open_app",
                    }
                if result.status == "ambiguous" and result.candidates:
                    names = ", ".join(item.name for item in result.candidates[:3])
                    return {
                        "matched": False,
                        "phrase": normalized,
                        "reason": f"Ambiguous application: {names}. Please be more specific.",
                    }
        return {
            "matched": False,
            "phrase": normalized,
            "reason": "No enabled built-in, custom command or automation uses this phrase.",
        }

    def handle_custom_phrase(self, text: str) -> str | None:
        entry = self.custom.match(text)
        if not entry:
            return None
        name = str(entry.get("name", "Custom command"))
        self._set_status("last_command", text)
        if entry.get("kind") == "automation":
            started = self.automations.run(entry)
            if not started:
                message = f"{name} is already running."
                self._set_status("last_result", message)
                self._set_face("failure")
                self._record_activity(message)
                return "automation_busy"
            self._set_status("last_result", f"Automation started: {name}")
            self._set_face("completed")
            self._record_activity(f"Automation started: {name}")
            return f"automation:{entry.get('id')}"
        try:
            ok, message = self.executor.execute(entry)
        except Exception as exc:
            log.exception("Custom command failed")
            ok, message = False, str(exc)
        self._set_status("last_result", message)
        self._set_face("completed" if ok else "failure")
        self._record_activity(message)
        return f"custom:{entry.get('id')}" if ok else "custom_failed"

    def _automation_status(self, text: str) -> None:
        self._set_status("last_result", text)
        self._record_activity(text)

    def custom_snapshot(self) -> dict:
        return self.custom.snapshot()

    def reserved_command_phrases(self) -> set[str]:
        return self.commands.reserved_phrases()

    def save_custom_command(self, entry: dict, entry_id: str | None = None) -> dict:
        saved = self.custom.save_command(entry, entry_id)
        self._refresh_grammar()
        self.bridge.settings_changed.emit(dict(self.settings))
        return saved

    def delete_custom_command(self, entry_id: str) -> bool:
        removed = self.custom.delete("commands", entry_id)
        self._refresh_grammar()
        self.bridge.settings_changed.emit(dict(self.settings))
        return removed

    def set_custom_command_enabled(self, entry_id: str, enabled: bool) -> bool:
        changed = self.custom.set_enabled("commands", entry_id, enabled)
        self._refresh_grammar()
        self.bridge.settings_changed.emit(dict(self.settings))
        return changed

    def save_automation(self, entry: dict, entry_id: str | None = None) -> dict:
        saved = self.custom.save_automation(entry, entry_id)
        self._refresh_grammar()
        self.bridge.settings_changed.emit(dict(self.settings))
        return saved

    def delete_automation(self, entry_id: str) -> bool:
        removed = self.custom.delete("automations", entry_id)
        self._refresh_grammar()
        self.bridge.settings_changed.emit(dict(self.settings))
        return removed

    def set_automation_enabled(self, entry_id: str, enabled: bool) -> bool:
        changed = self.custom.set_enabled("automations", entry_id, enabled)
        self._refresh_grammar()
        self.bridge.settings_changed.emit(dict(self.settings))
        return changed

    # -- shutdown ----------------------------------------------------------
    def shutdown(self) -> None:
        if self._stopped:
            return
        self._stopped = True
        log.info("Stopping TALCON services")
        for service in (self.voice, self.opencode, self.codex, self.deepai, self.cron):
            if service is not None:
                try:
                    service.stop()
                except Exception:
                    log.exception("Service stop failed")
        self.voice = None
        self.opencode = None
        self.codex = None
        self.deepai = None
        self.cron = None
        try:
            self.assistant.shutdown()
        except Exception:
            log.exception("Assistant shutdown failed")
        self.assistant.clear_context()
        try:
            self.speech.stop()
        except Exception:
            log.exception("Speech shutdown failed")
        # Session history is memory-only; clear it explicitly on the way out so
        # nothing can keep referencing it, and never persist it.
        with self._conversation_lock:
            self._conversation.clear()
            self._pending_request = None
        log.info("TALCON services stopped")
