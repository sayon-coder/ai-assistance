"""Live running-application discovery, graceful close and force-close safety.

Every Windows API call is faked.  These tests prove selection, grouping and
request mechanics; they do not prove that a real application closed.
"""
from __future__ import annotations

import json
from types import SimpleNamespace

from talcon.app_catalog import (
    extract_close_command, extract_force_close_command,
    extract_force_confirm_command, extract_running_query, is_force_cancel,
)
from talcon.apps import AppEntry
from talcon.commands import CommandRouter, build_recognition_grammar
from talcon.running_apps import (
    RunningApp, application_for_window, close_running_app, enumerate_running_apps,
)
from talcon.runtime import TalconRuntime
from talcon.windows_control import (
    CloseResult, ForceResult, WindowInfo, force_terminate, is_protected_process,
)


class FakeSpeech:
    enabled = True

    def __init__(self):
        self.spoken = []

    def say(self, text):
        self.spoken.append(str(text))


class FakeInstalled:
    def __init__(self, entries):
        self._entries = list(entries)

    def entries(self, force=False):
        return list(self._entries)

    def cached_entries(self):
        return list(self._entries)

    def refresh_async(self, callback=None):
        pass


class FakeWindowApi:
    def __init__(self, windows):
        self._windows = windows
        self.posted = []
        self.closed_on_post = set()
        self.terminated = []
        self.alive = set()

    def windows(self):
        return list(self._windows)

    def is_visible(self, hwnd):
        return self._windows[hwnd].get("visible", True)

    def is_minimized(self, hwnd):
        return self._windows[hwnd].get("minimized", False)

    def title(self, hwnd):
        return self._windows[hwnd].get("title", "")

    def class_name(self, hwnd):
        return self._windows[hwnd].get("class_name", "AppWindow")

    def pid(self, hwnd):
        return self._windows[hwnd].get("pid", 200)

    def owner(self, hwnd):
        return self._windows[hwnd].get("owner", 0)

    def ex_style(self, hwnd):
        return self._windows[hwnd].get("ex_style", 0)

    def is_cloaked(self, hwnd):
        return self._windows[hwnd].get("cloaked", False)

    def process_name(self, pid):
        return self._by_pid.get(pid, "app.exe")

    def post_close(self, hwnd):
        self.posted.append(hwnd)
        return self._windows[hwnd].get("post_ok", True)

    def is_window(self, hwnd):
        if hwnd in self.closed_on_post:
            return False
        return self._windows[hwnd].get("still_open", True)

    def terminate_process(self, pid):
        self.terminated.append(pid)
        self.alive.discard(pid)
        return self._windows.get(pid, {}).get("terminate_ok", True)

    def is_process_alive(self, pid):
        return pid in self.alive


def make_api(windows):
    api = FakeWindowApi(windows)
    api._by_pid = {info.get("pid", 200): info.get("process", "app.exe")
                   for info in windows.values()}
    return api


def make_running(name, process="App.exe", pids=(100,), windows=None,
                 app_mode=False, browser=""):
    wins = tuple(windows or (
        WindowInfo(hwnd=pids[0], title=name, class_name="App", pid=pids[0],
                   process=process),
    ))
    return RunningApp(name=name, windows=wins, process=process,
                      pids=tuple(pids), app_mode=app_mode, browser=browser)


class FakeApp:
    def __init__(self, running, close_result=None, force_result=None):
        self.running = list(running)
        self.close_result = close_result
        self.force_result = force_result
        self.closed = []
        self.forced = []

    def running_applications(self):
        return list(self.running)

    def close_application(self, app):
        self.closed.append(app)
        return self.close_result or CloseResult(
            asked=list(app.windows), closed=list(app.windows)
        )

    def force_close_application(self, app):
        self.forced.append(app)
        return self.force_result or ForceResult(terminated=list(app.pids))

    def refresh_apps(self, announce=True):
        pass

    def show_settings(self):
        pass


def build_router(running, installed=(), close_result=None, force_result=None):
    speech = FakeSpeech()
    app = FakeApp(running, close_result, force_result)
    router = CommandRouter(SimpleNamespace(current_mode="normal"), speech, app)
    router.set_application_library(FakeInstalled(installed), {})
    return router, speech, app


