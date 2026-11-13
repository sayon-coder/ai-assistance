"""OpenCode monitoring over its supported local event interface.

Verified against OpenCode v2.2.7 (2026-11) on this machine:

* The documented Server-Sent Events route is ``GET /api/event``. The bare
  ``/event`` path serves the desktop web UI's HTML on current builds, so it is
  only tried as a fallback (older builds exposed it).
* When OpenCode Desktop runs ``opencode-cli serve --service`` the route
  requires HTTP Basic authentication (``opencode`` + password). The password is
  generated locally by OpenCode and stored in
  ``~/.config/opencode/service.json`` — NOVA reads it from there and sends it
  only to ``127.0.0.1``.
* The desktop app picks its port dynamically. It is logged as
  ``url: 'http://127.0.0.1:<port>'`` inside
  ``%APPDATA%\\ai.opencode.desktop\\logs\\<session>\\main.log`` and is
  discovered from the newest log whenever the configured URL does not answer.
* Real frames look like::

      {"id":"evt_…","type":"session.step.started",
       "location":{"directory":"…"},"data":{"sessionID":"ses_…"}}

  so the session id lives under ``data`` (bus-style events use
  ``properties``; both are handled).

Nothing is ever inferred from a dropped connection: a turn is announced as
finished only after a real terminal event (``session.execution.succeeded`` /
``failed`` / ``interrupted`` / ``stopped``) or after the same session
previously reported ``busy`` and now reports ``idle``. All connections are
restricted to ``127.0.0.1`` / localhost so the stream can never leave the
machine or be reached from the LAN.
"""
from __future__ import annotations

import base64
import json
import logging
import re
import threading
from pathlib import Path
from urllib.parse import urlparse

import requests

log = logging.getLogger("nova.opencode")

SUCCESS_MESSAGE = (
    "your OpenCode task has been completed. You can review the changes now."
)
FAILURE_MESSAGE = (
    "your OpenCode task encountered an error. Please review the application."
)
INTERRUPTED_MESSAGE = "your OpenCode task was interrupted."

_LOCAL_HOSTS = {"127.0.0.1", "localhost", "::1"}
_EVENT_PATHS = ("/api/event", "/event")  # documented route first
_URL_LOG_REGEX = re.compile(r"http://127\.0\.0\.1:(\d{2,5})")


# ---------------------------------------------------------------------------
# local credential / endpoint discovery (127.0.0.1 only)
# ---------------------------------------------------------------------------
def discover_service_password() -> str:
    """Read the password OpenCode's own local service wrote to disk."""
    candidates = [
        Path.home() / ".config" / "opencode" / "service.json",
        Path.home() / "AppData" / "Roaming" / "opencode" / "service.json",
    ]
    for path in candidates:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            password = data.get("password")
            if isinstance(password, str) and password:
                log.info("OpenCode service password found in %s", path)
                return password
        except FileNotFoundError:
            continue
        except Exception as exc:  # a corrupt file must not break monitoring
            log.warning("Could not read %s: %s", path, exc)
    return ""


def discover_desktop_urls() -> list[str]:
    """Return base URLs logged by the newest OpenCode Desktop session."""
    root = Path.home() / "AppData" / "Roaming" / "ai.opencode.desktop" / "logs"
    try:
        sessions = sorted((p for p in root.iterdir() if p.is_dir()), reverse=True)[:2]
    except OSError:
        return []
    found: list[str] = []
    for session in sessions:
        try:
            text = (session / "main.log").read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for port in _URL_LOG_REGEX.findall(text):
            url = f"http://127.0.0.1:{port}"
            if url not in found:
                found.append(url)
        if found:
            break  # newest session first
    return found


def _auth_header(password: str) -> dict:
    if not password:
        return {}
    token = base64.b64encode(f"opencode:{password}".encode("utf-8")).decode("ascii")
    return {"Authorization": f"Basic {token}"}


