"""Configuration and filesystem layout for TALCON.

All data lives beside the application (or in %LOCALAPPDATA%\\TALCON when the
application folder is read-only, e.g. a packaged executable in Program Files).
Nothing here touches the network.
"""
from __future__ import annotations

import itertools
import json
import logging
import os
import re
import sys
import tempfile
import threading
import time
from pathlib import Path

from .apps import normalize_app_entry

log = logging.getLogger("talcon.config")

VERSION = "2.3.0"
APP_NAME = "TALCON"


def _writable(path: Path) -> bool:
    try:
        path.mkdir(parents=True, exist_ok=True)
        probe = path / ".talcon_write_test"
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
    "assistant_name": "TALCON",
    # Voice input
    "wake_enabled": True,
    "always_listen": True,
    "phrase_timeout": 4.0,
    "vosk_model_path": "models/vosk-model-small-en-us-0.15",
    "microphone_device": None,
    # Recognition timing and confidence.  The trailing-silence window is
    # deliberately longer than the old fixed 1.1 s so slow speech survives.
    "speech_start_timeout": 7.0,
    "speech_trailing_silence": 1.6,
    "speech_max_utterance": 20.0,
    "speech_min_confidence": 0.30,
    "speech_alt_min_confidence": 0.55,
    "speech_endpointer_mode": "long",
    "vad_noise_floor": 0.0,
    # Voice output
    "voice_enabled": True,
    # Stop taking commands (voice replies still work) until "take command".
    "commands_paused": False,
    # AI (Hermes) — natural-language answers, enabled by default.
    "ai_enabled": True,
    "hermes_host": "127.0.0.1",
    "hermes_port": 8642,
    "hermes_model": "hermes-agent",
    # In-memory response cache for repeated informational questions.
    "ai_cache_enabled": True,
    "ai_cache_entries": 100,
    "ai_cache_ttl_seconds": 600,
    # Seconds one AI request may take before TALCON stops it and says so.
    "ai_timeout_seconds": 75,
    # Voice conversations need a prompt answer.  Hermes agent runs use an SSE
    # tool-workflow which is useful for explicit automation, but can remain
    # open without a final answer.  Use the normal chat endpoint by default.
    "ai_use_agent_runs": False,
    "speech_backend": "kokoro",
    "speech_voice": "",
    "speech_voice_id": "",
    "speech_voice_ids": {},
    "speech_rate": 0,
    "speech_volume": 90,
    # Face
    "show_face": True,
    "face_size": 66,
    "face_expressions": True,
    "face_sleepy_after": 90,
    "face_sleep_after": 300,
    "face_x": None,
    "face_y": None,
    "face_screen": "",
    # Menu bar face (Windhawk macOS-style bar)
    "menu_bar_face_enabled": True,
    "menu_bar_face_size": 18,
    "menu_bar_face_x_offset": 0,
    "menu_bar_face_y_offset": 0,
    "menu_bar_face_height": 0,
    "menu_bar_face_animate": True,
    "menu_bar_face_x_ratio": 0.0,
    # Monitoring
    "opencode_enabled": True,
    "opencode_base_url": "http://127.0.0.1:4096",
    "codex_enabled": True,
    "deepai_enabled": True,
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
    "stock": {
        "greeting": "Certainly, sir. Your stock market environment is ready.",
        "urls": [
            "https://www.moneycontrol.com/",
            "https://economictimes.indiatimes.com/markets",
        ],
        "apps": [],
    },
}

BUILTIN_MODES = ("study", "entertainment", "stock")

# Keys used by fixed control-center sections.  A user-created mode must not
# reuse one of these, or the sidebar would show two pages with the same key.
RESERVED_MODE_KEYS = BUILTIN_MODES + (
    "normal", "dashboard", "modes", "voice", "commands", "appearance",
    "monitoring", "cron", "startup",
)


def mode_key(name: str) -> str:
    """Turn a user-visible mode name into a safe configuration key."""
    text = re.sub(r"[^a-z0-9]+", "_", str(name or "").lower()).strip("_")
    return text[:40]


def _clean_triggers(raw) -> list[str]:
    if isinstance(raw, str):
        raw = raw.splitlines()
    if not isinstance(raw, (list, tuple)):
        return []
    result: list[str] = []
    for item in raw:
        text = " ".join(str(item or "").lower().split())
        if text and text not in result:
            result.append(text)
    return result


