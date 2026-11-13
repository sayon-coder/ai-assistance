import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
import zstandard

from nova.monitors.codex import RolloutTail, RolloutTracker
from nova.monitors.opencode import OpenCodeEventTracker


# ---------------------------------------------------------------------------
# OpenCode event classification
# ---------------------------------------------------------------------------
def bus(event_type, **properties):
    return {"type": event_type, "properties": properties}


def test_opencode_requires_busy_before_announcing_completion():
    tracker = OpenCodeEventTracker()
    assert tracker.feed(bus("session.idle", sessionID="s1")) is None
    assert tracker.feed(bus("session.status", sessionID="s1", status={"type": "busy"}))[0] == "working"
    assert tracker.feed(bus("session.status", sessionID="s1", status={"type": "busy"})) is None
    kind, message, session = tracker.feed(bus("session.status", sessionID="s1", status={"type": "idle"}))
    assert (kind, session) == ("success", "s1")
    assert "completed" in message
    assert tracker.feed(bus("session.status", sessionID="s1", status={"type": "idle"})) is None


def test_opencode_idle_event_completes_a_busy_session():
    tracker = OpenCodeEventTracker()
    tracker.feed(bus("session.status", sessionID="s2", status={"type": "busy"}))
    result = tracker.feed(bus("session.idle", sessionID="s2"))
    assert result[0] == "success"


def test_opencode_error_is_a_failure():
    tracker = OpenCodeEventTracker()
    tracker.feed(bus("session.status", sessionID="s3", status={"type": "busy"}))
    kind, message, _ = tracker.feed(bus("session.error", sessionID="s3"))
    assert kind == "failure"
    assert "error" in message


def test_opencode_accepts_wrapped_and_legacy_payloads():
    tracker = OpenCodeEventTracker()
    wrapped = {"payload": bus("session.execution.succeeded", sessionID="s4")}
    assert tracker.feed(wrapped)[0] == "success"
    data_style = {"type": "session.status", "data": {"sessionID": "s5", "status": {"type": "busy"}}}
    assert tracker.feed(data_style)[0] == "working"


def test_opencode_sse_stream_end_to_end():
    """A real local SSE server: busy then idle must produce exactly one success."""
    frames = [
        {"type": "server.connected", "properties": {}},
        bus("session.status", sessionID="live", status={"type": "busy"}),
        bus("session.status", sessionID="live", status={"type": "idle"}),
    ]

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            if not self.path.startswith("/event"):
                self.send_error(404)
                return
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.end_headers()
            for frame in frames:
                self.wfile.write(f"data: {json.dumps(frame)}\n\n".encode())
                self.wfile.flush()
                time.sleep(0.05)
            time.sleep(1.0)

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    received = []
    stop = threading.Event()
    from nova.monitors.opencode import OpenCodeMonitor

    monitor = OpenCodeMonitor(
        f"http://127.0.0.1:{server.server_port}",
        lambda kind, message, session: (received.append((kind, session)), stop.set() if kind == "success" else None),
    )
    try:
        monitor.start()
        assert stop.wait(6.0), f"no completion event; received={received}"
        assert received[0][0] == "working"
        completed = [event for event in received if event[0] == "success"]
        assert len(completed) == 1
    finally:
        monitor.stop()
        server.shutdown()
        server.server_close()


# ---------------------------------------------------------------------------
# Codex rollout parsing
# ---------------------------------------------------------------------------
def event(kind, **payload):
    return {"timestamp": "2026-11-12T10:00:00Z", "type": "event_msg",
            "payload": {"type": kind, **payload}}


def test_codex_task_lifecycle_and_failure():
    tracker = RolloutTracker()
    result = tracker.feed(json.dumps(event("task_started", turn_id="t1")))
    assert result[0] == "working"
    result = tracker.feed(json.dumps(event("task_complete", turn_id="t1")))
    assert result[0] == "success"
    assert "completed" in result[1]

    result = tracker.feed(json.dumps(event(
        "task_complete", turn_id="t2", error={"message": "stream disconnected"}
    )))
    assert result[0] == "failure"

    result = tracker.feed(json.dumps(event("turn_aborted", turn_id="t3")))
    assert result[0] == "interrupted"


def test_codex_ignores_non_lifecycle_records():
    tracker = RolloutTracker()
    assert tracker.feed(json.dumps({"type": "turn_context", "payload": {}})) is None
    assert tracker.feed(json.dumps({"type": "event_msg", "payload": {"type": "token_count"}})) is None
    assert tracker.feed("not json") is None


def test_plain_rollout_tail_only_reads_new_lines(tmp_path):
    path = tmp_path / "rollout-2026-11-12T10-00-00-test.jsonl"
    path.write_text(json.dumps(event("task_started", turn_id="old")) + "\n", encoding="utf-8")
    tail = RolloutTail(path)
    try:
        assert tail.read_new() == []
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(event("task_complete", turn_id="old")) + "\n")
        lines = tail.read_new()
        assert len(lines) == 1
        assert b"task_complete" in lines[0]
    finally:
        tail.close()


def test_zstd_rollout_tail_reads_appended_frames(tmp_path):
    path = tmp_path / "rollout-2026-11-12T10-00-01-test.jsonl.zst"
    compressor = zstandard.ZstdCompressor()
    first = (json.dumps(event("task_started", turn_id="z1")) + "\n").encode()
    second = (json.dumps(event("task_complete", turn_id="z1")) + "\n").encode()
    path.write_bytes(compressor.compress(first))

    tail = RolloutTail(path)
    try:
        assert tail.read_new() == []
        with path.open("ab") as handle:
            handle.write(compressor.compress(second))
        lines = tail.read_new()
        assert len(lines) == 1
        assert json.loads(lines[0])["payload"]["type"] == "task_complete"
    finally:
        tail.close()


def test_codex_monitor_announces_only_new_turns(tmp_path, monkeypatch):
    import nova.monitors.codex as codex

    sessions = tmp_path / "sessions" / "2026" / "11" / "12"
    sessions.mkdir(parents=True)
    path = sessions / "rollout-2026-11-12T10-00-00-mon.jsonl"
    path.write_text(json.dumps(event("task_started", turn_id="before")) + "\n", encoding="utf-8")
    monkeypatch.setattr(codex, "sessions_root", lambda: tmp_path / "sessions")

    received = []
    done = threading.Event()

    def on_event(kind, message, session):
        received.append(kind)
        done.set()

    monitor = codex.CodexMonitor(lambda *args: on_event(*args))
    try:
        monitor.start()
        time.sleep(0.5)  # let it attach and skip existing lines
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(event("task_complete", turn_id="before")) + "\n")
        assert done.wait(6.0), f"completion not announced; got {received}"
        assert received == ["success"]
    finally:
        monitor.stop()