# ---------------------------------------------------------------------------
# event classification
# ---------------------------------------------------------------------------
def _session_of(props: dict) -> str:
    value = props.get("sessionID") or props.get("sessionId")
    if isinstance(value, str) and value:
        return value
    return "default"


def _status_state(props: dict):
    """Extract busy/idle from old and new status payload shapes."""
    status = props.get("status")
    if isinstance(status, dict):
        return status.get("type")
    if isinstance(status, str):
        return status
    if props.get("busy") is True:
        return "busy"
    if props.get("idle") is True or props.get("state") == "idle":
        return "idle"
    return None


class OpenCodeEventTracker:
    """Turns OpenCode events into high-confidence task notifications."""

    def __init__(self):
        self._busy: set[str] = set()
        self._seen: set[tuple[str, str]] = set()

    def _once(self, session: str, kind: str) -> bool:
        key = (session, kind)
        if key in self._seen:
            return False
        if len(self._seen) > 300:
            self._seen.clear()
        self._seen.add(key)
        return True

    def feed(self, event) -> tuple[str, str, str] | None:
        if not isinstance(event, dict):
            return None
        # Bus-shaped events wrap the real payload under "payload".
        if isinstance(event.get("payload"), dict) and "type" in event["payload"]:
            event = event["payload"]
        kind = event.get("type")
        props = event.get("properties")
        if not isinstance(props, dict):
            props = event.get("data") if isinstance(event.get("data"), dict) else {}
        session = _session_of(props)

        if kind in {"session.status", "SessionStatusUpdated"} or str(kind).endswith(
            ".status"
        ):
            state = _status_state(props)
            if state == "busy":
                self._busy.add(session)
                if self._once(session, "working"):
                    return ("working", "OpenCode is working on a task.", session)
                return None
            if state == "idle":
                if session in self._busy:
                    self._busy.discard(session)
                    return ("success", SUCCESS_MESSAGE, session)
            return None

        if kind == "session.idle":
            if session in self._busy:
                self._busy.discard(session)
                return ("success", SUCCESS_MESSAGE, session)
            return None

        if kind in {"session.error", "session.execution.failed"}:
            self._busy.discard(session)
            return ("failure", FAILURE_MESSAGE, session)

        if kind == "session.execution.started":
            self._busy.add(session)
            if self._once(session, "working"):
                return ("working", "OpenCode is working on a task.", session)
            return None
        if kind == "session.execution.succeeded":
            self._busy.discard(session)
            return ("success", SUCCESS_MESSAGE, session)
        if kind in {"session.execution.interrupted", "session.execution.stopped"}:
            self._busy.discard(session)
            return ("interrupted", INTERRUPTED_MESSAGE, session)

        if kind in {"session.deleted", "server.instance.disposed"}:
            self._busy.discard(session)
        return None


