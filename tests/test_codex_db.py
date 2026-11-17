"""Modern Codex CLI turn database watching (read-only, no rollout files)."""
import sqlite3

from talcon.monitors.codex import (
    INTERRUPTED_MESSAGE, SUCCESS_MESSAGE, TurnDatabase, turn_database_paths,
)


def make_db(path):
    con = sqlite3.connect(path)
    con.execute(
        "create table thread_turns (turn_id text, status text, completed_at integer)"
    )
    con.execute("insert into thread_turns values ('old', 'completed', 100)")
    con.commit()
    con.close()


def test_turn_database_reports_only_turns_completed_after_start(tmp_path):
    path = tmp_path / "thread_history_1.sqlite"
    make_db(path)
    tail = TurnDatabase(path)
    try:
        assert tail.read_new() == []
        con = sqlite3.connect(path)
        con.execute("insert into thread_turns values ('done', 'completed', 200)")
        con.execute("insert into thread_turns values ('bad', 'failed', 201)")
        con.execute("insert into thread_turns values ('stop', 'interrupted', 202)")
        con.execute("insert into thread_turns values ('open', 'inProgress', null)")
        con.commit()
        con.close()

        events = tail.read_new()
        assert events == [
            ("success", SUCCESS_MESSAGE, "done"),
            ("failure", "your coding task encountered an error. Please review the application.", "bad"),
            ("interrupted", INTERRUPTED_MESSAGE, "stop"),
        ]
        assert tail.read_new() == [], "the same turn is never announced twice"
    finally:
        tail.close()


def test_database_discovery_keeps_standard_and_configured_codex_homes(tmp_path, monkeypatch):
    configured = tmp_path / "configured"
    standard = tmp_path / ".codex"
    configured.mkdir()
    standard.mkdir()
    first = configured / "thread_history_1.sqlite"
    second = standard / "thread_history_2.sqlite"
    make_db(first)
    make_db(second)

    monkeypatch.setattr("talcon.monitors.codex.codex_home", lambda: configured)
    monkeypatch.setattr("talcon.monitors.codex.Path.home", lambda: tmp_path)

    assert set(turn_database_paths()) == {first, second}
