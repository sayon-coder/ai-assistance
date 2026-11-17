"""The session-only Commands log: heard transcript, interpretation, timing."""
from __future__ import annotations

import json
import threading
from types import SimpleNamespace

from talcon.bridge import UiBridge
from talcon.runtime import TalconRuntime


def make_log():
    return SimpleNamespace(
        _command_log=[],
        _command_log_lock=threading.Lock(),
        _next_command_id=1,
        status={"last_heard": "open crate"},
        bridge=UiBridge(),
    )


def record(target, **payload):
    TalconRuntime._record_command(target, json.dumps(payload))


def test_command_log_pairs_what_was_heard_with_what_ran():
    target = make_log()
    record(target, phrase="open krite", action="app_opened", ms=42,
           confidence=0.61)
    entries = TalconRuntime.command_log(target)
    assert len(entries) == 1
    entry = entries[0]
    assert entry["heard"] == "open crate"
    assert entry["phrase"] == "open krite"
    assert entry["action"] == "app_opened"
    assert entry["ms"] == 42
    assert entry["confidence"] == 0.61
    assert entry["time"]


def test_malformed_payloads_are_ignored_and_the_log_is_bounded():
    target = make_log()
    TalconRuntime._record_command(target, "not json")
    TalconRuntime._record_command(target, "[]")
    assert TalconRuntime.command_log(target) == []
    for index in range(130):
        record(target, phrase=f"phrase {index}", action="time", ms=index)
    entries = TalconRuntime.command_log(target)
    assert len(entries) == 100
    assert entries[-1]["phrase"] == "phrase 129"
    assert entries[0]["id"] == 31


def test_clearing_the_log_keeps_result_copies_independent():
    target = make_log()
    record(target, phrase="open chrome", action="app_opened", ms=5)
    snapshot = TalconRuntime.command_log(target)
    TalconRuntime.clear_command_log(target)
    assert TalconRuntime.command_log(target) == []
    assert snapshot[0]["phrase"] == "open chrome"
