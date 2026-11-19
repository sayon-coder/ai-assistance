"""Find, start and probe the locally-installed Hermes Agent gateway.

TALCON talks to Hermes over the loopback API server that ships with the
Hermes Agent.  A machine can have Hermes installed and configured yet the
gateway not running (nothing listening on 127.0.0.1:8642), which used to be
reported as a bare "cannot reach the backend" message.  This module makes
that state recoverable:

* locate the installation without touching the network,
* start the real gateway launcher detached (the same one Hermes itself uses),
* wait for the API port to accept connections,
* report exactly what is wrong when it still cannot start.

Everything here is local-only.  No host other than a loopback address is
ever probed or launched.
"""
from __future__ import annotations

import logging
import os
import socket
import subprocess
import threading
import time
from pathlib import Path

log = logging.getLogger("talcon.hermes.service")

#: A cold gateway takes about 15-25 s on Windows before the API port opens.
DEFAULT_WAIT_SECONDS = 40.0
POLL_SECONDS = 0.4
CONNECT_TIMEOUT = 0.35

#: One start attempt at a time, and never more than once per minute: a user
#: pressing "Test connection" repeatedly must not spawn an army of gateways.
_START_LOCK = threading.Lock()
_LAST_START_ATTEMPT = 0.0
START_BACKOFF_SECONDS = 60.0


def _clean(value) -> str:
    return str(value or "").strip()


def home_candidates() -> list[Path]:
    """Every place a Hermes install is known to live on Windows."""
    candidates: list[Path] = []
    env_home = _clean(os.environ.get("HERMES_HOME"))
    if env_home:
        candidates.append(Path(env_home))
    local = _clean(os.environ.get("LOCALAPPDATA"))
    if local:
        candidates.append(Path(local) / "hermes")
    home = Path.home()
    candidates.append(home / "AppData" / "Local" / "hermes")
    candidates.append(home / ".hermes")
    return candidates


def hermes_home() -> Path | None:
    """The first candidate that really looks like a Hermes installation."""
    for path in home_candidates():
        try:
            if (path / "gateway-service").is_dir() or (path / "hermes-agent").is_dir():
                return path
        except OSError:
            continue
    return None


def launcher() -> Path | None:
    """The detached gateway launcher, preferring the windowless VBS one."""
    home = hermes_home()
    if home is None:
        return None
    service = home / "gateway-service"
    for name in ("Hermes_Gateway.vbs", "Hermes_Gateway.cmd"):
        candidate = service / name
        if candidate.is_file():
            return candidate
    return None


def installed() -> bool:
    return launcher() is not None


def is_listening(host: str = "127.0.0.1", port: int = 8642,
                 timeout: float = CONNECT_TIMEOUT) -> bool:
    """True when the loopback API port accepts a TCP connection right now."""
    text = str(host or "").strip().lower()
    if not (text in {"127.0.0.1", "localhost", "::1"} or text.startswith("127.")):
        # Privacy: never open a socket to anything but this PC, even for a
        # harmless liveness check.
        return False
    try:
        with socket.create_connection((str(host), int(port)), timeout=timeout):
            return True
    except OSError:
        return False


def _spawn(launcher_path: Path) -> bool:
    """Start the gateway detached, with no console window."""
    suffix = launcher_path.suffix.lower()
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    if suffix == ".vbs":
        command = ["wscript.exe", str(launcher_path)]
    elif suffix == ".cmd" or suffix == ".bat":
        command = ["cmd.exe", "/c", str(launcher_path)]
    else:
        command = [str(launcher_path)]
    try:
        subprocess.Popen(
            command,
            cwd=str(launcher_path.parent),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=flags,
            close_fds=True,
        )
        return True
    except Exception:
        log.exception("Could not start the Hermes gateway launcher")
        return False


