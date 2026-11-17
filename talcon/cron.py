"""Persistent, local-only scheduled jobs for TALCON.

The scheduler is deliberately small: a daemon thread wakes a few times a
minute, looks at jobs stored in ``config/cron_jobs.json`` and runs only the
actions the user explicitly authorised when creating the job.

Safety properties
-----------------
* An action is a small structured object (open app / close app / activate
  mode).  Job text is never passed to PowerShell, ``cmd`` or a shell.
* Applications are launched through the existing catalogue descriptors.
* Closing uses the existing graceful ``WM_CLOSE`` path; force termination is
  never available to a job.
* Execution is silent: this module never calls speech, notifications or the
  UI.  Results are written to the job's history and shown in the dashboard.
* ``last_occurrence`` is persisted *before* running, so a restart cannot run
  the same occurrence twice.
"""
from __future__ import annotations

import copy
import logging
import re
import threading
import uuid
from datetime import date, datetime, time as dtime, timedelta
from pathlib import Path

from .app_catalog import resolve_application
from .apps import launch_app, normalize_app_entry
from .config import CONFIG_DIR, write_json
from .modes import ModeManager

log = logging.getLogger("talcon.cron")

CRON_JOBS_PATH = CONFIG_DIR / "cron_jobs.json"
SCHEMA_VERSION = 1
HISTORY_LIMIT = 25
MISSED_GRACE_SECONDS = 300

ACTION_TYPES = ("open_app", "close_app", "activate_mode")
SCHEDULE_KINDS = ("once", "daily", "weekly", "interval")
CATCH_UP_POLICIES = ("run_once", "skip")

WEEKDAYS = (
    ("mon", "Monday"), ("tue", "Tuesday"), ("wed", "Wednesday"),
    ("thu", "Thursday"), ("fri", "Friday"), ("sat", "Saturday"),
    ("sun", "Sunday"),
)
_WEEKDAY_LOOKUP = {
    "mon": 0, "monday": 0, "tue": 1, "tues": 1, "tuesday": 1,
    "wed": 2, "weds": 2, "wednesday": 2, "thu": 3, "thur": 3,
    "thurs": 3, "thursday": 3, "fri": 4, "friday": 4,
    "sat": 5, "saturday": 5, "sun": 6, "sunday": 6,
}
_MONTHS = {
    "january": 1, "jan": 1, "february": 2, "feb": 2, "march": 3,
    "mar": 3, "april": 4, "apr": 4, "may": 5, "june": 6, "jun": 6,
    "july": 7, "jul": 7, "august": 8, "aug": 8, "september": 9,
    "sep": 9, "sept": 9, "october": 10, "oct": 10, "november": 11,
    "nov": 11, "december": 12, "dec": 12,
}


class CronError(ValueError):
    """A user-visible validation or scheduling error."""


# ---------------------------------------------------------------------------
# time helpers
# ---------------------------------------------------------------------------
def local_now() -> datetime:
    """Current time in this PC's Windows local timezone, timezone-aware."""
    return datetime.now().astimezone()


