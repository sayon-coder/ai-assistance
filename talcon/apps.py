"""Windows application library: discovery, search and safe launching.

The library never installs anything.  It lists applications that are already
registered on this PC through the Start menu, the taskbar pins and the Windows
AppsFolder namespace, and it launches them without a shell.

Application descriptors are plain dictionaries so they can be stored in the
existing mode and custom-command configuration:

    {"kind": "executable",  "name": "Editor", "path": "C:/Tools/editor.exe",
     "args": ["--new-window"], "aumid": ""}

    {"kind": "shortcut",    "name": "Word",   "path": "C:/.../Word.lnk",
     "args": [], "aumid": ""}

    {"kind": "packaged_app", "name": "Calculator", "path": "",
     "args": [], "aumid": "Microsoft.WindowsCalculator_8wekyb3d8bbwe!App"}

    {"kind": "browser_app", "name": "YouTube", "path": "C:/.../YouTube.url",
     "args": [], "aumid": ""}
"""
from __future__ import annotations

import json
import logging
import os
import subprocess
import threading
import time
from dataclasses import dataclass
from pathlib import Path

log = logging.getLogger("talcon.apps")

APP_KINDS = ("executable", "shortcut", "packaged_app", "browser_app", "start_app")
SHORTCUT_SUFFIXES = {".lnk"}
BROWSER_SUFFIXES = {".url"}
BATCH_SUFFIXES = {".bat", ".cmd"}
EXECUTABLE_SUFFIXES = {".exe"}
SUPPORTED_FILE_SUFFIXES = SHORTCUT_SUFFIXES | BROWSER_SUFFIXES | BATCH_SUFFIXES | EXECUTABLE_SUFFIXES

CACHE_SECONDS = 300.0
_START_MENU_IGNORED_PREFIXES = ("uninstall", "readme", "help", "release notes")


# ---------------------------------------------------------------------------
# descriptors
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class AppEntry:
    """One discoverable or configured application."""

    name: str
    kind: str = "executable"
    path: str = ""
    args: tuple[str, ...] = ()
    aumid: str = ""
    source: str = ""
    #: True when the shortcut carries browser app-mode arguments
    #: (``--app-id`` / ``--app=``); such a shortcut must be launched itself so
    #: the installed web app opens in its own window, not a normal browser tab.
    app_mode: bool = False
    #: "edge" / "chrome" when the shortcut reliably points at that browser.
    browser: str = ""

    def key(self) -> tuple:
        if self.aumid:
            return ("packaged_app", self.aumid.lower())
        if self.kind in {"shortcut", "browser_app"}:
            # The same Start-menu and taskbar shortcut often has two different
            # .lnk paths, so shortcuts are deduplicated by name instead.  Two
            # installed web apps with the same name but different browsers are
            # different launch targets and stay separate.
            if self.app_mode:
                return ("browser_app", self.name.strip().lower(), self.browser or "web")
            return (self.kind, self.name.strip().lower())
        return (self.kind, self.path.lower())

    def to_dict(self) -> dict:
        return {
            "kind": self.kind,
            "name": self.name,
            "path": self.path,
            "args": list(self.args),
            "aumid": self.aumid,
        }


def _clean_args(raw) -> list[str]:
    if isinstance(raw, str):
        raw = raw.splitlines()
    if not isinstance(raw, (list, tuple)):
        return []
    return [str(item).strip() for item in raw if str(item).strip()]


def _infer_kind(path: str, aumid: str, requested: str | None) -> str:
    if requested in APP_KINDS:
        return requested
    if aumid and not path:
        return "packaged_app"
    suffix = Path(path).suffix.lower() if path else ""
    if suffix in SHORTCUT_SUFFIXES:
        return "shortcut"
    if suffix in BROWSER_SUFFIXES:
        return "browser_app"
    return "executable"


def normalize_app_entry(raw) -> dict | None:
    """Return a clean application descriptor, or None when it is unusable.

    Older configuration stored only ``{"path": ..., "args": [...]}``.  Those
    entries are migrated in place: a .lnk stays a shortcut, a .url stays a
    browser application and everything else becomes an executable.
    """
    if isinstance(raw, str):
        raw = {"path": raw}
    if not isinstance(raw, dict):
        return None
    path = str(raw.get("path", "") or "").strip()
    aumid = str(raw.get("aumid", "") or "").strip()
    kind = _infer_kind(path, aumid, str(raw.get("kind", "") or "").strip() or None)
    if kind in {"packaged_app", "start_app"}:
        if not aumid:
            return None
    elif not path:
        return None
    name = str(raw.get("name", "") or "").strip()
    if not name:
        name = Path(path).stem if path else aumid
    return {
        "kind": kind,
        "name": name,
        "path": path,
        "args": _clean_args(raw.get("args")),
        "aumid": aumid,
    }