# -- parsing ---------------------------------------------------------------
def test_close_and_running_parsers():
    assert extract_close_command("Close Chrome.") == "chrome"
    assert extract_close_command("exit the Spotify app") == "spotify"
    assert extract_close_command("quit VS Code") == "vs code"
    assert extract_close_command("close everything now") is None
    assert extract_close_command("close all my tabs") is None
    assert extract_force_close_command("force close Chrome") == "chrome"
    assert extract_force_confirm_command("confirm force close Chrome") == "chrome"
    assert is_force_cancel("cancel force close")
    assert extract_running_query("is Chrome running") == "chrome"
    assert extract_running_query("is spotify open") == "spotify"
    assert extract_running_query("is the chrome open") == "chrome"
    assert extract_running_query("what is chrome") is None


# -- discovery and grouping -----------------------------------------------
def test_running_apps_group_windows_and_link_installed_names():
    entries = [
        AppEntry(name="Visual Studio Code"), AppEntry(name="Notepad"),
        AppEntry(name="Google Chrome"),
    ]
    api = make_api({
        1: {"title": "app.py - Visual Studio Code", "pid": 10, "process": "Code.exe"},
        2: {"title": "Untitled - Notepad", "pid": 11, "process": "notepad.exe"},
        3: {"title": "News - Google Chrome", "pid": 12, "process": "chrome.exe"},
        4: {"title": "Docs - Google Chrome", "pid": 12, "process": "chrome.exe"},
    })
    apps = enumerate_running_apps(entries, api=api, own_pid=999)
    by_name = {app.name: app for app in apps}
    assert set(by_name) == {"Visual Studio Code", "Notepad", "Google Chrome"}
    assert by_name["Google Chrome"].window_count == 2
    assert by_name["Google Chrome"].pids == (12,)


def test_site_tab_windows_belong_to_the_installed_web_app():
    """'close youtube' must find YouTube even when it is open as a tab."""
    entries = [
        AppEntry(name="Brave", kind="browser", path="C:/brave.exe"),
        AppEntry(name="YouTube", kind="shortcut", path="C:/YouTube.lnk",
                 app_mode=True, browser="brave"),
    ]
    api = make_api({
        1: {"title": "YouTube - Personal browsing - Brave", "pid": 10,
            "process": "brave.exe"},
        2: {"title": "Watch later - YouTube - Brave", "pid": 10,
            "process": "brave.exe"},
        3: {"title": "Some Other Site - Brave", "pid": 10,
            "process": "brave.exe"},
    })
    apps = enumerate_running_apps(entries, api=api, own_pid=999)
    by_name = {app.name: app for app in apps}
    assert "YouTube" in by_name, sorted(by_name)
    assert by_name["YouTube"].window_count == 2
    assert by_name["YouTube"].app_mode is True
    # The unrelated tab still belongs to the browser itself.
    assert "Brave" in by_name
    assert by_name["Brave"].window_count == 1


def test_blank_and_pwa_titled_browser_windows_stay_classified_as_before():
    entries = [AppEntry(name="YouTube", kind="shortcut", path="C:/YouTube.lnk",
                        app_mode=True, browser="brave")]
    from talcon.running_apps import _browser_window_name
    assert _browser_window_name("brave", "Brave", entries) == ("Brave", False)
    assert _browser_window_name("brave", "New Tab - Brave", entries) == ("Brave", False)
    assert _browser_window_name("brave", "YouTube", entries) == ("YouTube", True)
    assert _browser_window_name("brave", "YouTube - Brave", entries) == ("YouTube", True)
    # A site tab with no installed app identity belongs to the browser.
    assert _browser_window_name("brave", "Random News - Brave", entries) == ("Brave", False)


