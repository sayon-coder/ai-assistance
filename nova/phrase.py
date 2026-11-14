"""Bounded assembly of Vosk final segments into one command phrase.

Vosk can finalise a short segment before the user has finished speaking, so a
command such as "open my code editor" may arrive as two or three finals.  The
assembler keeps a small, time-limited buffer, joins recent segments and only
reports a phrase when it exactly matches a registered command.

It never guesses: an incomplete prefix is held only while the phrase could
still become a known command, and unrelated speech is discarded.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field

from .commands import normalize_phrase

PHRASE_TIMEOUT = 4.0       # seconds a fragment may wait for its continuation
MAX_FRAGMENTS = 12         # bounded buffer, prevents runaway joins
MAX_WORDS = 24             # longer than any supported command phrase


@dataclass
class Assembly:
    """Result of adding one final transcript to the assembler."""

    heard: str = ""
    matched: str | None = None
    candidates: list[str] = field(default_factory=list)
    pending: bool = False


class PhraseAssembler:
    def __init__(self, vocabulary=None, timeout: float = PHRASE_TIMEOUT,
                 max_fragments: int = MAX_FRAGMENTS, max_words: int = MAX_WORDS):
        self._vocabulary = vocabulary
        self.timeout = float(timeout)
        self.max_fragments = max(1, int(max_fragments))
        self.max_words = max(1, int(max_words))
        self._fragments: list[tuple[list[str], float]] = []

    # -- configuration -----------------------------------------------------
    def set_vocabulary(self, vocabulary) -> None:
        self._vocabulary = vocabulary

    def reset(self) -> None:
        self._fragments.clear()

    def pending(self) -> bool:
        return bool(self._fragments)

    # -- buffer ------------------------------------------------------------
    def expire(self, now: float | None = None) -> bool:
        """Drop stale fragments.  Incomplete phrases are never executed."""
        if not self._fragments:
            return False
        now = time.monotonic() if now is None else now
        if now - self._fragments[-1][1] >= self.timeout:
            self.reset()
            return True
        return False

    def add(self, texts, now: float | None = None) -> Assembly:
        now = time.monotonic() if now is None else now
        if isinstance(texts, str):
            texts = [texts]
        variants: list[str] = []
        for text in texts or ():
            normalized = normalize_phrase(text)
            if normalized and normalized not in variants:
                variants.append(normalized)
        if not variants:
            return Assembly()

        self.expire(now)
        self._fragments.append((variants, now))
        if len(self._fragments) > self.max_fragments:
            del self._fragments[:-self.max_fragments]
        self._trim_words()

        candidates = self._candidates()
        # Report the whole buffered phrase, not just the newest segment, so the
        # Control Center shows what was actually heard.
        heard = normalize_phrase(
            " ".join(fragment[0][0] for fragment in self._fragments)
        )
        vocabulary = self._vocab()
        if vocabulary is None:
            # No registry available (for example a lightweight test router):
            # hand every candidate back so the caller can try exact matching.
            return Assembly(heard=heard, candidates=candidates)

        for candidate in candidates:
            if candidate in vocabulary:
                self.reset()
                return Assembly(heard=heard, matched=candidate, candidates=[candidate])

        if any(self._is_prefix(candidate, vocabulary) for candidate in candidates):
            return Assembly(heard=heard, candidates=candidates, pending=True)

        # Nothing in the buffer can become a known command.
        self.reset()
        return Assembly(heard=heard, candidates=candidates)

    # -- internals ---------------------------------------------------------
    def _vocab(self) -> set[str] | None:
        source = self._vocabulary
        if source is None:
            return None
        try:
            values = source() if callable(source) else source
            return {normalize_phrase(value) for value in values if normalize_phrase(value)}
        except Exception:
            return None

    @staticmethod
    def _is_prefix(candidate: str, vocabulary: set[str]) -> bool:
        prefix = candidate + " "
        return any(known.startswith(prefix) for known in vocabulary)

    def _trim_words(self) -> None:
        while len(self._fragments) > 1:
            total = sum(len(variants[0].split()) for variants, _ in self._fragments)
            if total <= self.max_words:
                return
            self._fragments.pop(0)

    def _candidates(self) -> list[str]:
        candidates: list[str] = []
        fragments = self._fragments
        # Longest joins first, but within one join depth the primary hypothesis
        # comes before Vosk's alternatives.  This keeps multi-segment commands
        # working without ever preferring a weaker alternative over the primary.
        for take in range(len(fragments), 0, -1):
            start = len(fragments) - take
            head = " ".join(fragments[index][0][0] for index in range(start, len(fragments) - 1))
            for variant in fragments[-1][0]:
                candidate = normalize_phrase(f"{head} {variant}".strip())
                if candidate and candidate not in candidates:
                    candidates.append(candidate)
        return candidates
