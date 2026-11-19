"""Hermes API client tests against a local scripted stand-in server.

The stub server binds 127.0.0.1 on an ephemeral port; nothing here touches
the real Hermes installation.
"""
from __future__ import annotations

import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from talcon.hermes_client import (
    HermesAuthError, HermesClient, HermesConfigError, HermesUnavailable,
    is_loopback_host, read_hermes_env_files, resolve_api_key,
)

TEST_KEY = "unit-test-secret-key-0123456789"
EVENTS: dict[str, list] = {}


class StubHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.0"
    server: "StubServer"

    def log_message(self, *args):  # silence stderr during tests
        pass

    def _authorized(self) -> bool:
        return self.headers.get("Authorization") == f"Bearer {TEST_KEY}"

    def _send(self, code, payload, ctype="application/json"):
        data = json.dumps(payload).encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _json_body(self):
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length) if length else b"{}"
        try:
            return json.loads(raw.decode() or "{}")
        except json.JSONDecodeError:
            return {}

    def do_GET(self):
        if not self._authorized():
            self._send(401, {"error": "no"})
            return
        if self.path == "/health":
            self._send(200, {"status": "ok", "platform": "hermes-agent"})
        elif self.path == "/v1/capabilities":
            self._send(200, {"object": "hermes.api_server.capabilities",
                             "features": {"run_submission": True,
                                          "run_events_sse": True}})
        elif self.path == "/v1/models":
            self._send(200, {"data": [{"id": "hermes-agent"}]})
        elif self.path.startswith("/v1/runs/") and self.path.endswith("/events"):
            run_id = self.path.split("/")[3]
            if run_id == "hold":
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.end_headers()
                self.wfile.write(
                    f"data: {json.dumps({'event': 'message.delta', 'delta': 'x'})}\n\n".encode()
                )
                self.wfile.flush()
                time.sleep(5)
                return
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.end_headers()
            for event in EVENTS.get(run_id, []):
                self.wfile.write(f"data: {json.dumps(event)}\n\n".encode())
                self.wfile.flush()
            return
        elif self.path.startswith("/v1/runs/"):
            self._send(200, {"object": "hermes.run", "status": "completed",
                             "output": "from status"})
        elif self.path == "/leak":
            self._send(500, {"detail": f"bad {TEST_KEY}"})
        else:
            self._send(404, {"error": "not found"})

    def do_POST(self):
        if not self._authorized():
            self._send(401, {"error": "no"})
            return
        body = self._json_body()
        self.server.requests.append((self.path, dict(self.headers), body))
        if self.path == "/v1/chat/completions":
            if body.get("stream"):
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.end_headers()
                for piece in ("chat ", "answer"):
                    chunk = {"choices": [{"delta": {"content": piece}}]}
                    self.wfile.write(
                        f"data: {json.dumps(chunk)}\n\n".encode()
                    )
                    self.wfile.flush()
                self.wfile.write(b"data: [DONE]\n\n")
                self.wfile.flush()
                self.close_connection = True
                return
            self._send(200, {"choices": [{"message": {
                "role": "assistant", "content": "chat answer"}}]})
        elif self.path == "/v1/runs":
            self._send(202, {"run_id": "r1", "status": "started",
                             "replayed": False})
        elif self.path.endswith("/stop"):
            self._send(200, {"status": "cancelled"})
        elif self.path.endswith("/approval"):
            self._send(200, {"ok": True})
        else:
            self._send(404, {"error": "nope"})


@pytest.fixture(scope="module")
def stub_server():
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), StubHandler)
    httpd.requests = []
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    yield httpd
    httpd.shutdown()
    thread.join(timeout=5)


@pytest.fixture()
def client(stub_server):
    return HermesClient(host="127.0.0.1", port=stub_server.server_address[1],
                        api_key=TEST_KEY)


# -- configuration and privacy -------------------------------------------------
def test_non_loopback_host_is_refused_before_any_socket():
    with pytest.raises(HermesConfigError):
        HermesClient(host="8.8.8.8", port=8642, api_key=TEST_KEY)
    with pytest.raises(HermesConfigError):
        HermesClient(host="hermes.example.com", api_key=TEST_KEY)


def test_localhost_is_normalized_and_loopbacks_accepted():
    for host in ("localhost", "127.0.0.53", "::1"):
        c = HermesClient(host=host, api_key=TEST_KEY)
        assert c.host in {"127.0.0.1", "127.0.0.53", "::1"}
    assert is_loopback_host("127.0.0.1") and not is_loopback_host("10.0.0.1")


def test_missing_key_is_a_config_error():
    with pytest.raises(HermesConfigError):
        HermesClient(env={}, home="Z:/definitely-not-here")


def test_short_key_is_rejected():
    with pytest.raises(HermesConfigError):
        HermesClient(api_key="abc")


def test_repr_and_str_never_contain_the_key():
    c = HermesClient(api_key=TEST_KEY)
    assert TEST_KEY not in repr(c) and TEST_KEY not in str(c)
    assert TEST_KEY not in repr(c.base_url)


