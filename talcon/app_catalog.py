"""Resolve a spoken application name against the trusted Windows catalogue.

This module never launches anything and never builds a shell command.  It maps
a normalised query to one :class:`~talcon.apps.AppEntry` from the discovered
catalogue, or reports that the name is ambiguous / unknown.

Matching priority (as required by the voice interface):

1. exact display-name match (case-insensitive),
2. exact configured alias,
3. normalised name match (punctuation/whitespace-insensitive),
4. conservative fuzzy match above a confidence threshold.

Fuzzy results are only accepted when the best candidate is clearly ahead of the
next one.  Two plausible applications produce ``ambiguous`` instead of an
arbitrary launch; no match produces suggestions.
"""
from __future__ import annotations

import difflib
import json
import logging
import re
from dataclasses import dataclass
from pathlib import Path

from .apps import AppEntry
from .config import CONFIG_DIR

log = logging.getLogger("talcon.app_catalog")

MATCH_THRESHOLD = 0.82
AMBIGUITY_GAP = 0.06
MAX_SUGGESTIONS = 3
MAX_QUERY_WORDS = 8

#: Spoken forms that the small English model commonly produces.  These map an
#: alias to the canonical display name; the catalogue is still required to
#: contain that name, so nothing is hardcoded into the command registry.
DEFAULT_ALIASES = {
    "vs code": "visual studio code",
    "vscode": "visual studio code",
    "v s code": "visual studio code",
    "vs-code": "visual studio code",
    "code": "visual studio code",
    "edge": "microsoft edge",
    "ms edge": "microsoft edge",
    "microsoft edge browser": "microsoft edge",
    "chrome": "google chrome",
    "store": "microsoft store",
    "word": "microsoft word",
    "excel": "microsoft excel",
    "powerpoint": "microsoft powerpoint",
    "outlook": "microsoft outlook",
    "firefox": "mozilla firefox",
    "chat gpt": "chatgpt",
    "chat-gpt": "chatgpt",
    "open ai": "chatgpt",
    "whats app": "whatsapp",
    "what's app": "whatsapp",
    "whats": "whatsapp",
    "wats app": "whatsapp",
    "you tube": "youtube",
    "u tube": "youtube",
    # The small model frequently decodes "YouTube" as these two-word forms.
    "you do": "youtube",
    "you to": "youtube",
    "g mail": "gmail",
    "google mail": "gmail",
    "power point": "powerpoint",
    "excel sheet": "excel",
    "word doc": "word",
    "note pad": "notepad",
    "microsoft store": "microsoft store",
    "store": "microsoft store",
    "file explorer": "file explorer",
    "explorer": "file explorer",
    "task manager": "task manager",
    "command prompt": "command prompt",
    "cmd": "command prompt",
    "windows terminal": "terminal",
}

_PUNCTUATION = re.compile(r"[^\w\s]+")
_UNSAFE = re.compile(r"[\\/|&;<>$`\r\n]")

#: Words that carry no application identity in a spoken "open …" request.
_LEADING_FILLERS = {"the", "a", "an", "my", "our", "up"}
_TRAILING_FILLERS = {"app", "application", "program", "please"}
_APP_VERBS = ("open", "launch", "start", "run")
_CLOSE_VERBS = ("close", "exit", "quit")
_TRAILING_WINDOW_WORDS = {"window", "windows", "app", "application", "program"}


def normalize_app_name(text: str) -> str:
    """Lower-case, strip punctuation and collapse whitespace."""
    value = _PUNCTUATION.sub(" ", str(text or "").lower())
    value = value.replace("&", " and ")
    return " ".join(value.split())


def extract_app_command(text: str) -> str | None:
    """Return the application name from "open/launch/start/run …" or None.

    This is a pure parser.  It never executes text and the returned name is
    only ever resolved against the trusted application catalogue.
    """
    words = normalize_app_name(text).split()
    if not words or words[0] not in _APP_VERBS:
        return None
    rest = words[1:]
    while rest and rest[0] in _LEADING_FILLERS:
        rest.pop(0)
    while rest and rest[-1] in _TRAILING_FILLERS:
        rest.pop()
    query = " ".join(rest).strip()
    return query or None


