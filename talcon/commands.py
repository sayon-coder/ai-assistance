"""Deterministic, offline voice/typed command routing.

There is no language model and no fuzzy matching here.  Every built-in
command is an exact alias of a normalised phrase stored in a registry, so a
transcript either matches a known command exactly or it does not run at all.
The registry also makes duplicate/conflicting phrases impossible to add.
"""
from __future__ import annotations

import datetime as _dt
import json
import logging
import re
import time
from dataclasses import dataclass, field

from .app_catalog import (
    _CLOSE_VERBS, extract_app_command, extract_close_command,
    extract_force_close_command, extract_force_confirm_command,
    extract_running_query, is_force_cancel, normalize_app_name,
    resolve_application,
)
from .apps import launch_app
from .intent import IntentMatch, match_intent
from .windows_control import is_protected_process
from .config import BUILTIN_MODES, CONFIG_DIR

log = logging.getLogger("talcon.commands")

_PUNCTUATION = re.compile(r"[^\w\s']+")
_WAKE_PREFIXES = (
    "hey talcon ", "okay talcon ", "ok talcon ", "talcon ",
    "tell can ", "tal con ", "talc on ", "talk on ",
    # The small model mishears "hey TALCON" in several ways.
    "you tell can ", "a tell can ", "the talcon ", "hey tal can ",
    "hey talcon ", "okay talcon ", "ok talcon ", "talcon ", "no va ",
    "you talcon ", "hey talcon a ",
)
_WAKE_WORDS = {
    "talcon", "hey talcon", "okay talcon", "ok talcon", "no va",
    "talcon", "hey talcon", "okay talcon", "ok talcon",
    "tell can", "tal con", "talc on", "talk on",
}
# The command grammar only stores the bare form, so the spoken wake-prefixed
# variants of the introduce request are added explicitly.  Without them the
# small model tends to decode "TALCON introduce yourself" as an unrelated
# phrase from the command list.
_EXTRA_GRAMMAR_PHRASES = frozenset({
    "tell can introduce yourself",
    "tal con introduce yourself",
    "talc on introduce yourself",
    "talk on introduce yourself",
    "hey tell can introduce yourself",
    "okay tell can introduce yourself",
    "tell can introduce",
    # "Close it" refers to the application TALCON opened last.  These are not
    # registry commands (the close parser handles them), but Vosk only hears
    # words from the grammar, so they must be listed here.
    "close it",
    "close that",
    "close this",
    "close it now",
    "now close it",
    "close the app",
    "close that app",
    "close this app",
    "close the application",
    "quit it",
    "exit it",
})
_POLITE_PREFIXES = (
    "you know what ", "please ", "kindly ", "can you ", "could you ",
    "would you ", "will you ", "i want you to ", "i would like you to ",
    "i want to ", "to ", "just ", "hey ", "okay ", "ok ", "so ", "um ", "uh ",
    "well ", "now ", "and ", "then ", "also ", "next ", "alright ",
)
_POLITE_SUFFIXES = (" please",)

#: Short answers accepted while a dangerous action is waiting for confirmation.
_CONFIRM_WORDS = frozenset({
    "yes", "yeah", "yep", "confirm", "confirmed", "do it", "go ahead",
    "yes do it", "yes please", "proceed", "yes proceed", "okay do it",
    "ok do it", "sure", "yes confirm",
})
_DENY_WORDS = frozenset({
    "no", "nope", "cancel", "stop", "do not", "don't", "no cancel",
    "do not do it", "don't do it",
})

#: Words that may surround such a pronoun without changing its meaning.
_PRONOUN_CLOSE_FILLERS = frozenset({
    "the", "a", "an", "app", "application", "program", "window", "now",
    "please", "again", "it", "that", "this", "one", "for", "me", "down",
    "sir", "talcon",
})


def is_pronoun_query(query: str) -> bool:
    """True when the name to close is really "it"/"that app"/"this one"."""
    words = str(query or "").split()
    if not words:
        return False
    if not any(
        word in {"it", "that", "this", "app", "application"} for word in words
    ):
        return False
    return all(word in _PRONOUN_CLOSE_FILLERS for word in words)


def is_pronoun_close(text: str) -> bool:
    """True for "close it", "close the app", "quit that now" and similar."""
    words = str(text or "").split()
    if not words or words[0] not in _CLOSE_VERBS:
        return False
    rest = words[1:]
    return bool(rest) and is_pronoun_query(" ".join(rest))

#: Filler words that may surround an answer to "which one?".  They are removed
#: before looking for an ordinal, so "yes, the first one" means "first".
_ANSWER_FILLERS = frozenset({
    "yes", "yeah", "yep", "yup", "ok", "okay", "sure", "please", "the",
    "a", "an", "open", "launch", "start", "do", "it", "and", "um", "uh",
    "well", "so", "number", "option", "one", "my", "that", "this", "i",
    "want", "you", "to", "sir", "talcon", "hey",
})
#: Ordinal answers, checked after fillers are removed.
_ANSWER_ORDINALS = {
    "first": 0, "1st": 0, "1": 0, "one": 0,
    "second": 1, "2nd": 1, "2": 1, "two": 1,
    "third": 2, "3rd": 2, "3": 2, "three": 2,
    "fourth": 3, "4th": 3, "4": 3, "four": 3,
}
_ANSWER_YES = frozenset({
    "yes", "yeah", "yep", "yup", "sure", "ok", "okay", "correct",
    "right", "please", "go", "ahead", "confirm", "affirmative",
})
_ANSWER_NO = frozenset({
    "no", "nope", "nah", "cancel", "nothing", "neither", "stop",
    "forget", "nevermind", "never",
})

# Utterances that begin with one of these verbs are treated as failed command
# attempts rather than unrelated background conversation.  This keeps the
# single "please repeat" request deterministic and avoids nagging on chatter.
# Only imperative/action words belong here: ordinary questions and statements
# must stay silent.
_COMMAND_INTENT_PREFIXES = {
    "open", "launch", "start", "activate", "switch", "mute", "unmute",
    "enable", "disable", "hide", "show", "exit", "close", "quit", "stop",
    "begin", "prepare", "listen", "capture", "take", "turn", "set", "run",
    "play",
}


def normalize_phrase(phrase: str) -> str:
    """Lower-case, strip punctuation and remove wake/polite lead-ins.

    This is still deterministic phrase matching: only known filler prefixes are
    removed, never arbitrary words, and the remaining phrase must match a
    registered alias exactly.
    """
    text = str(phrase or "").lower().strip()
    text = _PUNCTUATION.sub(" ", text)
    text = " ".join(text.split())
    # Vosk often truncates "mode" to "mod" in short phrases.
    text = re.sub(r"\bmod\b", "mode", text)
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


def strip_wake_phrase(phrase: str) -> str:
    """Return the command after a wake word, or "" when only the wake word was said."""
    remainder = normalize_phrase(phrase)
    if not remainder or remainder in _WAKE_WORDS:
        return ""
    return remainder