def entry_from_dict(raw) -> AppEntry | None:
    clean = normalize_app_entry(raw)
    if clean is None:
        return None
    return AppEntry(
        name=clean["name"],
        kind=clean["kind"],
        path=clean["path"],
        args=tuple(clean["args"]),
        aumid=clean["aumid"],
        source=str((raw or {}).get("source", "") if isinstance(raw, dict) else ""),
    )


def app_key(raw) -> tuple[str, str] | None:
    """Return the duplicate-detection key for a stored application descriptor.

    The user interface and the library must agree on what "the same
    application" means, so both use this helper (and ``AppEntry.key``).
    """
    if isinstance(raw, AppEntry):
        return raw.key()
    entry = entry_from_dict(raw)
    return entry.key() if entry is not None else None


# ---------------------------------------------------------------------------
# discovery
# ---------------------------------------------------------------------------
def start_menu_dirs() -> list[Path]:
    dirs: list[Path] = []
    appdata = os.environ.get("APPDATA")
    if appdata:
        dirs.append(Path(appdata) / "Microsoft" / "Windows" / "Start Menu" / "Programs")
    programdata = os.environ.get("PROGRAMDATA")
    if programdata:
        dirs.append(Path(programdata) / "Microsoft" / "Windows" / "Start Menu" / "Programs")
    return dirs


def taskbar_dirs() -> list[Path]:
    appdata = os.environ.get("APPDATA")
    if not appdata:
        return []
    base = Path(appdata) / "Microsoft" / "Internet Explorer" / "Quick Launch"
    return [
        base / "User Pinned" / "TaskBar",
        base / "ImplicitAppShortcuts",
    ]


def shortcut_hints(path: Path) -> tuple[bool, str]:
    """Return ``(is_browser_app_shortcut, browser)`` for an installed shortcut.

    A browser-installed web app is an ordinary ``.lnk`` file whose arguments
    contain ``--app-id=<id>`` (Chrome/Edge) or ``--app=<url>``.  Reading the
    shortcut bytes is enough to flag it; the shortcut itself is always used for
    launching so its arguments are preserved exactly.
    """
    if path.suffix.lower() not in SHORTCUT_SUFFIXES:
        return False, ""
    try:
        data = path.read_bytes()
    except OSError:
        return False, ""
    try:
        text = data.decode("utf-16-le", "ignore")
    except Exception:
        text = ""
    haystack = (data.decode("latin-1", "ignore") + "\n" + text).lower()
    # Chrome/Edge web apps are often launched through ``chrome_proxy.exe`` /
    # ``msedge_proxy.exe`` rather than with visible ``--app`` arguments, so the
    # proxy executable is just as reliable an app-mode signal.
    app_mode = any(
        marker in haystack
        for marker in (
            "--app-id", "--app=", "chrome_proxy.exe",
            "msedge_proxy.exe", "chrome_pwa_launcher.exe",
        )
    )
    if "msedge" in haystack or "microsoft edge" in haystack:
        browser = "edge"
    elif "brave" in haystack:
        browser = "brave"
    elif "chrome" in haystack:
        browser = "chrome"
    else:
        browser = ""
    return app_mode, browser


def discover_shortcuts(dirs, source: str = "Start Menu") -> list[AppEntry]:
    """Walk Start-menu or taskbar folders for shortcuts and web apps."""
    found: list[AppEntry] = []
    seen: set[str] = set()
    for folder in dirs or ():
        root = Path(folder)
        if not root.is_dir():
            continue
        try:
            paths = root.rglob("*")
            for path in paths:
                if not path.is_file() or path.suffix.lower() not in (SHORTCUT_SUFFIXES | BROWSER_SUFFIXES):
                    continue
                name = path.stem.strip()
                if not name or name.lower().startswith(_START_MENU_IGNORED_PREFIXES):
                    continue
                key = str(path).lower()
                if key in seen:
                    continue
                seen.add(key)
                kind = "browser_app" if path.suffix.lower() in BROWSER_SUFFIXES else "shortcut"
                app_mode, browser = shortcut_hints(path)
                found.append(AppEntry(
                    name=name, kind=kind, path=str(path), source=source,
                    app_mode=app_mode, browser=browser,
                ))
        except OSError:
            log.warning("Could not read application folder %s", folder, exc_info=True)
    return found


def _run_get_start_apps() -> str:
    """Ask Windows once for the registered AppsFolder entries."""
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    result = subprocess.run(
        [
            "powershell.exe", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
            "-Command", "Get-StartApps | ConvertTo-Json -Compress",
        ],
        capture_output=True, text=True, timeout=40, creationflags=flags,
    )
    if result.returncode != 0:
        raise RuntimeError((result.stderr or result.stdout or "Get-StartApps failed").strip())
    return result.stdout


