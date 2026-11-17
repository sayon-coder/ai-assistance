"""Live catalogue of user-facing applications that are currently running.

Windows are enumerated with the existing :mod:`talcon.windows_control` layer
(which already excludes shell infrastructure and critical services).  This
module groups the remaining visible windows into applications, links them to
the installed-application catalogue when possible, and keeps browser windows
and installed browser PWAs separate.

Nothing here terminates anything: closing is done with ``WM_CLOSE`` by
``windows_control``; force termination only happens after a separate confirmed
command in :mod:`talcon.commands`.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path

from .app_catalog import normalize_app_name, resolve_application
from .windows_control import (
    CloseResult, WindowInfo, close_windows, enumerate_process_windows,
    enumerate_windows,
)

log = logging.getLogger("talcon.running_apps")

BROWSER_EXECUTABLES = {
    "chrome.exe": "chrome",
    "msedge.exe": "edge",
    "brave.exe": "brave",
    "firefox.exe": "firefox",
    "opera.exe": "opera",
    "vivaldi.exe": "vivaldi",
}

BROWSER_DISPLAY_NAMES = {
    "chrome": "Google Chrome",
    "edge": "Microsoft Edge",
    "brave": "Brave",
    "firefox": "Mozilla Firefox",
    "opera": "Opera",
    "vivaldi": "Vivaldi",
}

#: Tab titles that carry no site identity; they belong to the browser.
_BLANK_TAB_TITLES = {
    "new tab", "new window", "brave search", "google search",
    "start page", "speed dial", "history", "downloads", "settings",
}

#: Process names whose executable name is a poor search term on its own.
PROCESS_NAME_ALIASES = {
    "msedge": "microsoft edge",
    "chrome": "google chrome",
    "brave": "brave",
    "code": "visual studio code",
    "explorer": "file explorer",
    "notepad": "notepad",
    "winword": "microsoft word",
    "excel": "microsoft excel",
    "powerpnt": "microsoft powerpoint",
    "outlook": "microsoft outlook",
    "acrobat": "adobe acrobat",
    "acrord32": "adobe acrobat reader",
}


@dataclass(frozen=True)
class RunningApp:
    """One user-facing application and all of its visible windows."""

    name: str
    windows: tuple[WindowInfo, ...] = field(default_factory=tuple)
    process: str = ""
    pids: tuple[int, ...] = field(default_factory=tuple)
    app_mode: bool = False
    browser: str = ""
    kind: str = "running_app"
    source: str = "Running"
    path: str = ""
    aumid: str = ""

    @property
    def window_count(self) -> int:
        return len(self.windows)

    def key(self) -> tuple:
        return (
            "running",
            normalize_app_name(self.name),
            self.browser if self.app_mode else "",
        )

    @property
    def label(self) -> str:
        windows = f"{self.window_count} window" + ("s" if self.window_count != 1 else "")
        return f"{self.name} ({windows})"


def _normalise_title(title: str) -> str:
    return normalize_app_name(title)


def _contains_name(title_words: list[str], name_words: list[str]) -> bool:
    if not name_words or len(name_words) > len(title_words):
        return False
    width = len(name_words)
    for index in range(len(title_words) - width + 1):
        if title_words[index:index + width] == name_words:
            return True
    return False


def _browser_window_name(browser: str, title: str, entries) -> tuple[str, bool]:
    """Return ``(display name, is_app_mode)`` for a browser-owned window.

    Tab titles end with the browser's own product name, often behind a
    profile segment ("YouTube - Personal browsing - Google Chrome").  When
    the page part names an installed web app, the window is attributed to
    that application so "close youtube" finds it even though it is a tab
    window hosted by the browser; otherwise the window belongs to the
    browser itself.
    """
    title_norm = _normalise_title(title)
    display = BROWSER_DISPLAY_NAMES.get(browser, browser.title())
    display_norm = normalize_app_name(display)
    # A window whose whole title is the browser's own name.
    if title_norm == display_norm:
        return display, False
    is_tab = title_norm.endswith(" " + display_norm)
    page_norm = title_norm
    if is_tab:
        # Normalisation has already removed the " - " separators, so the page
        # part is whatever precedes the browser name ("youtube personal
        # browsing"); a known app name still matches it as a word prefix.
        page_norm = title_norm[: -len(display_norm) - 1].strip()
        if not page_norm or page_norm in _BLANK_TAB_TITLES:
            return display, False

    # Installed web apps open with their own title (for example "ChatGPT")
    # or as a tab named after the site.
    candidates = []
    for entry in entries or ():
        if not entry.app_mode:
            continue
        if entry.browser and entry.browser != browser:
            continue
        name = normalize_app_name(entry.name)
        if not name:
            continue
        name_words = name.split()
        # PWA windows carry the app name verbatim; tab titles may surround it
        # with page or profile segments ("watch - youtube personal browsing").
        probes = [title_norm] if not is_tab else []
        if page_norm:
            probes.append(page_norm)
        hit = any(
            probe == name or probe.startswith(name + " ")
            or (is_tab and _contains_name(probe.split(), name_words))
            for probe in probes
        )
        if hit:
            candidates.append((len(name), entry.name))
    if candidates:
        return max(candidates)[1], True
    if is_tab:
        # Ordinary tab for a site that is not an installed application.
        return display, False
    # Unknown installed web app in this browser: trust the window's own title.
    label = str(title or "").strip()
    if " - " in label:
        label = label.split(" - ", 1)[0].strip()
    return label or display, True


def _installed_name_for_process(process: str, entries, aliases) -> str:
    stem = Path(str(process or "")).stem.lower()
    if not stem:
        return ""
    query = PROCESS_NAME_ALIASES.get(stem, stem)
    result = resolve_application(query, entries or (), aliases)
    if result.status == "match" and result.entry is not None:
        return result.entry.name
    return stem.replace("_", " ").title()


def application_for_window(window: WindowInfo, entries=None, aliases=None):
    """Map one visible window to ``(display name, app_mode, browser)``."""
    process = str(window.process or "").lower()
    browser = BROWSER_EXECUTABLES.get(process, "")
    entries = list(entries or ())
    if browser:
        name, app_mode = _browser_window_name(browser, window.title, entries)
        return name, app_mode, browser

    title_words = _normalise_title(window.title).split()
    best: tuple[int, str] | None = None
    for entry in entries:
        name_words = normalize_app_name(entry.name).split()
        if _contains_name(title_words, name_words):
            candidate = (len(name_words), entry.name)
            if best is None or candidate > best:
                best = candidate
    if best is not None:
        return best[1], False, ""
    return _installed_name_for_process(process, entries, aliases), False, ""


def enumerate_running_apps(entries=None, api=None, own_pid=None,
                           aliases=None) -> list[RunningApp]:
    """Return the live user-facing application catalogue.

    Multiple visible windows belonging to the same application are grouped.
    Installed PWAs are separated from their host browser and from same-named
    PWAs installed in a different browser.
    """
    windows = enumerate_windows(api=api, own_pid=own_pid)
    groups: dict[tuple, dict] = {}
    for window in windows:
        try:
            name, app_mode, browser = application_for_window(window, entries, aliases)
        except Exception:
            log.debug("Could not classify window %s", window.title, exc_info=True)
            name, app_mode, browser = Path(window.process).stem.title(), False, ""
        if not name:
            continue
        key = ("running", normalize_app_name(name), browser if app_mode else "")
        group = groups.setdefault(key, {
            "name": name, "app_mode": app_mode, "browser": browser,
            "windows": [], "pids": set(), "processes": [],
        })
        group["windows"].append(window)
        group["pids"].add(window.pid)
        if window.process and window.process not in group["processes"]:
            group["processes"].append(window.process)
    result: list[RunningApp] = []
    for group in groups.values():
        result.append(RunningApp(
            name=group["name"],
            windows=tuple(group["windows"]),
            process=group["processes"][0] if group["processes"] else "",
            pids=tuple(sorted(group["pids"])),
            app_mode=bool(group["app_mode"]),
            browser=str(group["browser"] or ""),
        ))
    result.sort(key=lambda item: (item.name.lower(), item.browser))
    return result


def close_running_app(app: RunningApp, api=None, wait: float = 1.5) -> CloseResult:
    """Ask every window of one running application to close with WM_CLOSE."""
    if not app.windows and (app.process or app.path):
        windows = _windows_for_application(app, api=api)
        if windows:
            return close_windows(windows, api=api, wait=wait)
    return close_windows(app.windows, api=api, wait=wait)


def _windows_for_application(app, api=None) -> list[WindowInfo]:
    """Every top-level window of an app, including ones hidden in the tray."""
    processes = []
    if getattr(app, "process", ""):
        processes.append(app.process)
    if getattr(app, "path", ""):
        try:
            processes.append(Path(app.path).name)
        except (TypeError, ValueError):
            pass
    for candidate in (getattr(app, "name", "") or "",):
        processes.extend((candidate, f"{candidate}.exe", f"{candidate.lower()}.exe"))
    return enumerate_process_windows(processes, api=api)


def close_application_by_name(name: str, path: str = "", api=None,
                              wait: float = 1.5) -> CloseResult:
    """Close an application by name/path, even if its windows are hidden."""
    probe = RunningApp(name=str(name or ""), process="", path=str(path or ""))
    windows = _windows_for_application(probe, api=api)
    if not windows:
        return CloseResult()
    return close_windows(windows, api=api, wait=wait)
