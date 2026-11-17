"""Thin, replaceable client for the locally-installed Hermes Agent API.

Everything in this module talks to a Hermes server over plain HTTP using only
the Python standard library.  The rest of TALCON depends on this one file, so
a future backend can be swapped by replacing the client without touching the
voice, command or UI layers.

Privacy rules baked in here:

* Only loopback hosts are ever contacted (127.0.0.1 / localhost / ::1).  A
  non-loopback host raises :class:`HermesConfigError` before any socket opens.
* The bearer key is never placed in an exception message, a log line or a
  ``repr``; :meth:`HermesClient.redact` scrubs it from anything that leaves
  this module.
* No audio bytes are sent — the API accepts and returns text only, and that
  restriction lives in the callers, not the transport.
"""
from __future__ import annotations

import json
import logging
import os
import socket
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

log = logging.getLogger("talcon.hermes")

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8642
DEFAULT_MODEL = "hermes-agent"

#: The only hosts this client will ever contact.
LOOPBACK_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})


class HermesError(Exception):
    """Base error; messages from this module never contain the API key."""


class HermesConfigError(HermesError):
    """Bad host, missing key or another local configuration problem."""


class HermesUnavailable(HermesError):
    """The server could not be reached (not running, wrong port, refused)."""


class HermesAuthError(HermesError):
    """The server rejected the bearer key (401/403)."""


class HermesHTTPError(HermesError):
    def __init__(self, status: int, detail: str = ""):
        super().__init__(f"Hermes API returned HTTP {status}"
                         + (f": {detail}" if detail else ""))
        self.status = int(status)
        self.detail = detail


def _clean(value: str) -> str:
    return str(value or "").strip().strip('"').strip("'").rstrip("\r\n")


def read_hermes_env_files(home: str | None = None) -> dict:
    """Merge the documented Hermes ``.env`` locations (first hit wins).

    CRLF-terminated values are normalised here so a Windows-edited file can
    never smuggle a stray ``\\r`` into the API key or port.
    """
    home = Path(home or Path.home())
    candidates = [
        home / ".hermes" / ".env",
        home / "AppData" / "Local" / "hermes" / ".env",
    ]
    merged: dict[str, str] = {}
    for path in candidates:
        try:
            if not path.is_file():
                continue
            for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, value = line.split("=", 1)
                merged.setdefault(_clean(key), _clean(value))
        except OSError:
            continue
    return merged


def resolve_api_key(explicit: str = "", env: dict | None = None,
                    home: str | None = None) -> tuple[str, str]:
    """Return ``(key, source)`` using the documented precedence.

    ``HERMES_API_KEY`` overrides the Hermes gateway's own ``API_SERVER_KEY``,
    which in turn may live either in the process environment or one of the
    two local ``.env`` files.
    """
    env = dict(env if env is not None else os.environ)
    explicit = _clean(explicit)
    if explicit:
        return explicit, "argument"
    for name in ("HERMES_API_KEY", "API_SERVER_KEY"):
        value = _clean(env.get(name, ""))
        if value:
            return value, f"environment:{name}"
    file_vars = read_hermes_env_files(home)
    for name in ("HERMES_API_KEY", "API_SERVER_KEY"):
        value = file_vars.get(name, "")
        if value:
            return value, f"file:{name}"
    return "", ""


def is_loopback_host(host: str) -> bool:
    host = _clean(host)
    if host.lower() in LOOPBACK_HOSTS:
        return True
    if host.startswith("127."):
        try:
            return socket.inet_aton(host) is not None
        except OSError:
            return False
    return False


def _normalize_host(host: str) -> str:
    host = _clean(host) or DEFAULT_HOST
    if host.lower() == "localhost":
        return "127.0.0.1"  # avoid a resolver hop (and any hosts-file surprises)
    return host