def test_youtube_mishearings_resolve_through_aliases():
    from pathlib import Path
    from talcon.app_catalog import resolve_application, load_aliases
    entries = [AppEntry(name="YouTube", kind="shortcut", path="C:/YouTube.lnk",
                        app_mode=True, browser="brave")]
    aliases = load_aliases(path=Path("nonexistent-aliases.json"))
    for spoken in ("you tube", "u tube", "you do", "you to", "youtube"):
        result = resolve_application(spoken, entries, aliases)
        assert result.status == "match" and result.entry.name == "YouTube", spoken


def test_installed_pwas_are_separated_from_the_browser_and_each_other():
    entries = [
        AppEntry(name="Google Chrome"),
        AppEntry(name="ChatGPT", kind="shortcut", path="C:/Brave.lnk",
                 app_mode=True, browser="brave"),
        AppEntry(name="ChatGPT", kind="shortcut", path="C:/Chrome.lnk",
                 app_mode=True, browser="chrome"),
    ]
    api = make_api({
        1: {"title": "ChatGPT", "pid": 10, "process": "chrome.exe"},
        2: {"title": "ChatGPT", "pid": 11, "process": "brave.exe"},
        3: {"title": "Search - Google Chrome", "pid": 10, "process": "chrome.exe"},
    })
    apps = enumerate_running_apps(entries, api=api, own_pid=999)
    keys = {app.key() for app in apps}
    assert ("running", "chatgpt", "chrome") in keys
    assert ("running", "chatgpt", "brave") in keys
    assert ("running", "google chrome", "") in keys
    chrome_pwa = next(app for app in apps if app.key() == ("running", "chatgpt", "chrome"))
    assert chrome_pwa.app_mode is True
    assert chrome_pwa.browser == "chrome"


def test_multiple_windows_of_one_application_are_one_running_app():
    api = make_api({
        1: {"title": "one.txt - Notepad", "pid": 10, "process": "notepad.exe"},
        2: {"title": "two.txt - Notepad", "pid": 10, "process": "notepad.exe"},
    })
    apps = enumerate_running_apps([AppEntry(name="Notepad")], api=api, own_pid=999)
    assert len(apps) == 1
    assert apps[0].window_count == 2


def test_process_name_maps_to_installed_display_name():
    api = make_api({
        1: {"title": "Untitled document", "pid": 10, "process": "Code.exe"},
    })
    apps = enumerate_running_apps([AppEntry(name="Visual Studio Code")],
                                  api=api, own_pid=999)
    assert apps[0].name == "Visual Studio Code"


def test_security_and_shell_processes_are_excluded_from_running_apps():
    assert is_protected_process("MsMpEng.exe")
    assert is_protected_process("avastui.exe")
    assert is_protected_process("explorer.exe")
    assert is_protected_process("pythonw.exe")
    assert not is_protected_process("chrome.exe")
    api = make_api({
        1: {"title": "Antivirus", "pid": 10, "process": "avastui.exe"},
        2: {"title": "TALCON", "pid": 999, "process": "pythonw.exe"},
        3: {"title": "Taskbar", "class_name": "Shell_TrayWnd", "pid": 300},
        4: {"title": "Chrome", "pid": 12, "process": "chrome.exe"},
    })
    apps = enumerate_running_apps([], api=api, own_pid=999)
    assert [app.name for app in apps] == ["Chrome"]


# -- graceful close --------------------------------------------------------
def test_graceful_close_posts_wm_close_to_every_window_of_the_app():
    api = make_api({
        1: {"title": "one.txt - Notepad", "pid": 10, "process": "notepad.exe"},
        2: {"title": "two.txt - Notepad", "pid": 10, "process": "notepad.exe"},
    })
    api.closed_on_post = {1, 2}
    app = make_running("Notepad", "notepad.exe", pids=(10,),
                       windows=(WindowInfo(1, "one.txt - Notepad", "App", 10, "notepad.exe"),
                                WindowInfo(2, "two.txt - Notepad", "App", 10, "notepad.exe")))
    result = close_running_app(app, api=api)
    assert api.posted == [1, 2]
    assert len(result.closed) == 2
    assert result.refused == []


