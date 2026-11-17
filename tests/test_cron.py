"""Cron job scheduling, persistence, catch-up and silent execution."""
from __future__ import annotations

import datetime as dt
import json
import threading
import time

import pytest

from talcon.cron import (
    CronError, CronExecutor, CronScheduler, CronStore,
    next_occurrence, normalize_schedule, parse_natural_job, validate_job,
)


def local(hour=8, minute=0, day=15):
    now = dt.datetime.now().astimezone()
    return now.replace(year=2026, month=11, day=day, hour=hour, minute=minute,
                       second=0, microsecond=0)


def open_chrome():
    return {
        "name": "Open Chrome",
        "enabled": True,
        "action": {"type": "open_app", "name": "Google Chrome"},
        "schedule": {"kind": "daily", "time": "09:00"},
        "catch_up": "run_once",
    }


def test_daily_and_weekly_next_occurrence():
    now = local(8)
    daily = normalize_schedule({"kind": "daily", "time": "09:00"})
    assert next_occurrence(daily, now).hour == 9
    assert next_occurrence(daily, now).date() == now.date()
    after = local(10)
    assert next_occurrence(daily, after).date() == (now + dt.timedelta(days=1)).date()

    weekly = normalize_schedule({
        "kind": "weekly", "time": "10:30", "weekdays": [0],
    })
    monday = next_occurrence(weekly, now)
    assert monday.weekday() == 0
    assert (monday.hour, monday.minute) == (10, 30)


def test_one_time_and_interval_schedules():
    now = local(8)
    once = normalize_schedule({"kind": "once", "run_at": local(9).isoformat()})
    assert next_occurrence(once, now) == local(9)
    assert next_occurrence(once, local(10)) is None

    interval = normalize_schedule({
        "kind": "interval", "minutes": 90, "start_at": local(9).isoformat(),
    })
    assert next_occurrence(interval, now) == local(9)
    assert next_occurrence(interval, local(9)) == local(10, 30)


def test_natural_language_schedules_and_actions():
    now = local(8)
    parsed = parse_natural_job("Open Chrome every day at 9 AM.", now=now)
    assert parsed["action"] == {"type": "open_app", "name": "chrome"}
    assert parsed["schedule"] == {"kind": "daily", "time": "09:00"}

    weekly = parse_natural_job("Open Notepad every Monday at 10:30", now=now)
    assert weekly["schedule"]["kind"] == "weekly"
    assert weekly["schedule"]["weekdays"] == [0]

    weekdays = parse_natural_job("Open Chrome every weekday at 8:15 AM", now=now)
    assert weekdays["schedule"]["weekdays"] == [0, 1, 2, 3, 4]

    interval = parse_natural_job("Open Calculator every 2 hours", now=now)
    assert interval["schedule"]["kind"] == "interval"
    assert interval["schedule"]["minutes"] == 120

    once = parse_natural_job("Close Notepad tomorrow at 6 PM", now=now)
    assert once["action"]["type"] == "close_app"
    assert once["schedule"]["kind"] == "once"

    mode = parse_natural_job("Activate study mode every day at 7 AM", now=now)
    assert mode["action"] == {"type": "activate_mode", "mode": "study"}


def test_unsafe_or_unknown_job_text_is_rejected():
    with pytest.raises(CronError):
        parse_natural_job("Delete all files every day at 9 AM")
    with pytest.raises(CronError):
        parse_natural_job("Open Chrome")
    with pytest.raises(CronError):
        normalize_schedule({"kind": "weekly", "time": "09:00", "weekdays": []})


