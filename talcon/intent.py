"""Deterministic intent patterns for flexible, natural command wording.

The exact-phrase registry in :mod:`talcon.commands` is deliberately strict: a
transcript either matches a known alias or nothing happens.  That is the right
rule for safety, but it makes the assistant sound deaf to natural variations
("how many programs do I have open right now?").

This module adds a small, fully deterministic intent layer on top of it.  Each
intent is a regular expression with optional filler words and named slots.  It
never executes anything by itself: it only answers "which known action did the
user mean, and what name/number did they say?" so the router can call an
existing, tested handler.  There is still no language model and no fuzzy
matching, and a malformed or hostile transcript can at worst fail to match.
"""
from __future__ import annotations

import re
from typing import NamedTuple


class IntentMatch(NamedTuple):
    """One action plus an optional slot (an application name, volume, …)."""

    action: str
    slot: str = ""
    label: str = ""


#: Digits and the number words people actually say for a volume level.
_NUMBER_WORDS = {
    "zero": 0, "oh": 0,
    "one": 1, "two": 2, "three": 3, "four": 4, "five": 5,
    "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10,
    "eleven": 11, "twelve": 12, "thirteen": 13, "fourteen": 14,
    "fifteen": 15, "sixteen": 16, "seventeen": 17, "eighteen": 18,
    "nineteen": 19, "twenty": 20, "thirty": 30, "forty": 40,
    "fifty": 50, "sixty": 60, "seventy": 70, "eighty": 80,
    "ninety": 90, "hundred": 100,
}


def parse_level(value: str) -> int | None:
    """Parse "40", "forty", "forty five", "max" or "full" into 0–100."""
    text = str(value or "").strip().lower().replace("-", " ")
    text = " ".join(text.split())
    if not text:
        return None
    if text in {"max", "maximum", "full", "loudest", "top"}:
        return 100
    if text in {"min", "minimum", "lowest", "bottom"}:
        return 0
    if text.isdigit():
        return max(0, min(100, int(text)))
    words = text.split()
    if not words or any(word not in _NUMBER_WORDS for word in words):
        return None
    # "forty five" is 45, "one hundred" is 100, "ninety nine" is 99.
    total = 0
    for word in words:
        value = _NUMBER_WORDS[word]
        total = total + value if value == 100 else total + value
    return max(0, min(100, total))


def _rx(pattern: str) -> re.Pattern:
    return re.compile(pattern)