def _strip_app_fillers(words: list[str]) -> list[str]:
    rest = list(words)
    while rest and rest[0] in _LEADING_FILLERS:
        rest.pop(0)
    while rest and rest[-1] in (_TRAILING_FILLERS | _TRAILING_WINDOW_WORDS):
        rest.pop()
    return rest


def extract_close_command(text: str) -> str | None:
    """Return the application name from "close/exit/quit …" or None."""
    words = normalize_app_name(text).split()
    if not words or words[0] not in _CLOSE_VERBS:
        return None
    rest = _strip_app_fillers(words[1:])
    # "close everything / close all windows" belong to the existing close-all
    # command, never to an individual running application.
    if any(word in {"everything", "all"} for word in rest):
        return None
    query = " ".join(rest).strip()
    return query or None


def extract_force_close_command(text: str) -> str | None:
    """Return the name in "force close <app>", or None."""
    words = normalize_app_name(text).split()
    if words[:2] != ["force", "close"]:
        return None
    query = " ".join(_strip_app_fillers(words[2:])).strip()
    return query or None


def extract_force_confirm_command(text: str) -> str | None:
    """Return the name in "confirm force close <app>", or None."""
    words = normalize_app_name(text).split()
    if words[:3] != ["confirm", "force", "close"]:
        return None
    query = " ".join(_strip_app_fillers(words[3:])).strip()
    return query or None


def is_force_cancel(text: str) -> bool:
    words = normalize_app_name(text).split()
    return words[:3] == ["cancel", "force", "close"]


def extract_running_query(text: str) -> str | None:
    """Return the name from "is <name> running/open?", or None."""
    words = normalize_app_name(text).split()
    if len(words) < 3 or words[0] != "is":
        return None
    if words[-1] not in {"running", "open"}:
        return None
    middle = _strip_app_fillers(words[1:-1])
    query = " ".join(middle).strip()
    return query or None


def is_safe_query(query: str) -> bool:
    """Reject anything that looks like a path or shell fragment.

    The query is only ever compared with catalogue names; this is an extra
    guard so a hostile transcript can never reach any shell layer even if a
    future launch path changes.
    """
    text = str(query or "")
    if not text.strip() or len(text) > 80:
        return False
    if _UNSAFE.search(text) or ".." in text:
        return False
    return len(text.split()) <= MAX_QUERY_WORDS


def load_aliases(path: Path | None = None) -> dict[str, str]:
    """Merge built-in speech aliases with the optional user alias file.

    ``config/app_aliases.json`` is a flat ``{"spoken name": "App name"}`` map.
    Reading it never writes settings or any other configuration file.
    """
    aliases = dict(DEFAULT_ALIASES)
    target = path if path is not None else CONFIG_DIR / "app_aliases.json"
    try:
        if not target.is_file():
            return aliases
        data = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        log.warning("Could not read application aliases from %s", target, exc_info=True)
        return aliases
    if not isinstance(data, dict):
        return aliases
    for key, value in data.items():
        spoken = normalize_app_name(key)
        canonical = str(value or "").strip()
        if spoken and canonical:
            aliases[spoken] = canonical
    return aliases


@dataclass(frozen=True)
class AppResolution:
    """Outcome of matching one spoken application name."""

    status: str  # "match" | "ambiguous" | "none"
    query: str
    entry: AppEntry | None = None
    score: float = 0.0
    candidates: tuple[AppEntry, ...] = ()
    suggestions: tuple[str, ...] = ()
    reason: str = ""

    @property
    def matched(self) -> bool:
        return self.status == "match" and self.entry is not None


def _unique(entries) -> list[AppEntry]:
    result: list[AppEntry] = []
    seen: set[tuple] = set()
    for entry in entries:
        if not (entry.path or entry.aumid):
            # Test/fixture entries without a launch target must never collapse
            # into each other; real catalogue entries always have one.
            result.append(entry)
            continue
        key = entry.key()
        if key in seen:
            continue
        seen.add(key)
        result.append(entry)
    return result


def _exact_name(query: str, entries) -> list[AppEntry]:
    lowered = str(query or "").strip().lower()
    return [entry for entry in entries if entry.name.strip().lower() == lowered]


def _normalized_name(query: str, entries) -> list[AppEntry]:
    wanted = normalize_app_name(query)
    return [entry for entry in entries if normalize_app_name(entry.name) == wanted]