class HermesClient:
    """Blocking HTTP client for the loopback Hermes API server."""

    #: seconds to wait for the server to accept a connection
    CONNECT_TIMEOUT = 4.0
    #: default read timeout for ordinary JSON calls
    REQUEST_TIMEOUT = 30.0
    #: how long an SSE read may stay silent before we re-check cancellation
    STREAM_POLL = 0.5

    def __init__(self, host: str = DEFAULT_HOST, port: int = DEFAULT_PORT,
                 model: str = DEFAULT_MODEL, api_key: str = "",
                 env: dict | None = None, home: str | None = None,
                 opener=None):
        self.host = _normalize_host(host)
        if not is_loopback_host(self.host):
            raise HermesConfigError(
                f"Refusing to contact non-loopback Hermes host {self.host!r}; "
                "TALCON only talks to a local Hermes server."
            )
        try:
            self.port = int(port)
        except (TypeError, ValueError):
            raise HermesConfigError("Hermes port must be a number") from None
        if not (0 < self.port < 65536):
            raise HermesConfigError("Hermes port must be between 1 and 65535")
        self.model = _clean(model) or DEFAULT_MODEL
        key, source = resolve_api_key(api_key, env=env, home=home)
        if not key:
            raise HermesConfigError(
                "No Hermes API key found (set HERMES_API_KEY or API_SERVER_KEY "
                "in the environment or in ~/.hermes/.env)."
            )
        if len(key) < 16:
            raise HermesConfigError(
                f"The Hermes API key from {source or 'configuration'} is too "
                "short to be a real secret."
            )
        self._key = key
        self.key_source = source
        self._opener = opener or urllib.request.build_opener()

    # -- basics ------------------------------------------------------------
    @property
    def base_url(self) -> str:
        return f"http://{self.host}:{self.port}"

    def __repr__(self) -> str:  # never leak the key through logs/tracebacks
        return (f"HermesClient(host={self.host!r}, port={self.port!r}, "
                f"model={self.model!r}, key=<{self.key_source} redacted>")

    __str__ = __repr__

    def redact(self, text) -> str:
        text = str(text or "")
        return text.replace(self._key, "<redacted>")

    # -- transport ---------------------------------------------------------
    def _request(self, method: str, path: str, payload=None,
                 timeout: float | None = None, headers: dict | None = None,
                 stream: bool = False):
        url = self.base_url + path
        data = None
        request_headers = {
            "Authorization": f"Bearer {self._key}",
            "Accept": "text/event-stream" if stream else "application/json",
        }
        if payload is not None:
            data = json.dumps(payload).encode("utf-8")
            request_headers["Content-Type"] = "application/json"
        request_headers.update(headers or {})
        req = urllib.request.Request(url, data=data, headers=request_headers,
                                     method=method)
        try:
            return self._opener.open(req, timeout=timeout or self.REQUEST_TIMEOUT)
        except urllib.error.HTTPError as exc:
            body = ""
            try:
                body = exc.read(400).decode("utf-8", "replace")
            except Exception:
                pass
            if exc.code in (401, 403):
                raise HermesAuthError(
                    "The Hermes server rejected TALCON's API key."
                ) from None
            raise HermesHTTPError(exc.code, self.redact(body)[:200]) from None
        except urllib.error.URLError as exc:
            reason = getattr(exc, "reason", exc)
            if isinstance(reason, socket.timeout):
                raise HermesUnavailable(
                    f"Hermes at {self.host}:{self.port} took too long to respond."
                ) from None
            raise HermesUnavailable(
                f"Cannot reach Hermes at {self.host}:{self.port}: "
                f"{self.redact(reason)}"
            ) from None
        except (TimeoutError, socket.timeout):
            raise HermesUnavailable(
                f"Hermes at {self.host}:{self.port} took too long to respond."
            ) from None
        except OSError as exc:
            raise HermesUnavailable(
                f"Cannot reach Hermes at {self.host}:{self.port}: "
                f"{self.redact(exc)}"
            ) from None

    def _json(self, method: str, path: str, payload=None,
              timeout: float | None = None) -> dict:
        with self._request(method, path, payload=payload, timeout=timeout) as resp:
            raw = resp.read(2_000_000).decode("utf-8", "replace")
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            raise HermesError("Hermes returned invalid JSON on " + path) from None
        return self._redact_tree(data)

    def _redact_tree(self, data):
        key = self._key
        if isinstance(data, str):
            return data.replace(key, "<redacted>")
        if isinstance(data, dict):
            return {k: self._redact_tree(v) for k, v in data.items()}
        if isinstance(data, list):
            return [self._redact_tree(v) for v in data]
        return data

    # -- discovery ---------------------------------------------------------
    def health(self, timeout: float = 4.0) -> dict:
        return self._json("GET", "/health", timeout=timeout)

    def capabilities(self, timeout: float = 6.0) -> dict:
        return self._json("GET", "/v1/capabilities", timeout=timeout)

    def models(self, timeout: float = 6.0) -> list[str]:
        data = self._json("GET", "/v1/models", timeout=timeout)
        return [str(item.get("id")) for item in data.get("data", [])
                if item.get("id")]

    # -- plain chat (simple questions) --------------------------------------
    def chat(self, messages: list[dict], timeout: float = 90.0) -> str:
        payload = {"model": self.model, "messages": list(messages)}
        data = self._json("POST", "/v1/chat/completions", payload, timeout=timeout)
        choices = data.get("choices") or []
        if not choices:
            raise HermesError("Hermes returned no completion choices.")
        message = choices[0].get("message") or {}
        return str(message.get("content") or "").strip()

    # -- agent runs (multi-step, tools, approvals) ---------------------------
    def submit_run(self, input_text: str, idempotency_key: str = "",
                   timeout: float = 15.0) -> dict:
        headers = {"Idempotency-Key": idempotency_key} if idempotency_key else None
        with self._request("POST", "/v1/runs", payload={"input": str(input_text)},
                           timeout=timeout, headers=headers) as resp:
            body = resp.read(200_000).decode("utf-8", "replace")
        try:
            data = json.loads(body)
        except json.JSONDecodeError:
            raise HermesError("Hermes returned invalid JSON for the run request.") from None
        if not data.get("run_id"):
            raise HermesError("Hermes did not return a run id.")
        return self._redact_tree(data)

    def run_status(self, run_id: str, timeout: float = 10.0) -> dict:
        return self._json("GET", f"/v1/runs/{run_id}", timeout=timeout)

    def stop_run(self, run_id: str, timeout: float = 10.0) -> dict:
        return self._json("POST", f"/v1/runs/{run_id}/stop", payload={},
                          timeout=timeout)

    def respond_approval(self, run_id: str, approved: bool,
                         approval_id: str = "", timeout: float = 10.0) -> dict:
        payload: dict = {"approved": bool(approved),
                         "decision": "allow" if approved else "deny"}
        if approval_id:
            payload["approval_id"] = str(approval_id)
        return self._json("POST", f"/v1/runs/{run_id}/approval", payload,
                          timeout=timeout)

    def open_event_stream(self, run_id: str):
        """Open a raw, individually-interruptible SSE connection.

        ``http.client`` poisons a response forever after one socket timeout
        ("cannot read from timed out object"), so the event stream needs its
        own socket where a timeout simply means "no data yet".
        """
        import http.client

        conn = http.client.HTTPConnection(self.host, self.port,
                                          timeout=self.CONNECT_TIMEOUT)
        conn.connect()
        sock = conn.sock
        sock.settimeout(self.CONNECT_TIMEOUT)
        request = (
            f"GET /v1/runs/{run_id}/events HTTP/1.1\r\n"
            f"Host: {self.host}:{self.port}\r\n"
            f"Authorization: Bearer {self._key}\r\n"
            f"Accept: text/event-stream\r\n"
            f"\r\n"
        )
        sock.sendall(request.encode())
        # Read headers byte-by-byte so a body byte is never swallowed here.
        head = b""
        while b"\r\n\r\n" not in head:
            chunk = sock.recv(1)
            if not chunk:
                conn.close()
                raise HermesError("Hermes closed the event stream before "
                                  "answering.")
            head += chunk
        status_line = head.split(b"\r\n", 1)[0].decode("latin-1")
        try:
            code = int(status_line.split(" ", 2)[1])
        except (IndexError, ValueError):
            conn.close()
            raise HermesError("Hermes sent an invalid event-stream response.")
        if code == 404:
            conn.close()
            raise HermesHTTPError(404, "run not found")
        if code in (401, 403):
            conn.close()
            raise HermesAuthError("The Hermes server rejected TALCON's API key.")
        if code != 200:
            conn.close()
            raise HermesHTTPError(code, "event stream")
        sock.settimeout(self.STREAM_POLL)
        return conn, sock

    def stream_run_events(self, run_id: str, on_event,
                          cancel: threading.Event | None = None,
                          max_seconds: float = 300.0,
                          idle_seconds: float = 45.0) -> bool:
        """Replay/follow one run's SSE event stream, calling ``on_event``.

        Returns ``True`` when the stream ended because the run reached a
        terminal event (or closed cleanly), ``False`` when cancelled, out of
        time, or silent for ``idle_seconds``.  Silence on the wire re-checks
        the cancel flag, so a long tool step never looks like a dead
        connection, but a truly stalled run is abandoned instead of hanging.
        """
        terminal_seen = False
        deadline = _monotonic() + max_seconds
        last_data = _monotonic()
        conn = sock = None
        try:
            conn, sock = self.open_event_stream(run_id)
            pending = b""
            eof_hits = 0
            while True:
                now = _monotonic()
                if cancel is not None and cancel.is_set():
                    break
                if now > deadline:
                    break
                if idle_seconds and now - last_data > idle_seconds:
                    log.warning("Hermes run %s went silent for %.0fs; giving up",
                                run_id, idle_seconds)
                    break
                try:
                    chunk = sock.recv(65536)
                except (socket.timeout, TimeoutError):
                    continue
                except OSError as exc:
                    if _monotonic() > deadline:
                        break
                    log.debug("Hermes event stream read error: %s", self.redact(exc))
                    break
                if not chunk:
                    eof_hits += 1
                    if eof_hits >= 2:
                        terminal_seen = True  # server hung up: stream finished
                        break
                    continue
                pending += chunk
                last_data = _monotonic()
                while b"\n" in pending:
                    line, pending = pending.split(b"\n", 1)
                    text = line.decode("utf-8", "replace").strip()
                    if not text.startswith("data:"):
                        continue
                    body = text.split(":", 1)[1].strip()
                    if not body:
                        continue
                    if body == "[DONE]":
                        terminal_seen = True
                        continue
                    try:
                        event = json.loads(body)
                    except json.JSONDecodeError:
                        continue
                    event = self._redact_tree(event)
                    name = str(event.get("event") or "")
                    if name in ("run.completed", "run.failed", "run.cancelled"):
                        terminal_seen = True
                    on_event(event)
                if terminal_seen:
                    break  # the server may hold the socket open after the end
        finally:
            for closer in (lambda: sock and sock.close(),
                           lambda: conn and conn.close()):
                try:
                    closer()
                except Exception:
                    pass
        return terminal_seen


def _monotonic() -> float:
    return time.monotonic()
