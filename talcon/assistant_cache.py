"""Bounded, RAM-only response cache for the TALCON AI assistant.

Only *self-contained, informational* answers are cached: the same question,
asked again in an equivalent conversation context, with the same model, gets
the same stored answer back without a second Hermes request.  Everything that
touches the machine (tools, files, apps, live data) is deliberately excluded,
so a cached answer can never be mistaken for a freshly executed operation.

Design notes
------------
* Pure standard library.  No SQLite, no Redis, no vector store, no new
  dependency: an ``OrderedDict`` plus ``time.monotonic`` is all that is used.
* Nothing is ever written to disk.  The cache lives in the process and is
  cleared on complete shutdown (hiding to the tray keeps it).
* Only complete final responses are stored (see :func:`is_cacheable`), never
  streamed deltas, reasoning text, tool arguments or execution logs.
* Tool-result caching is intentionally *not* implemented: TALCON only observes
  Hermes' tool events and never issues the tool call itself, so it has no
  invalidation hook for file or system state.  Caching observations without a
  reliable invalidation signal would risk serving stale data, which the brief
  forbids, so the response cache stands alone.
"""
from __future__ import annotations

import hashlib
import logging
import re
import time
from collections import OrderedDict

log = logging.getLogger("talcon.cache")

#: Bounded by default: 100 answers is a few hundred kilobytes at most.
DEFAULT_MAX_ENTRIES = 100
#: Ten minutes: long enough to catch repeated questions, short enough to age out.
DEFAULT_TTL_SECONDS = 600.0
#: A very long answer is unlikely to be repeated verbatim; do not store it.
DEFAULT_MAX_RESPONSE_CHARS = 4000

#: Words that make a request depend on the previous turn.  Such requests are
#: keyed by the exact conversation they belong to (and never answered from a
#: cache entry produced in a different conversation).
_ANAPHORA = frozenset({
    "it", "its", "it's", "that", "this", "these", "those", "they", "them",
    "their", "he", "she", "him", "her", "his", "there", "same", "again",
    "previous", "above", "former", "latter", "instead", "also",
})
_CONTEXT_LEADERS = ("and ", "but ", "so ", "then ", "what about", "how about",
                    "ok and", "okay and", "now ")

#: Live or state-dependent information must never be answered from cache.
_LIVE_INFORMATION = frozenset({
    "price", "prices", "pricing", "cost", "costs", "stock", "stocks", "share",
    "shares", "market", "ticker", "news", "headline", "headlines", "weather",
    "forecast", "temperature", "rain", "raining", "snow", "humidity",
    "wind", "score", "scores", "result", "results", "standings", "election",
    "today", "tonight", "tomorrow", "yesterday", "now", "currently",
    "current", "latest", "recent", "recently", "live", "right", "upcoming",
    "this week", "this month", "this year", "time", "date", "clock",
    "exchange", "rate", "rates", "bitcoin", "crypto", "ethereum", "traffic",
    "flight", "flights", "availability", "in stock", "balance", "inbox",
    "unread", "down", "online", "offline", "status", "battery", "memory",
    "disk", "cpu", "ram", "usage", "running", "installed", "version",
})

#: Anything that runs, changes, opens or observes the machine.
_SIDE_EFFECTS = frozenset({
    "open", "launch", "start", "close", "quit", "kill", "terminate", "stop",
    "delete", "remove", "erase", "uninstall", "install", "download", "upload",
    "save", "write", "create", "make", "rename", "move", "copy", "paste",
    "edit", "modify", "change", "update", "upgrade", "set", "enable",
    "disable", "turn", "toggle", "switch", "reset", "clear", "clean", "empty",
    "format", "run", "execute", "exec", "command", "terminal", "shell",
    "script", "browse", "browser", "click", "type", "scroll", "navigate",
    "send", "email", "message", "post", "tweet", "schedule", "remind",
    "book", "order", "buy", "purchase", "pay", "transfer", "cancel",
    "shutdown", "reboot", "restart", "sleep", "lock", "logout", "sign",
    "print", "screenshot", "record", "play", "pause", "mute", "unmute",
})

#: Content that must never be stored in a shared cache entry.
_SENSITIVE_PATTERNS = (
    re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+"),                    # e-mail
    re.compile(r"\b(?:sk|pk|ghp|gho|xox[baprs])[-_][A-Za-z0-9_-]{12,}"),  # keys
    re.compile(r"\bBearer\s+[A-Za-z0-9._-]{12,}", re.I),
    re.compile(r"\bpassword\b|\bpasscode\b|\bapi[ _-]?key\b", re.I),
    re.compile(r"[A-Za-z]:\\Users\\[^\\\s]+", re.I),            # home paths
    re.compile(r"\+?\d[\d\s().-]{8,}\d"),                       # phone numbers
)


