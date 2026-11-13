"""Deterministic, offline voice/typed command routing.

There is no language model and no fuzzy matching here.  Every built-in
command is an exact alias of a normalised phrase stored in a registry, so a
transcript either matches a known command exactly or it does not run at all.
The registry also makes duplicate/conflicting phrases impossible to add.
"""
from __future__ import annotations

import datetime as _dt
import logging
import re
from dataclasses import dataclass, field

log = logging.getLogger("nova.commands")

_PUNCTUATION = re.compile(r"[^\w\s']+")
_WAKE_PREFIXES = (
    "hey nova ", "okay nova ", "ok nova ", "nova ", "no va ",
)
_WAKE_WORDS = {"nova", "hey nova", "okay nova", "ok nova", "no va"}
_POLITE_PREFIXES = (
    "please ", "kindly ", "can you ", "could you ", "would you ", "will you ",
    "i want you to ", "i would like you to ", "i want to ", "just ",
)
_POLITE_SUFFIXES = (" please",)


def normalize_phrase(phrase: str) -> str:
    """Lower-case, strip punctuation and remove wake/polite lead-ins.

    This is still deterministic phrase matching: only known filler prefixes are
    removed, never arbitrary words, and the remaining phrase must match a
    registered alias exactly.
    """
    text = str(phrase or "").lower().strip()
    text = _PUNCTUATION.sub(" ", text)
    text = " ".join(text.split())
    changed = True
    while changed and text:
        changed = False
        for prefix in _WAKE_PREFIXES + _POLITE_PREFIXES:
            if text.startswith(prefix):
                text = text[len(prefix):].strip()
                changed = True
                break
    for suffix in _POLITE_SUFFIXES:
        if text.endswith(suffix):
            text = text[: -len(suffix)].strip()
    return text


class CommandConflictError(ValueError):
    """Raised when a phrase would belong to more than one command."""


@dataclass(frozen=True)
class Command:
    action: str
    phrases: tuple[str, ...]
    description: str = ""
    reserved: bool = True
    source: str = "builtin"


@dataclass
class CommandRegistry:
    """Exact-phrase command registry with duplicate protection."""

    _by_phrase: dict[str, Command] = field(default_factory=dict)
    commands: dict[str, Command] = field(default_factory=dict)

    def register(self, action: str, phrases, description: str = "",
                 reserved: bool = True, source: str = "builtin") -> Command:
        if not action or not str(action).strip():
            raise ValueError("A command needs an action name.")
        clean: list[str] = []
        for phrase in phrases:
            normalized = normalize_phrase(phrase)
            if not normalized:
                raise ValueError(f"Command {action!r} has an empty phrase.")
            if normalized in _WAKE_WORDS:
                raise ValueError(f"{normalized!r} is reserved for the wake word.")
            if normalized in self._by_phrase:
                owner = self._by_phrase[normalized].action
                raise CommandConflictError(
                    f"Phrase {normalized!r} is already used by {owner!r}."
                )
            if normalized in clean:
                continue
            clean.append(normalized)
        if not clean:
            raise ValueError(f"Command {action!r} needs at least one phrase.")
        command = Command(action, tuple(clean), description, reserved, source)
        self.commands[action] = command
        for phrase in clean:
            self._by_phrase[phrase] = command
        return command

    def match(self, text: str) -> Command | None:
        return self._by_phrase.get(normalize_phrase(text))

    def phrases(self) -> set[str]:
        return set(self._by_phrase)

    def action_for_phrase(self, phrase: str) -> str | None:
        command = self.match(phrase)
        return command.action if command else None


