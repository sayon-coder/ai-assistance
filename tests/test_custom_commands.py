"""Custom command storage, validation and automation execution tests."""
from __future__ import annotations

import threading
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

import nova.custom as custom
from nova.commands import build_builtin_registry
from nova.custom import (
    ActionExecutor, AutomationRunner, CustomStore, validate_automation,
    validate_command, validate_step,
)
from nova.runtime import NovaRuntime

RESERVED = {"study mode", "open settings", "nova", "hey nova"}


def store(tmp_path):
    return CustomStore(tmp_path / "custom_commands.json", reserved_phrases=RESERVED)


def command(trigger="open my code editor", path=r"C:\Tools\editor.exe"):
    return {
        "name": "Open editor",
        "triggers": [trigger],
        "action": "open_app",
        "params": {"path": path, "args": ["--new-window"]},
    }


# -- validation -------------------------------------------------------------
def test_command_validation_requires_name_triggers_and_action():
    with pytest.raises(ValueError, match="name"):
        validate_command({"triggers": ["x"], "action": "open_app",
                          "params": {"path": "C:/x.exe"}}, RESERVED)
    with pytest.raises(ValueError, match="trigger"):
        validate_command({"name": "x", "triggers": [], "action": "open_app",
                          "params": {"path": "C:/x.exe"}}, RESERVED)
    with pytest.raises(ValueError, match="supported action"):
        validate_command({"name": "x", "triggers": ["x"], "action": "shell",
                          "params": {}}, RESERVED)


def test_command_cannot_override_a_reserved_builtin():
    with pytest.raises(ValueError, match="reserved"):
        validate_command(command("study mode"), RESERVED)


def test_command_url_is_validated():
    with pytest.raises(ValueError, match="website"):
        validate_command({"name": "bad", "triggers": ["go there"],
                          "action": "open_url", "params": {"url": "javascript:alert(1)"}},
                         RESERVED)
    clean = validate_command({"name": "site", "triggers": ["open my site"],
                              "action": "open_url", "params": {"url": "example.com"}},
                             RESERVED)
    assert clean["params"]["url"] == "https://example.com"


def test_wait_is_bounded_and_unknown_mode_rejected():
    clean = validate_command({"name": "pause", "triggers": ["pause"],
                              "action": "wait", "params": {"seconds": 999}}, RESERVED)
    assert clean["params"]["seconds"] == 30.0
    with pytest.raises(ValueError, match="Study, Entertainment or Stock"):
        validate_command({"name": "mode", "triggers": ["switch"],
                          "action": "activate_mode", "params": {"mode": "cinema"}},
                         RESERVED)


def test_automation_requires_steps_and_valid_urls():
    with pytest.raises(ValueError, match="at least one action"):
        validate_automation({"name": "x", "triggers": ["x"], "steps": []}, RESERVED)
    with pytest.raises(ValueError, match="website"):
        validate_step({"action": "open_url", "params": {"url": "file:///C:/secret"}})
    step = validate_step({"action": "wait", "params": {"seconds": 2}, "delay": 99})
    assert step["delay"] == 10.0


# -- persistence ------------------------------------------------------------
def test_save_reload_match_and_delete(tmp_path):
    path = tmp_path / "custom_commands.json"
    first = CustomStore(path, reserved_phrases=RESERVED)
    saved = first.save_command(command())
    assert path.exists()
    assert saved["id"]

    second = CustomStore(path, reserved_phrases=RESERVED)
    hit = second.match("Open my code editor.")
    assert hit is not None
    assert hit["kind"] == "command"
    assert hit["name"] == "Open editor"
    assert second.delete("commands", saved["id"]) is True
    assert second.match("open my code editor") is None


def test_duplicate_triggers_across_commands_and_automations_are_rejected(tmp_path):
    data = store(tmp_path)
    data.save_command(command("open notes"))
    with pytest.raises(ValueError, match="already used"):
        data.save_command(command("open notes"))
    with pytest.raises(ValueError, match="already used"):
        data.save_automation({
            "name": "Automation",
            "triggers": ["open notes"],
            "steps": [{"action": "activate_mode", "params": {"mode": "study"}}],
        })