def is_command_like(phrase: str, vocabulary=()) -> bool:
    """Deterministic guess: could this utterance be a failed command attempt?

    Only used to decide whether to ask the user to repeat.  It never executes
    anything and never changes command matching.
    """
    text = normalize_phrase(phrase)
    if not text:
        return False
    if text.split()[0] in _COMMAND_INTENT_PREFIXES:
        return True
    prefix = text + " "
    return any(str(known).startswith(prefix) for known in vocabulary if known)


# Words that the installed small model cannot pronounce as one token.  Vosk
# drops a grammar phrase entirely when one of its words is missing from the
# model's word list (it logs "Ignoring word missing in vocabulary"), so the
# grammar uses the spoken form instead.  The registry registers both spellings,
# which keeps recognition exact.
_GRAMMAR_SPOKEN_FORMS = {
    "unmute": "un mute",
}


def spoken_form(phrase: str) -> str:
    """Return the form of a phrase that the installed Vosk model can decode."""
    words = normalize_phrase(phrase).split()
    return " ".join(_GRAMMAR_SPOKEN_FORMS.get(word, word) for word in words)


def grammar_form(phrase: str) -> str:
    """Like :func:`spoken_form`, but keeps any spoken wake prefix.

    The grammar needs the wake-prefixed variants ("tell can introduce
    yourself") to recognise a one-shot request, so it must not strip them.
    """
    text = _PUNCTUATION.sub(" ", str(phrase).lower())
    text = " ".join(text.split())
    # Match normalize_phrase's deterministic fixes without dropping wake words.
    text = re.sub(r"\bmod\b", "mode", text)
    words = text.split()
    return " ".join(_GRAMMAR_SPOKEN_FORMS.get(word, word) for word in words)


def build_recognition_grammar(phrases, include_unknown: bool = True) -> str:
    """Build the Vosk phrase-list grammar from normalised command phrases."""
    clean: list[str] = []
    for phrase in phrases or ():
        if "[unk]" in str(phrase).lower():
            continue
        normalized = grammar_form(phrase)
        if normalized and normalized not in clean:
            clean.append(normalized)
    clean.sort()
    if include_unknown:
        clean.append("[unk]")
    return json.dumps(clean, ensure_ascii=False)


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

    def is_prefix(self, text: str) -> bool:
        """True when the phrase is a proper prefix of a registered command."""
        phrase = normalize_phrase(text)
        if not phrase:
            return False
        prefix = phrase + " "
        return any(known.startswith(prefix) for known in self._by_phrase)

    def phrases(self) -> set[str]:
        return set(self._by_phrase)

    def remove_source(self, source: str) -> None:
        """Remove every command registered by one dynamic source."""
        actions = [
            action for action, command in self.commands.items()
            if command.source == source
        ]
        for action in actions:
            command = self.commands.pop(action)
            for phrase in command.phrases:
                if self._by_phrase.get(phrase) is command:
                    self._by_phrase.pop(phrase, None)

    def action_for_phrase(self, phrase: str) -> str | None:
        command = self.match(phrase)
        return command.action if command else None