def build_builtin_registry() -> CommandRegistry:
    registry = CommandRegistry()

    registry.register(
        "study",
        (
            "study mode", "study", "activate study mode", "activate my study mode",
            "start study mode", "start study", "start studying", "begin study mode",
            "begin studying", "open study mode", "prepare my study environment",
            "prepare study environment", "prepare my study", "get me ready for study",
            "get ready for study", "get ready for my study mode", "ready for study",
        ),
        "Activate Study Mode.",
    )
    registry.register(
        "entertainment",
        (
            "entertainment mode", "entertainment", "activate entertainment mode",
            "activate my entertainment mode", "start entertainment",
            "start entertainment mode", "open entertainment mode", "music mode",
            "start music mode", "prepare my entertainment environment",
            "prepare entertainment environment", "prepare entertainment",
            "get me ready for entertainment", "get ready for entertainment",
            "get ready for my entertainment mode", "ready for entertainment",
        ),
        "Activate Entertainment Mode.",
    )
    registry.register(
        "mute",
        ("mute", "mute voice", "mute yourself", "be quiet", "silence", "stop speaking"),
        "Mute NOVA's spoken output.",
    )
    registry.register(
        "unmute",
        ("unmute", "unmute voice", "voice on", "speak again", "you can talk", "enable voice"),
        "Unmute NOVA's spoken output.",
    )
    registry.register(
        "wake_off",
        (
            "stop listening", "stop the microphone", "turn off the microphone",
            "stop listening for me", "disable wake word", "stop wake word",
        ),
        "Stop listening for the wake word.",
    )
    registry.register(
        "wake_on",
        (
            "start listening", "turn on the microphone", "listen for me", "wake up",
            "enable wake word", "start wake word",
        ),
        "Start listening for the wake word.",
    )
    registry.register(
        "listen_now",
        ("listen now", "listen to me now", "capture a command", "take a command"),
        "Capture one command immediately.",
    )
    registry.register(
        "settings",
        (
            "open settings", "open the settings", "settings", "configuration",
            "open configuration", "open control center", "control center",
            "open nova settings",
        ),
        "Open the NOVA Control Center.",
    )
    registry.register(
        "hide",
        ("hide nova", "hide yourself", "hide to tray", "hide the face", "hide",
         "hide nova to tray"),
        "Hide the Control Center in the tray.",
    )
    registry.register(
        "show",
        ("show nova", "show yourself", "show the face", "show face",
         "show nova control center"),
        "Show NOVA's face and Control Center.",
    )
    registry.register(
        "exit",
        (
            "exit", "exit nova", "exit assistant", "close nova", "quit nova",
            "shut down nova", "goodbye nova", "goodbye", "close", "quit",
            "close assistant",
        ),
        "Exit NOVA completely.",
    )
    registry.register(
        "time",
        (
            "what time is it", "what is the time", "the time", "tell me the time",
            "time please", "what time is it now",
        ),
        "Read the current time.",
    )
    registry.register(
        "status",
        (
            "what mode is active", "what mode", "which mode", "current mode",
            "what is my current mode", "which mode is active",
        ),
        "Report the active mode.",
    )
    registry.register(
        "help",
        ("help", "what can you do", "list commands", "your commands",
         "what can i say", "show commands"),
        "Read the built-in command help.",
    )
    registry.register(
        "thanks",
        ("thank you", "thanks", "thank you nova"),
        "A short acknowledgement.",
    )
    return registry


class CommandRouter:
    def __init__(self, modes, speech, app):
        self.modes = modes
        self.speech = speech
        self.app = app
        self.registry = build_builtin_registry()
        # Set by NovaRuntime: callable(text) -> action string | None.
        self.custom_handler = None

    # -- public API --------------------------------------------------------
    def reserved_phrases(self) -> set[str]:
        return self.registry.phrases() | set(_WAKE_WORDS)

    def handle(self, phrase: str, announce_unknown: bool = True) -> str:
        text = normalize_phrase(phrase)
        log.info("Recognized command: %s", text or "(empty)")
        if not text:
            return "none"

        command = self.registry.match(text)
        if command is not None:
            return self._invoke(command.action)

        if self.custom_handler is not None:
            try:
                custom_action = self.custom_handler(text)
            except Exception:
                log.exception("Custom command failed")
                custom_action = None
            if custom_action:
                return str(custom_action)

        log.info("Unrecognized command: %s", text)
        if announce_unknown:
            self.speech.say("I did not recognize that command, sir. Please try again.")
        return "unknown"

    # -- built-in handlers -------------------------------------------------
    def _invoke(self, action: str) -> str:
        handler = getattr(self, f"_do_{action}", None)
        if handler is None:
            log.error("Command %r has no handler", action)
            return "unknown"
        return handler()

    def _do_study(self) -> str:
        self.app.activate_mode("study")
        return "study"

    def _do_entertainment(self) -> str:
        self.app.activate_mode("entertainment")
        return "entertainment"

    def _do_mute(self) -> str:
        self.app.set_muted(True)
        return "mute"

    def _do_unmute(self) -> str:
        self.app.set_muted(False)
        self.speech.say("Voice announcements are enabled, sir.")
        return "unmute"

    def _do_wake_off(self) -> str:
        self.app.set_wake_enabled(False)
        self.speech.say("I have stopped listening for the wake word, sir.")
        return "wake_off"

    def _do_wake_on(self) -> str:
        self.app.set_wake_enabled(True)
        self.speech.say("I am listening for the wake word again, sir.")
        return "wake_on"

    def _do_listen_now(self) -> str:
        self.app.listen_now()
        return "listen_now"

    def _do_settings(self) -> str:
        self.app.show_settings()
        return "settings"

    def _do_hide(self) -> str:
        self.app.hide_to_tray()
        return "hide"

    def _do_show(self) -> str:
        self.app.set_face_visible(True)
        self.app.show_settings()
        return "show"

    def _do_exit(self) -> str:
        self.app.exit_completely()
        return "exit"

    def _do_time(self) -> str:
        now = _dt.datetime.now().strftime("%I:%M %p").lstrip("0")
        self.speech.say(f"It is {now}, sir.")
        return "time"

    def _do_status(self) -> str:
        mode = getattr(self.modes, "current_mode", "normal")
        self.speech.say(f"Your current mode is {mode}, sir.")
        return "status"

    def _do_help(self) -> str:
        self.speech.say(
            "I can activate your study or entertainment mode, mute or unmute my voice, "
            "open my settings, hide or show my face, stop or start listening, run your "
            "custom commands and automations, and tell you the time, sir."
        )
        return "help"

    def _do_thanks(self) -> str:
        self.speech.say("You are most welcome, sir.")
        return "thanks"