def test_enable_disable_controls_matching(tmp_path):
    data = store(tmp_path)
    saved = data.save_command(command())
    assert data.match("open my code editor") is not None
    data.set_enabled("commands", saved["id"], False)
    assert data.match("open my code editor") is None
    data.set_enabled("commands", saved["id"], True)
    assert data.match("open my code editor") is not None


def test_invalid_stored_entry_is_dropped_on_reload(tmp_path):
    path = tmp_path / "custom_commands.json"
    path.write_text(
        '{"version": 1, "commands": [{"name": "bad", "triggers": ["study mode"],'
        ' "action": "open_app", "params": {"path": "C:/x.exe"}}], "automations": []}',
        encoding="utf-8",
    )
    data = CustomStore(path, reserved_phrases=RESERVED)
    assert data.snapshot()["commands"] == []


def test_duplicate_triggers_inside_the_json_file_are_dropped(tmp_path):
    path = tmp_path / "custom_commands.json"
    path.write_text(
        '{"version": 1, "commands": ['
        '{"name": "one", "triggers": ["open notes"], "action": "open_app",'
        ' "params": {"path": "C:/one.exe"}},'
        '{"name": "two", "triggers": ["open notes"], "action": "open_app",'
        ' "params": {"path": "C:/two.exe"}}], "automations": []}',
        encoding="utf-8",
    )
    data = CustomStore(path, reserved_phrases=RESERVED)
    assert [entry["name"] for entry in data.snapshot()["commands"]] == ["one"]


# -- execution --------------------------------------------------------------
def test_open_app_missing_path_is_reported():
    executor = ActionExecutor(app=None)
    ok, message = executor.open_app(r"C:\definitely\missing\nova.exe")
    assert ok is False
    assert "not found" in message.lower()


def test_open_app_uses_a_list_and_tracks_the_process(tmp_path):
    app_file = tmp_path / "editor.exe"
    app_file.write_text("stub")

    class FakeProcess:
        pid = 4321

        def poll(self):
            return None

    launched = []

    def launcher(path, args):
        launched.append((str(path), list(args)))
        return FakeProcess()

    executor = ActionExecutor(app=None, app_launcher=launcher)
    ok, message = executor.open_app(str(app_file), ["--safe"])
    assert ok is True
    assert launched == [(str(app_file), ["--safe"])]
    assert executor._launched[str(app_file).lower()]


def test_close_app_refuses_untracked_processes(tmp_path):
    app_file = tmp_path / "editor.exe"
    app_file.write_text("stub")
    executor = ActionExecutor(app=None)
    ok, message = executor.close_app(str(app_file))
    assert ok is False
    assert "did not start" in message.lower()


def test_close_tracked_app_uses_graceful_taskkill_without_force(tmp_path, monkeypatch):
    app_file = tmp_path / "editor.exe"
    app_file.write_text("stub")

    class FakeProcess:
        pid = 9876

        def __init__(self):
            self.waited = False

        def poll(self):
            return None

        def wait(self, timeout=None):
            self.waited = True
            return 0

    calls = []

    def fake_run(args, **kwargs):
        calls.append((args, kwargs))
        return None

    monkeypatch.setattr(custom.os, "name", "nt")
    monkeypatch.setattr(custom.subprocess, "run", fake_run)
    executor = ActionExecutor(app=None)
    process = FakeProcess()
    executor._launched[str(app_file).lower()] = [process]
    ok, message = executor.close_app(str(app_file))
    assert ok is True
    assert process.waited is True
    assert calls and calls[0][0] == ["taskkill", "/PID", "9876"]
    assert "/F" not in calls[0][0]


def test_invalid_url_action_fails_safely():
    executor = ActionExecutor(app=None)
    ok, message = executor.open_url("javascript:alert(1)")
    assert ok is False
    assert "invalid" in message.lower()