def test_store_persists_jobs_and_history(tmp_path):
    path = tmp_path / "cron_jobs.json"
    store = CronStore(path)
    job = store.create(open_chrome(), now=local(8))
    assert job["next_run"]
    occurrence = dt.datetime.fromisoformat(job["next_run"])
    claimed = store.begin_occurrence(job["id"], occurrence, None)
    assert claimed is not None
    store.finish_occurrence(job["id"], occurrence, True, "Opened Google Chrome")

    reopened = CronStore(path)
    saved = reopened.get(job["id"])
    assert saved is not None
    assert saved["last_status"] == "success"
    assert saved["last_detail"] == "Opened Google Chrome"
    assert saved["history"][0]["status"] == "success"
    # The same occurrence can never be claimed twice, even after reload.
    assert reopened.begin_occurrence(job["id"], occurrence, None) is None


def test_past_one_time_jobs_still_load_for_history(tmp_path):
    path = tmp_path / "cron_jobs.json"
    old = validate_job({
        "name": "Old one-time job",
        "enabled": False,
        "action": {"type": "open_app", "name": "Chrome"},
        "schedule": {"kind": "once", "run_at": local(9).isoformat()},
        "catch_up": "skip",
        "last_status": "success",
    }, now=local(12), allow_past=True)
    path.write_text(json.dumps({"version": 1, "jobs": [old]}), encoding="utf-8")
    store = CronStore(path)
    assert len(store.snapshot()) == 1
    assert store.snapshot()[0]["last_status"] == "success"


def test_pause_resume_and_due_queries(tmp_path):
    store = CronStore(tmp_path / "cron_jobs.json")
    job = store.create(open_chrome(), now=local(8))
    assert store.due(local(10))
    store.set_enabled(job["id"], False, now=local(8))
    assert store.due(local(10)) == []
    resumed = store.set_enabled(job["id"], True, now=local(10))
    assert resumed["enabled"] is True
    assert dt.datetime.fromisoformat(resumed["next_run"]) > local(10)


def test_resuming_a_past_one_time_job_is_refused_without_mutation(tmp_path):
    store = CronStore(tmp_path / "cron_jobs.json")
    job = store.create({
        "name": "Past reminder",
        "enabled": False,
        "action": {"type": "open_app", "name": "Notepad"},
        "schedule": {"kind": "once", "run_at": local(9).isoformat()},
        "catch_up": "run_once",
    }, now=local(8))
    with pytest.raises(CronError):
        store.set_enabled(job["id"], True, now=local(12))
    assert store.get(job["id"])["enabled"] is False


def test_one_time_job_completes_and_cannot_run_twice(tmp_path):
    store = CronStore(tmp_path / "cron_jobs.json")
    job = store.create({
        "name": "Reminder app",
        "enabled": True,
        "action": {"type": "open_app", "name": "Notepad"},
        "schedule": {"kind": "once", "run_at": local(9).isoformat()},
        "catch_up": "run_once",
    }, now=local(8))
    occurrence = dt.datetime.fromisoformat(job["next_run"])
    claimed = store.begin_occurrence(job["id"], occurrence, None)
    assert claimed is not None
    done = store.finish_occurrence(job["id"], occurrence, True, "Opened Notepad")
    assert done["enabled"] is False
    assert done["next_run"] == ""
    assert store.begin_occurrence(job["id"], occurrence, None) is None


class RecordingExecutor:
    def __init__(self, ok=True, detail="done", fail=False):
        self.calls = []
        self.ok = ok
        self.detail = detail
        self.fail = fail
        self.done = threading.Event()

    def execute(self, action):
        self.calls.append(action)
        if self.fail:
            raise RuntimeError("simulated failure")
        self.done.set()
        return self.ok, self.detail


def make_scheduler(tmp_path, executor, now):
    store = CronStore(tmp_path / "cron_jobs.json")
    scheduler = CronScheduler(
        object(), store=store, executor=executor, clock=lambda: now,
        poll_seconds=0.5,
    )
    return store, scheduler


def test_scheduler_runs_due_job_once_and_logs_result(tmp_path):
    now = local(9, 0)
    executor = RecordingExecutor(detail="Opened Chrome")
    store, scheduler = make_scheduler(tmp_path, executor, now)
    job = store.create(open_chrome(), now=local(8))
    scheduler.tick(now=now)
    assert executor.done.wait(2)
    assert len(executor.calls) == 1
    time.sleep(0.1)
    saved = store.get(job["id"])
    assert saved["last_status"] == "success"
    assert saved["last_detail"] == "Opened Chrome"
    # A second tick for the same occurrence must not run it again.
    scheduler.tick(now=now)
    time.sleep(0.2)
    assert len(executor.calls) == 1