def build_builtin_registry() -> CommandRegistry:
    registry = CommandRegistry()

    registry.register(
        "study",
        (
            "study mode", "study", "activate study mode", "activate my study mode",
            "start study mode", "start my study mode", "start study", "start studying",
            "begin study mode", "begin study", "begin studying", "open study mode",
            "open my study mode", "switch to study mode", "switch to my study mode",
            "prepare study", "prepare my study", "prepare my study environment",
            "prepare study environment", "get me ready for study", "get ready for study",
            "get ready for my study mode", "ready for study",
        ),
        "Activate Study Mode.",
    )
    registry.register(
        "entertainment",
        (
            "entertainment mode", "entertainment", "activate entertainment mode",
            "activate my entertainment mode", "start entertainment",
            "start entertainment mode", "start my entertainment mode",
            "begin entertainment", "begin entertainment mode",
            "open entertainment mode", "open my entertainment mode",
            "switch to entertainment mode", "switch to my entertainment mode",
            "music mode", "start music mode", "prepare my entertainment environment",
            "prepare entertainment environment", "prepare entertainment",
            "get me ready for entertainment", "get ready for entertainment",
            "get ready for my entertainment mode", "ready for entertainment",
        ),
        "Activate Entertainment Mode.",
    )
    registry.register(
        "stock",
        (
            "stock mode", "stock", "stock market", "stock market mode",
            "market mode", "open news", "open the news", "open stock market",
            "open stock mode", "show me the news", "business news",
            "open business news", "news mode", "news", "activate stock mode",
            "start stock mode", "switch to stock mode", "get ready for stock",
            "prepare stock mode",
        ),
        "Activate Stock Mode.",
    )
    registry.register(
        "mute",
        ("mute", "mute voice", "mute yourself", "stop speaking",
         "disable voice", "turn off voice", "stop voice"),
        "Mute TALCON's spoken output.",
    )
    registry.register(
        "unmute",
        ("unmute", "unmute voice", "un mute", "un mute voice", "voice on",
         "speak again", "you can talk", "enable voice", "turn on voice"),
        "Unmute TALCON's spoken output.",
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
        "open_eyes",
        (
            "open your eyes", "open the eyes", "open eyes", "open up your eyes",
            "show your eyes", "show me your eyes", "show your face",
            "look at me", "are you awake", "are you there",
        ),
        "Wake the face and show it on screen.",
    )
    registry.register(
        "close_eyes",
        (
            "close your eyes", "close the eyes", "close eyes",
            "hide your eyes", "go to sleep", "sleep now",
        ),
        "Close the face without stopping the assistant.",
    )
    registry.register(
        "listen_now",
        ("listen now", "listen to me now", "capture a command", "take a command"),
        "Capture one command immediately.",
    )
    registry.register(
        "commands_off",
        (
            "don't take commands", "dont take commands", "do not take commands",
            "stop taking commands", "pause commands", "no commands",
            "ignore commands", "stop listening for commands",
            "no more commands", "pause taking commands",
        ),
        "Stop taking commands until the user says 'take command'.",
    )
    registry.register(
        "commands_on",
        (
            "take command", "take commands", "start taking commands",
            "resume commands", "start commands", "take my command",
            "you can take commands", "start taking command",
            "resume taking commands", "take my commands",
            "begin taking commands", "listen for commands",
        ),
        "Start taking commands again after a pause.",
    )
    registry.register(
        "assistant_cancel",
        (
            "stop that", "never mind", "nevermind", "forget it", "cancel that",
            "stop the request", "cancel the request", "abort that", "stop it",
        ),
        "Cancel the AI request that is currently running.",
    )
    registry.register(
        "settings",
        (
            "open settings", "open the settings", "settings", "configuration",
            "open configuration", "open control center", "control center",
            "open talcon settings", "open my settings", "talcon settings",
            "open talcon settings", "talcon settings",
        ),
        "Open the TALCON Control Center.",
    )
    registry.register(
        "hide",
        ("hide talcon", "hide talcon", "hide yourself", "hide your self",
         "hide the face", "hide", "hide your face"),
        "Hide TALCON's faces without opening or hiding the Control Center.",
    )
    registry.register(
        "hide_to_tray",
        ("hide to tray", "hide talcon to tray", "hide the control center",
         "hide control center", "hide in tray"),
        "Hide the Control Center in the system tray.",
    )
    registry.register(
        "show",
        ("show talcon", "show talcon", "show yourself", "show your self",
         "show the face", "show face", "show yourself again"),
        "Show TALCON's faces without opening the Control Center.",
    )
    registry.register(
        "show_control_center",
        ("show talcon control center", "show the control center",
         "open the control center"),
        "Open the TALCON Control Center.",
    )
    registry.register(
        "exit",
        (
            "exit", "exit talcon", "exit assistant", "close talcon", "quit talcon",
            "shut down talcon", "goodbye talcon", "goodbye", "close", "quit",
            "close assistant",
        ),
        "Exit TALCON completely.",
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
        "introduce",
        (
            "introduce yourself", "talcon introduce yourself", "introduce",
            "who are you", "what are you", "tell me about yourself",
        ),
        "Greet everyone and describe what TALCON can do on this PC.",
    )
    registry.register(
        "thanks",
        ("thank you", "thanks", "thank you talcon"),
        "A short acknowledgement.",
    )
    registry.register(
        "refresh_apps",
        (
            "refresh app list", "refresh apps", "refresh application list",
            "refresh applications", "refresh the app list", "update app list",
            "update apps", "update applications", "rescan apps",
            "rescan applications", "reload app list",
        ),
        "Discover installed Windows applications again.",
    )
    registry.register(
        "media_toggle",
        (
            "pause video", "pause youtube", "pause playback", "pause music",
            "resume video", "play video", "resume playback", "resume music",
            "play music", "stop video", "stop music",
        ),
        "Send the Windows play/pause media key to the active media session.",
    )
    registry.register(
        "list_running",
        (
            "what applications are currently running", "what apps are running",
            "what applications are running", "what is currently running",
            "what is running", "whats running", "what's running",
            "what are running", "show running applications", "show running apps",
            "list running applications", "list running apps",
            "which applications are running", "which apps are running",
            "which apps are open", "what apps are open",
            "how many apps are running", "how many app are running",
            "how many applications are running",
            "how many apps are currently running",
            "how many applications are currently running",
            "how many apps are running right now",
            "how many app are running right now",
            "how many applications are running right now",
            "how many apps are open right now",
            "how many applications are open right now",
            "how many apps are open", "how many applications are open",
            "number of running apps", "count running apps",
            "count running applications", "how many programs are running",
            "how many programs are open",
        ),
        "Read the live list of user applications that are open.",
    )
    registry.register(
        "close_all",
        (
            "close everything", "close everything on my desktop",
            "close all applications", "close all apps", "close all open windows",
        ),
        "Ask every open application window to close, after confirmation.",
    )
    registry.register(
        "confirm_close_all",
        (
            "confirm close everything", "confirm close all", "yes close all",
            "yes close everything",
        ),
        "Confirm the pending close-all request.",
    )
    registry.register(
        "cancel_close_all",
        ("cancel close everything", "cancel close all", "no close everything"),
        "Cancel the pending close-all request.",
    )
    registry.register(
        "shutdown",
        (
            "shutdown", "shut down", "shut down computer", "shutdown my pc",
            "turn off my computer", "shut down the computer",
            "shut down my computer", "turn off the computer",
            "shutdown the computer", "shut down pc", "turn off pc",
            "power off", "power off the computer",
        ),
        "Shut down Windows, after confirmation.",
    )
    registry.register(
        "confirm_shutdown",
        (
            "confirm shutdown", "confirm shut down", "confirm the shutdown",
            "confirm the shut down", "yes confirm shutdown",
            "yes shut down", "yes shutdown", "yes shut down the computer",
            "yes shut down my computer",
        ),
        "Confirm the pending Windows shutdown request.",
    )
    registry.register(
        "cancel_shutdown",
        (
            "cancel shutdown", "cancel shut down", "no shutdown",
            "no shut down", "do not shut down", "don't shut down",
        ),
        "Cancel a pending or counting-down Windows shutdown.",
    )
    return registry