def test_batch_files_use_cmd_exe_without_a_shell(tmp_path, monkeypatch):
    batch = tmp_path / "start.bat"
    batch.write_text("echo hello", encoding="utf-8")
    calls = []

    class FakeProcess:
        pid = 111

    def fake_popen(args, **kwargs):
        calls.append((args, kwargs))
        return FakeProcess()

    monkeypatch.setattr(custom.subprocess, "Popen", fake_popen)
    executor = ActionExecutor(app=None)
    ok, message = executor.open_app(str(batch), ["one"])
    assert ok is True
    assert calls[0][0] == ["cmd.exe", "/c", str(batch), "one"]
    assert "shell" not in calls[0][1]


def test_unsupported_application_type_is_rejected(tmp_path):
    script = tmp_path / "script.ps1"
    script.write_text("Write-Host unsafe", encoding="utf-8")
    executor = ActionExecutor(app=None)
    ok, message = executor.open_app(str(script))
    assert ok is False
    assert "unsupported" in message.lower()


def test_custom_trigger_uses_the_models_spoken_form(tmp_path):
    store = CustomStore(tmp_path / "custom_commands.json", reserved_phrases=RESERVED)
    saved = store.save_command({
        "name": "Unmute music",
        "triggers": ["unmute music"],
        "action": "open_url",
        "params": {"url": "https://music.example"},
    })
    assert store.match("unmute music")["id"] == saved["id"]
    assert store.match("un mute music")["id"] == saved["id"]


def test_test_phrase_reports_a_match_without_executing(tmp_path):
    store = CustomStore(tmp_path / "custom_commands.json", reserved_phrases=RESERVED)
    store.save_command(command())
    runtime = NovaRuntime.__new__(NovaRuntime)
    runtime.commands = SimpleNamespace(registry=build_builtin_registry())
    runtime.custom = store

    result = runtime.test_phrase("Open my code editor.")
    assert result["matched"] is True
    assert result["kind"] == "command"
    assert result["name"] == "Open editor"

    builtin = runtime.test_phrase("Study mode")
    assert builtin["matched"] is True
    assert builtin["kind"] == "builtin"
    assert builtin["action"] == "study"

    missing = runtime.test_phrase("make me a sandwich")
    assert missing["matched"] is False
    assert "No enabled" in missing["reason"]


# -- automation ordering and duplicate prevention ---------------------------
class RecordingExecutor:
    def __init__(self):
        self.calls = []

    def execute(self, step):
        self.calls.append(step["action"])
        return True, "ok"


def test_automation_runs_steps_in_order():
    executor = RecordingExecutor()
    runner = AutomationRunner(executor)
    automation = {
        "id": "a1",
        "name": "Study",
        "steps": [
            {"action": "activate_mode", "params": {"mode": "study"}},
            {"action": "open_app", "params": {"path": "C:/x.exe"}},
            {"action": "open_url", "params": {"url": "https://example.com"}},
            {"action": "wait", "params": {"seconds": 1}},
        ],
    }
    results = runner.run_sequence(automation)
    assert [ok for ok, _ in results] == [True, True, True, True]
    assert executor.calls == ["activate_mode", "open_app", "open_url", "wait"]


def test_automation_delays_are_honoured(monkeypatch):
    sleeps = []
    monkeypatch.setattr(custom.time, "sleep", lambda seconds: sleeps.append(seconds))
    runner = AutomationRunner(RecordingExecutor())
    runner.run_sequence({
        "id": "a2",
        "steps": [
            {"action": "activate_mode", "params": {"mode": "study"}, "delay": 0.5},
            {"action": "wait", "params": {"seconds": 2}, "delay": 1.25},
        ],
    })
    assert sleeps == [0.5, 1.25]


def test_automation_cannot_run_twice_simultaneously():
    gate = threading.Event()

    class BlockingExecutor:
        def execute(self, step):
            gate.wait(2.0)
            return True, "ok"

    runner = AutomationRunner(BlockingExecutor())
    automation = {
        "id": "busy",
        "name": "Busy",
        "steps": [{"action": "wait", "params": {"seconds": 0}}],
    }
    assert runner.run(automation) is True
    assert runner.run(automation) is False
    gate.set()
    deadline = time.monotonic() + 2.0
    while runner.is_running("busy") and time.monotonic() < deadline:
        time.sleep(0.01)
    assert runner.is_running("busy") is False
