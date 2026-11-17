"""DeepAI Code task monitoring from its own local state database (read-only).

DeepAI Code (the T3-based desktop app) records every orchestration run in
``%USERPROFILE%\\.deepai-code\\userdata\\statev2.sqlite``.  A run row moves
through ``queued`` -> ``running`` -> ``completed`` / ``failed`` /
``cancelled`` (or ``interrupted``).  TALCON watches those rows directly:

* the database is opened read-only and only three columns plus the thread
  title are queried;
* nothing is inferred from a closed window or a stopped process;
* only runs that start after TALCON begins watching are announced, so the
  existing history is never replayed;
* every connection is local (a SQLite file), no HTTP, no auth token, no cloud.
"""
from __future__ import annotations

import logging
import os
import sqlite3
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

log = logging.getLogger("talcon.deepai")

SUCCESS_MESSAGE = (
    "your DeepAI Code task has been completed. You can review the changes now."
)
FAILURE_MESSAGE = (
    "your DeepAI Code task encountered an error. Please review the application."
)
INTERRUPTED_MESSAGE = "your DeepAI Code task was interrupted."
WORKING_MESSAGE = "DeepAI Code is working on a task."

RUNNING_STATES = {"queued", "pending", "starting", "running", "in_progress"}
SUCCESS_STATES = {"completed", "succeeded", "success", "done"}
FAILURE_STATES = {"failed", "error"}
INTERRUPTED_STATES = {"cancelled", "canceled", "interrupted", "stopped", "aborted"}

MAX_RUNS = 40
POLL_SECONDS = 2.0


def deepai_home() -> Path:
    configured = os.environ.get("DEEPAI_CODE_HOME")
    if configured:
        return Path(configured).expanduser()
    return Path.home() / ".deepai-code"


def state_db_path() -> Path:
    return deepai_home() / "userdata" / "statev2.sqlite"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace(
        "+00:00", "Z"
    )


def _fetch_runs(db_path: Path) -> list[dict]:
    """Read the newest runs without ever writing to DeepAI Code's database."""
    path = Path(db_path)
    if not path.is_file():
        raise FileNotFoundError(str(path))
    connection = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=1.5)
    connection.row_factory = sqlite3.Row
    try:
        rows = connection.execute(
            """
            SELECT r.run_id, r.thread_id, r.status, r.requested_at,
                   r.completed_at, COALESCE(t.title, '') AS title
            FROM orchestration_v2_projection_runs AS r
            LEFT JOIN orchestration_v2_projection_threads AS t
                   ON t.thread_id = r.thread_id
            ORDER BY r.requested_at DESC
            LIMIT ?
            """,
            (MAX_RUNS,),
        ).fetchall()
    finally:
        connection.close()
    return [dict(row) for row in rows]


class DeepAIRunTracker:
    """Pure run-state machine; no database or threads, so it is easy to test."""

    def __init__(self, started_at: str | None = None):
        self.started_at = started_at or _now_iso()
        self._status: dict[str, str] = {}
        self._working: set[str] = set()
        self._primed = False

    def prime(self, runs) -> None:
        """Remember existing runs so their history is not announced."""
        for run in runs or ():
            run_id = str(run.get("run_id") or "")
            if run_id:
                self._status[run_id] = str(run.get("status") or "").lower()
        self._primed = True

    def _working_once(self, run_id: str) -> bool:
        if run_id in self._working:
            return False
        self._working.add(run_id)
        return True

    def poll(self, runs) -> list[tuple[str, str, str]]:
        if not self._primed:
            self.prime(runs)
            return []
        events: list[tuple[str, str, str]] = []
        current: dict[str, str] = {}
        for run in runs or ():
            run_id = str(run.get("run_id") or "")
            if not run_id:
                continue
            status = str(run.get("status") or "").lower()
            requested = str(run.get("requested_at") or "")
            current[run_id] = status
            previous = self._status.get(run_id)
            is_new = previous is None
            if is_new and requested and requested < self.started_at:
                # Pre-existing history: record it without announcing.
                continue
            if is_new and not requested:
                continue
            if status == previous:
                continue
            if status in RUNNING_STATES:
                if self._working_once(run_id):
                    events.append(("working", WORKING_MESSAGE, run_id))
            elif status in SUCCESS_STATES:
                events.append(("success", SUCCESS_MESSAGE, run_id))
            elif status in FAILURE_STATES:
                events.append(("failure", FAILURE_MESSAGE, run_id))
            elif status in INTERRUPTED_STATES:
                events.append(("interrupted", INTERRUPTED_MESSAGE, run_id))
        # Keep the map bounded to runs we just read.
        self._status = current
        if len(self._working) > MAX_RUNS * 2:
            self._working.clear()
        return events


class DeepAICodeMonitor:
    """Read-only poller for DeepAI Code's local orchestration database."""

    def __init__(self, on_event, status_callback=None, db_path=None,
                 poll_seconds: float = POLL_SECONDS):
        self.on_event = on_event
        self.status_callback = status_callback or (lambda value: None)
        self.db_path = Path(db_path) if db_path is not None else state_db_path()
        self.poll_seconds = max(1.0, float(poll_seconds))
        self.tracker = DeepAIRunTracker()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._last_status = ""

    def _status(self, message: str) -> None:
        if message == self._last_status:
            return
        self._last_status = message
        try:
            self.status_callback(message)
        except Exception:
            log.exception("DeepAI Code status callback failed")

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._loop, name="TALCON-DeepAI", daemon=True
        )
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread and self._thread.is_alive() and self._thread is not threading.current_thread():
            self._thread.join(timeout=2.0)

    def _loop(self) -> None:
        announced_ready = False
        while not self._stop.is_set():
            try:
                runs = _fetch_runs(self.db_path)
            except FileNotFoundError:
                self._status(
                    "DeepAI Code not found · no local statev2.sqlite yet"
                )
                self._stop.wait(self.poll_seconds * 3)
                continue
            except sqlite3.Error as exc:
                self._status("DeepAI Code database busy · retrying")
                log.debug("DeepAI Code database read failed: %s", exc)
                self._stop.wait(self.poll_seconds)
                continue
            except Exception:
                log.exception("DeepAI Code database read failed")
                self._status("DeepAI Code monitoring error · retrying")
                self._stop.wait(self.poll_seconds)
                continue

            if not announced_ready:
                announced_ready = True
                self._status("DeepAI Code connected · watching task events (local)")
                log.info("DeepAI Code monitor watching %s", self.db_path)
            for kind, message, run_id in self.tracker.poll(runs):
                log.info("DeepAI Code %s (%s)", kind, run_id)
                try:
                    self.on_event(kind, message, run_id)
                except Exception:
                    log.exception("DeepAI Code event callback failed")
            self._stop.wait(self.poll_seconds)