def test_refused_close_reports_unsaved_work_instead_of_force_killing():
    api = make_api({
        1: {"title": "report.docx - Word", "pid": 10, "process": "WINWORD.EXE"},
    })
    app = make_running("Word", "WINWORD.EXE", pids=(10,))
    router, speech, fake = build_router(
        [app], close_result=CloseResult(asked=list(app.windows), refused=list(app.windows))
    )
    assert router.handle("close word") == "app_close_refused"
    assert "still open" in speech.spoken[-1]
    assert "save" in speech.spoken[-1].lower()
    assert fake.forced == []


def test_close_not_running_and_unknown_are_distinguished():
    installed = [AppEntry(name="Chrome")]
    router, speech, _app = build_router([], installed=installed)
    assert router.handle("close chrome") == "app_not_running"
    assert "isn't currently running" in speech.spoken[-1]
    router, speech, _app = build_router([make_running("Spotify")], installed=installed)
    assert router.handle("close calculator") == "running_unknown"
    assert "couldn't identify" in speech.spoken[-1]


def test_close_ambiguous_prompts_and_then_closes_the_chosen_app():
    brave = make_running("ChatGPT", "brave.exe", app_mode=True, browser="brave")
    chrome = make_running("ChatGPT", "chrome.exe", app_mode=True, browser="chrome")
    router, speech, fake = build_router([brave, chrome])
    assert router.handle("close chatgpt") == "close_ambiguous"
    assert "Which one should I close" in speech.spoken[-1]
    assert router.handle("second") == "app_closed"
    assert fake.closed and fake.closed[0] is chrome


def test_shell_injection_cannot_close_an_unmatched_application():
    app = make_running("Google Chrome", "chrome.exe")
    router, _speech, fake = build_router([app])
    for phrase in ("close chrome & calc", "close C:\\Windows\\calc.exe",
                   "quit calc.exe; shutdown /s"):
        assert router.handle(phrase) in {"running_unknown", "app_not_running"}
    assert fake.closed == []


# -- running application questions ----------------------------------------
def test_is_running_and_list_running():
    router, speech, _app = build_router(
        [make_running("Google Chrome"), make_running("Notepad")]
    )
    assert router.handle("is chrome running") == "running_yes"
    assert "Yes" in speech.spoken[-1]
    assert router.handle("is spotify open") == "running_no"
    assert "not running" in speech.spoken[-1]
    assert router.handle("what applications are currently running") == "running_list"
    assert "Google Chrome" in speech.spoken[-1] and "Notepad" in speech.spoken[-1]
    assert router.handle("how many apps are running right now") == "running_list"
    assert "2 applications" in speech.spoken[-1]


# -- force close -----------------------------------------------------------
def test_force_close_requires_exact_separate_confirmation():
    app = make_running("Spotify", "Spotify.exe", pids=(10,))
    router, speech, fake = build_router([app])
    assert router.handle("force close spotify") == "force_close_pending"
    assert "unsaved work" in speech.spoken[-1]
    # A mismatched confirmation aborts and never terminates anything.
    assert router.handle("confirm force close chrome") == "force_close_mismatch"
    assert fake.forced == []
    # The separate exact confirmation is required.
    assert router.handle("force close spotify") == "force_close_pending"
    assert router.handle("confirm force close spotify") == "app_force_closed"
    assert [item.name for item in fake.forced] == ["Spotify"]


def test_force_close_can_be_cancelled():
    router, speech, fake = build_router([make_running("Spotify")])
    assert router.handle("force close spotify") == "force_close_pending"
    assert router.handle("cancel force close") == "force_close_cancelled"
    assert fake.forced == []
    assert "Nothing was closed" in speech.spoken[-1]


def test_protected_process_cannot_be_force_closed():
    app = make_running("Windows Security", "MsMpEng.exe", pids=(10,))
    router, speech, fake = build_router([app])
    assert router.handle("force close windows security") == "app_protected"
    assert fake.forced == []
    assert "protected" in speech.spoken[-1].lower()


def test_force_terminate_refuses_system_own_and_duplicate_pids():
    api = make_api({})
    api.alive = {500}
    result = force_terminate([0, 4, 999, 500], api=api, own_pid=999,
                             wait=0.0, sleep=lambda _s: None, now=lambda: 0.0)
    assert api.terminated == [500]
    assert set(result.refused) == {0, 4, 999}
    assert result.terminated == [500]