def start_gateway(wait_seconds: float = DEFAULT_WAIT_SECONDS,
                  host: str = "127.0.0.1", port: int = 8642,
                  on_progress=None) -> tuple[bool, str]:
    """Start Hermes and wait for its API port.

    Returns ``(started, detail)``.  ``started`` means the port is accepting
    connections; the caller still performs an authorised health request.
    """
    global _LAST_START_ATTEMPT
    if is_listening(host, port):
        return True, "already running"
    launcher_path = launcher()
    if launcher_path is None:
        return False, (
            "Hermes is not installed on this PC, so the local AI gateway "
            "cannot be started."
        )
    with _START_LOCK:
        now = time.monotonic()
        if now - _LAST_START_ATTEMPT < 3.0:
            # Another caller is already waiting on the same launch.
            pass
        elif now - _LAST_START_ATTEMPT < START_BACKOFF_SECONDS:
            return False, (
                "The Hermes gateway was started moments ago but its API is "
                "still not answering; check the Hermes gateway log."
            )
        else:
            _LAST_START_ATTEMPT = now
            if not _spawn(launcher_path):
                return False, "The Hermes gateway launcher could not be started."
            log.info("Started the Hermes gateway from %s", launcher_path)

    if callable(on_progress):
        try:
            on_progress("Starting the local AI gateway…")
        except Exception:
            log.debug("Hermes start progress callback failed", exc_info=True)

    deadline = time.monotonic() + max(1.0, float(wait_seconds))
    announced = 0.0
    while time.monotonic() < deadline:
        if is_listening(host, port):
            log.info("Hermes API port %s:%s is accepting connections", host, port)
            return True, "started"
        now = time.monotonic()
        if callable(on_progress) and now - announced >= 4.0:
            announced = now
            try:
                on_progress("Waiting for the local AI gateway…")
            except Exception:
                pass
        time.sleep(POLL_SECONDS)
    return False, (
        "The Hermes gateway was started but its API did not open within "
        f"{int(wait_seconds)} seconds."
    )


def ensure_running(host: str = "127.0.0.1", port: int = 8642,
                   autostart: bool = True,
                   wait_seconds: float = DEFAULT_WAIT_SECONDS,
                   on_progress=None) -> tuple[bool, str]:
    """Make sure something is listening on the API port, starting Hermes if needed."""
    if is_listening(host, port):
        return True, "already running"
    if not autostart:
        return False, f"Nothing is listening on {host}:{port}."
    return start_gateway(wait_seconds=wait_seconds, host=host, port=port,
                         on_progress=on_progress)


def describe(host: str = "127.0.0.1", port: int = 8642) -> dict:
    """A UI-friendly snapshot of the installation and port state."""
    home = hermes_home()
    launcher_path = launcher()
    return {
        "installed": launcher_path is not None,
        "home": str(home) if home else "",
        "launcher": str(launcher_path) if launcher_path else "",
        "host": str(host),
        "port": int(port),
        "listening": is_listening(host, port),
    }


def probe(host: str = "127.0.0.1", port: int = 8642,
          start_if_down: bool = False,
          wait_seconds: float = DEFAULT_WAIT_SECONDS,
          on_progress=None) -> dict:
    """Return the real state of the local Hermes API, optionally starting it."""
    info = describe(host, port)
    info.update(reachable=False, started=False, version="", models=[],
                detail="")
    if not info["listening"] and start_if_down:
        started, detail = ensure_running(
            host, port, autostart=True, wait_seconds=wait_seconds,
            on_progress=on_progress,
        )
        info["started"] = bool(started)
        info["listening"] = is_listening(host, port)
        if not started:
            info["detail"] = detail
    if not info["listening"]:
        if not info["detail"]:
            info["detail"] = (
                f"Nothing is listening on {host}:{port}"
                + ("" if info["installed"] else "; Hermes is not installed")
            )
        return info
    try:
        from .hermes_client import HermesClient

        client = HermesClient(host=host, port=port, allow_keyless=True)
        health = client.health(timeout=4.0)
        info["reachable"] = True
        info["version"] = _clean(health.get("version"))
        try:
            info["models"] = client.models(timeout=5.0)
        except Exception:
            info["models"] = []
        info["detail"] = "ready"
    except Exception as exc:
        info["detail"] = str(exc)
    return info
