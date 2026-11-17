"""Study, Entertainment and Stock modes: editable lists of websites and applications."""
from __future__ import annotations

import logging
import webbrowser
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlparse

from .apps import launch_app, normalize_app_entry
from .config import ROOT

log = logging.getLogger("talcon.modes")


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
        # Kept injectable for tests; None means use the shared descriptor launcher.
        self.app_launcher = app_launcher
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
            clean = normalize_app_entry(entry)
            if clean is None:
                result.failures.append("Ignored an invalid application entry.")
                continue
            try:
                if self.app_launcher is not None and clean["kind"] != "packaged_app":
                    path = Path(clean["path"]).expanduser()
                    if not path.is_absolute():
                        path = (ROOT / path).resolve()
                    if not path.exists():
                        raise FileNotFoundError(f"Application not found: {path}")
                    self.app_launcher(path, list(clean["args"]))
                else:
                    launch_app(clean)
                result.opened += 1
            except Exception as exc:
                log.exception("Could not launch application %s", entry)
                result.failures.append(
                    f"Could not launch {clean.get('name') or clean.get('path')}: {exc}"
                )

        if result.opened:
            self.current_mode = mode_name
            title = str(mode.get("title") or mode_name.replace("_", " ").title())
            greeting = mode.get("greeting") or f"{title} mode is ready, sir."
            self.speech.say(greeting)
            if result.failures:
                log.warning("Mode failures: %s", result.failures)
            return True

        title = str(mode.get("title") or mode_name.replace("_", " ").title())
        self.speech.say(
            f"I could not open anything in {title} mode, sir. Please check its settings."
        )
        if result.failures:
            log.warning("Mode activation failed: %s", result.failures)
        return False