# -- grammar ---------------------------------------------------------------
def test_grammar_contains_open_close_and_running_queries():
    entries = [AppEntry(name="Chrome", kind="shortcut", path="C:/Chrome.lnk")]
    running = [make_running("Chrome", "chrome.exe")]

    runtime = TalconRuntime.__new__(TalconRuntime)
    runtime.apps = FakeInstalled(entries)
    runtime.commands = SimpleNamespace(
        recognition_phrases=lambda: {"study mode"},
        pending_phrases=lambda: set(),
        app_aliases={},
    )
    runtime.custom = SimpleNamespace(snapshot=lambda: {"commands": [], "automations": []})
    runtime.voice = object()  # causes live running-app phrases to be included
    runtime.running_applications = lambda: running

    phrases = runtime.recognition_phrases()
    for expected in ("open chrome", "close chrome", "exit chrome",
                     "is chrome running", "is chrome open"):
        assert expected in phrases
    grammar = json.loads(build_recognition_grammar(phrases))
    assert "close chrome" in grammar
    assert "is chrome running" in grammar


# ------------------------------------------------ tray applications
def test_hidden_tray_window_is_still_found_and_closed():
    """MechVibes hides its window in the tray; it must still be closable."""
    from talcon.running_apps import close_application_by_name
    from talcon.windows_control import enumerate_process_windows

    api = make_api({
        1: {"title": "Mechvibes", "class_name": "Window Class",
            "pid": 656, "process": "mechvibes.exe", "visible": False,
            "still_open": False},
        2: {"title": "", "class_name": "tray_icon_app",
            "pid": 656, "process": "mechvibes.exe", "visible": False},
    })
    windows = enumerate_process_windows(["mechvibes.exe"], api=api)
    assert [w.title for w in windows] == ["Mechvibes"]

    result = close_application_by_name("Mechvibes", api=api)
    assert api.posted == [1]
    assert [w.hwnd for w in result.closed] == [1]


def test_visible_window_enumeration_still_ignores_hidden_apps():
    from talcon.windows_control import enumerate_windows

    api = make_api({
        1: {"title": "Mechvibes", "pid": 656, "process": "mechvibes.exe",
            "visible": False},
    })
    assert enumerate_windows(api=api) == []


def test_close_it_uses_the_hidden_window_when_nothing_is_visible(monkeypatch):
    """Regression: 'close it' after opening a tray app answered nothing."""
    import talcon.running_apps as running_apps

    posted = []

    def fake_close(name, path="", api=None, wait=1.5):
        posted.append(name)
        return CloseResult(asked=[], closed=[object()], refused=[], errors=[])

    monkeypatch.setattr(running_apps, "close_application_by_name", fake_close)

    router, speech, _app = build_router([], installed=[AppEntry(name="Mechvibes")])
    router._last_opened = AppEntry(name="Mechvibes")
    assert router.handle("now close it") == "app_closed"
    assert posted == ["Mechvibes"]
    assert speech.spoken[-1] == "Closing Mechvibes, sir."


def test_close_it_says_so_when_the_tray_app_really_is_gone(monkeypatch):
    import talcon.running_apps as running_apps

    monkeypatch.setattr(
        running_apps, "close_application_by_name", lambda *a, **k: CloseResult()
    )
    router, speech, _app = build_router([], installed=[AppEntry(name="Mechvibes")])
    router._last_opened = AppEntry(name="Mechvibes")
    assert router.handle("close it") == "app_not_running"
    assert "isn't running" in speech.spoken[-1]

def test_flexible_running_wording_matches_the_same_intents():
    router, speech, _app = build_router(
        [make_running("Google Chrome"), make_running("Notepad", pids=(101,))]
    )
    assert router.handle("how many programs do i have open right now") == "running_list"
    assert "2 applications" in speech.spoken[-1]
    assert router.handle("do i have chrome open") == "running_yes"
    assert "Google Chrome is running" in speech.spoken[-1]
    assert router.handle("is spotify up") == "running_no"
    assert "spotify" in speech.spoken[-1].lower()
