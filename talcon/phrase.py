"""Bounded assembly of Vosk final segments into one command phrase.

Vosk can finalise a short segment before the user has finished speaking, so a
command such as "open my code editor" may arrive as two or three finals.  The
assembler keeps a small, time-limited buffer, joins recent segments and only
reports a phrase when it exactly matches a registered command.

Alternatives and their confidence scores are preserved across every segment,
not just the newest one.  The assembler never guesses: an incomplete prefix is
held only while it could still become a known command, and the primary
hypothesis wins ties when no confidence is available.
"""
from __future__ import annotations

import difflib
import time
from dataclasses import dataclass, field

from .commands import normalize_phrase

PHRASE_TIMEOUT = 4.0       # seconds a fragment may wait for its continuation
MAX_FRAGMENTS = 12         # bounded buffer, prevents runaway joins
MAX_WORDS = 24             # longer than any supported command phrase
MAX_CANDIDATES = 36        # beam width for cross-segment alternatives


@dataclass(frozen=True)
class Hypothesis:
    """One Vosk transcript plus its confidence, when the model supplies one."""

    text: str
    confidence: float | None = None

    def normalized(self) -> str:
        return normalize_phrase(self.text)


@dataclass
class Assembly:
    """Result of adding one final transcript to the assembler."""

    heard: str = ""
    matched: str | None = None
    candidates: list[str] = field(default_factory=list)
    pending: bool = False
    confidence: float | None = None
    scores: dict[str, float] = field(default_factory=dict)
    ambiguous: bool = False


def coerce_hypotheses(values) -> list[Hypothesis]:
    """Accept strings, ``(text, confidence)`` pairs or Vosk-shaped dicts."""
    if values is None:
        return []
    if isinstance(values, (str, Hypothesis, dict)):
        values = [values]
    result: list[Hypothesis] = []
    seen: dict[str, int] = {}
    for value in values:
        if isinstance(value, Hypothesis):
            text = value.text
            confidence = value.confidence
        elif isinstance(value, dict):
            text = str(value.get("text", "") or "")
            confidence = value.get("confidence", value.get("conf"))
        elif isinstance(value, (tuple, list)) and value:
            text = str(value[0] or "")
            confidence = value[1] if len(value) > 1 else None
        else:
            text = str(value or "")
            confidence = None
        normalized = normalize_phrase(text)
        if not normalized:
            continue
        try:
            score = float(confidence) if confidence is not None else None
        except (TypeError, ValueError):
            score = None
        if score is not None:
            score = max(0.0, min(1.0, score))
        if normalized in seen:
            index = seen[normalized]
            previous = result[index]
            if (score is not None
                    and (previous.confidence is None or score > previous.confidence)):
                result[index] = Hypothesis(text, score)
            continue
        seen[normalized] = len(result)
        result.append(Hypothesis(text, score))
    return result


class PhraseAssembler:
    def __init__(self, vocabulary=None, timeout: float = PHRASE_TIMEOUT,
                 max_fragments: int = MAX_FRAGMENTS, max_words: int = MAX_WORDS):
        self._vocabulary = vocabulary
        self.timeout = float(timeout)
        self.max_fragments = max(1, int(max_fragments))
        self.max_words = max(1, int(max_words))
        self._fragments: list[tuple[list[Hypothesis], float]] = []

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
        variants = coerce_hypotheses(texts)
        if not variants:
            return Assembly()

        self.expire(now)
        self._fragments.append((variants, now))
        if len(self._fragments) > self.max_fragments:
            del self._fragments[:-self.max_fragments]
        self._trim_words()

        ranked = self._ranked_candidates()
        candidates = [item[0] for item in ranked]
        # Report the whole buffered primary phrase, not the newest segment.
        heard = normalize_phrase(
            " ".join(fragment[0][0].normalized() for fragment in self._fragments)
        )
        scores = {text: score for text, score, _order in ranked}
        vocabulary = self._vocab()
        if vocabulary is None:
            # No registry available (for example a lightweight test router):
            # hand every candidate back so the caller can try exact matching.
            return Assembly(heard=heard, candidates=candidates, scores=scores)

        top_candidate = ranked[0][0] if ranked else ""
        for candidate, score, _order in ranked:
            if candidate in vocabulary:
                # A lower-ranked known phrase is only safe when it is nearly
                # identical to the best hypothesis.  Otherwise an inserted or
                # misheard word could silently turn "open up notepad" into the
                # unrelated built-in "open news".
                if (candidate != top_candidate
                        and difflib.SequenceMatcher(
                            None, top_candidate, candidate
                        ).ratio() < 0.90):
                    continue
                self.reset()
                return Assembly(
                    heard=heard, matched=candidate, candidates=[candidate],
                    confidence=None if score is None else score, scores=scores,
                )

        if any(self._is_prefix(candidate, vocabulary) for candidate in candidates):
            return Assembly(
                heard=heard, candidates=candidates, pending=True, scores=scores,
            )

        # Nothing in the buffer can become a known command.
        self.reset()
        return Assembly(heard=heard, candidates=candidates, scores=scores)

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
            total = sum(
                len(variants[0].normalized().split())
                for variants, _ in self._fragments
            )
            if total <= self.max_words:
                return
            self._fragments.pop(0)

    def _ranked_candidates(self) -> list[tuple[str, float | None, int]]:
        """Beam-join alternatives from every segment, best first."""
        beam: list[tuple[str, float | None, float, int, int]] = [
            ("", None, 0.0, 0, 0)
        ]
        for variants, _stamp in self._fragments:
            expanded: list[tuple[str, float | None, float, int, int]] = []
            for prefix, _average, total, count, order in beam:
                for variant_index, hypothesis in enumerate(variants):
                    text = normalize_phrase(
                        f"{prefix} {hypothesis.normalized()}".strip()
                    )
                    if not text:
                        continue
                    confidence = hypothesis.confidence
                    next_total = total + (confidence or 0.0)
                    next_count = count + (1 if confidence is not None else 0)
                    next_average = (
                        next_total / next_count if next_count else None
                    )
                    expanded.append((
                        text, next_average, next_total,
                        next_count, order * 10 + variant_index,
                    ))
            # Deduplicate the beam, keeping the best-scoring path for a text.
            best: dict[str, tuple[str, float | None, float, int, int]] = {}
            for text, average, total, count, order in expanded:
                existing = best.get(text)
                if existing is None or (
                    (average if average is not None else -1.0)
                    > (existing[1] if existing[1] is not None else -1.0)
                ):
                    best[text] = (text, average, total, count, order)
            beam = sorted(
                best.values(),
                key=lambda item: (
                    -(item[1] if item[1] is not None else 1.0), item[4]
                ),
            )[:MAX_CANDIDATES]
        ranked = sorted(
            ((text, average, order) for text, average, _total, _count, order in beam),
            key=lambda item: (-(item[1] if item[1] is not None else 1.0), item[2]),
        )
        return ranked