class CommandRouter:
    def __init__(self, modes, speech, app):
        self.modes = modes
        self.speech = speech
        self.app = app
        self.registry = build_builtin_registry()
        # Set by TalconRuntime: callable(text) -> action string | None.
        self.custom_handler = None
        # Set by TalconRuntime for the AI assistant: callables taking the
        # normalised phrase and returning True when the phrase was consumed.
        # ``natural_handler`` runs only after every local parser has failed,
        # so local commands always keep priority; ``control_handler`` consumes
        # "stop that"-style phrases while an AI request is in flight.
        self.natural_handler = None
        self.control_handler = None
        # Discovered application catalogue, aliases and the launch function.
        # ``app_launcher`` is injectable so tests never start a real program.
        self.apps = None
        self.app_aliases: dict[str, str] = {}
        self.app_launcher = launch_app
        self._pending_apps: tuple = ()
        self._pending_query = ""
        self._pending_kind = ""
        #: The application TALCON opened last, so "close it" means that one.
        self._last_opened = None
        # (app, exact name, deadline) for an explicitly confirmed force close.
        self._force_pending: dict | None = None
        # Dynamic user-created modes: action name -> mode key.
        self._mode_actions: dict[str, str] = {}

    # -- applications ------------------------------------------------------
    def set_application_library(self, library, aliases=None) -> None:
        self.apps = library
        self.app_aliases = dict(aliases or {})

    def pending_phrases(self) -> set[str]:
        """Bare names the user may say to answer an ambiguity prompt."""
        phrases: set[str] = set()
        for entry in self._pending_apps[:6]:
            name = normalize_phrase(entry.name)
            if name:
                phrases.add(name)
        if self._pending_apps:
            # The prompt offers numbered choices, so the numbered answers must
            # be in the grammar or Vosk can never decode them.  "yes" and "no"
            # cover the "Did you mean X?" form.
            phrases.update({
                "yes", "yeah", "yes please", "no", "nope", "cancel",
                "first", "first one", "the first one", "number one",
                "second", "second one", "the second one", "number two",
                "third", "third one", "the third one", "number three",
            })
        return phrases

    # -- dynamic modes -----------------------------------------------------
    def register_modes(self, modes: dict, strict: bool = False) -> list[str]:
        """Register the voice triggers of user-created modes.

        Built-in modes keep their large alias lists from the static registry.
        Returns a list of human-readable conflicts when strict is False.
        """
        self.registry.remove_source("mode")
        self._mode_actions = {}
        conflicts: list[str] = []
        for key, mode in (modes or {}).items():
            key = str(key)
            if key in BUILTIN_MODES or not isinstance(mode, dict):
                continue
            triggers = mode.get("triggers") or []
            if not triggers:
                continue
            title = str(mode.get("title") or key.replace("_", " ").title())
            action = f"mode__{key}"
            try:
                self.registry.register(
                    action, triggers, f"Activate {title} mode.", source="mode"
                )
            except (CommandConflictError, ValueError) as exc:
                message = f"{title}: {exc}"
                conflicts.append(message)
                if strict:
                    raise ValueError(message) from exc
                log.warning("Custom mode trigger ignored: %s", message)
                continue
            self._mode_actions[action] = key
        return conflicts

    def mode_for_action(self, action: str) -> str | None:
        return self._mode_actions.get(str(action))

    # -- public API --------------------------------------------------------
    def reserved_phrases(self) -> set[str]:
        return self.registry.phrases() | set(_WAKE_WORDS)

    def recognition_phrases(self) -> set[str]:
        """Every enabled built-in phrase, for the Vosk command grammar."""
        return self.registry.phrases() | set(_EXTRA_GRAMMAR_PHRASES)

    def match_phrase(self, phrase: str) -> Command | None:
        return self.registry.match(phrase)

    def looks_like_command(self, phrase: str, vocabulary=()) -> bool:
        known = self.registry.phrases() | set(vocabulary or ())
        return is_command_like(phrase, known)

    def handle(self, phrase: str, announce_unknown: bool = True) -> str:
        text = normalize_phrase(phrase)
        log.info("Recognized command: %s", text or "(empty)")
        if not text:
            return "none"

        command = self.registry.match(text)
        if command is not None:
            return self._invoke(command.action)

        pending = self._pending_action_kind()
        if pending and text in _CONFIRM_WORDS:
            return self._confirm_pending(pending)
        if pending and text in _DENY_WORDS:
            return self._cancel_pending(pending)

        if self.control_handler is not None:
            try:
                if self.control_handler(text):
                    return "assistant_cancel"
            except Exception:
                log.exception("Assistant control handler failed")

        if self.custom_handler is not None:
            try:
                custom_action = self.custom_handler(text)
            except Exception:
                log.exception("Custom command failed")
                custom_action = None
            if custom_action:
                return str(custom_action)

        # Flexible, fully deterministic intent patterns ("how many programs do
        # I have open right now?", "turn it up", "say that again").  They only
        # map natural wording onto existing, tested handlers.
        intent = match_intent(text)
        if intent is not None:
            return self._run_intent(intent)

        pending = self._serve_pending_app(text)
        if pending is not None:
            return pending

        confirm_force = extract_force_confirm_command(text)
        if confirm_force is not None:
            return self._confirm_force_close(confirm_force)
        if is_force_cancel(text):
            return self._cancel_force_close()
        force_query = extract_force_close_command(text)
        if force_query:
            return self._do_force_close(force_query)

        running_query = extract_running_query(text)
        if running_query:
            return self._do_is_running(running_query)

        close_query = extract_close_command(text)
        if close_query is None and is_pronoun_close(text):
            close_query = "it"
        if close_query:
            return self._do_close_app(close_query)

        query = extract_app_command(text)
        if query:
            return self._do_open_app(query)

        if self.natural_handler is not None:
            try:
                if self.natural_handler(text):
                    return "assistant"
            except Exception:
                log.exception("Natural-language handler failed")

        log.info("Unrecognized command: %s", text)
        if announce_unknown:
            self.speech.say("Sorry, I didn't catch that command. Please repeat.")
        return "unknown"

    # -- built-in handlers -------------------------------------------------
    def commands_paused(self) -> bool:
        """True while command execution is paused (voice replies still work)."""
        state = getattr(self.app, "commands_paused", None)
        if callable(state):
            try:
                return bool(state())
            except Exception:
                return False
        if isinstance(state, bool):
            return state
        return False

    def _pending_action_kind(self) -> str:
        """The dangerous action (if any) currently waiting for confirmation."""
        pending = getattr(self.app, "pending", None)
        current = getattr(pending, "current", None)
        if not callable(current):
            return ""
        try:
            return str(current() or "")
        except Exception:
            return ""

    def _confirm_pending(self, kind: str) -> str:
        if kind == "ai_approval":
            return self._do_answer_ai_approval(True)
        if kind == "shutdown":
            return self._do_confirm_shutdown()
        if kind == "close_all":
            return self._do_confirm_close_all()
        return "none"

    def _cancel_pending(self, kind: str) -> str:
        if kind == "ai_approval":
            return self._do_answer_ai_approval(False)
        if kind == "shutdown":
            return self._do_cancel_shutdown()
        if kind == "close_all":
            return self._do_cancel_close_all()
        return "none"

    def _do_answer_ai_approval(self, approved: bool) -> str:
        answer = getattr(self.app, "answer_ai_approval", None)
        if not callable(answer):
            return "none"
        try:
            answered = bool(answer(approved))
        except Exception:
            log.exception("Could not deliver the AI approval answer")
            return "none"
        if answered:
            return "ai_approved" if approved else "ai_denied"
        return "none"

    def _invoke(self, action: str) -> str:
        mode_key = self._mode_actions.get(action)
        if mode_key is not None:
            return self._do_custom_mode(mode_key)
        handler = getattr(self, f"_do_{action}", None)
        if handler is None:
            log.error("Command %r has no handler", action)
            return "unknown"
        return handler()

    def _do_custom_mode(self, mode_key: str) -> str:
        ok = bool(self.app.activate_mode(mode_key))
        if ok:
            title = str(mode_key or "").replace("_", " ").strip().title()
            self.speech.say(f"{title or 'Custom'} mode activated, sir.")
        else:
            self.speech.say("I could not activate that mode, sir.")
        return "mode" if ok else "mode_failed"

    def _do_study(self) -> str:
        ok = bool(self.app.activate_mode("study"))
        self.speech.say(
            "Study mode activated, sir." if ok else "I could not activate study mode, sir."
        )
        return "study"

    def _do_entertainment(self) -> str:
        ok = bool(self.app.activate_mode("entertainment"))
        self.speech.say(
            "Entertainment mode activated, sir."
            if ok else "I could not activate entertainment mode, sir."
        )
        return "entertainment"

    def _do_stock(self) -> str:
        ok = bool(self.app.activate_mode("stock"))
        self.speech.say(
            "Stock mode activated, sir." if ok else "I could not activate stock mode, sir."
        )
        return "stock"

    def _do_mute(self) -> str:
        self.app.set_muted(True, announce=True)
        return "mute"

    def _do_commands_off(self) -> str:
        self.app.set_commands_paused(True)
        # Drop any half-finished "which application?" question so a name or
        # "first" spoken much later cannot run an old request.
        self._clear_pending()
        self._notify_grammar()
        self.speech.say(
            "I will stop taking commands, sir. Say take command when you "
            "want me to listen again."
        )
        return "commands_off"

    def _do_commands_on(self) -> str:
        self.app.set_commands_paused(False)
        self.speech.say("I am taking commands again, sir.")
        return "commands_on"

    def _do_assistant_cancel(self) -> str:
        """"Stop that"-style phrases, resolved by what is actually pending.

        A dangerous confirmation, an AI permission request, an open "which
        one?" question and a running AI request are all cancellable, so the
        phrase must never silently do nothing.
        """
        pending = self._pending_action_kind()
        if pending == "ai_approval":
            result = self._do_answer_ai_approval(False)
            assistant = getattr(self.app, "assistant", None)
            if assistant is not None:
                try:
                    assistant.cancel()
                except Exception:
                    log.exception("Could not cancel the AI request")
            self.speech.say("Stopped that request, sir.")
            return result if result != "none" else "assistant_cancel"
        if pending:
            # A close-all or shutdown countdown takes priority over the AI.
            return self._cancel_pending(pending)

        if self._pending_apps:
            self._clear_pending()
            self._notify_grammar()
            self.speech.say("Okay, I will not open anything, sir.")
            return "app_cancelled"

        assistant = getattr(self.app, "assistant", None)
        busy = False
        if assistant is not None:
            try:
                busy = bool(assistant.is_busy())
            except Exception:
                busy = False
        if not busy:
            # Nothing to cancel: stay silent instead of pretending.
            return "none"
        try:
            assistant.cancel()
        except Exception:
            log.exception("Could not cancel the AI request")
        self.speech.say("Stopped that request, sir.")
        return "assistant_cancel"

    def _do_unmute(self) -> str:
        self.app.set_muted(False, announce=True)
        self.speech.say("Voice announcements are enabled, sir.")
        return "unmute"

    def _do_wake_off(self) -> str:
        self.app.set_wake_enabled(False)
        self.speech.say("I have stopped listening for the wake word, sir.")
        return "wake_off"

    def _do_wake_on(self) -> str:
        self.app.set_wake_enabled(True)
        self.app.set_face_visible(True)
        self.speech.say("I am listening for the wake word again, sir.")
        return "wake_on"

    def _do_open_eyes(self) -> str:
        self.app.set_wake_enabled(True)
        self.app.set_face_visible(True)
        self.speech.say("I am awake, sir.")
        return "open_eyes"

    def _do_close_eyes(self) -> str:
        self.app.set_face_visible(False)
        self.speech.say("I will keep listening with my eyes closed, sir.")
        return "close_eyes"

    def _do_listen_now(self) -> str:
        self.app.listen_now()
        return "listen_now"

    def _do_settings(self) -> str:
        self.speech.say("Opening the control center, sir.")
        self.app.show_settings()
        return "settings"

    def _do_hide(self) -> str:
        setter = getattr(self.app, "set_all_faces_visible", None)
        if callable(setter):
            setter(False)
        else:
            self.app.set_face_visible(False)
        self.speech.say("Hiding myself, sir.")
        return "hide"

    def _do_hide_to_tray(self) -> str:
        self.speech.say("Hiding the control center, sir.")
        self.app.hide_to_tray()
        return "hide_to_tray"

    def _do_show(self) -> str:
        setter = getattr(self.app, "set_all_faces_visible", None)
        if callable(setter):
            setter(True)
        else:
            self.app.set_face_visible(True)
        self.speech.say("Here I am, sir.")
        return "show"

    def _do_show_control_center(self) -> str:
        self.app.set_face_visible(True)
        self.speech.say("Opening the control center, sir.")
        self.app.show_settings()
        return "show_control_center"

    def _do_exit(self) -> str:
        waiter = getattr(self.speech, "say_and_wait", None)
        try:
            if callable(waiter):
                waiter("Goodbye, sir.", timeout=3.0)
            else:
                self.speech.say("Goodbye, sir.")
        except Exception:
            log.debug("Could not speak the goodbye", exc_info=True)
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
            "I can activate your study, entertainment or stock mode, mute or unmute my voice, "
            "open my settings, hide or show my face, stop or start listening, pause or resume "
            "taking commands, run your custom modes, and tell you the time, sir."
        )
        return "help"

    def _do_introduce(self) -> str:
        self.speech.say(
            "Hello everyone. I am TALCON, your personal desktop assistant. "
            "I run entirely on this PC. I can open your study, entertainment and stock modes, "
            "launch websites and applications, tell you the time, play or pause media, "
            "close your open windows, and shut down your computer. "
            "Say TALCON followed by a command, and I will help."
        )
        return "introduce"

    def _do_thanks(self) -> str:
        self.speech.say("You are most welcome, sir.")
        return "thanks"

    # -- flexible intents --------------------------------------------------
    def _run_intent(self, intent: IntentMatch) -> str:
        """Map one natural-wording intent onto an existing handler."""
        action = intent.action
        if action == "list_running":
            return self._do_list_running()
        if action == "is_running":
            return self._do_is_running(intent.slot)
        if action == "set_volume":
            return self._do_set_volume(int(intent.slot or 90))
        if action == "nudge_volume_up":
            return self._do_nudge_volume(+15)
        if action == "nudge_volume_down":
            return self._do_nudge_volume(-15)
        if action == "nudge_rate_up":
            return self._do_nudge_rate(+1)
        if action == "nudge_rate_down":
            return self._do_nudge_rate(-1)
        if action == "reset_rate":
            return self._do_set_rate(0)
        if action == "silence":
            return self._do_silence()
        if action == "repeat":
            return self._do_repeat()
        if action == "wellbeing":
            self.speech.say(
                "I am running smoothly, sir. All systems nominal. "
                "What can I do for you?"
            )
            return "wellbeing"
        if action == "greeting":
            return self._do_greeting()
        if action == "introduce":
            return self._do_introduce()
        if action == "reopen":
            return self._do_reopen()
        log.info("Intent %r has no handler", action)
        return "none"

    def _voice_setting_int(self, key: str, default: int) -> int:
        settings = getattr(self.app, "settings", None) or {}
        try:
            return int(settings.get(key, default))
        except (TypeError, ValueError):
            return default

    def _save_voice_setting(self, key: str, value: int) -> None:
        saver = getattr(self.app, "save_settings", None)
        if callable(saver):
            try:
                saver({key: value})
                return
            except Exception:
                log.exception("Could not save %s", key)
        setter = getattr(
            self.speech, "set_volume" if key == "speech_volume" else "set_rate", None
        )
        if callable(setter):
            setter(value)

    def _do_set_volume(self, level: int) -> str:
        level = max(0, min(100, int(level)))
        self._save_voice_setting("speech_volume", level)
        if level == 0:
            return "volume_set"
        self.speech.say(f"Volume set to {level} percent, sir.")
        return "volume_set"

    def _do_nudge_volume(self, delta: int) -> str:
        current = self._voice_setting_int("speech_volume", 90)
        level = max(0, min(100, current + int(delta)))
        self._save_voice_setting("speech_volume", level)
        self.speech.say("Louder, sir." if delta > 0 else "Quieter, sir.")
        return "volume_up" if delta > 0 else "volume_down"

    def _do_set_rate(self, rate: int) -> str:
        rate = max(-10, min(10, int(rate)))
        self._save_voice_setting("speech_rate", rate)
        self.speech.say("Back to normal speed, sir.")
        return "rate_reset"

    def _do_nudge_rate(self, delta: int) -> str:
        current = self._voice_setting_int("speech_rate", 0)
        rate = max(-10, min(10, current + int(delta)))
        self._save_voice_setting("speech_rate", rate)
        self.speech.say("A little faster, sir." if delta > 0 else "A little slower, sir.")
        return "rate_up" if delta > 0 else "rate_down"

    def _do_silence(self) -> str:
        stopper = getattr(self.speech, "stop_speaking", None)
        if callable(stopper):
            stopper()
        else:
            clearer = getattr(self.speech, "clear", None)
            if callable(clearer):
                clearer()
        return "silenced"

    def _do_repeat(self) -> str:
        last = str(getattr(self.speech, "last_spoken", "") or "").strip()
        if not last:
            self.speech.say("I have not said anything yet, sir.")
            return "nothing_to_repeat"
        self.speech.say(last)
        return "repeated"

    def _do_greeting(self) -> str:
        hour = _dt.datetime.now().hour
        if hour < 12:
            line = "Good morning, sir."
        elif hour < 18:
            line = "Good afternoon, sir."
        else:
            line = "Good evening, sir."
        self.speech.say(f"{line} How can I help?")
        return "greeting"

    def _do_reopen(self) -> str:
        entry = getattr(self, "_last_opened", None)
        if entry is None:
            self.speech.say("I have not opened anything yet, sir.")
            return "nothing_to_reopen"
        return self._launch_catalogued(entry)

    def _do_media_toggle(self) -> str:
        ok, _message = self.app.media_toggle()
        return "media_toggle" if ok else "media_failed"

    # -- universal application launching ----------------------------------
    def _do_refresh_apps(self) -> str:
        refresher = getattr(self.app, "refresh_apps", None)
        if callable(refresher):
            refresher()
            self.speech.say("Refreshing the application list, sir.")
            return "apps_refreshing"
        if self.apps is None:
            self.speech.say("The application list is not available, sir.")
            return "app_unavailable"
        self.apps.refresh_async()
        self.speech.say("Refreshing the application list, sir.")
        return "apps_refreshing"

    def _launch_catalogued(self, entry) -> str:
        """Launch one trusted catalogue entry and speak the real outcome."""
        try:
            self.app_launcher(entry)
        except FileNotFoundError:
            log.warning("Catalogued application disappeared: %s", entry.path or entry.aumid)
            if self.apps is not None:
                try:
                    self.apps.invalidate(entry)
                except Exception:
                    log.debug("Could not invalidate %s", entry.name, exc_info=True)
                refresher = getattr(self.app, "refresh_apps", None)
                if callable(refresher):
                    try:
                        refresher(announce=False)
                    except Exception:
                        log.debug("Background refresh failed", exc_info=True)
                else:
                    self.apps.refresh_async()
            self.speech.say(
                f"{entry.name} is no longer installed on this PC, sir. "
                "I am refreshing the application list."
            )
            return "app_missing"
        except Exception as exc:
            log.exception("Could not launch application %s", entry.name)
            self.speech.say(
                f"I found {entry.name}, sir, but it could not be opened: {exc}"
            )
            return "app_failed"
        self._pending_apps = ()
        self._pending_query = ""
        self._pending_kind = ""
        self._notify_grammar()
        # Remember it: "close it" (and "close that app") refer back to this.
        self._last_opened = entry
        self.speech.say(f"Opening {entry.name}, sir.")
        return "app_opened"

    def _clear_pending(self) -> None:
        self._pending_apps = ()
        self._pending_query = ""
        self._pending_kind = ""

    def _notify_grammar(self) -> None:
        """Let the runtime rebuild the grammar after an ambiguity prompt."""
        refresh = getattr(self.app, "_refresh_grammar", None)
        if callable(refresh):
            try:
                refresh()
            except Exception:
                log.debug("Could not refresh the grammar", exc_info=True)

    def _do_open_app(self, query: str) -> str:
        if self.apps is None:
            self.speech.say("The application list is not available, sir.")
            return "app_unavailable"
        try:
            entries = self.apps.entries()
        except Exception:
            log.exception("Could not read the application catalogue")
            self.speech.say("I could not read the application list, sir.")
            return "app_unavailable"
        if not entries:
            # First use before discovery finished: one bounded refresh.
            try:
                entries = self.apps.refresh()
            except Exception:
                log.exception("Application discovery failed")
                entries = []
        result = resolve_application(query, entries, self.app_aliases)
        if result.status == "match" and result.entry is not None:
            log.info("Application match: %r -> %s (%s)", query, result.entry.name,
                     result.reason)
            return self._launch_catalogued(result.entry)
        if result.status == "ambiguous" and result.candidates:
            self._pending_apps = tuple(result.candidates)
            self._pending_query = result.query
            self._pending_kind = "open"
            self._notify_grammar()
            labels = self._candidate_labels(result.candidates)
            if len(labels) == 2:
                joined = f"{labels[0]} and {labels[1]}"
            else:
                joined = ", ".join(labels[:-1]) + f" and {labels[-1]}"
            log.info("Ambiguous application %r: %s", query, labels)
            self.speech.say(
                f"I found {len(result.candidates)} applications matching "
                f"{result.query or query}: {joined}. Which one should I open? "
                "You can reply first or second."
            )
            return "app_ambiguous"
        self._pending_apps = ()
        self._pending_query = ""
        self._pending_kind = ""
        self._notify_grammar()
        log.info("Application not found: %r (best %.2f)", query, result.score)
        if result.suggestions:
            names = list(result.suggestions[:3])
            # Arm the suggestion so "yes" (or "the first one") can open it.
            # Without this the prompt was a dead end: the user answered and
            # nothing happened because no pending state survived the question.
            suggested = self._entries_for_names(names, entries)
            if suggested:
                self._pending_apps = tuple(suggested)
                self._pending_query = result.query or normalize_phrase(query)
                self._pending_kind = "suggest"
                self._notify_grammar()
            joined = names[0] if len(names) == 1 else ", ".join(names[:-1]) + f" or {names[-1]}"
            question = "Is that the one you meant?" if len(names) == 1 else (
                "Should I open one of them? You can say first or second."
            )
            self.speech.say(
                f"I could not find an application called {result.query or query}, sir. "
                f"Did you mean {joined}? {question}"
            )
            return "app_not_found"
        self.speech.say(
            f"I could not find an application called {result.query or query}, sir."
        )
        return "app_not_found"

    @staticmethod
    def _entries_for_names(names, entries) -> list:
        """Resolve suggested names back to catalogue entries, in order."""
        wanted = [normalize_app_name(name) for name in names]
        found: list = []
        seen: set[str] = set()
        for entry in entries or ():
            key = normalize_app_name(entry.name)
            if key in wanted and key not in seen:
                found.append(entry)
                seen.add(key)
        # Preserve the suggestion order rather than catalogue order.
        ordered = []
        for name in wanted:
            for entry in found:
                if normalize_app_name(entry.name) == name:
                    ordered.append(entry)
                    break
        return ordered

    @staticmethod
    def _candidate_labels(entries) -> list[str]:
        """Distinguish same-named candidates in a spoken question."""
        names = [entry.name for entry in entries]
        duplicates = {name for name in names if names.count(name) > 1}
        labels: list[str] = []
        for entry in entries[:3]:
            if entry.name not in duplicates:
                labels.append(entry.name)
                continue
            if entry.app_mode:
                kind = f"{entry.browser.title()} app" if entry.browser else "web app"
            elif entry.kind in {"packaged_app", "start_app"}:
                kind = "installed app"
            elif entry.kind == "browser_app":
                kind = "web shortcut"
            else:
                kind = entry.source or "shortcut"
            labels.append(f"{entry.name} ({kind})")
        return labels

    def _serve_pending_app(self, text: str) -> str | None:
        """Answer a previous ambiguity or suggestion prompt.

        Accepts a bare "yes"/"no", an ordinal in any natural wrapper
        ("yes, the first one", "number two"), or the application's name.  The
        ordinal is looked for after filler words are removed, which is what
        previously made "yes, the first one" fall through to a fuzzy name
        match and open nothing.
        """
        if not self._pending_apps:
            return None
        words = normalize_phrase(text).split()
        if not words:
            return None

        affirm = any(word in _ANSWER_YES for word in words)
        deny = any(word in _ANSWER_NO for word in words)
        if deny and not affirm:
            pending_count = len(self._pending_apps)
            self._clear_pending()
            self._notify_grammar()
            self.speech.say(
                "Okay, I will not open anything, sir." if pending_count
                else "Okay, sir."
            )
            return "app_cancelled"

        index = None
        for word in words:
            if word in _ANSWER_ORDINALS:
                index = _ANSWER_ORDINALS[word]
                break
        if index is None:
            stripped = [w for w in words if w not in _ANSWER_FILLERS]
            for word in stripped:
                if word in _ANSWER_ORDINALS:
                    index = _ANSWER_ORDINALS[word]
                    break
        if index is None and affirm:
            # "yes" on its own accepts the best candidate, which is the one the
            # question named first.
            index = 0
        if index is not None and index < len(self._pending_apps):
            entry = self._pending_apps[index]
            if affirm or index is not None:
                log.info("Pending answer chose %s (index %d)", entry.name, index)
            return self._dispatch_pending(entry)

        result = resolve_application(
            text, self._pending_apps, self.app_aliases, threshold=0.9
        )
        if result.status == "match" and result.entry is not None:
            self._learn_alias(text, result.entry)
            return self._dispatch_pending(result.entry)
        return None

    def _learn_alias(self, spoken: str, entry) -> None:
        """Remember how the user just pronounced an ambiguous application.

        Only a successful, high-confidence answer to an ambiguity prompt is
        learned.  The spoken text is never executed, and writing the small
        alias map only affects future catalogue lookups.
        """
        phrase = normalize_phrase(spoken)
        if not phrase or len(phrase) > 40 or len(phrase.split()) > 6:
            return
        if phrase == normalize_app_name(entry.name):
            return
        self.app_aliases[phrase] = entry.name
        target = CONFIG_DIR / "app_aliases.json"
        try:
            data = {}
            if target.is_file():
                loaded = json.loads(target.read_text(encoding="utf-8"))
                if isinstance(loaded, dict):
                    data = loaded
            data[phrase] = entry.name
            temporary = target.with_suffix(".json.tmp")
            temporary.write_text(
                json.dumps(data, indent=2, ensure_ascii=False) + "\n",
                encoding="utf-8",
            )
            temporary.replace(target)
            log.info("Learned application pronunciation %r -> %s", phrase, entry.name)
        except (OSError, ValueError):
            log.warning("Could not save application pronunciation", exc_info=True)

    def _dispatch_pending(self, entry) -> str:
        if self._pending_kind == "close":
            return self._close_running_app(entry)
        return self._launch_catalogued(entry)

    # -- running applications ---------------------------------------------
    def _running_apps(self):
        getter = getattr(self.app, "running_applications", None)
        if not callable(getter):
            return None
        try:
            return list(getter())
        except Exception:
            log.exception("Could not enumerate running applications")
            return None

    def _do_list_running(self) -> str:
        apps = self._running_apps()
        if apps is None:
            log.info("Running-application discovery is unavailable")
            return "unknown"
        if not apps:
            self.speech.say("No user applications are currently running, sir.")
            return "running_empty"
        names = []
        for app in apps:
            if app.name not in names:
                names.append(app.name)
        shown = names[:8]
        joined = ", ".join(shown)
        if len(names) > len(shown):
            joined += f", and {len(names) - len(shown)} more"
        self.speech.say(f"{len(names)} applications are currently running, sir: {joined}.")
        return "running_list"

    def _do_is_running(self, query: str) -> str:
        apps = self._running_apps()
        if apps is None:
            log.info("Running-application discovery is unavailable")
            return "unknown"
        result = resolve_application(query, apps, self.app_aliases)
        if result.status == "match" and result.entry is not None:
            self.speech.say(f"Yes, {result.entry.name} is running, sir.")
            return "running_yes"
        if result.status == "ambiguous" and result.candidates:
            names = [entry.name for entry in result.candidates[:3]]
            joined = " and ".join(names) if len(names) == 2 else ", ".join(names)
            self.speech.say(f"Yes, {joined} are running, sir.")
            return "running_yes"
        name = self._installed_display_name(query) or (result.query or query)
        self.speech.say(f"No, {name} is not running, sir.")
        return "running_no"

    def _installed_display_name(self, query: str) -> str:
        if self.apps is None:
            return ""
        try:
            entries = self.apps.entries()
        except Exception:
            return ""
        result = resolve_application(query, entries, self.app_aliases)
        if result.status == "match" and result.entry is not None:
            return result.entry.name
        return ""

    def _do_close_app(self, query: str) -> str:
        pronoun = is_pronoun_query(normalize_phrase(query))
        if pronoun:
            # "close it" refers to whatever TALCON opened last.
            entry = getattr(self, "_last_opened", None)
            if entry is None:
                self.speech.say(
                    "Which application should I close, sir? Tell me its name."
                )
                return "close_needs_name"
            running = self._running_apps()
            if running is None:
                return "unknown"
            wanted = normalize_app_name(entry.name)
            match = next(
                (app for app in running
                 if normalize_app_name(app.name) == wanted), None
            )
            if match is None:
                # Tray applications (MechVibes, for example) hide their main
                # window, so they never show up in the visible-window list.
                outcome = self._close_hidden_windows(entry)
                if outcome:
                    return outcome
                self.speech.say(f"{entry.name} isn't running any more, sir.")
                return "app_not_running"
            log.info("Closing the app just opened: %s", entry.name)
            return self._close_running_app(match)
        apps = self._running_apps()
        if apps is None:
            log.info("Running-application discovery is unavailable")
            self.speech.say(
                "I cannot inspect the running applications right now, sir."
            )
            return "close_unavailable"
        result = resolve_application(query, apps, self.app_aliases)
        if result.status == "match" and result.entry is not None:
            return self._close_running_app(result.entry)
        if result.status == "ambiguous" and result.candidates:
            self._pending_apps = tuple(result.candidates)
            self._pending_query = result.query
            self._pending_kind = "close"
            self._notify_grammar()
            labels = self._candidate_labels(result.candidates)
            joined = " and ".join(labels) if len(labels) == 2 else ", ".join(labels)
            self.speech.say(
                f"I found more than one running application matching "
                f"{result.query or query}: {joined}. Which one should I close? "
                "You can reply first or second."
            )
            return "close_ambiguous"
        name = self._installed_display_name(query)
        if name:
            self.speech.say(f"{name} isn't currently running, sir.")
            return "app_not_running"
        self.speech.say(
            f"I couldn't identify {result.query or query} among the running "
            "applications, sir."
        )
        return "running_unknown"

    def _close_running_app(self, app) -> str:
        if is_protected_process(app.process):
            self.speech.say(
                f"{app.name} is a protected Windows or security process, sir. "
                "I will not close it."
            )
            return "app_protected"
        closer = getattr(self.app, "close_application", None)
        if not callable(closer):
            self.speech.say("Closing applications is not available, sir.")
            return "close_unavailable"
        try:
            result = closer(app)
        except Exception as exc:
            log.exception("Could not close %s", app.name)
            self.speech.say(f"I could not close {app.name}, sir: {exc}")
            return "app_close_failed"
        self._clear_pending()
        self._notify_grammar()
        if result is None:
            self.speech.say(f"I could not close {app.name}, sir.")
            return "app_close_failed"
        refused = list(getattr(result, "refused", []) or [])
        errors = list(getattr(result, "errors", []) or [])
        closed = list(getattr(result, "closed", []) or [])
        if errors and not closed:
            self.speech.say(
                f"I could not close {app.name}, sir. {errors[0]}"
            )
            return "app_close_failed"
        if refused:
            self.speech.say(
                f"{app.name} is still open, sir. It may be waiting for you to save "
                "your work or answer a confirmation prompt."
            )
            return "app_close_refused"
        if not closed:
            self.speech.say(f"{app.name} isn't currently running, sir.")
            return "app_not_running"
        self.speech.say(f"{app.name} has been closed, sir.")
        return "app_closed"

    # -- force close (explicit two-step confirmation) ----------------------
    def _close_hidden_windows(self, entry) -> str:
        """Close an app whose main window is hidden in the tray."""
        try:
            from .running_apps import close_application_by_name
            result = close_application_by_name(
                entry.name, getattr(entry, "path", "") or ""
            )
        except Exception:
            log.exception("Could not close %s by process name", entry.name)
            return ""
        if result.closed:
            log.info("Closed hidden-window application %s", entry.name)
            self.speech.say(f"Closing {entry.name}, sir.")
            return "app_closed"
        if result.refused:
            self.speech.say(f"{entry.name} refused to close, sir.")
            return "app_refused"
        return ""

    def _do_force_close(self, query: str) -> str:
        apps = self._running_apps()
        if apps is None:
            log.info("Running-application discovery is unavailable")
            return "unknown"
        result = resolve_application(query, apps, self.app_aliases)
        if result.status != "match" or result.entry is None:
            if result.status == "ambiguous" and result.candidates:
                labels = self._candidate_labels(result.candidates)
                joined = " and ".join(labels) if len(labels) == 2 else ", ".join(labels)
                self.speech.say(
                    f"More than one running application matches: {joined}. "
                    "Please say force close with the full application name."
                )
                return "force_close_ambiguous"
            name = self._installed_display_name(query)
            if name:
                self.speech.say(f"{name} isn't currently running, sir.")
                return "app_not_running"
            self.speech.say(
                f"I couldn't identify {result.query or query} among the running "
                "applications, sir."
            )
            return "running_unknown"
        app = result.entry
        if is_protected_process(app.process):
            self.speech.say(
                f"{app.name} is a protected Windows or security process, sir. "
                "I will not force it closed."
            )
            return "app_protected"
        self._force_pending = {
            "app": app,
            "name": app.name,
            "deadline": time.monotonic() + 20.0,
        }
        self.speech.say(
            f"Force closing {app.name} will discard any unsaved work. "
            f"Say confirm force close {app.name} to proceed, or cancel force close."
        )
        return "force_close_pending"

    def _confirm_force_close(self, query: str) -> str:
        pending = self._force_pending
        if not pending:
            self.speech.say("There is no force-close request waiting, sir.")
            return "force_close_none"
        if time.monotonic() > float(pending.get("deadline", 0.0)):
            self._force_pending = None
            self.speech.say("The force-close request expired, sir. Nothing was closed.")
            return "force_close_expired"
        expected = normalize_phrase(str(pending.get("name", "")))
        spoken = normalize_phrase(query)
        if spoken != expected:
            self._force_pending = None
            self.speech.say(
                f"That confirmation does not match {pending.get('name')}, sir. "
                "Nothing was force closed."
            )
            return "force_close_mismatch"
        app = pending.get("app")
        self._force_pending = None
        if app is None or is_protected_process(getattr(app, "process", "")):
            self.speech.say("I will not force close a protected process, sir.")
            return "app_protected"
        closer = getattr(self.app, "force_close_application", None)
        if not callable(closer):
            self.speech.say("Force closing is not available, sir.")
            return "force_close_unavailable"
        try:
            result = closer(app)
        except Exception as exc:
            log.exception("Force close failed for %s", app.name)
            self.speech.say(f"I could not force close {app.name}, sir: {exc}")
            return "force_close_failed"
        terminated = list(getattr(result, "terminated", []) or [])
        refused = list(getattr(result, "refused", []) or [])
        if terminated and not refused:
            self.speech.say(
                f"{app.name} was force closed, sir. Unsaved work may have been lost."
            )
            self._notify_grammar()
            return "app_force_closed"
        self.speech.say(
            f"I could not force close {app.name}, sir. It may still be running."
        )
        return "force_close_failed"

    def _cancel_force_close(self) -> str:
        if self._force_pending is None:
            self.speech.say("There is no force-close request to cancel, sir.")
            return "force_close_none"
        self._force_pending = None
        self.speech.say("Force close cancelled, sir. Nothing was closed.")
        return "force_close_cancelled"

    def _do_close_all(self) -> str:
        self.app.request_close_all()
        return "close_all_pending"

    def _do_confirm_close_all(self) -> str:
        ok, _message = self.app.confirm_close_all()
        return "close_all" if ok else "close_all_not_confirmed"

    def _do_cancel_close_all(self) -> str:
        ok, _message = self.app.cancel_close_all()
        return "close_all_cancelled" if ok else "close_all_nothing_pending"

    def _do_shutdown(self) -> str:
        self.app.request_shutdown()
        return "shutdown_pending"

    def _do_confirm_shutdown(self) -> str:
        ok, _message = self.app.confirm_shutdown()
        return "shutdown" if ok else "shutdown_failed"

    def _do_cancel_shutdown(self) -> str:
        ok, _message = self.app.cancel_shutdown()
        return "shutdown_cancelled" if ok else "shutdown_nothing_pending"
