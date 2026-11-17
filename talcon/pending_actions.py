"""Explicit, single-use confirmation state for dangerous voice actions.

A dangerous request only arms a pending action.  Nothing runs until the exact
confirmation phrase is recognised, the pending action expires after a short
window, and a pending action can never be confirmed twice.
"""
from __future__ import annotations

import threading
import time

DEFAULT_TIMEOUT = 15.0


class PendingActions:
    """Tracks at most one dangerous action awaiting spoken confirmation."""

    def __init__(self, clock=time.monotonic, timeout: float = DEFAULT_TIMEOUT):
        self.clock = clock
        self.timeout = max(1.0, float(timeout))
        self._lock = threading.RLock()
        self._kind: str | None = None
        self._expires_at: float = 0.0

    def request(self, kind: str, timeout: float | None = None) -> bool:
        kind = str(kind or "").strip()
        if not kind:
            return False
        seconds = self.timeout if timeout is None else max(1.0, float(timeout))
        with self._lock:
            self._kind = kind
            self._expires_at = self.clock() + seconds
        return True

    def current(self) -> str | None:
        with self._lock:
            if self._kind is None:
                return None
            if self.clock() >= self._expires_at:
                self._kind = None
                self._expires_at = 0.0
                return None
            return self._kind

    def remaining(self) -> float:
        with self._lock:
            if self.current() is None:
                return 0.0
            return max(0.0, self._expires_at - self.clock())

    def confirm(self, kind: str) -> bool:
        """Consume the pending action only when it matches and has not expired."""
        with self._lock:
            if self.current() != str(kind or "").strip():
                return False
            self._kind = None
            self._expires_at = 0.0
            return True

    def cancel(self, kind: str | None = None) -> bool:
        with self._lock:
            if self._kind is None:
                return False
            if kind is not None and self.current() != str(kind).strip():
                return False
            self._kind = None
            self._expires_at = 0.0
            return True

    def clear(self) -> None:
        with self._lock:
            self._kind = None
            self._expires_at = 0.0