#: ``(action, compiled pattern, human label)`` checked in order.
_RULES: tuple[tuple[str, re.Pattern, str], ...] = (
    # -- questions about running applications -----------------------------
    (
        "list_running",
        _rx(
            r"^(?:so |and )?(?:(?:tell|show|give) me )?"
            r"(?:how many|number of) "
            r"(?:apps?|applications?|programs?|windows?)"
            r"(?: do i have| have i got| are there)?"
            r"(?: are| is| do i have)?(?: currently| right now)?"
            r" (?:running|open|opened|up|active|on screen)"
            r"(?: right now| now| at the moment| currently)?$"
        ),
        "how many applications are running",
    ),
    (
        "list_running",
        _rx(
            r"^(?:what|which)(?:'s| is| are)? "
            r"(?:(?:apps?|applications?|programs?|windows?) )?"
            r"(?:are |is )?(?:currently |right now )?"
            r"(?:running|open|opened|up|active|on screen)"
            r"(?: right now| now| at the moment| currently)?$"
        ),
        "what is running",
    ),
    (
        "list_running",
        _rx(
            r"^(?:do i have|have i got|are there)(?: any)? "
            r"(?:apps?|applications?|programs?|windows?) "
            r"(?:running|open|opened|up|active)(?: right now| now)?$"
        ),
        "are there any applications open",
    ),
    (
        "list_running",
        _rx(
            r"^(?:show|tell|give)(?: me)? (?:the )?"
            r"(?:list of )?(?:running|open|opened) "
            r"(?:apps?|applications?|programs?|windows?)$"
        ),
        "show the running applications",
    ),
    # -- is one specific application running ------------------------------
    (
        "is_running",
        _rx(
            r"^(?:is|are) (?P<app>.+?) "
            r"(?:currently )?(?:running|open|opened|up|active|on screen)"
            r"(?: right now| now| at the moment)?$"
        ),
        "is <app> running",
    ),
    (
        "is_running",
        _rx(
            r"^(?:do i have|have i got) (?P<app>.+?) "
            r"(?:running|open|opened|up|active)(?: right now| now)?$"
        ),
        "do i have <app> open",
    ),
    # -- repeat the last answer -------------------------------------------
    (
        "repeat",
        _rx(
            r"^(?:repeat(?: that| it)?(?: again)?|say (?:that|it) again|"
            r"what did you (?:just )?say|come again|pardon|"
            r"i (?:did not|didn't) (?:catch|hear) that)$"
        ),
        "say that again",
    ),
    # -- silence the current utterance without muting ---------------------
    (
        "silence",
        _rx(
            r"^(?:stop talking|stop speaking now|be quiet|be quiet now|"
            r"quiet|quiet now|quiet down|silence|shush|shut up|"
            r"enough talking|that is enough|that's enough)$"
        ),
        "be quiet for now",
    ),
    # -- voice volume ------------------------------------------------------
    (
        "set_volume",
        _rx(
            r"^(?:set|change|turn|put) (?:the |your )?(?:volume|sound|speaker) "
            r"(?:to |at )?(?P<level>\d{1,3}|[a-z]+(?: [a-z]+)?)"
            r"(?: percent| per cent| out of a hundred)?$"
        ),
        "set the volume to <level>",
    ),
    (
        "set_volume",
        _rx(r"^(?:volume|sound) (?:to |at )?(?P<level>\d{1,3})$"),
        "volume <level>",
    ),
    (
        "nudge_volume_up",
        _rx(
            r"^(?:turn |make |put |bring )?(?:it|the volume|the sound|"
            r"your voice|the speaker) (?:up|louder)$"
            r"|^(?:volume|sound) up$"
            r"|^(?:louder|speak up|speak louder|talk louder)$"
            r"|^(?:increase|raise|boost) (?:the |your )?"
            r"(?:volume|voice|sound)$"
        ),
        "turn the volume up",
    ),
    (
        "nudge_volume_down",
        _rx(
            r"^(?:turn |make |put |bring )?(?:it|the volume|the sound|"
            r"your voice|the speaker) (?:down|quieter|softer)$"
            r"|^(?:volume|sound) down$"
            r"|^(?:quieter|softer|lower your voice|speak softer|"
            r"speak quietly|talk quieter)$"
            r"|^(?:lower|reduce|decrease) (?:the |your )?"
            r"(?:volume|voice|sound)$"
        ),
        "turn the volume down",
    ),
    # -- speaking speed ----------------------------------------------------
    (
        "nudge_rate_down",
        _rx(
            r"^(?:speak|talk) (?:a (?:bit|little) )?slower$"
            r"|^slow down(?: a (?:bit|little))?$"
            r"|^slower$"
        ),
        "speak slower",
    ),
    (
        "nudge_rate_up",
        _rx(
            r"^(?:speak|talk) (?:a (?:bit|little) )?(?:faster|quicker)$"
            r"|^speed up(?: a (?:bit|little))?$"
            r"|^faster$"
        ),
        "speak faster",
    ),
    (
        "reset_rate",
        _rx(r"^(?:speak|talk) (?:normally|at (?:a )?normal speed|normally again)$"),
        "return to normal speed",
    ),
    # -- courtesy and small talk ------------------------------------------
    (
        "wellbeing",
        _rx(
            r"^(?:hey |so )?(?:how are you(?: doing| today)?|"
            r"how(?:'s| is) it going|how are things|"
            r"you (?:okay|alright|good)|how do you feel)(?: today| sir)?$"
        ),
        "how are you",
    ),
    (
        "greeting",
        _rx(
            r"^(?:good morning|good afternoon|good evening|good night)"
            r"(?: talcon| talcon)?$"
        ),
        "good morning",
    ),
    (
        "introduce",
        _rx(
            r"^(?:what(?:'s| is) your name|what are you called|"
            r"who am i talking to)$"
        ),
        "what is your name",
    ),
    # -- follow-up context -------------------------------------------------
    (
        "reopen",
        _rx(
            r"^(?:open|launch|start|reopen) "
            r"(?:it|that|the app|the application|that app)(?: again| back)?$"
        ),
        "open that again",
    ),
)


def match_intent(text: str) -> IntentMatch | None:
    """Return the first deterministic intent matching ``text``, else ``None``.

    ``text`` must already be normalised by
    :func:`talcon.commands.normalize_phrase`; the router does that once.
    """
    phrase = str(text or "").strip()
    if not phrase:
        return None
    for action, pattern, label in _RULES:
        found = pattern.fullmatch(phrase)
        if not found:
            continue
        slot = ""
        try:
            group = found.groupdict()
        except AttributeError:
            group = {}
        if group.get("app"):
            slot = str(group["app"]).strip()
        elif group.get("level"):
            level = parse_level(group["level"])
            if level is None:
                continue
            slot = str(level)
        return IntentMatch(action, slot, label)
    return None


def intent_labels() -> list[str]:
    """Every human-readable intent wording, for the Help page and tests."""
    return [label for _action, _pattern, label in _RULES]