#: Formatting marks stripped from a spoken answer (see ``to_speech_text``).
_LINK = re.compile(r"\[([^\]]+)\]\([^)]*\)")
_CODE = re.compile(r"`{1,3}([^`]*)`{1,3}")
_BOLD = re.compile(r"(\*\*|__)(.*?)\1")
_ITALIC = re.compile(r"(^|\s)[*_]([^*_\n]+)[*_]")
_HEADING = re.compile(r"^\s{0,3}#{1,6}\s*", re.M)
_QUOTE = re.compile(r"^\s{0,3}>\s?", re.M)
_BULLET = re.compile(r"^\s*[-*\u2022]\s+", re.M)
_NUMBERED = re.compile(r"^\s*\d+[.)]\s+", re.M)
_SPACES = re.compile(r"[ \t]+")
_PARA_AFTER_PUNCT = re.compile(r"(?<=[.!?:;])\n{2,}")
_BLANK_LINES = re.compile(r"\n{2,}")
_NEWLINE = re.compile(r"\s*\n\s*")
_SPACE_PUNCT = re.compile(r"\s+([,.;:!?])")
_DOT_RUN = re.compile(r"(?:\s*\.\s*){2,}")


def to_speech_text(text: str) -> str:
    """Strip formatting that sounds wrong when read aloud.

    The assistant is voice-first: bold markers, back-ticks, bullets and
    markdown links would otherwise be spoken as characters.  The cleaned text
    is what gets cached, spoken, previewed and stored in History, so all four
    always agree.
    """
    text = str(text or "")
    text = _LINK.sub(r"\1", text)
    text = _CODE.sub(r"\1", text)
    text = _BOLD.sub(r"\2", text)
    text = _ITALIC.sub(r"\2", text)
    text = _HEADING.sub("", text)
    text = _QUOTE.sub("", text)
    text = _BULLET.sub("", text)
    text = _NUMBERED.sub("", text)
    text = text.replace("|", ", ")
    text = _SPACES.sub(" ", text)
    text = _PARA_AFTER_PUNCT.sub(" ", text)
    text = _BLANK_LINES.sub(". ", text)
    text = _NEWLINE.sub(" ", text)
    text = _SPACE_PUNCT.sub(r"\1", text)
    text = _DOT_RUN.sub(". ", text)
    return text.strip()


def normalize_request(text: str) -> str:
    """Collapse whitespace and case so trivial phrasing differences match."""
    return " ".join(str(text or "").lower().split())


def context_fingerprint(turns) -> str:
    """Stable hash of the conversation turns that precede a request."""
    digest = hashlib.sha256()
    for turn in turns or ():
        role = str((turn or {}).get("role") or "")
        content = normalize_request((turn or {}).get("content") or "")
        if not content:
            continue
        digest.update(role.encode("utf-8", "replace"))
        digest.update(b"\x1f")
        digest.update(content.encode("utf-8", "replace"))
        digest.update(b"\x1e")
    return digest.hexdigest()[:16]


def is_context_dependent(request: str) -> bool:
    """True when a request only makes sense as a follow-up to earlier turns."""
    words = normalize_request(request).replace("?", " ").split()
    if not words:
        return True
    lowered = " " + normalize_request(request) + " "
    if any(lowered.startswith(leader) for leader in _CONTEXT_LEADERS):
        return True
    return any(word.strip(".,!?") in _ANAPHORA for word in words)


def _contains_sensitive(text: str) -> bool:
    return any(pattern.search(text or "") for pattern in _SENSITIVE_PATTERNS)


def is_cacheable_request(request: str) -> bool:
    """True when the *question itself* is informational and self-contained."""
    text = normalize_request(request)
    if len(text) < 8 or len(text) > 400:
        return False
    if _contains_sensitive(text):
        return False
    words = re.findall(r"[a-z']+", text)
    if not words:
        return False
    # "open x", "what is the price of y" and similar are never cache candidates.
    if words[0] in _SIDE_EFFECTS:
        return False
    return not any(word in _LIVE_INFORMATION for word in words)


def is_cacheable_response(text: str) -> bool:
    """True when a completed answer is safe to store and reuse verbatim."""
    text = str(text or "").strip()
    if len(text) < 2 or len(text) > DEFAULT_MAX_RESPONSE_CHARS:
        return False
    if _contains_sensitive(text):
        return False
    # An answer that talks about having done something must never be replayed.
    lowered = text.lower()
    if re.search(r"\b(i(?:\s+have|'ve)\s+(?:opened|closed|deleted|created|"
                 r"moved|renamed|installed|uninstalled|saved|sent|run|"
                 r"executed|started|stopped|changed|updated|set|killed|"
                 r"launched|downloaded|uploaded|removed|terminated))\b",
                 lowered):
        return False
    return True


