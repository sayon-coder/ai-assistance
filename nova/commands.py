"""Voice/typed command parsing. Kept independent of audio so it can be tested."""
from __future__ import annotations

import datetime as _dt
import logging
import re

log = logging.getLogger("nova.commands")

_PUNCTUATION = re.compile(r"[^\w\s']+")
_WAKE_PREFIXES = ("hey nova ", "okay nova ", "ok nova ", "nova ", "no va ")


def normalize_phrase(phrase: str) -> str:
    text = str(phrase or "").lower().strip()
    text = _PUNCTUATION.sub(" ", text)
    text = " ".join(text.split())
    for prefix in _WAKE_PREFIXES:
        if text.startswith(prefix):
            text = text[len(prefix):].strip()
            break
    return text


class CommandRouter:
    def __init__(self, modes, speech, app):
        self.modes = modes
        self.speech = speech
        self.app = app

    def handle(self, phrase: str) -> str:
        text = normalize_phrase(phrase)
        log.info("Recognized command: %s", text or "(empty)")
        if not text:
            return "none"

        if "study mode" in text or text in {"study", "start study", "start studying", "activate study"}:
            self.app.activate_mode("study")
            return "study"

        if ("entertainment" in text or "entertainment mode" in text) or text in {
            "entertainment", "music mode", "start entertainment", "activate entertainment"
        }:
            self.app.activate_mode("entertainment")
            return "entertainment"

        if text in {"mute", "mute voice", "mute yourself", "be quiet", "silence", "stop speaking"}:
            self.app.set_muted(True)
            return "mute"

        if text in {"unmute", "unmute voice", "voice on", "speak again", "you can talk"}:
            self.app.set_muted(False)
            self.speech.say("Voice announcements are enabled, sir.")
            return "unmute"

        if any(phrase_text in text for phrase_text in ("what mode", "which mode", "current mode")):
            self.speech.say(f"Your current mode is {self.modes.current_mode}, sir.")
            return "status"

        if any(phrase_text in text for phrase_text in ("what time", "the time", "tell me the time")):
            now = _dt.datetime.now().strftime("%I:%M %p").lstrip("0")
            self.speech.say(f"It is {now}, sir.")
            return "time"

        if text in {"thank you", "thanks", "thank you nova"}:
            self.speech.say("You are most welcome, sir.")
            return "thanks"

        if text in {"help", "what can you do", "list commands", "your commands", "what can i say"}:
            self.speech.say(
                "I can activate your study or entertainment mode, mute or unmute my voice, "
                "open my settings, hide or show my face, stop or start listening, and tell you the time, sir."
            )
            return "help"

        if text in {"open settings", "open the settings", "settings", "configuration",
                    "open configuration", "open control center", "control center"}:
            self.app.show_settings()
            return "settings"

        if text in {"hide nova", "hide yourself", "hide to tray", "hide the face", "hide"}:
            self.app.hide_to_tray()
            return "hide"

        if text in {"show nova", "show yourself", "show the face", "show face"}:
            self.app.set_face_visible(True)
            self.app.show_settings()
            return "show"

        if text in {"stop listening", "stop the microphone", "turn off the microphone",
                    "stop listening for me"}:
            self.app.set_wake_enabled(False)
            self.speech.say("I have stopped listening for the wake word, sir.")
            return "wake_off"

        if text in {"start listening", "turn on the microphone", "listen for me", "wake up"}:
            self.app.set_wake_enabled(True)
            self.speech.say("I am listening for the wake word again, sir.")
            return "wake_on"

        if text in {"exit", "exit nova", "exit assistant", "close nova", "quit nova",
                    "shut down nova", "goodbye nova", "goodbye", "close"}:
            self.app.exit_completely()
            return "exit"

        log.info("Unrecognized command: %s", text)
        self.speech.say("I did not recognize that command, sir. Please try again.")
        return "unknown"