def _local(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt.astimezone()
    return dt.astimezone()


def _localize_naive(dt: datetime) -> datetime:
    """Interpret a naive wall-clock value in the current Windows timezone."""
    return dt.replace(tzinfo=None).astimezone()


def _parse_iso(value) -> datetime:
    text = str(value or "").strip()
    if not text:
        raise CronError("A date and time is required.")
    try:
        return _local(datetime.fromisoformat(text.replace("Z", "+00:00")))
    except ValueError as exc:
        raise CronError(f"Invalid date and time: {text}") from exc


def _iso(dt: datetime | None) -> str:
    return _local(dt).isoformat() if isinstance(dt, datetime) else ""


def _parse_clock(text: str) -> tuple[int, int]:
    match = re.search(r"(\d{1,2})(?::(\d{2}))?\s*(am|pm)?", str(text or "").lower())
    if not match:
        raise CronError("Include a time such as 9:00 AM.")
    hour = int(match.group(1))
    minute = int(match.group(2) or 0)
    meridiem = match.group(3)
    if not 0 <= minute < 60:
        raise CronError("Minutes must be between 00 and 59.")
    if meridiem:
        if not 1 <= hour <= 12:
            raise CronError("Use an hour from 1 to 12 with AM or PM.")
        hour = hour % 12 + (12 if meridiem == "pm" else 0)
    elif hour == 24:
        hour = 0
    if not 0 <= hour < 24:
        raise CronError("Hour must be between 0 and 23.")
    return hour, minute


def _clock_text(value: str) -> str:
    try:
        hour, minute = _parse_clock(value)
    except CronError:
        return str(value or "")
    suffix = "AM" if hour < 12 else "PM"
    display = hour % 12 or 12
    return f"{display}:{minute:02d} {suffix}"


def _time_value(value: str) -> dtime:
    hour, minute = _parse_clock(value)
    return dtime(hour, minute)


# ---------------------------------------------------------------------------
# schedule validation / occurrence calculation
# ---------------------------------------------------------------------------
def normalize_schedule(raw) -> dict:
    if not isinstance(raw, dict):
        raise CronError("Choose a schedule.")
    kind = str(raw.get("kind") or "").strip().lower()
    if kind not in SCHEDULE_KINDS:
        raise CronError("Choose one-time, daily, weekly or custom interval.")
    if kind == "once":
        run_at = _parse_iso(raw.get("run_at"))
        return {"kind": "once", "run_at": _iso(run_at)}
    if kind == "daily":
        hour, minute = _parse_clock(str(raw.get("time") or ""))
        return {"kind": "daily", "time": f"{hour:02d}:{minute:02d}"}
    if kind == "weekly":
        hour, minute = _parse_clock(str(raw.get("time") or ""))
        weekdays = []
        for item in raw.get("weekdays") or ():
            try:
                value = int(item)
            except (TypeError, ValueError):
                continue
            if 0 <= value <= 6 and value not in weekdays:
                weekdays.append(value)
        if not weekdays:
            raise CronError("Choose at least one weekday.")
        return {
            "kind": "weekly",
            "time": f"{hour:02d}:{minute:02d}",
            "weekdays": sorted(weekdays),
        }
    try:
        minutes = int(raw.get("minutes"))
    except (TypeError, ValueError):
        raise CronError("Choose how often the job repeats.")
    if not 1 <= minutes <= 60 * 24 * 30:
        raise CronError("The interval must be between 1 minute and 30 days.")
    start_at = raw.get("start_at")
    if start_at:
        start_at = _iso(_parse_iso(start_at))
    else:
        start_at = _iso(local_now() + timedelta(minutes=minutes))
    return {"kind": "interval", "minutes": minutes, "start_at": start_at}


def next_occurrence(schedule: dict, after: datetime | None = None,
                    inclusive: bool = False) -> datetime | None:
    """First occurrence strictly after *after* (or equal when inclusive)."""
    after = _local(after or local_now())
    kind = schedule.get("kind")
    if kind == "once":
        try:
            run_at = _parse_iso(schedule.get("run_at"))
        except CronError:
            return None
        if run_at > after or (inclusive and run_at == after):
            return run_at
        return None
    if kind in {"daily", "weekly"}:
        clock = _time_value(str(schedule.get("time") or ""))
        weekdays = set(schedule.get("weekdays") or ())
        for offset in range(0, 15):
            day = (after + timedelta(days=offset)).date()
            if kind == "weekly" and day.weekday() not in weekdays:
                continue
            candidate = _localize_naive(datetime.combine(day, clock))
            if candidate > after or (inclusive and candidate == after):
                return candidate
        return None
    if kind == "interval":
        try:
            minutes = max(1, int(schedule.get("minutes")))
            anchor = _parse_iso(schedule.get("start_at"))
        except (TypeError, ValueError, CronError):
            return None
        step = timedelta(minutes=minutes)
        if anchor > after or (inclusive and anchor == after):
            return anchor
        elapsed = (after - anchor) / step
        return anchor + step * (int(elapsed) + 1)
    return None


def describe_schedule(schedule: dict) -> str:
    kind = schedule.get("kind")
    if kind == "once":
        try:
            run_at = _parse_iso(schedule.get("run_at"))
        except CronError:
            return "One time"
        return f"One time · {run_at:%a %d %b %Y, }" + _clock_text(
            f"{run_at.hour:02d}:{run_at.minute:02d}"
        )
    if kind == "daily":
        return f"Every day at {_clock_text(schedule.get('time', ''))}"
    if kind == "weekly":
        days = sorted(set(schedule.get("weekdays") or []))
        if days == [0, 1, 2, 3, 4]:
            label = "Every weekday"
        elif days == [5, 6]:
            label = "Every weekend"
        else:
            label = "Every " + ", ".join(WEEKDAYS[d][1] for d in days)
        return f"{label} at {_clock_text(schedule.get('time', ''))}"
    if kind == "interval":
        minutes = int(schedule.get("minutes") or 0)
        if minutes % 1440 == 0:
            amount, unit = minutes // 1440, "day"
        elif minutes % 60 == 0:
            amount, unit = minutes // 60, "hour"
        else:
            amount, unit = minutes, "minute"
        suffix = "" if amount == 1 else "s"
        return f"Every {amount} {unit}{suffix}"
    return "Unsupported schedule"


def occurrence_key(occurrence: datetime) -> str:
    return _iso(occurrence)


# ---------------------------------------------------------------------------
# natural-language parser
# ---------------------------------------------------------------------------
def _action_from_text(text: str) -> dict:
    clean = " ".join(str(text or "").lower().split()).strip(" ,.")
    if not clean:
        raise CronError("Describe what TALCON should do, for example “Open Chrome”.")
    mode = re.match(r"^(?:activate|switch to)\s+(.+)$", clean)
    if mode and "mode" in clean:
        name = re.sub(r"\s+mode$", "", mode.group(1).strip()).strip()
        if name:
            return {"type": "activate_mode", "mode": name}
    open_match = re.match(r"^(?:open|launch|start|run)\s+(.+)$", clean)
    if open_match:
        target = open_match.group(1).strip()
        target = re.sub(r"\s+(?:app|application)$", "", target).strip()
        if not target:
            raise CronError("Name the application to open.")
        return {"type": "open_app", "name": target}
    close_match = re.match(r"^(?:close|exit|quit)\s+(.+)$", clean)
    if close_match:
        target = re.sub(
            r"\s+(?:app|application)$", "", close_match.group(1).strip()
        ).strip()
        if not target:
            raise CronError("Name the application to close.")
        return {"type": "close_app", "name": target}
    raise CronError(
        "Only “Open …”, “Close …”, “Activate … mode” and a schedule are supported."
    )


def _extract_time(text: str) -> tuple[int, int] | None:
    lowered = text.lower()
    if re.search(r"\bnoon\b", lowered):
        return 12, 0
    if re.search(r"\bmidnight\b", lowered):
        return 0, 0
    at = re.search(
        r"\bat\s+(\d{1,2})(?::(\d{2}))?\s*(am|pm)?"
        r"(?:\s+(?:in\s+the\s+)?(morning|afternoon|evening))?",
        lowered,
    )
    if at:
        hour = int(at.group(1))
        minute = int(at.group(2) or 0)
        meridiem = at.group(3)
        part = at.group(4)
        if part and not meridiem:
            if part == "morning":
                meridiem = "am"
            elif part in {"afternoon", "evening"} and hour < 12:
                hour += 12
        if meridiem:
            if not 1 <= hour <= 12:
                raise CronError("Use an hour from 1 to 12 with AM or PM.")
            hour = hour % 12 + (12 if meridiem == "pm" else 0)
        if not 0 <= minute < 60 or not 0 <= hour < 24:
            raise CronError("That time is not valid.")
        return hour, minute
    loose = re.search(r"\b(\d{1,2})(?::(\d{2}))?\s*(am|pm)\b", lowered)
    if loose:
        hour = int(loose.group(1)) % 12
        if loose.group(3) == "pm":
            hour += 12
        return hour, int(loose.group(2) or 0)
    return None


def _weekdays_in(text: str) -> list[int]:
    found: list[int] = []
    for word in re.findall(r"\b[a-z]+\b", text.lower()):
        day = _WEEKDAY_LOOKUP.get(word)
        if day is not None and day not in found:
            found.append(day)
    return sorted(found)


def _one_time_date(text: str, now: datetime, clock: tuple[int, int]) -> datetime:
    lowered = text.lower()
    day: date | None = None
    if re.search(r"\btomorrow\b", lowered):
        day = (now + timedelta(days=1)).date()
    elif re.search(r"\btoday\b", lowered):
        day = now.date()
    else:
        named_day = re.search(r"\b(?:on|next)\s+([a-z]+)\b", lowered)
        if named_day and named_day.group(1) in _WEEKDAY_LOOKUP:
            target = _WEEKDAY_LOOKUP[named_day.group(1)]
            delta = (target - now.weekday()) % 7
            if delta == 0 or named_day.group(0).startswith("next"):
                delta = delta or 7
            day = (now + timedelta(days=delta)).date()
        iso = re.search(r"\bon\s+(\d{4})-(\d{1,2})-(\d{1,2})\b", lowered)
        if iso:
            try:
                day = date(int(iso.group(1)), int(iso.group(2)), int(iso.group(3)))
            except ValueError as exc:
                raise CronError("That calendar date is not valid.") from exc
        if day is None:
            numeric = re.search(r"\bon\s+(\d{1,2})[/-](\d{1,2})(?:[/-](\d{2,4}))?\b", lowered)
            if numeric:
                first, second = int(numeric.group(1)), int(numeric.group(2))
                year = int(numeric.group(3) or now.year)
                if year < 100:
                    year += 2000
                # Prefer day/month, then month/day when only one is possible.
                for month, dom in ((second, first), (first, second)):
                    try:
                        day = date(year, month, dom)
                        break
                    except ValueError:
                        continue
                if day is None:
                    raise CronError("That calendar date is not valid.")
        if day is None:
            named = re.search(
                r"\bon\s+(\d{1,2})(?:st|nd|rd|th)?\s+([a-z]+)\b", lowered
            )
            if named and named.group(2) in _MONTHS:
                try:
                    day = date(now.year, _MONTHS[named.group(2)], int(named.group(1)))
                except ValueError as exc:
                    raise CronError("That calendar date is not valid.") from exc
        if day is None:
            named = re.search(r"\bon\s+([a-z]+)\s+(\d{1,2})(?:st|nd|rd|th)?\b", lowered)
            if named and named.group(1) in _MONTHS:
                try:
                    day = date(now.year, _MONTHS[named.group(1)], int(named.group(2)))
                except ValueError as exc:
                    raise CronError("That calendar date is not valid.") from exc
    candidate = _localize_naive(datetime.combine(day or now.date(), dtime(*clock)))
    if candidate <= now and day is None:
        candidate += timedelta(days=1)
    return candidate


def parse_natural_job(text: str, now: datetime | None = None) -> dict:
    """Turn “Open Chrome every day at 9 AM” into a safe job description.

    Only the action verbs understood by the UI are accepted.  The result is a
    proposal; the caller still resolves the application and validates the job
    before it is saved and authorised.
    """
    now = _local(now or local_now())
    raw = " ".join(str(text or "").strip().split())
    if not raw:
        raise CronError("Describe the job first.")
    lowered = re.sub(r"[^\w\s:/-]", " ", raw.lower())
    lowered = " ".join(lowered.split())

    marker = re.search(
        r"\b(?:every|each|daily|weekly|at|on|tomorrow|today|next)\b", lowered
    )
    action_text = lowered[: marker.start()] if marker else lowered
    schedule_text = lowered[marker.start():] if marker else ""
    action = _action_from_text(action_text)

    weekdays = _weekdays_in(schedule_text)
    interval = re.search(
        r"\bevery\s+(\d+)?\s*(minute|hour|day)s?\b", schedule_text
    )
    interval_minutes = None
    if interval:
        amount = int(interval.group(1) or 1)
        unit = interval.group(2)
        # "every day" means a daily wall-clock job, not a 24-hour timer
        # anchored to the moment it was created.
        if not (unit == "day" and not interval.group(1)):
            interval_minutes = amount * {"minute": 1, "hour": 60, "day": 1440}[unit]

    recurring_word = bool(re.search(r"\bevery\b|\beach\b", schedule_text))
    weekly = (
        (bool(weekdays) and recurring_word)
        or bool(re.search(r"\bweekly\b|\bevery\s+week\b", schedule_text))
        or bool(re.search(r"\bweekdays?\b", schedule_text))
        or bool(re.search(r"\bweekends?\b", schedule_text))
    )
    daily = bool(re.search(r"\b(?:daily|every\s+day|each\s+day)\b", schedule_text))
    if re.search(r"\bweekdays?\b", schedule_text):
        weekdays = [0, 1, 2, 3, 4]
    elif re.search(r"\bweekends?\b", schedule_text):
        weekdays = [5, 6]
    if weekly and not weekdays:
        weekdays = list(range(7))

    clock = _extract_time(schedule_text)
    if interval_minutes:
        schedule = normalize_schedule({
            "kind": "interval",
            "minutes": interval_minutes,
            "start_at": _iso(now + timedelta(minutes=interval_minutes)),
        })
    elif weekly:
        if clock is None:
            raise CronError("Include a time, for example “every Monday at 9 AM”.")
        schedule = normalize_schedule({
            "kind": "weekly",
            "weekdays": weekdays,
            "time": f"{clock[0]:02d}:{clock[1]:02d}",
        })
    elif daily:
        if clock is None:
            raise CronError("Include a time, for example “every day at 9 AM”.")
        schedule = normalize_schedule({
            "kind": "daily", "time": f"{clock[0]:02d}:{clock[1]:02d}",
        })
    else:
        if clock is None:
            raise CronError("Include a date and time for a one-time job.")
        run_at = _one_time_date(schedule_text, now, clock)
        if run_at <= now:
            raise CronError("Choose a time in the future.")
        schedule = normalize_schedule({"kind": "once", "run_at": _iso(run_at)})

    target = action.get("name") or action.get("mode") or ""
    verb = {
        "open_app": "Open", "close_app": "Close", "activate_mode": "Activate",
    }[action["type"]]
    name = f"{verb} {target}".strip()
    if action["type"] == "activate_mode" and "mode" not in name.lower():
        name += " mode"
    return {
        "name": name.title() if name.islower() else name,
        "action": action,
        "schedule": schedule,
        "catch_up": "run_once",
    }


# ---------------------------------------------------------------------------
# job validation
# ---------------------------------------------------------------------------
def normalize_action(raw) -> dict:
    if not isinstance(raw, dict):
        raise CronError("Choose an action.")
    kind = str(raw.get("type") or "").strip()
    if kind not in ACTION_TYPES:
        raise CronError("Choose a supported action.")
    if kind == "open_app":
        descriptor = raw.get("app")
        if isinstance(descriptor, dict):
            clean = normalize_app_entry(descriptor)
            if clean is None:
                raise CronError("Choose a valid installed application.")
            return {"type": "open_app", "app": clean}
        name = str(raw.get("name") or "").strip()
        if not name:
            raise CronError("Choose the application to open.")
        return {"type": "open_app", "name": name}
    if kind == "close_app":
        name = str(raw.get("name") or "").strip()
        if not name:
            raise CronError("Choose the application to close.")
        return {"type": "close_app", "name": name}
    mode = str(raw.get("mode") or "").strip().lower()
    if not mode or not re.fullmatch(r"[a-z0-9_]+", mode):
        raise CronError("Choose a mode to activate.")
    return {"type": "activate_mode", "mode": mode}


def validate_job(raw: dict, now: datetime | None = None,
                 existing_id: str | None = None, allow_past: bool = False) -> dict:
    if not isinstance(raw, dict):
        raise CronError("The job must be an object.")
    now = _local(now or local_now())
    name = " ".join(str(raw.get("name") or "").split())
    if not name:
        raise CronError("Give the job a name.")
    if len(name) > 80:
        raise CronError("Keep the job name under 80 characters.")
    action = normalize_action(raw.get("action"))
    schedule = normalize_schedule(raw.get("schedule"))
    if (not allow_past and schedule["kind"] == "once"
            and _parse_iso(schedule["run_at"]) <= now):
        raise CronError("A one-time job must be scheduled in the future.")
    catch_up = str(raw.get("catch_up") or "run_once").strip().lower()
    if catch_up not in CATCH_UP_POLICIES:
        catch_up = "run_once"
    return {
        "id": existing_id or str(raw.get("id") or uuid.uuid4().hex),
        "name": name,
        "enabled": bool(raw.get("enabled", True)),
        "action": action,
        "schedule": schedule,
        "catch_up": catch_up,
        "created_at": str(raw.get("created_at") or _iso(now)),
        "authorized_at": str(raw.get("authorized_at") or _iso(now)),
        "next_run": str(raw.get("next_run") or ""),
        "last_run": str(raw.get("last_run") or ""),
        "last_status": str(raw.get("last_status") or "never"),
        "last_detail": str(raw.get("last_detail") or "Not run yet."),
        "last_occurrence": str(raw.get("last_occurrence") or ""),
        "history": copy.deepcopy(raw.get("history") or []),
    }


def initial_next_run(job: dict, now: datetime | None = None) -> str:
    now = _local(now or local_now())
    if job["schedule"]["kind"] == "once":
        return job["schedule"]["run_at"]
    occurrence = next_occurrence(job["schedule"], now)
    return _iso(occurrence) if occurrence else ""


# ---------------------------------------------------------------------------
# persistence
# ---------------------------------------------------------------------------
class CronStore:
    """Thread-safe JSON persistence for jobs and their execution history."""

    def __init__(self, path: Path | str = CRON_JOBS_PATH, clock=local_now):
        self.path = Path(path)
        self.clock = clock
        self._lock = threading.RLock()
        self._jobs: list[dict] = []
        self.load()

    def load(self) -> None:
        jobs: list[dict] = []
        if self.path.exists():
            try:
                import json
                with self.path.open("r", encoding="utf-8") as handle:
                    data = json.load(handle)
                if isinstance(data, dict):
                    jobs = list(data.get("jobs") or [])
            except Exception as exc:
                log.error("Could not read cron jobs: %s", exc)
                try:
                    self.path.replace(self.path.with_suffix(".invalid.json"))
                except OSError:
                    pass
        cleaned = []
        for raw in jobs:
            try:
                job = validate_job(raw, now=self.clock(), allow_past=True)
            except Exception as exc:
                log.warning("Ignoring invalid saved cron job: %s", exc)
                continue
            if not job.get("next_run") and job.get("enabled"):
                job["next_run"] = initial_next_run(job, self.clock())
            if job.get("last_status") == "running":
                job["last_status"] = "failure"
                job["last_detail"] = "Interrupted when TALCON stopped."
            cleaned.append(job)
        with self._lock:
            self._jobs = cleaned

    def _save_locked(self) -> None:
        write_json(self.path, {"version": SCHEMA_VERSION, "jobs": self._jobs})

    def snapshot(self) -> list[dict]:
        with self._lock:
            return copy.deepcopy(self._jobs)

    def get(self, job_id: str) -> dict | None:
        with self._lock:
            for job in self._jobs:
                if job["id"] == job_id:
                    return copy.deepcopy(job)
        return None

    def create(self, raw: dict, now: datetime | None = None) -> dict:
        now = _local(now or self.clock())
        with self._lock:
            job = validate_job(raw, now=now)
            if any(item["id"] == job["id"] for item in self._jobs):
                raise CronError("A job with that id already exists.")
            job["next_run"] = initial_next_run(job, now)
            self._jobs.append(job)
            self._save_locked()
            return copy.deepcopy(job)

    def update(self, job_id: str, raw: dict, now: datetime | None = None) -> dict:
        now = _local(now or self.clock())
        with self._lock:
            for index, old in enumerate(self._jobs):
                if old["id"] != job_id:
                    continue
                merged = dict(raw)
                merged.update({
                    "id": job_id,
                    "created_at": old.get("created_at"),
                    "authorized_at": _iso(now),
                    "last_run": old.get("last_run", ""),
                    "last_status": old.get("last_status", "never"),
                    "last_detail": old.get("last_detail", "Not run yet."),
                    "last_occurrence": old.get("last_occurrence", ""),
                    "history": copy.deepcopy(old.get("history") or []),
                })
                job = validate_job(merged, now=now, existing_id=job_id)
                schedule_changed = job["schedule"] != old.get("schedule")
                job["next_run"] = (
                    initial_next_run(job, now)
                    if schedule_changed or not old.get("next_run")
                    else old.get("next_run", "")
                )
                self._jobs[index] = job
                self._save_locked()
                return copy.deepcopy(job)
        raise CronError("That job no longer exists.")

    def delete(self, job_id: str) -> bool:
        with self._lock:
            before = len(self._jobs)
            self._jobs = [job for job in self._jobs if job["id"] != job_id]
            if len(self._jobs) != before:
                self._save_locked()
                return True
        return False

    def set_enabled(self, job_id: str, enabled: bool,
                    now: datetime | None = None) -> dict:
        now = _local(now or self.clock())
        with self._lock:
            for job in self._jobs:
                if job["id"] != job_id:
                    continue
                next_run = job.get("next_run", "")
                detail = "Paused."
                if enabled:
                    if job["schedule"]["kind"] == "once":
                        run_at = _parse_iso(job["schedule"]["run_at"])
                        if run_at <= now:
                            raise CronError(
                                "This one-time job's time has passed. Edit it and choose "
                                "a future date and time."
                            )
                        next_run = job["schedule"]["run_at"]
                    else:
                        occurrence = next_occurrence(job["schedule"], now)
                        next_run = _iso(occurrence) if occurrence else ""
                    detail = "Resumed. Waiting for the next scheduled time."
                job["enabled"] = bool(enabled)
                job["next_run"] = next_run
                job["last_detail"] = detail
                self._save_locked()
                return copy.deepcopy(job)
        raise CronError("That job no longer exists.")

    def due(self, now: datetime | None = None) -> list[dict]:
        now = _local(now or self.clock())
        result = []
        with self._lock:
            for job in self._jobs:
                if not job.get("enabled") or not job.get("next_run"):
                    continue
                try:
                    due_at = _parse_iso(job["next_run"])
                except CronError:
                    continue
                if due_at <= now:
                    result.append(copy.deepcopy(job))
        return result

    def begin_occurrence(self, job_id: str, occurrence: datetime,
                         next_run: datetime | None) -> dict | None:
        """Atomically claim one occurrence before it is executed."""
        key = occurrence_key(occurrence)
        with self._lock:
            for job in self._jobs:
                if job["id"] != job_id:
                    continue
                if not job.get("enabled"):
                    return None
                if job.get("last_occurrence") == key:
                    return None
                if job.get("next_run"):
                    try:
                        if _parse_iso(job["next_run"]) != occurrence:
                            return None
                    except CronError:
                        return None
                job["last_occurrence"] = key
                job["last_run"] = key
                job["last_status"] = "running"
                job["last_detail"] = "Running…"
                job["next_run"] = _iso(next_run) if next_run else ""
                self._save_locked()
                return copy.deepcopy(job)
        return None

    def finish_occurrence(self, job_id: str, occurrence: datetime,
                          ok: bool, detail: str,
                          now: datetime | None = None) -> dict | None:
        now = _local(now or self.clock())
        key = occurrence_key(occurrence)
        with self._lock:
            for job in self._jobs:
                if job["id"] != job_id:
                    continue
                status = "success" if ok else "failure"
                job["last_status"] = status
                job["last_detail"] = str(detail or status)
                job["history"] = (list(job.get("history") or []) + [{
                    "occurrence": key,
                    "started": key,
                    "finished": _iso(now),
                    "status": status,
                    "detail": str(detail or status),
                }])[-HISTORY_LIMIT:]
                if job["schedule"]["kind"] == "once":
                    job["enabled"] = False
                    job["next_run"] = ""
                    job["last_detail"] += " · One-time job completed."
                self._save_locked()
                return copy.deepcopy(job)
        return None

    def record_missed(self, job_id: str, occurrence: datetime,
                      detail: str = "Missed while TALCON was not running.",
                      now: datetime | None = None) -> dict | None:
        now = _local(now or self.clock())
        key = occurrence_key(occurrence)
        with self._lock:
            for job in self._jobs:
                if job["id"] != job_id:
                    continue
                job["last_occurrence"] = key
                job["last_run"] = key
                job["last_status"] = "missed"
                job["last_detail"] = detail
                job["history"] = (list(job.get("history") or []) + [{
                    "occurrence": key,
                    "started": "",
                    "finished": _iso(now),
                    "status": "missed",
                    "detail": detail,
                }])[-HISTORY_LIMIT:]
                schedule = job["schedule"]
                if schedule["kind"] == "once":
                    job["enabled"] = False
                    job["next_run"] = ""
                else:
                    nxt = next_occurrence(schedule, now)
                    job["next_run"] = _iso(nxt) if nxt else ""
                self._save_locked()
                return copy.deepcopy(job)
        return None

    def mark_missed_occurrence(self, job_id: str, now: datetime | None = None) -> dict | None:
        job = self.get(job_id)
        if not job or not job.get("next_run"):
            return None
        return self.record_missed(job_id, _parse_iso(job["next_run"]), now=now)


# ---------------------------------------------------------------------------
# execution
# ---------------------------------------------------------------------------
class _SilentSpeech:
    """A no-op speech sink for mode activation during a scheduled job."""

    def say(self, *_args, **_kwargs) -> None:
        return None


class CronExecutor:
    """Run only the structured actions the scheduler supports."""

    def __init__(self, runtime, app_launcher=launch_app, url_opener=None):
        self.runtime = runtime
        self.app_launcher = app_launcher
        self.url_opener = url_opener
        self.speech = _SilentSpeech()

    def _catalogue(self):
        library = getattr(self.runtime, "apps", None)
        if library is None:
            return []
        try:
            return list(library.entries())
        except Exception:
            log.exception("Could not read the application catalogue for a cron job")
            return []

    def _aliases(self):
        commands = getattr(self.runtime, "commands", None)
        return getattr(commands, "app_aliases", {}) or {}

    def _open_app(self, action: dict) -> tuple[bool, str]:
        descriptor = action.get("app")
        if isinstance(descriptor, dict):
            clean = normalize_app_entry(descriptor)
            if clean is None:
                return False, "The saved application descriptor is invalid."
            label = clean.get("name") or clean.get("path") or clean.get("aumid")
            try:
                self.app_launcher(clean)
            except Exception as exc:
                log.exception("Scheduled launch failed for %s", label)
                return False, f"Could not open {label}: {exc}"
            return True, f"Opened {label}"
        query = str(action.get("name") or "").strip()
        result = resolve_application(query, self._catalogue(), self._aliases())
        if result.status != "match" or result.entry is None:
            reason = "not found" if result.status == "none" else "ambiguous"
            return False, f"{query or 'Application'} was {reason}."
        try:
            self.app_launcher(result.entry)
        except Exception as exc:
            log.exception("Scheduled launch failed for %s", result.entry.name)
            return False, f"Could not open {result.entry.name}: {exc}"
        return True, f"Opened {result.entry.name}"

    def _close_app(self, action: dict) -> tuple[bool, str]:
        getter = getattr(self.runtime, "running_applications", None)
        closer = getattr(self.runtime, "close_application", None)
        if not callable(getter) or not callable(closer):
            return False, "Running-application control is unavailable."
        try:
            running = list(getter())
        except Exception as exc:
            log.exception("Scheduled close could not list running applications")
            return False, f"Could not list running applications: {exc}"
        query = str(action.get("name") or "").strip()
        result = resolve_application(query, running, self._aliases())
        if result.status != "match" or result.entry is None:
            if result.status == "none":
                return True, f"{query or 'The application'} was not running."
            return False, f"{query or 'The application'} matched more than one running app."
        try:
            closed = closer(result.entry)
        except Exception as exc:
            log.exception("Scheduled close failed for %s", result.entry.name)
            return False, f"Could not close {result.entry.name}: {exc}"
        if getattr(closed, "errors", None):
            return False, "; ".join(str(item) for item in closed.errors)
        if getattr(closed, "closed", None):
            return True, f"Closed {result.entry.name} ({len(closed.closed)} window(s))."
        return False, f"{result.entry.name} did not close."

    def _activate_mode(self, action: dict) -> tuple[bool, str]:
        modes = getattr(self.runtime, "modes_data", {}) or {}
        mode = str(action.get("mode") or "").strip().lower()
        if mode not in modes:
            return False, f"Mode “{mode}” no longer exists."
        manager = ModeManager(
            modes, self.speech, url_opener=self.url_opener,
            app_launcher=None,
        )
        try:
            ok = manager.activate(mode)
        except Exception as exc:
            log.exception("Scheduled mode activation failed")
            return False, f"Could not activate {mode} mode: {exc}"
        title = str((modes.get(mode) or {}).get("title") or mode.replace("_", " ").title())
        return (True, f"Activated {title} mode.") if ok else (
            False, f"{title} mode could not open anything."
        )

    def execute(self, action: dict) -> tuple[bool, str]:
        kind = str((action or {}).get("type") or "")
        if kind == "open_app":
            return self._open_app(action)
        if kind == "close_app":
            return self._close_app(action)
        if kind == "activate_mode":
            return self._activate_mode(action)
        return False, f"Unsupported scheduled action: {kind}"


# ---------------------------------------------------------------------------
# scheduler
# ---------------------------------------------------------------------------
class CronScheduler:
    """Small in-process background scheduler with persisted catch-up."""

    def __init__(self, runtime, store: CronStore | None = None,
                 executor: CronExecutor | None = None, clock=local_now,
                 poll_seconds: float = 2.0):
        self.runtime = runtime
        self.clock = clock
        self.store = store or CronStore(clock=clock)
        self.executor = executor or CronExecutor(runtime)
        self.poll_seconds = max(0.5, float(poll_seconds))
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()
        self._running: set[str] = set()

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._loop, name="TALCON-Cron", daemon=True
        )
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if (self._thread and self._thread.is_alive()
                and self._thread is not threading.current_thread()):
            self._thread.join(timeout=2.0)

    def _loop(self) -> None:
        # Run one tick immediately so missed work is handled at startup.
        while not self._stop.is_set():
            try:
                self.tick()
            except Exception:
                log.exception("Cron scheduler tick failed")
            self._stop.wait(self.poll_seconds)

    def snapshot(self) -> list[dict]:
        return self.store.snapshot()

    def status_text(self) -> str:
        jobs = self.store.snapshot()
        active = [job for job in jobs if job.get("enabled")]
        if not jobs:
            return "No scheduled jobs"
        if not active:
            return f"{len(jobs)} job(s) · all paused"
        upcoming = []
        for job in active:
            try:
                upcoming.append(_parse_iso(job.get("next_run")))
            except CronError:
                continue
        if not upcoming:
            return f"{len(active)} active job(s)"
        nxt = min(upcoming)
        return f"{len(active)} active · next {nxt:%d %b, %H:%M}"

    def tick(self, now: datetime | None = None) -> list[tuple[str, str, str]]:
        """Process every due job once.  Returns ``(job_id, status, detail)``."""
        now = _local(now or self.clock())
        results: list[tuple[str, str, str]] = []
        for job in self.store.due(now):
            job_id = str(job.get("id"))
            with self._lock:
                if job_id in self._running:
                    continue
            try:
                due_at = _parse_iso(job.get("next_run"))
            except CronError:
                continue
            overdue = (now - due_at).total_seconds()
            if job.get("catch_up") == "skip" and overdue > MISSED_GRACE_SECONDS:
                missed = self.store.record_missed(
                    job_id, due_at,
                    "Missed while TALCON was asleep or not running; catch-up is set to skip.",
                    now=now,
                )
                if missed:
                    results.append((job_id, "missed", missed.get("last_detail", "")))
                continue
            schedule = job["schedule"]
            nxt = next_occurrence(schedule, now)
            claimed = self.store.begin_occurrence(job_id, due_at, nxt)
            if claimed is None:
                continue
            with self._lock:
                self._running.add(job_id)
            threading.Thread(
                target=self._execute, args=(claimed, due_at),
                name=f"TALCON-Cron-{job_id[:6]}", daemon=True,
            ).start()
            results.append((job_id, "running", "Started."))
        return results

    def _execute(self, job: dict, occurrence: datetime) -> None:
        job_id = str(job["id"])
        try:
            ok, detail = self.executor.execute(job.get("action") or {})
        except Exception as exc:
            log.exception("Cron job %s failed", job.get("name"))
            ok, detail = False, f"Execution error: {exc}"
        try:
            self.store.finish_occurrence(job_id, occurrence, ok, detail, now=self.clock())
        except Exception:
            log.exception("Could not save the result of cron job %s", job_id)
        finally:
            with self._lock:
                self._running.discard(job_id)
        log.info("Cron job %s: %s | %s", job.get("name"), "success" if ok else "failure", detail)

    def create_job(self, raw: dict) -> dict:
        return self.store.create(raw, now=self.clock())

    def update_job(self, job_id: str, raw: dict) -> dict:
        return self.store.update(job_id, raw, now=self.clock())

    def delete_job(self, job_id: str) -> bool:
        return self.store.delete(job_id)

    def set_enabled(self, job_id: str, enabled: bool) -> dict:
        return self.store.set_enabled(job_id, enabled, now=self.clock())


# Backwards-friendly alias used by the UI/runtime.
CronManager = CronScheduler

__all__ = [
    "CronError", "CronExecutor", "CronManager", "CronScheduler", "CronStore",
    "ACTION_TYPES", "CATCH_UP_POLICIES", "CRON_JOBS_PATH", "SCHEDULE_KINDS",
    "describe_schedule", "initial_next_run", "local_now", "next_occurrence",
    "normalize_action", "normalize_schedule", "parse_natural_job", "validate_job",
]
