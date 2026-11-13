"""User-defined voice commands and ordered automation sequences.

Everything here is deliberately explicit: a command may open a configured
application or website, switch mode or wait.  Nothing accepts free-form shell
commands, PowerShell or arbitrary terminal input.
"""
from __future__ import annotations

import copy
import json
import logging
import os
import subprocess
import threading
import time
import uuid
import webbrowser
from pathlib import Path

from .commands import normalize_phrase
from .config import CUSTOM_COMMANDS_PATH, ROOT, resolve_path, write_json
from .modes import normalize_url

log = logging.getLogger("nova.custom")

COMMAND_ACTIONS = ("open_app", "open_url", "activate_mode", "wait")
STEP_ACTIONS = ("open_app", "close_app", "open_url", "activate_mode", "wait")
MODE_NAMES = ("study", "entertainment")

ACTION_LABELS = {
    "open_app": "Open an application",
    "close_app": "Close an application NOVA started",
    "open_url": "Open a website",
    "activate_mode": "Activate Study or Entertainment mode",
    "wait": "Wait for a delay",
}


def _clamp_delay(value) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return 0.0
    return max(0.0, min(10.0, number))


def _clamp_wait(value) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return 0.0
    return max(0.0, min(30.0, number))


def normalize_triggers(raw) -> list[str]:
    if isinstance(raw, str):
        raw = raw.replace(",", "\n").splitlines()
    if not isinstance(raw, (list, tuple)):
        return []
    result: list[str] = []
    for item in raw:
        normalized = normalize_phrase(item)
        if normalized and normalized not in result:
            result.append(normalized)
    return result


def _clean_args(raw) -> list[str]:
    if isinstance(raw, str):
        raw = raw.splitlines()
    if not isinstance(raw, (list, tuple)):
        return []
    return [str(item).strip() for item in raw if str(item).strip()]


def validate_command(entry: dict, reserved=(), used=(), existing_id: str | None = None) -> dict:
    """Return a clean command entry or raise ValueError with a readable reason."""
    if not isinstance(entry, dict):
        raise ValueError("The command must be an object.")
    name = str(entry.get("name", "")).strip()
    if not name:
        raise ValueError("Give the command a name.")
    triggers = normalize_triggers(entry.get("triggers"))
    if not triggers:
        raise ValueError("Add at least one spoken trigger phrase.")
    action = str(entry.get("action", "")).strip()
    if action not in COMMAND_ACTIONS:
        raise ValueError("Choose a supported action.")
    reserved = set(reserved)
    used = set(used)
    for trigger in triggers:
        if trigger in reserved:
            raise ValueError(f"“{trigger}” is a reserved built-in command phrase.")
        if trigger in used:
            raise ValueError(f"“{trigger}” is already used by another custom command.")
    params = entry.get("params")
    if not isinstance(params, dict):
        params = {}
    clean_params: dict = {}
    if action == "open_app":
        path = str(params.get("path", "")).strip()
        if not path:
            raise ValueError("Choose the application to open.")
        clean_params = {"path": path, "args": _clean_args(params.get("args"))}
    elif action == "open_url":
        url = normalize_url(str(params.get("url", "")))
        if url is None:
            raise ValueError("Enter a valid http:// or https:// website address.")
        clean_params = {"url": url}
    elif action == "activate_mode":
        mode = str(params.get("mode", "")).strip().lower()
        if mode not in MODE_NAMES:
            raise ValueError("Choose Study or Entertainment mode.")
        clean_params = {"mode": mode}
    elif action == "wait":
        clean_params = {"seconds": _clamp_wait(params.get("seconds", 1.0))}
    return {
        "id": existing_id or str(entry.get("id") or uuid.uuid4().hex),
        "name": name,
        "enabled": bool(entry.get("enabled", True)),
        "triggers": triggers,
        "action": action,
        "params": clean_params,
    }


