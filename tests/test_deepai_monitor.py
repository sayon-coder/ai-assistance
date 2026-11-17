import sqlite3

import pytest

from talcon.monitors import deepai


def _run(run_id, status, requested, title="Task"):
    return {
        "run_id": run_id,
        "thread_id": "thread-1",
        "status": status,
        "requested_at": requested,
        "completed_at": "",
        "title": title,
    }


def test_existing_history_is_not_replayed():
    tracker = deepai.DeepAIRunTracker("2026-11-15T09:00:00Z")
    tracker.prime([
        _run("old", "completed", "2026-11-15T08:00:00Z"),
        _run("active", "running", "2026-11-15T08:30:00Z"),
    ])
    assert tracker.poll([_run("old", "completed", "2026-11-15T08:00:00Z")]) == []


def test_pre_existing_running_task_announces_completion():
    tracker = deepai.DeepAIRunTracker("2026-11-15T09:00:00Z")
    tracker.prime([_run("active", "running", "2026-11-15T08:30:00Z")])
    events = tracker.poll([_run("active", "completed", "2026-11-15T08:30:00Z")])
    assert events == [("success", deepai.SUCCESS_MESSAGE, "active")]


def test_new_task_working_once_then_success():
    tracker = deepai.DeepAIRunTracker("2026-11-15T09:00:00Z")
    tracker.prime([])
    running = [_run("new", "running", "2026-11-15T09:01:00Z")]
    first = tracker.poll(running)
    assert [event[0] for event in first] == ["working"]
    assert tracker.poll(running) == []
    done = [_run("new", "completed", "2026-11-15T09:01:00Z")]
    assert [event[0] for event in tracker.poll(done)] == ["success"]


def test_failure_and_interruption_are_distinct():
    tracker = deepai.DeepAIRunTracker("2026-11-15T09:00:00Z")
    tracker.prime([])
    runs = [_run("bad", "failed", "2026-11-15T09:01:00Z"),
            _run("stop", "cancelled", "2026-11-15T09:02:00Z")]
    events = tracker.poll(runs)
    assert ("failure", deepai.FAILURE_MESSAGE, "bad") in events
    assert ("interrupted", deepai.INTERRUPTED_MESSAGE, "stop") in events


def test_terminal_run_created_after_start_is_announced():
    tracker = deepai.DeepAIRunTracker("2026-11-15T09:00:00Z")
    tracker.prime([])
    events = tracker.poll([_run("fast", "completed", "2026-11-15T09:00:30Z")])
    assert [event[0] for event in events] == ["success"]


def test_fetch_runs_reads_local_state_database(tmp_path):
    db = tmp_path / "statev2.sqlite"
    connection = sqlite3.connect(db)
    connection.executescript(
        """
        CREATE TABLE orchestration_v2_projection_threads (
            thread_id TEXT PRIMARY KEY, title TEXT
        );
        CREATE TABLE orchestration_v2_projection_runs (
            run_id TEXT PRIMARY KEY, thread_id TEXT, status TEXT,
            requested_at TEXT, completed_at TEXT
        );
        """
    )
    connection.execute(
        "INSERT INTO orchestration_v2_projection_threads VALUES ('t1', 'talcon')"
    )
    connection.execute(
        "INSERT INTO orchestration_v2_projection_runs VALUES "
        "('r1', 't1', 'completed', '2026-11-15T09:00:00Z', '2026-11-15T09:01:00Z')"
    )
    connection.commit()
    connection.close()

    runs = deepai._fetch_runs(db)
    assert runs[0]["run_id"] == "r1"
    assert runs[0]["title"] == "talcon"


def test_fetch_runs_missing_database_raises(tmp_path):
    with pytest.raises(FileNotFoundError):
        deepai._fetch_runs(tmp_path / "missing.sqlite")
