"""Codex Desktop monitoring from its own local session logs (read-only).

Codex writes one rollout file per session under ``%USERPROFILE%\\\\.codex\\\\sessions``
(``CODEX_HOME`` is honoured). Inside, ``event_msg`` records carry the turn
lifecycle: ``task_started``, ``task_complete`` (with an ``error`` object when the
turn failed) and ``turn_aborted``. Newer builds compress these files with zstd,
so the tail decompresses appended frames incrementally.

NOVA only ever reads data appended to the newest session file *after* it starts
watching. It never starts its own app-server session, never treats a process
exit as completion, and never guesses: if the logs cannot be read, the UI says
so instead of announcing anything.
"""
from __future__ import annotations

import json
import logging
import os
import threading
import time
from collections import deque
from pathlib import Path

try:  # zstandard ships with the NOVA installer; without it only plain logs work.
    import zstandard as _zstandard
except Exception:  # pragma: no cover - depends on installation
    _zstandard = None

log = logging.getLogger("nova.codex")

SUCCESS_MESSAGE = "your coding task has been completed. You can review the changes now."
FAILURE_MESSAGE = "your coding task encountered an error. Please review the application."
INTERRUPTED_MESSAGE = "your coding task was interrupted."


def codex_home() -> Path:
    configured = os.environ.get("CODEX_HOME")
    if configured:
        return Path(configured).expanduser()
    return Path.home() / ".codex"


def sessions_root() -> Path:
    return codex_home() / "sessions"


def _is_rollout(path: Path) -> bool:
    name = path.name
    return name.startswith("rollout-") and (name.endswith(".jsonl") or name.endswith(".jsonl.zst"))


def newest_session_file(root: Path) -> Path | None:
    """Find the most recently modified rollout without walking the whole tree."""
    if not root.is_dir():
        return None
    candidates: list[Path] = []
    try:
        years = sorted((d for d in root.iterdir() if d.is_dir()), reverse=True)[:2]
    except OSError:
        return None
    for year in years:
        try:
            months = sorted((d for d in year.iterdir() if d.is_dir()), reverse=True)[:2]
        except OSError:
            continue
        for month in months:
            try:
                days = sorted((d for d in month.iterdir() if d.is_dir()), reverse=True)[:4]
            except OSError:
                continue
            for day in days:
                try:
                    files = [p for p in day.iterdir() if p.is_file() and _is_rollout(p)]
                except OSError:
                    continue
                if files:
                    candidates.extend(files)
            if candidates:
                break
        if candidates:
            break
    if not candidates:
        return None
    try:
        return max(candidates, key=lambda p: p.stat().st_mtime)
    except OSError:
        return None


class RolloutTail:
    """Incremental reader for a growing plain or zstd-compressed JSONL file."""

    def __init__(self, path: Path, skip_existing: bool = True):
        self.path = Path(path)
        self.compressed = self.path.name.endswith(".zst")
        self.error: str | None = None
        self._buffer = b""
        self._fh = self.path.open("rb")
        if self.compressed:
            if _zstandard is None:
                self._fh.close()
                raise RuntimeError("zstandard module is missing")
            self._context = _zstandard.ZstdDecompressor()
            self._stream = self._context.decompressobj()
        else:
            self._context = None
            self._stream = None
            self._fh.seek(0, os.SEEK_END)
        if skip_existing:
            self._consume(self._fh.read(), discard=True)

    def _decompress(self, chunk: bytes) -> bytes:
        if not self.compressed or self._stream is None:
            return chunk
        output = bytearray()
        data = chunk
        for _ in range(128):
            if not data:
                break
            try:
                output += self._stream.decompress(data)
            except Exception as exc:
                self.error = str(exc)
                log.warning("Codex zstd stream error: %s", exc)
                return bytes(output)
            if self._stream.eof:
                data = self._stream.unused_data
                self._stream = self._context.decompressobj()
            else:
                data = b""
        return bytes(output)

    def _consume(self, chunk: bytes, discard: bool) -> list[bytes]:
        if not chunk:
            return []
        self._buffer += self._decompress(chunk)
        parts = self._buffer.split(b"\n")
        self._buffer = parts.pop()
        if discard:
            return []
        return [line for line in parts if line.strip()]

    def read_new(self) -> list[bytes]:
        try:
            size = self.path.stat().st_size
            position = self._fh.tell()
            if size < position:  # file replaced / truncated
                self._fh.close()
                self._fh = self.path.open("rb")
                if self.compressed:
                    self._stream = self._context.decompressobj()
                self._buffer = b""
            chunk = self._fh.read()
        except OSError as exc:
            self.error = str(exc)
            return []
        return self._consume(chunk, discard=False)

    def close(self) -> None:
        try:
            self._fh.close()
        except Exception:
            pass