_LEGACY_KEYS = ("porcupine_access_key", "porcupine_keyword_path")


#: Serialises every settings/modes write in this process.  TALCON saves from
#: the UI thread, the speech worker and the menu-bar face, and two writers
#: sharing one temp file made ``Path.replace`` fail with FileNotFoundError,
#: which silently dropped a change the user had just made.
_WRITE_LOCK = threading.RLock()
_TEMP_SEQUENCE = itertools.count(1)


def _write_json_unlocked(path: Path, data) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    # A unique temp name per write: concurrent writers can never clobber each
    # other's staging file, and a crashed write leaves nothing to trip over.
    temp = path.with_name(
        f"{path.name}.{os.getpid()}-{next(_TEMP_SEQUENCE)}.tmp"
    )
    try:
        with temp.open("w", encoding="utf-8") as handle:
            json.dump(data, handle, indent=2, ensure_ascii=False)
            handle.write("\n")
            handle.flush()
            try:
                os.fsync(handle.fileno())
            except OSError:
                pass
        last_error: OSError | None = None
        for attempt in range(6):
            try:
                temp.replace(path)
                return
            except OSError as exc:  # OneDrive / antivirus can briefly lock it
                last_error = exc
                time.sleep(0.1 * (attempt + 1))
        raise last_error if last_error else OSError(f"Could not write {path}")
    finally:
        try:
            temp.unlink(missing_ok=True)
        except OSError:
            pass


def write_json(path: Path, data) -> None:
    with _WRITE_LOCK:
        _write_json_unlocked(Path(path), data)


