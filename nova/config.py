"""Configuration and filesystem layout for NOVA.

All data lives beside the application (or in %LOCALAPPDATA%\\NOVA when the
application folder is read-only, e.g. a packaged executable in Program Files).
Nothing here touches the network.
"""
from __future__ import annotations

import json
import logging
import os
import sys
import tempfile
import time
from pathlib import Path

log = logging.getLogger("nova.config")

VERSION = "2.1.0"
APP_NAME = "NOVA"


def _writable(path: Path) -> bool:
    try:
        path.mkdir(parents=True, exist_ok=True)
        probe = path / ".nova_write_test"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
        return True
    except OSError:
        return False


def _app_root() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent


ROOT = _app_root()

# If the install folder cannot be written to, keep user data in LOCALAPPDATA.
_DATA_ROOT = ROOT
if not _writable(ROOT):
    _DATA_ROOT = Path(os.environ.get("LOCALAPPDATA", tempfile.gettempdir())) / APP_NAME
    _DATA_ROOT.mkdir(parents=True, exist_ok=True)

CONFIG_DIR = _DATA_ROOT / "config"
LOGS_DIR = _DATA_ROOT / "logs"
MODELS_DIR = _DATA_ROOT / "models"
ASSETS_DIR = ROOT / "assets"
SETTINGS_PATH = CONFIG_DIR / "settings.json"
MODES_PATH = CONFIG_DIR / "modes.json"
CUSTOM_COMMANDS_PATH = CONFIG_DIR / "custom_commands.json"

for _folder in (CONFIG_DIR, LOGS_DIR, MODELS_DIR):
    try:
        _folder.mkdir(parents=True, exist_ok=True)
    except OSError:
        log.warning("Could not create %s", _folder)

DEFAULT_SETTINGS: dict = {
    "schema_version": 2,
    "setup_complete": False,
    "assistant_name": "NOVA",
    # Voice input
    "wake_enabled": True,
    "always_listen": True,
    "vosk_model_path": "models/vosk-model-small-en-us-0.15",
    "microphone_device": None,
    # Voice output
    "voice_enabled": True,
    "speech_voice": "",
    "speech_rate": 0,
    "speech_volume": 90,
    # Face
    "show_face": True,
    "face_size": 66,
    "face_x": None,
    "face_y": None,
    "face_screen": "",
    # Monitoring
    "opencode_enabled": True,
    "opencode_base_url": "http://127.0.0.1:4096",
    "codex_enabled": True,
    "announce_interruptions": False,
    # Windows integration
    "start_with_windows": False,
    "wizard_version": 2,
}

DEFAULT_MODES: dict = {
    "study": {
        "greeting": "Welcome back, sir. Your study environment is ready.",
        "urls": ["https://www.google.com", "https://stackoverflow.com"],
        "apps": [],
    },
    "entertainment": {
        "greeting": "Certainly, sir. Your entertainment environment is ready.",
        "urls": [
            "https://www.youtube.com",
            "https://music.youtube.com",
            "https://open.spotify.com",
        ],
        "apps": [],
    },
}

_LEGACY_KEYS = ("porcupine_access_key", "porcupine_keyword_path")


def _write_json_unlocked(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    with temp.open("w", encoding="utf-8") as handle:
        json.dump(data, handle, indent=2, ensure_ascii=False)
        handle.write("\n")
    last_error: OSError | None = None
    for attempt in range(5):
        try:
            temp.replace(path)
            return
        except OSError as exc:  # OneDrive / antivirus can briefly lock the file
            last_error = exc
            time.sleep(0.12 * (attempt + 1))
    raise last_error if last_error else OSError(f"Could not write {path}")


def write_json(path: Path, data) -> None:
    _write_json_unlocked(Path(path), data)


def _read_json(path: Path, fallback: dict) -> dict:
    path = Path(path)
    if not path.exists():
        write_json(path, fallback)
        return json.loads(json.dumps(fallback))
    try:
        with path.open("r", encoding="utf-8") as handle:
            data = json.load(handle)
        if not isinstance(data, dict):
            raise ValueError("JSON root must be an object")
        return data
    except Exception as exc:
        backup = path.with_suffix(path.suffix + f".invalid-{int(time.time())}")
        try:
            path.replace(backup)
        except OSError:
            pass
        write_json(path, fallback)
        log.error("Invalid %s (%s); defaults restored, old file kept at %s", path.name, exc, backup.name)
        return json.loads(json.dumps(fallback))


def _clamp(value, low, high, default):
    try:
        number = int(value)
    except (TypeError, ValueError):
        return default
    return max(low, min(high, number))


def normalize_settings(raw: dict) -> dict:
    data = dict(raw or {})
    for key in _LEGACY_KEYS:
        data.pop(key, None)
    merged = dict(DEFAULT_SETTINGS)
    merged.update(data)
    merged["schema_version"] = 2
    merged["face_size"] = _clamp(merged.get("face_size"), 52, 128, 66)
    merged["speech_rate"] = _clamp(merged.get("speech_rate"), -10, 10, 0)
    merged["speech_volume"] = _clamp(merged.get("speech_volume"), 0, 100, 90)
    for key in ("face_x", "face_y"):
        value = merged.get(key)
        if value is not None:
            try:
                merged[key] = int(value)
            except (TypeError, ValueError):
                merged[key] = None
    if merged.get("microphone_device") is not None:
        try:
            merged["microphone_device"] = int(merged["microphone_device"])
        except (TypeError, ValueError):
            merged["microphone_device"] = None
    for key in (
        "wake_enabled", "voice_enabled", "show_face", "opencode_enabled",
        "codex_enabled", "announce_interruptions", "start_with_windows",
        "setup_complete",
    ):
        merged[key] = bool(merged.get(key))
    merged["opencode_base_url"] = str(merged.get("opencode_base_url") or "").strip()
    return merged


def load_settings() -> dict:
    raw = _read_json(SETTINGS_PATH, DEFAULT_SETTINGS)
    return normalize_settings(raw)


def save_settings(settings: dict) -> dict:
    clean = normalize_settings(settings)
    write_json(SETTINGS_PATH, clean)
    return clean


def _normalize_mode(name: str, mode) -> dict:
    if not isinstance(mode, dict):
        return {"greeting": f"{name.title()} mode is ready.", "urls": [], "apps": []}
    urls = [u for u in mode.get("urls", []) if isinstance(u, str) and u.strip()]
    apps = []
    for app in mode.get("apps", []):
        if isinstance(app, dict) and isinstance(app.get("path"), str) and app["path"].strip():
            args = app.get("args", [])
            if not isinstance(args, list) or not all(isinstance(a, str) for a in args):
                args = []
            apps.append({"path": app["path"].strip(), "args": args})
    greeting = mode.get("greeting")
    if not isinstance(greeting, str) or not greeting.strip():
        greeting = f"{name.title()} mode is ready."
    return {"greeting": greeting.strip(), "urls": urls, "apps": apps}


def load_modes() -> dict:
    raw = _read_json(MODES_PATH, DEFAULT_MODES)
    cleaned = {}
    for name in set(DEFAULT_MODES) | set(raw):
        cleaned[name] = _normalize_mode(name, raw.get(name, {}))
    return cleaned


def save_modes(modes: dict) -> dict:
    cleaned = {name: _normalize_mode(name, mode) for name, mode in (modes or {}).items()
               if isinstance(mode, dict)}
    write_json(MODES_PATH, cleaned)
    return cleaned


def resolve_path(value: str | os.PathLike) -> Path:
    path = Path(str(value)).expanduser()
    if not path.is_absolute():
        path = (ROOT / path).resolve()
    return path