def validate_step(step: dict) -> dict:
    if not isinstance(step, dict):
        raise ValueError("Each automation step must be an object.")
    action = str(step.get("action", "")).strip()
    if action not in STEP_ACTIONS:
        raise ValueError("Choose a supported automation action.")
    params = step.get("params")
    if not isinstance(params, dict):
        params = {}
    clean_params: dict = {}
    if action in {"open_app", "close_app"}:
        path = str(params.get("path", "")).strip()
        if not path:
            raise ValueError("Choose an application for this step.")
        clean_params = {"path": path}
        if action == "open_app":
            clean_params["args"] = _clean_args(params.get("args"))
    elif action == "open_url":
        url = normalize_url(str(params.get("url", "")))
        if url is None:
            raise ValueError("Enter a valid http:// or https:// website address.")
        clean_params = {"url": url}
    elif action == "activate_mode":
        mode = str(params.get("mode", "")).strip().lower()
        if mode not in MODE_NAMES:
            raise ValueError("Choose Study or Entertainment mode.")
        clean_params = {"mode": mode}
    elif action == "wait":
        clean_params = {"seconds": _clamp_wait(params.get("seconds", 1.0))}
    return {
        "action": action,
        "params": clean_params,
        "delay": _clamp_delay(step.get("delay", 0.0)),
    }


def validate_automation(entry: dict, reserved=(), used=(), existing_id: str | None = None) -> dict:
    if not isinstance(entry, dict):
        raise ValueError("The automation must be an object.")
    name = str(entry.get("name", "")).strip()
    if not name:
        raise ValueError("Give the automation a name.")
    triggers = normalize_triggers(entry.get("triggers"))
    if not triggers:
        raise ValueError("Add at least one spoken trigger phrase.")
    reserved = set(reserved)
    used = set(used)
    for trigger in triggers:
        if trigger in reserved:
            raise ValueError(f"“{trigger}” is a reserved built-in command phrase.")
        if trigger in used:
            raise ValueError(f"“{trigger}” is already used by another custom command.")
    raw_steps = entry.get("steps")
    if not isinstance(raw_steps, list) or not raw_steps:
        raise ValueError("Add at least one action to the automation.")
    steps = [validate_step(step) for step in raw_steps]
    return {
        "id": existing_id or str(entry.get("id") or uuid.uuid4().hex),
        "name": name,
        "enabled": bool(entry.get("enabled", True)),
        "triggers": triggers,
        "steps": steps,
    }


class CustomStore:
    """Persistent JSON store for custom commands and automations."""

    def __init__(self, path=CUSTOM_COMMANDS_PATH, reserved_phrases=()):
        self.path = Path(path)
        self.reserved = set(reserved_phrases or ())
        self._lock = threading.RLock()
        self.data: dict = {"version": 1, "commands": [], "automations": []}
        self.load()

    # -- persistence -------------------------------------------------------
    def load(self) -> dict:
        with self._lock:
            raw = {}
            try:
                if self.path.exists():
                    with self.path.open("r", encoding="utf-8") as handle:
                        raw = json.load(handle)
            except Exception:
                log.exception("Could not read custom commands; starting empty")
                raw = {}
            if not isinstance(raw, dict):
                raw = {}
            commands = []
            seen: set[str] = set()
            for entry in raw.get("commands", []) or []:
                try:
                    clean = validate_command(entry, self.reserved, seen, entry.get("id"))
                    commands.append(clean)
                    seen.update(clean["triggers"])
                except ValueError as exc:
                    log.warning("Dropped invalid custom command: %s", exc)
            automations = []
            for entry in raw.get("automations", []) or []:
                try:
                    clean = validate_automation(entry, self.reserved, seen, entry.get("id"))
                    automations.append(clean)
                    seen.update(clean["triggers"])
                except ValueError as exc:
                    log.warning("Dropped invalid automation: %s", exc)
            self.data = {"version": 1, "commands": commands, "automations": automations}
            return self.snapshot()

    def save(self) -> dict:
        with self._lock:
            write_json(self.path, self.data)
            return self.snapshot()

    def snapshot(self) -> dict:
        with self._lock:
            return copy.deepcopy(self.data)

    # -- queries -----------------------------------------------------------
    def _entries(self, kind: str) -> list[dict]:
        return self.data.get(kind, [])

    def used_triggers(self, exclude_id: str | None = None) -> set[str]:
        used: set[str] = set()
        for kind in ("commands", "automations"):
            for entry in self._entries(kind):
                if exclude_id and entry.get("id") == exclude_id:
                    continue
                used.update(entry.get("triggers", []))
        return used

    def get(self, kind: str, entry_id: str) -> dict | None:
        for entry in self._entries(kind):
            if entry.get("id") == entry_id:
                return copy.deepcopy(entry)
        return None

    def match(self, text: str) -> dict | None:
        phrase = normalize_phrase(text)
        if not phrase:
            return None
        with self._lock:
            for kind in ("commands", "automations"):
                for entry in self._entries(kind):
                    if not entry.get("enabled", True):
                        continue
                    if phrase in entry.get("triggers", []):
                        result = copy.deepcopy(entry)
                        result["kind"] = "command" if kind == "commands" else "automation"
                        return result
        return None

    # -- mutations ---------------------------------------------------------
    def save_command(self, entry: dict, entry_id: str | None = None) -> dict:
        with self._lock:
            clean = validate_command(
                entry, self.reserved, self.used_triggers(exclude_id=entry_id), entry_id
            )
            self._replace("commands", clean)
            self.save()
            return copy.deepcopy(clean)

    def save_automation(self, entry: dict, entry_id: str | None = None) -> dict:
        with self._lock:
            clean = validate_automation(
                entry, self.reserved, self.used_triggers(exclude_id=entry_id), entry_id
            )
            self._replace("automations", clean)
            self.save()
            return copy.deepcopy(clean)

    def _replace(self, kind: str, entry: dict) -> None:
        entries = self._entries(kind)
        for index, existing in enumerate(entries):
            if existing.get("id") == entry["id"]:
                entries[index] = entry
                return
        entries.append(entry)

    def delete(self, kind: str, entry_id: str) -> bool:
        with self._lock:
            entries = self._entries(kind)
            for index, existing in enumerate(entries):
                if existing.get("id") == entry_id:
                    entries.pop(index)
                    self.save()
                    return True
        return False

    def set_enabled(self, kind: str, entry_id: str, enabled: bool) -> bool:
        with self._lock:
            for entry in self._entries(kind):
                if entry.get("id") == entry_id:
                    entry["enabled"] = bool(enabled)
                    self.save()
                    return True
        return False