def discover_start_apps(raw_json: str | None = None, runner=None) -> list[AppEntry]:
    """Return every entry Windows registers for the Start menu / AppsFolder.

    ``Get-StartApps`` reports both packaged (Microsoft Store) applications and
    classic registered desktop applications.  The JSON is parsed; the console
    ``Format-Table`` output is never parsed.
    """
    if raw_json is None:
        runner = runner or _run_get_start_apps
        try:
            raw_json = runner()
        except Exception:
            log.warning("Get-StartApps could not run", exc_info=True)
            return []
    if not raw_json or not str(raw_json).strip():
        return []
    try:
        data = json.loads(raw_json)
    except (TypeError, ValueError):
        log.warning("Get-StartApps returned unreadable data")
        return []
    if isinstance(data, dict):
        data = [data]
    found: list[AppEntry] = []
    seen: set[str] = set()
    for item in data or ():
        if not isinstance(item, dict):
            continue
        aumid = str(item.get("AppID", "") or "").strip()
        name = str(item.get("Name", "") or "").strip()
        if not name or not aumid or aumid.lower() in seen:
            continue
        seen.add(aumid.lower())
        # Packaged/Store apps contain a package family and app id separated by
        # "!".  Classic entries are still launchable through AppsFolder and are
        # kept unless a real Start-menu shortcut is found for the same name.
        kind = "packaged_app" if "!" in aumid else "start_app"
        source = "Installed" if kind == "packaged_app" else "Start Apps"
        found.append(AppEntry(name=name, kind=kind, aumid=aumid, source=source))
    return found


def discover_packaged_apps(raw_json: str | None = None, runner=None) -> list[AppEntry]:
    """Return only packaged applications that have a real AUMID."""
    return [
        entry for entry in discover_start_apps(raw_json, runner)
        if entry.kind == "packaged_app"
    ]


def _dedupe_preference(entry: AppEntry) -> tuple:
    """Rank duplicates so the most capable launch target wins."""
    if entry.app_mode:
        return (0, entry.name.lower())
    if entry.kind in {"shortcut", "browser_app"}:
        return (1, entry.name.lower())
    if entry.source == "Taskbar":
        return (2, entry.name.lower())
    if entry.source == "Start Menu":
        return (3, entry.name.lower())
    if entry.kind == "packaged_app":
        return (4, entry.name.lower())
    return (5, entry.name.lower())


def _name_key(entry: AppEntry) -> str:
    return " ".join(entry.name.strip().lower().split())


def deduplicate(entries) -> list[AppEntry]:
    """Remove the same application found through more than one source.

    Preference order: browser app-mode shortcut, ordinary shortcut, taskbar
    pin, Start-menu entry, packaged AppsFolder entry.  A classic Start Apps
    entry is dropped when a real shortcut with the same name exists, because
    the shortcut preserves the original target and arguments.  Entries that
    are genuinely different (for example the same display name but a packaged
    app and a PWA) are kept and become an ambiguity prompt at launch time.
    """
    ordered = sorted(entries or (), key=_dedupe_preference)
    shortcut_names = {
        _name_key(entry) for entry in ordered
        if entry.kind in {"shortcut", "browser_app"}
    }
    app_mode_names = {_name_key(entry) for entry in ordered if entry.app_mode}
    result: list[AppEntry] = []
    seen: set[tuple] = set()
    for entry in ordered:
        if entry.kind == "start_app" and _name_key(entry) in shortcut_names:
            continue
        # A real app-mode shortcut is the only target that opens an installed
        # web app in its own window, so a same-named .url/plain shortcut is
        # treated as the same application and dropped.
        if (entry.kind in {"shortcut", "browser_app"} and not entry.app_mode
                and _name_key(entry) in app_mode_names):
            continue
        key = entry.key()
        if key in seen:
            continue
        seen.add(key)
        result.append(entry)
    result.sort(key=lambda item: (item.name.lower(), item.kind))
    return result