class RolloutTracker:
    """Pure parser for rollout JSONL records; easy to unit test."""

    def __init__(self):
        self._recent: deque = deque(maxlen=60)

    def feed(self, raw: bytes | str) -> tuple[str, str, str] | None:
        if isinstance(raw, bytes):
            try:
                raw = raw.decode("utf-8", "replace")
            except Exception:
                return None
        raw = raw.strip()
        if not raw:
            return None
        try:
            record = json.loads(raw)
        except Exception:
            return None
        if not isinstance(record, dict) or record.get("type") != "event_msg":
            return None
        payload = record.get("payload")
        if not isinstance(payload, dict):
            return None
        kind = payload.get("type")
        turn = str(payload.get("turn_id") or payload.get("root_turn_id") or "")

        result = None
        if kind == "task_started":
            result = ("working", "Codex is working on a task.", turn)
        elif kind == "task_complete":
            if payload.get("error"):
                result = ("failure", FAILURE_MESSAGE, turn)
            else:
                result = ("success", SUCCESS_MESSAGE, turn)
        elif kind == "turn_aborted":
            result = ("interrupted", INTERRUPTED_MESSAGE, turn)
        elif kind == "error":
            result = ("failure", FAILURE_MESSAGE, turn)
        if result is None:
            return None
        key = (result[0], turn, str(record.get("timestamp", "")))
        if key in self._recent:
            return None
        self._recent.append(key)
        return result


class CodexMonitor:
    POLL_SECONDS = 1.5

    def __init__(self, on_event, status_callback=None):
        self.on_event = on_event
        self.status_callback = status_callback or (lambda value: None)
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._tail: RolloutTail | None = None
        self.tracker = RolloutTracker()
        self._last_status = ""

    def _status(self, message: str) -> None:
        if message == self._last_status:
            return
        self._last_status = message
        try:
            self.status_callback(message)
        except Exception:
            log.exception("Codex status callback failed")

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, name="NOVA-Codex", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._tail is not None:
            self._tail.close()
            self._tail = None
        if self._thread and self._thread.is_alive() and self._thread is not threading.current_thread():
            self._thread.join(timeout=2.0)

    def _loop(self) -> None:
        root = sessions_root()
        if not root.is_dir():
            self._status(
                "Codex monitoring unavailable · no local .codex\\sessions folder found"
            )
            log.info("Codex sessions folder not found at %s", root)
            return
        if _zstandard is None:
            self._status(
                "Codex sessions are zstd-compressed · reinstall NOVA to enable monitoring"
            )
            log.warning("zstandard is not installed; compressed Codex logs cannot be read")
        self._status("Codex Desktop · waiting for a session log")
        while not self._stop.is_set():
            try:
                newest = newest_session_file(root)
                if newest is None:
                    self._status("Codex Desktop · no session log yet")
                else:
                    if self._tail is None or self._tail.path != newest:
                        if self._tail is not None:
                            self._tail.close()
                        self._tail = RolloutTail(newest, skip_existing=True)
                        log.info("Watching Codex session log %s", newest.name)
                        self._status("Codex Desktop connected · watching current session (read-only)")
                    for line in self._tail.read_new():
                        result = self.tracker.feed(line)
                        if result is not None:
                            kind, message, turn = result
                            log.info("Codex turn event: %s (%s)", kind, turn)
                            try:
                                self.on_event(kind, message, turn)
                            except Exception:
                                log.exception("Codex event callback failed")
            except RuntimeError as exc:
                self._status(f"Codex monitoring unavailable · {exc}")
                log.warning("Codex monitor stopped: %s", exc)
                return
            except Exception:
                log.exception("Codex monitor iteration failed")
                self._status("Codex monitoring error · retrying")
            self._stop.wait(self.POLL_SECONDS)