def _read_json(path: Path, fallback: dict) -> dict:
    path = Path(path)
    if not path.exists():
        # A OneDrive mount or an antivirus scan can hide the file for a moment;
        # retry before deciding that every setting should be reset.
        for attempt in range(5):
            time.sleep(0.08 * (attempt + 1))
            if path.exists():
                break
        else:
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
    # Imported lazily so configuration stays usable even if the speech module
    # is unavailable for some reason.
    from .speech import normalize_backend

    merged["speech_backend"] = normalize_backend(merged.get("speech_backend"))
    merged["speech_voice"] = str(merged.get("speech_voice") or "").strip()
    merged["speech_voice_id"] = str(merged.get("speech_voice_id") or "").strip()
    # One engine means one saved voice.  Older settings may contain Windows
    # engine keys; they are dropped rather than carried forward.
    raw_voice_ids = merged.get("speech_voice_ids")
    if not isinstance(raw_voice_ids, dict):
        raw_voice_ids = {}
    kokoro_voice = str(raw_voice_ids.get("kokoro") or "").strip()
    merged["speech_voice_ids"] = {"kokoro": kokoro_voice} if kokoro_voice else {}
    for key in ("face_x", "face_y"):
        value = merged.get(key)
        if value is not None:
            try:
                merged[key] = int(value)
            except (TypeError, ValueError):
                merged[key] = None
    merged["face_sleepy_after"] = _clamp(
        merged.get("face_sleepy_after"), 20, 1800, 90
    )
    merged["face_sleep_after"] = _clamp(
        merged.get("face_sleep_after"), 60, 7200, 300
    )
    merged["menu_bar_face_size"] = _clamp(
        merged.get("menu_bar_face_size"), 12, 32, 18
    )
    merged["menu_bar_face_x_offset"] = _clamp(
        merged.get("menu_bar_face_x_offset"), -600, 900, 0
    )
    merged["menu_bar_face_y_offset"] = _clamp(
        merged.get("menu_bar_face_y_offset"), -16, 16, 0
    )
    merged["menu_bar_face_height"] = _clamp(
        merged.get("menu_bar_face_height"), 0, 64, 0
    )
    try:
        ratio = float(merged.get("menu_bar_face_x_ratio", 0.0) or 0.0)
    except (TypeError, ValueError):
        ratio = 0.0
    merged["menu_bar_face_x_ratio"] = max(0.0, min(1.0, ratio))
    # Sleeping must always come after sleepy, otherwise the face would skip a
    # stage or never nap at all.
    if merged["face_sleep_after"] <= merged["face_sleepy_after"]:
        merged["face_sleep_after"] = merged["face_sleepy_after"] + 60
    if merged.get("microphone_device") is not None:
        try:
            merged["microphone_device"] = int(merged["microphone_device"])
        except (TypeError, ValueError):
            merged["microphone_device"] = None
    for key in (
        "wake_enabled", "always_listen", "voice_enabled", "show_face",
        "face_expressions", "opencode_enabled", "codex_enabled", "deepai_enabled",
        "announce_interruptions", "start_with_windows", "setup_complete",
        "menu_bar_face_enabled", "menu_bar_face_animate", "commands_paused",
        "ai_enabled", "ai_cache_enabled", "ai_use_agent_runs",
    ):
        merged[key] = bool(merged.get(key))
    merged["hermes_host"] = str(merged.get("hermes_host") or "127.0.0.1").strip()
    try:
        merged["hermes_port"] = max(1, min(65535, int(merged.get("hermes_port", 8642))))
    except (TypeError, ValueError):
        merged["hermes_port"] = 8642
    merged["hermes_model"] = str(merged.get("hermes_model") or "hermes-agent").strip()
    try:
        merged["ai_cache_entries"] = max(1, min(1000, int(merged.get("ai_cache_entries", 100))))
    except (TypeError, ValueError):
        merged["ai_cache_entries"] = 100
    try:
        merged["ai_cache_ttl_seconds"] = max(
            30.0, min(86400.0, float(merged.get("ai_cache_ttl_seconds", 600)))
        )
    except (TypeError, ValueError):
        merged["ai_cache_ttl_seconds"] = 600.0
    try:
        merged["ai_timeout_seconds"] = max(
            15.0, min(300.0, float(merged.get("ai_timeout_seconds", 75)))
        )
    except (TypeError, ValueError):
        merged["ai_timeout_seconds"] = 75.0
    try:
        merged["phrase_timeout"] = max(2.0, min(8.0, float(merged.get("phrase_timeout", 4.0))))
    except (TypeError, ValueError):
        merged["phrase_timeout"] = 4.0
    for key, low, high, default in (
        ("speech_start_timeout", 2.0, 15.0, 7.0),
        ("speech_trailing_silence", 0.6, 4.0, 1.6),
        ("speech_max_utterance", 6.0, 45.0, 20.0),
        ("speech_min_confidence", 0.0, 1.0, 0.30),
        ("speech_alt_min_confidence", 0.0, 1.0, 0.55),
        ("vad_noise_floor", 0.0, 5000.0, 0.0),
    ):
        try:
            merged[key] = max(low, min(high, float(merged.get(key, default))))
        except (TypeError, ValueError):
            merged[key] = default
    mode = str(merged.get("speech_endpointer_mode") or "long").strip().lower()
    merged["speech_endpointer_mode"] = (
        mode if mode in {"default", "short", "long"} else "long"
    )
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
        clean = normalize_app_entry(app)
        if clean is not None:
            apps.append(clean)
    greeting = mode.get("greeting")
    if not isinstance(greeting, str) or not greeting.strip():
        greeting = f"{name.title()} mode is ready."
    result = {"greeting": greeting.strip(), "urls": urls, "apps": apps}
    title = str(mode.get("title", "") or "").strip()
    if title:
        result["title"] = title
    triggers = _clean_triggers(mode.get("triggers"))
    if triggers:
        result["triggers"] = triggers
    return result


def normalize_modes(modes: dict) -> dict:
    """Normalise every mode without writing to disk."""
    return {
        str(name): _normalize_mode(str(name), mode)
        for name, mode in (modes or {}).items()
        if isinstance(mode, dict)
    }


def load_modes() -> dict:
    raw = _read_json(MODES_PATH, DEFAULT_MODES)
    cleaned = {}
    for name in set(DEFAULT_MODES) | set(raw):
        mode = raw.get(name, {})
        # Ignore malformed extra keys so they cannot become empty sidebar pages.
        if name not in DEFAULT_MODES and not isinstance(mode, dict):
            continue
        cleaned[name] = _normalize_mode(name, mode)
    return cleaned


def save_modes(modes: dict) -> dict:
    cleaned = normalize_modes(modes)
    write_json(MODES_PATH, cleaned)
    return cleaned


def resolve_path(value: str | os.PathLike) -> Path:
    path = Path(str(value)).expanduser()
    if not path.is_absolute():
        path = (ROOT / path).resolve()
    return path