def is_cacheable(request: str, response: str, tools_used: bool = False) -> bool:
    """The single gate the assistant asks before storing an answer."""
    if tools_used:
        return False
    return is_cacheable_request(request) and is_cacheable_response(response)


class ResponseCache:
    """Thread-safe LRU + TTL cache of complete, informational answers."""

    def __init__(self, max_entries: int = DEFAULT_MAX_ENTRIES,
                 ttl_seconds: float = DEFAULT_TTL_SECONDS,
                 max_response_chars: int = DEFAULT_MAX_RESPONSE_CHARS,
                 clock=time.monotonic):
        self.max_entries = max(1, int(max_entries))
        self.ttl_seconds = max(1.0, float(ttl_seconds))
        self.max_response_chars = max(64, int(max_response_chars))
        self._clock = clock
        self._entries: OrderedDict[str, tuple[str, float, str]] = OrderedDict()
        self._hits = 0
        self._misses = 0
        self._expirations = 0
        self._evictions = 0
        self._stores = 0
        self._bypasses = 0

    # -- configuration -----------------------------------------------------
    def configure(self, max_entries: int | None = None,
                  ttl_seconds: float | None = None,
                  max_response_chars: int | None = None) -> None:
        """Apply new bounds, evicting the oldest entries if the cap shrinks."""
        if max_entries is not None:
            self.max_entries = max(1, int(max_entries))
        if ttl_seconds is not None:
            self.ttl_seconds = max(1.0, float(ttl_seconds))
        if max_response_chars is not None:
            self.max_response_chars = max(64, int(max_response_chars))
        self._trim()

    # -- keys --------------------------------------------------------------
    @staticmethod
    def make_key(request: str, fingerprint: str, model: str,
                 options: str = "") -> str:
        """A key that can never collide across context, model or options."""
        basis = "\x1f".join((
            normalize_request(request),
            str(fingerprint or ""),
            str(model or ""),
            str(options or ""),
        ))
        return hashlib.sha256(basis.encode("utf-8", "replace")).hexdigest()[:32]

    # -- operations --------------------------------------------------------
    def get(self, key: str, fingerprint: str = "") -> str | None:
        entry = self._entries.get(key)
        if entry is None:
            self._misses += 1
            return None
        value, expires_at, stored_fingerprint = entry
        if self._clock() >= expires_at:
            self._entries.pop(key, None)
            self._expirations += 1
            self._misses += 1
            return None
        if fingerprint and stored_fingerprint != fingerprint:
            # Defensive: a context mismatch must never serve an answer.  The
            # entry stays for the caller whose context it belongs to.
            self._misses += 1
            return None
        # Refresh LRU position only; the TTL keeps counting from the store time.
        self._entries.move_to_end(key)
        self._hits += 1
        return value

    def put(self, key: str, value: str, fingerprint: str = "",
            ttl_seconds: float | None = None) -> bool:
        text = str(value or "").strip()
        if not text or len(text) > self.max_response_chars:
            self._bypasses += 1
            return False
        ttl = self.ttl_seconds if ttl_seconds is None else max(1.0, float(ttl_seconds))
        self._entries[key] = (text, self._clock() + ttl, str(fingerprint or ""))
        self._entries.move_to_end(key)
        self._stores += 1
        self._trim()
        return True

    def note_bypass(self) -> None:
        """Record a request that was sent upstream because it is not cacheable."""
        self._bypasses += 1

    def clear(self) -> None:
        self._entries.clear()

    def _trim(self) -> None:
        while len(self._entries) > self.max_entries:
            self._entries.popitem(last=False)
            self._evictions += 1

    def prune(self) -> int:
        """Drop expired entries; returns how many were removed."""
        now = self._clock()
        stale = [key for key, (_v, expires, _f) in self._entries.items()
                 if now >= expires]
        for key in stale:
            self._entries.pop(key, None)
            self._expirations += 1
        return len(stale)

    # -- introspection -----------------------------------------------------
    def __len__(self) -> int:
        return len(self._entries)

    def stats(self) -> dict:
        lookups = self._hits + self._misses
        return {
            "entries": len(self._entries),
            "max_entries": self.max_entries,
            "ttl_seconds": self.ttl_seconds,
            "hits": self._hits,
            "misses": self._misses,
            "expirations": self._expirations,
            "evictions": self._evictions,
            "stores": self._stores,
            "bypasses": self._bypasses,
            "hit_rate": (self._hits / lookups) if lookups else 0.0,
            "lookups": lookups,
        }

    def reset_stats(self) -> None:
        self._hits = self._misses = self._expirations = 0
        self._evictions = self._stores = self._bypasses = 0

    def __repr__(self) -> str:
        return (f"ResponseCache(entries={len(self._entries)}/{self.max_entries}, "
                f"ttl={self.ttl_seconds:.0f}s, hits={self._hits}, "
                f"misses={self._misses})")