def test_catch_up_runs_once_after_missed_time(tmp_path):
    now = local(12)
    executor = RecordingExecutor(detail="caught up")
    store, scheduler = make_scheduler(tmp_path, executor, now)
    job = store.create(open_chrome(), now=local(8))
    store._jobs[0]["next_run"] = local(9).isoformat()
    store._save_locked()
    scheduler.tick(now=now)
    assert executor.done.wait(2)
    assert len(executor.calls) == 1
    saved = store.get(job["id"])
    assert saved["last_status"] == "success"
    assert dt.datetime.fromisoformat(saved["next_run"]) > now


def test_skip_policy_records_missed_without_execution(tmp_path):
    now = local(12)
    executor = RecordingExecutor()
    store, scheduler = make_scheduler(tmp_path, executor, now)
    job = store.create({**open_chrome(), "catch_up": "skip"}, now=local(8))
    store._jobs[0]["next_run"] = local(9).isoformat()
    store._save_locked()
    scheduler.tick(now=now)
    assert not executor.done.is_set()
    saved = store.get(job["id"])
    assert saved["last_status"] == "missed"
    assert "skip" in saved["last_detail"].lower()
    assert saved["next_run"]


def test_failure_is_recorded_silently(tmp_path):
    now = local(9)
    executor = RecordingExecutor(fail=True)
    store, scheduler = make_scheduler(tmp_path, executor, now)
    job = store.create(open_chrome(), now=local(8))
    scheduler.tick(now=now)
    deadline = time.time() + 2
    while time.time() < deadline:
        saved = store.get(job["id"])
        if saved["last_status"] == "failure":
            break
        time.sleep(0.05)
    saved = store.get(job["id"])
    assert saved["last_status"] == "failure"
    assert "simulated failure" in saved["last_detail"]


class FakeSpeech:
    def __init__(self):
        self.calls = []

    def say(self, *args, **kwargs):
        self.calls.append((args, kwargs))


class FakeRuntime:
    def __init__(self, running=None):
        self.speech = FakeSpeech()
        self.running = list(running or [])
        self.closed = []

    def running_applications(self):
        return list(self.running)

    def close_application(self, app):
        self.closed.append(app.name)

        class Result:
            closed = [object()]
            errors = []
        return Result()


def test_executor_open_app_is_silent():
    launched = []
    runtime = FakeRuntime()
    executor = CronExecutor(runtime, app_launcher=lambda entry: launched.append(entry))
    ok, detail = executor.execute({
        "type": "open_app",
        "app": {"name": "Chrome", "path": "C:/Chrome.exe", "kind": "executable"},
    })
    assert ok is True
    assert launched
    assert runtime.speech.calls == []


def test_executor_mode_activation_is_silent():
    opened = []
    runtime = FakeRuntime()
    runtime.modes_data = {
        "study": {"title": "Study Mode", "urls": ["https://example.com"], "apps": []},
    }
    executor = CronExecutor(runtime, url_opener=lambda url: opened.append(url))
    ok, detail = executor.execute({"type": "activate_mode", "mode": "study"})
    assert ok is True
    assert opened == ["https://example.com"]
    assert runtime.speech.calls == []


def test_executor_close_app_is_graceful_and_silent():
    runtime = FakeRuntime(running=[
        type("App", (), {
            "name": "Notepad", "path": "", "aumid": "", "kind": "running_app",
        })(),
    ])
    executor = CronExecutor(runtime)
    ok, detail = executor.execute({"type": "close_app", "name": "Notepad"})
    assert ok is True
    assert runtime.closed == ["Notepad"]
    assert runtime.speech.calls == []