def test_error_messages_are_redacted(client):
    from talcon.hermes_client import HermesHTTPError
    with pytest.raises(HermesHTTPError) as excinfo:
        client._json("GET", "/leak")
    assert TEST_KEY not in str(excinfo.value)


def test_key_resolution_precedence(tmp_path, monkeypatch):
    hermes_dir = tmp_path / ".hermes"
    hermes_dir.mkdir()
    (hermes_dir / ".env").write_text(
        f"API_SERVER_KEY=file-secret-value-1234\r\nAPI_SERVER_PORT=8642\r\n",
        encoding="utf-8",
    )
    merged = read_hermes_env_files(home=str(tmp_path))
    assert merged["API_SERVER_KEY"] == "file-secret-value-1234"  # CR stripped
    key, source = resolve_api_key("", env={}, home=str(tmp_path))
    assert key == "file-secret-value-1234" and source.endswith("API_SERVER_KEY")
    key, source = resolve_api_key("", env={"API_SERVER_KEY": "x" * 32},
                                  home=str(tmp_path))
    assert key == "x" * 32 and source == "environment:API_SERVER_KEY"
    key, _ = resolve_api_key("override-secret-0123456789")
    assert key == "override-secret-0123456789"


# -- happy paths ----------------------------------------------------------------
def test_health_and_models_and_capabilities(client):
    assert client.health()["status"] == "ok"
    assert "hermes-agent" in client.models()
    assert client.capabilities()["features"]["run_submission"] is True


def test_chat_completion_returns_message_text(client):
    answer = client.chat([{"role": "user", "content": "hi"}])
    assert answer == "chat answer"


def test_chat_stream_reports_text_as_it_arrives(client):
    seen: list[str] = []
    answer = client.chat_stream(
        [{"role": "user", "content": "hi"}], on_delta=seen.append
    )
    assert answer == "chat answer"
    assert seen == ["chat ", "answer"]


def test_keyless_loopback_client_is_opt_in():
    with pytest.raises(HermesConfigError):
        HermesClient(env={}, home="Z:/definitely-not-here")
    client = HermesClient(env={}, home="Z:/definitely-not-here",
                          allow_keyless=True)
    assert client.key_source == "none"
    assert client.redact("plain text") == "plain text"


def test_run_submission_status_stop_and_approval(client, stub_server):
    stub_server.requests.clear()
    submission = client.submit_run("do it", idempotency_key="k-1")
    assert submission["run_id"] == "r1"
    path, headers, body = stub_server.requests[-1]
    assert path == "/v1/runs" and body == {"input": "do it"}
    assert headers.get("Idempotency-Key") == "k-1"
    assert client.run_status("r1")["status"] == "completed"
    assert client.stop_run("r1")["status"] == "cancelled"
    client.respond_approval("r1", True, "a9")
    path, _headers, body = stub_server.requests[-1]
    assert path == "/v1/runs/r1/approval" and body["approved"] is True
    assert body["approval_id"] == "a9"


def test_stream_events_reports_deltas_and_completion(client):
    EVENTS["r1"] = [
        {"event": "tool.started", "tool": "browser"},
        {"event": "message.delta", "delta": "Par"},
        {"event": "message.delta", "delta": "is"},
        {"event": "run.completed", "output": "Paris"},
    ]
    seen = []
    done = client.stream_run_events("r1", seen.append, max_seconds=10)
    assert done is True
    names = [event.get("event") for event in seen]
    assert names == ["tool.started", "message.delta", "message.delta",
                     "run.completed"]


def test_stream_is_cancelable(client):
    cancel = threading.Event()
    seen = []

    def on_event(event):
        seen.append(event)
        cancel.set()  # stop as soon as the first delta arrives

    done = client.stream_run_events("hold", on_event, cancel=cancel,
                                    max_seconds=8)
    assert done is False and seen and seen[0]["event"] == "message.delta"
    # The stream must not sit in the server's 5-second hold.
    started = time.monotonic()
    cancel2 = threading.Event()
    cancel2.set()
    client.stream_run_events("hold", lambda e: None, cancel=cancel2,
                             max_seconds=8)
    assert time.monotonic() - started < 2.0


def test_bad_port_and_bearer_auth_errors(stub_server):
    with pytest.raises(HermesConfigError):
        HermesClient(port=0, api_key=TEST_KEY)
    wrong = HermesClient(host="127.0.0.1",
                         port=stub_server.server_address[1],
                         api_key="wrong-key-0123456789ab")
    with pytest.raises(HermesAuthError):
        wrong.health()


def test_server_down_is_unavailable():
    # Port 1 is reserved and never accepts our request.
    c = HermesClient(port=1, api_key=TEST_KEY)
    started = time.monotonic()
    with pytest.raises(HermesUnavailable):
        c.health(timeout=2.0)
    assert time.monotonic() - started < 10