class ApplicationLibrary:
    """Cached, thread-safe view of the applications installed on this PC."""

    def __init__(self, start_dirs=None, pinned_dirs=None, packaged_loader=None,
                 refresh_interval: float = CACHE_SECONDS, clock=time.monotonic):
        self.start_dirs = list(start_dirs) if start_dirs is not None else None
        self.pinned_dirs = list(pinned_dirs) if pinned_dirs is not None else None
        self.packaged_loader = packaged_loader
        self.refresh_interval = max(0.0, float(refresh_interval))
        self.clock = clock
        self._entries: list[AppEntry] = []
        self._loaded_at: float | None = None
        self._lock = threading.RLock()

    # -- discovery ---------------------------------------------------------
    def _discover(self) -> list[AppEntry]:
        entries: list[AppEntry] = []
        entries.extend(discover_shortcuts(
            self.start_dirs if self.start_dirs is not None else start_menu_dirs(),
            "Start Menu",
        ))
        entries.extend(discover_shortcuts(
            self.pinned_dirs if self.pinned_dirs is not None else taskbar_dirs(),
            "Taskbar",
        ))
        loader = self.packaged_loader
        if loader is not None:
            try:
                entries.extend(loader())
            except Exception:
                log.warning("Packaged application discovery failed", exc_info=True)
        return deduplicate(entries)

    def entries(self, force: bool = False) -> list[AppEntry]:
        with self._lock:
            stale = (
                self._loaded_at is None
                or force
                or (self.clock() - self._loaded_at) >= self.refresh_interval
            )
            if stale:
                self._entries = self._discover()
                self._loaded_at = self.clock()
            return list(self._entries)

    def cached_entries(self) -> list[AppEntry]:
        """Return the last discovery result without touching the filesystem."""
        with self._lock:
            return list(self._entries)

    @property
    def is_loaded(self) -> bool:
        with self._lock:
            return self._loaded_at is not None

    def is_stale(self) -> bool:
        with self._lock:
            return (
                self._loaded_at is None
                or (self.clock() - self._loaded_at) >= self.refresh_interval
            )

    def refresh(self) -> list[AppEntry]:
        return self.entries(force=True)

    def invalidate(self, entry: AppEntry) -> None:
        """Drop one cached entry whose shortcut/target no longer exists."""
        with self._lock:
            key = entry.key()
            self._entries = [item for item in self._entries if item.key() != key]

    def refresh_async(self, callback=None) -> None:
        """Discover off the UI thread; callback(entries, error) is optional."""
        def work() -> None:
            error = ""
            entries: list[AppEntry] = []
            try:
                entries = self.refresh()
            except Exception as exc:
                error = str(exc)
                log.exception("Application refresh failed")
            if callback is not None:
                try:
                    callback(entries, error)
                except Exception:
                    log.exception("Application refresh callback failed")

        threading.Thread(target=work, name="TALCON-AppDiscovery", daemon=True).start()

    # -- queries -----------------------------------------------------------
    def search(self, query: str = "", category: str = "") -> list[AppEntry]:
        text = str(query or "").strip().lower()
        wanted = str(category or "").strip().lower()
        result = []
        for entry in self.entries():
            if wanted and entry.source.lower() != wanted:
                continue
            if text and text not in entry.name.lower() and text not in entry.path.lower():
                continue
            result.append(entry)
        return result

    def categories(self) -> dict[str, list[AppEntry]]:
        result: dict[str, list[AppEntry]] = {"Installed": [], "Taskbar": [], "Start Menu": []}
        for entry in self.entries():
            result.setdefault(entry.source or "Installed", []).append(entry)
        return result


# ---------------------------------------------------------------------------
# launching
# ---------------------------------------------------------------------------
def launch_app(entry, popen=None, startfile=None):
    """Launch one descriptor safely, without a shell.

    Returns the process object for direct executables, otherwise None.
    Raises ValueError for descriptors that are incomplete or unsupported.
    """
    popen = popen or subprocess.Popen
    startfile = startfile or (getattr(os, "startfile", None))
    if isinstance(entry, AppEntry):
        data = entry.to_dict()
    elif isinstance(entry, str):
        data = {"path": entry}
    elif isinstance(entry, dict):
        data = entry
    else:
        raise ValueError("Unsupported application description.")
    clean = normalize_app_entry(data)
    if clean is None:
        raise ValueError("The application description is incomplete.")

    kind = clean["kind"]
    path = clean["path"]
    args = list(clean["args"])
    if kind in {"packaged_app", "start_app"}:
        # An AppID is not a filesystem path; Explorer resolves it through the
        # shell AppsFolder namespace.  The id comes from the trusted catalogue,
        # never from the spoken phrase.
        return popen(["explorer.exe", f"shell:AppsFolder\\{clean['aumid']}"], close_fds=True)
    target = Path(path).expanduser()
    if not target.is_absolute():
        from .config import ROOT
        target = (ROOT / target).resolve()
    if not target.exists():
        raise FileNotFoundError(f"Application not found: {target}")
    suffix = target.suffix.lower()
    if kind in {"shortcut", "browser_app"} or suffix in (SHORTCUT_SUFFIXES | BROWSER_SUFFIXES):
        if not callable(startfile):
            raise ValueError("Shortcut launching is not available on this platform.")
        startfile(str(target))
        return None
    if suffix in BATCH_SUFFIXES:
        return popen(
            ["cmd.exe", "/c", str(target), *args],
            close_fds=True,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    if suffix in EXECUTABLE_SUFFIXES:
        return popen(
            [str(target), *args],
            close_fds=True,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    raise ValueError(f"Unsupported application type: {suffix or 'no extension'}")