def _alias_target(query: str, aliases: dict[str, str]) -> str:
    wanted = normalize_app_name(query)
    value = aliases.get(wanted)
    if value is None:
        # Allow punctuation-sparse aliases such as "what's app" to match after
        # normalisation even when the configured key used an apostrophe.
        for key, target in aliases.items():
            if normalize_app_name(key) == wanted:
                value = target
                break
    return normalize_app_name(value) if value else ""


def _fuzzy_score(query: str, name: str) -> float:
    score = difflib.SequenceMatcher(None, query, name).ratio()
    query_words = query.split()
    name_words = name.split()
    if query_words and name_words:
        if name == query:
            score = 1.0
        elif set(query_words) <= set(name_words):
            # Every spoken word must appear in the candidate name, so extra
            # transcript words ("chrome calc") can never match "Chrome".
            if name.startswith(query + " "):
                score = max(score, 0.92)
            else:
                score = max(score, 0.88)
    return min(1.0, score)


def resolve_application(
    query: str,
    entries,
    aliases: dict[str, str] | None = None,
    threshold: float = MATCH_THRESHOLD,
    ambiguity_gap: float = AMBIGUITY_GAP,
) -> AppResolution:
    """Resolve *query* to one catalogue entry, an ambiguity, or no match."""
    raw = str(query or "").strip()
    normalized = normalize_app_name(raw)
    aliases = aliases or {}
    pool = _unique([entry for entry in (entries or ()) if entry.name.strip()])
    if not raw or not pool:
        return AppResolution("none", normalized, reason="empty catalogue or query")
    if not is_safe_query(raw):
        return AppResolution("none", normalized, reason="unsafe application query")

    exact = _exact_name(raw, pool)
    if len(exact) == 1:
        return AppResolution("match", normalized, exact[0], 1.0, reason="exact name")
    if len(exact) > 1:
        return AppResolution(
            "ambiguous", normalized, candidates=tuple(exact),
            suggestions=tuple(item.name for item in exact[:MAX_SUGGESTIONS]),
            reason="several applications share that exact name",
        )

    target = _alias_target(raw, aliases)
    if target:
        by_target = [
            entry for entry in pool
            if normalize_app_name(entry.name) == target
        ]
        by_target = _unique(by_target)
        if len(by_target) == 1:
            return AppResolution(
                "match", normalized, by_target[0], 0.98, reason="configured alias"
            )
        if len(by_target) > 1:
            return AppResolution(
                "ambiguous", normalized, candidates=tuple(by_target),
                suggestions=tuple(item.name for item in by_target[:MAX_SUGGESTIONS]),
                reason="alias names several applications",
            )

    normalized_matches = _normalized_name(raw, pool)
    if len(normalized_matches) == 1:
        return AppResolution(
            "match", normalized, normalized_matches[0], 0.96, reason="normalised name"
        )
    if len(normalized_matches) > 1:
        return AppResolution(
            "ambiguous", normalized, candidates=tuple(normalized_matches),
            suggestions=tuple(item.name for item in normalized_matches[:MAX_SUGGESTIONS]),
            reason="several applications match that name",
        )

    scored = sorted(
        ((_fuzzy_score(normalized, normalize_app_name(entry.name)), entry)
         for entry in pool),
        key=lambda item: item[0],
        reverse=True,
    )
    best_score, best_entry = scored[0]
    if best_score < threshold:
        suggestions = tuple(
            entry.name for score, entry in scored[:MAX_SUGGESTIONS]
            if score >= 0.45
        )
        return AppResolution(
            "none", normalized, score=best_score, suggestions=suggestions,
            reason="no confident match",
        )
    close = [
        entry for score, entry in scored[1:]
        if score >= threshold and best_score - score < ambiguity_gap
    ]
    if close:
        # Preserve the best candidate first so a disambiguation prompt is stable.
        candidates = _unique([best_entry, *close])
        return AppResolution(
            "ambiguous", normalized, score=best_score,
            candidates=tuple(candidates),
            suggestions=tuple(item.name for item in candidates[:MAX_SUGGESTIONS]),
            reason="two or more applications are similarly close",
        )
    return AppResolution(
        "match", normalized, best_entry, best_score, reason="fuzzy name match"
    )