# ---------------------------------------------------------------------------
# monitor
# ---------------------------------------------------------------------------
class OpenCodeMonitor:
    """Localhost-only SSE listener: auth, endpoint fallback, reconnect."""

    def __init__(self, base_url: str, on_event, status_callback=None,
                 password: str | None = None, auto_discover: bool = True):
        self.base_url = str(base_url or "").rstrip("/")
        self.on_event = on_event
        self.status_callback = status_callback or (lambda value: None)
        self.tracker = OpenCodeEventTracker()
        self._password = discover_service_password() if password is None else password
        self._auto_discover = bool(auto_discover)
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._response = None
        self._last_status = ""

    # -- lifecycle ---------------------------------------------------------
    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, name="NOVA-OpenCode", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        response = self._response
        if response is not None:
            try:
                response.close()
            except Exception:
                pass
        thread = self._thread
        if thread and thread.is_alive() and thread is not threading.current_thread():
            thread.join(timeout=2.0)

    def _status(self, message: str) -> None:
        if message == self._last_status:
            return
        self._last_status = message
        try:
            self.status_callback(message)
        except Exception:
            log.exception("OpenCode status callback failed")

    # -- connection --------------------------------------------------------
    def _validate_url(self, url: str) -> bool:
        try:
            parsed = urlparse(url)
        except ValueError:
            parsed = None
        if (
            parsed is None
            or parsed.scheme != "http"
            or (parsed.hostname or "") not in _LOCAL_HOSTS
        ):
            self._status("OpenCode blocked · use a local http://127.0.0.1 address")
            log.error("Refusing non-local OpenCode URL: %s", url)
            return False
        return True

    def _candidates(self) -> list[str]:
        """Configured URL first, then ports the desktop app actually logged."""
        ordered: list[str] = []
        if self.base_url:
            ordered.append(self.base_url)
        if self._auto_discover:
            for url in discover_desktop_urls():
                if url not in ordered:
                    ordered.append(url)
        return [url for url in ordered if self._validate_url(url)]

    def _open_stream(self, base: str):
        """Try /api/event then /event; return a live SSE response or None."""
        headers = {
            "Accept": "text/event-stream",
            "Cache-Control": "no-cache",
            **_auth_header(self._password),
        }
        auth_failed = False
        for path in _EVENT_PATHS:
            if self._stop.is_set():
                return None
            try:
                response = requests.get(
                    base + path, stream=True, timeout=(5, 50), headers=headers,
                )
            except Exception as exc:
                log.debug("OpenCode %s%s unreachable: %s", base, path, exc)
                continue
            if response.status_code == 401:
                response.close()
                auth_failed = True
                log.info("OpenCode %s%s requires authentication", base, path)
                break  # same server: trying the other path cannot help
            if response.status_code != 200:
                response.close()
                continue
            content_type = response.headers.get("content-type", "")
            if "text/event-stream" not in content_type:
                # On current builds the bare /event path serves the HTML app.
                response.close()
                log.info(
                    "OpenCode %s%s is not an event stream (%s)",
                    base, path, content_type or "no content-type",
                )
                continue
            return response
        if auth_failed:
            self._status("OpenCode authentication required · service password not found")
        return None

    def _loop(self) -> None:
        attempt = 0
        while not self._stop.is_set():
            connected = False
            for base in self._candidates():
                if self._stop.is_set():
                    break
                self._status("OpenCode connecting…")
                response = self._open_stream(base)
                if response is None:
                    continue
                self._response = response
                note = "authenticated" if self._password else "local"
                self._status(f"OpenCode connected · watching task events ({note})")
                log.info("OpenCode event stream connected: %s", base)
                attempt = 0
                connected = True
                try:
                    self._consume(response)
                except Exception as exc:
                    if not self._stop.is_set():
                        log.info("OpenCode event stream dropped: %s", exc)
                finally:
                    self._response = None
                    try:
                        response.close()
                    except Exception:
                        pass
                break
            if self._stop.is_set():
                break
            if not connected:
                if "authentication required" not in self._last_status:
                    self._status("OpenCode not running · retrying")
                delay = min(30, 2 * (2 ** min(attempt, 4)))
                attempt += 1
                self._stop.wait(delay)
                continue
            # Was connected, stream ended (server restart): retry quickly.
            self._stop.wait(1.0)

    def _consume(self, response) -> None:
        data_lines: list[str] = []
        for raw in response.iter_lines(decode_unicode=True, chunk_size=512):
            if self._stop.is_set():
                return
            if raw is None:
                continue
            line = raw.strip()
            if not line:
                if data_lines:
                    self._dispatch("\n".join(data_lines))
                    data_lines.clear()
                continue
            if line.startswith(":"):
                continue  # heartbeat comment
            if line.startswith("data:"):
                data_lines.append(line[5:].strip())
        if data_lines:
            self._dispatch("\n".join(data_lines))

    def _dispatch(self, payload: str) -> None:
        try:
            event = json.loads(payload)
        except Exception:
            log.debug("Ignoring non-JSON OpenCode frame: %s", payload[:200])
            return
        result = self.tracker.feed(event)
        if result is None:
            return
        kind, message, session = result
        log.info("OpenCode %s (%s)", kind, session)
        try:
            self.on_event(kind, message, session)
        except Exception:
            log.exception("OpenCode event callback failed")