class ActionExecutor:
    """Runs only the explicitly supported actions, without a shell."""

    def __init__(self, app, notify=None, url_opener=None, app_launcher=None,
                 process_closer=None):
        self.app = app
        self.notify = notify or (lambda title, body: None)
        self.url_opener = url_opener or (lambda url: webbrowser.open(url, new=2))
        self.app_launcher = app_launcher or self._launch
        self.process_closer = process_closer or self._close_tracked
        self._launched: dict[str, list[subprocess.Popen]] = {}
        self._lock = threading.Lock()

    # -- launching ---------------------------------------------------------
    def _resolve(self, path: str) -> Path:
        target = Path(str(path)).expanduser()
        if not target.is_absolute():
            target = (ROOT / target).resolve()
        return target

    def _launch(self, path: Path, args: list[str]):
        if path.suffix.lower() in {".lnk", ".url"}:
            os.startfile(str(path))  # noqa: S606 - user-configured shortcut
            return None
        return subprocess.Popen(
            [str(path), *args], close_fds=True,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )

    def open_app(self, path: str, args=None) -> tuple[bool, str]:
        target = self._resolve(path)
        if not target.exists():
            return False, f"Application not found: {target}"
        try:
            process = self.app_launcher(target, _clean_args(args))
        except Exception as exc:
            log.exception("Could not launch %s", target)
            return False, f"Could not open {target.name}: {exc}"
        if process is not None:
            with self._lock:
                self._launched.setdefault(str(target).lower(), []).append(process)
            return True, f"Opened {target.name}"
        return True, f"Opened {target.name} (shortcut)"

    def open_url(self, url: str) -> tuple[bool, str]:
        clean = normalize_url(url)
        if clean is None:
            return False, "Invalid website address."
        try:
            self.url_opener(clean)
        except Exception as exc:
            log.exception("Could not open %s", clean)
            return False, f"Could not open {clean}: {exc}"
        return True, f"Opened {clean}"

    def activate_mode(self, mode: str) -> tuple[bool, str]:
        if mode not in MODE_NAMES:
            return False, "Unknown mode."
        try:
            ok = bool(self.app.activate_mode(mode))
        except Exception as exc:
            log.exception("Could not activate mode %s", mode)
            return False, f"Could not activate {mode} mode: {exc}"
        return (True, f"{mode.title()} mode activated.") if ok else (
            False, f"{mode.title()} mode could not be activated."
        )

    def close_app(self, path: str) -> tuple[bool, str]:
        return self.process_closer(self._resolve(path))

    def _close_tracked(self, target: Path) -> tuple[bool, str]:
        key = str(target).lower()
        with self._lock:
            processes = list(self._launched.get(key, []))
        if not processes:
            return False, (
                f"NOVA did not start {target.name}; it was left running for safety."
            )
        closed = 0
        for process in processes:
            if process.poll() is not None:
                continue
            try:
                if os.name == "nt":
                    # Without /F Windows posts WM_CLOSE, giving the app a chance
                    # to save.  Only PIDs launched by NOVA are ever touched.
                    subprocess.run(
                        ["taskkill", "/PID", str(process.pid)],
                        capture_output=True, timeout=5, check=False,
                    )
                else:
                    process.terminate()
                try:
                    process.wait(timeout=4)
                except subprocess.TimeoutExpired:
                    return False, (
                        f"{target.name} did not close; it was left running to avoid data loss."
                    )
                closed += 1
            except Exception as exc:
                log.exception("Could not close %s", target)
                return False, f"Could not close {target.name}: {exc}"
        if closed:
            return True, f"Closed {target.name}"
        return True, f"{target.name} was already closed."

    def wait(self, seconds: float) -> tuple[bool, str]:
        delay = _clamp_wait(seconds)
        if delay:
            time.sleep(delay)
        return True, f"Waited {delay:g} s"

    # -- dispatch ----------------------------------------------------------
    def execute(self, step: dict) -> tuple[bool, str]:
        action = str(step.get("action", ""))
        params = step.get("params") if isinstance(step.get("params"), dict) else {}
        if action == "open_app":
            return self.open_app(params.get("path", ""), params.get("args"))
        if action == "close_app":
            return self.close_app(params.get("path", ""))
        if action == "open_url":
            return self.open_url(params.get("url", ""))
        if action == "activate_mode":
            return self.activate_mode(str(params.get("mode", "")))
        if action == "wait":
            return self.wait(params.get("seconds", 1.0))
        return False, f"Unsupported action: {action}"


