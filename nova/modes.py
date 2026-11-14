"""Study, Entertainment and Stock modes: editable lists of websites and applications."""
from __future__ import annotations

import logging
import os
import subprocess
import webbrowser
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlparse

from .config import ROOT

log = logging.getLogger("nova.modes")


def normalize_url(value: str) -> str | None:
    """Return a safe http(s) URL, or None. Bare hosts become https:// URLs."""
    if not isinstance(value, str):
        return None
    text = value.strip()
    if not text or any(ch.isspace() for ch in text):
        return None
    if "://" not in text:
        # Reject explicit non-web schemes such as javascript:, mailto: or data:
        # while still allowing host:port forms like localhost:4096.
        head = text.split(":", 1)[0].lower()
        if ":" in text and "." not in head and head not in {"localhost", "127", "[::1]"}:
            return None
        text = "https://" + text
    parsed = urlparse(text)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return None
    return text


def _open_url(url: str) -> None:
    webbrowser.open(url, new=2)


def _launch_app(path: Path, args: list[str]) -> None:
    if path.suffix.lower() in {".lnk", ".url"}:
        os.startfile(str(path))  # noqa: S606 - launching a user-configured shortcut
        return
    subprocess.Popen([str(path), *args], close_fds=True)


@dataclass
class ModeResult:
    opened: int = 0
    failures: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.opened > 0


class ModeManager:
    def __init__(self, modes: dict, speech, notify=None, url_opener=None, app_launcher=None):
        self.modes = modes or {}
        self.speech = speech
        self.notify = notify or (lambda title, body: None)
        self.url_opener = url_opener or _open_url
        self.app_launcher = app_launcher or _launch_app
        self.current_mode = "normal"

    def reload(self, modes: dict) -> None:
        self.modes = modes or {}

    def activate(self, mode_name: str) -> bool:
        mode = self.modes.get(mode_name)
        if not isinstance(mode, dict):
            self.speech.say(f"I could not find the {mode_name} mode configuration, sir.")
            return False

        result = ModeResult()
        for raw_url in mode.get("urls", []):
            url = normalize_url(raw_url)
            if url is None:
                result.failures.append(f"Rejected invalid website: {raw_url}")
                continue
            try:
                self.url_opener(url)
                result.opened += 1
            except Exception as exc:
                log.exception("Could not open URL %s", url)
                result.failures.append(f"Could not open {url}: {exc}")

        for entry in mode.get("apps", []):
            if not isinstance(entry, dict) or not isinstance(entry.get("path"), str):
                result.failures.append("Ignored an invalid application entry.")
                continue
            try:
                path = Path(entry["path"]).expanduser()
                if not path.is_absolute():
                    path = (ROOT / path).resolve()
                if not path.exists():
                    result.failures.append(f"Application not found: {path}")
                    continue
                args = entry.get("args", [])
                if not isinstance(args, list) or not all(isinstance(a, str) for a in args):
                    args = []
                self.app_launcher(path, args)
                result.opened += 1
            except Exception as exc:
                log.exception("Could not launch application %s", entry)
                result.failures.append(f"Could not launch {entry.get('path')}: {exc}")

        if result.opened:
            self.current_mode = mode_name
            greeting = mode.get("greeting") or f"{mode_name.title()} mode is ready, sir."
            self.speech.say(greeting)
            body = f"{mode_name.title()} mode opened {result.opened} item(s)."
            if result.failures:
                body += f"\n{len(result.failures)} item(s) could not be opened — see the log."
                log.warning("Mode failures: %s", result.failures)
            self.notify("NOVA mode activated", body)
            return True

        self.speech.say(
            f"I could not open anything in {mode_name} mode, sir. Please check its settings."
        )
        if result.failures:
            log.warning("Mode activation failed: %s", result.failures)
        return False