class AutomationRunner:
    """Runs ordered automation sequences once at a time, off the GUI thread."""

    def __init__(self, executor: ActionExecutor, notify=None, status=None):
        self.executor = executor
        self.notify = notify or (lambda title, body: None)
        self.status = status or (lambda text: None)
        self._running: set[str] = set()
        self._lock = threading.Lock()

    def is_running(self, automation_id: str) -> bool:
        with self._lock:
            return automation_id in self._running

    def run(self, automation: dict) -> bool:
        automation_id = str(automation.get("id", ""))
        with self._lock:
            if not automation_id or automation_id in self._running:
                return False
            self._running.add(automation_id)
        thread = threading.Thread(
            target=self._run, args=(automation,), name="NOVA-Automation", daemon=True
        )
        thread.start()
        return True

    def run_sequence(self, automation: dict) -> list[tuple[bool, str]]:
        """Synchronous execution used by tests and by the worker thread."""
        results: list[tuple[bool, str]] = []
        for step in automation.get("steps", []):
            delay = _clamp_delay(step.get("delay", 0.0))
            if delay:
                time.sleep(delay)
            try:
                result = self.executor.execute(step)
            except Exception as exc:
                log.exception("Automation step failed")
                result = (False, str(exc))
            results.append(result)
        return results

    def _run(self, automation: dict) -> None:
        automation_id = str(automation.get("id", ""))
        name = str(automation.get("name", "Automation"))
        try:
            self.status(f"Running automation · {name}")
            results = self.run_sequence(automation)
            failures = [message for ok, message in results if not ok]
            if failures:
                self.status(f"{name}: {len(failures)} step(s) failed")
                self.notify(f"{name} needs attention", "\n".join(failures[:4]))
            else:
                self.status(f"{name} completed")
                self.notify(name, "All configured steps completed.")
        finally:
            with self._lock:
                self._running.discard(automation_id)
