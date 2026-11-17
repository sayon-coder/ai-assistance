"""Window enumeration and graceful WM_CLOSE tests with a fake Windows API."""
from __future__ import annotations

from talcon import windows_control
from talcon.windows_control import close_windows, enumerate_windows, preview


class FakeWindowApi:
    def __init__(self, windows):
        self._windows = windows
        self.posted: list[int] = []
        self.closed_on_post: set[int] = set()

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
        return self._windows_by_pid.get(pid, "app.exe")

    def post_close(self, hwnd):
        self.posted.append(hwnd)
        return self._windows[hwnd].get("post_ok", True)

    def is_window(self, hwnd):
        if hwnd in self.closed_on_post:
            return False
        return self._windows[hwnd].get("still_open", True)


def make_api(windows):
    api = FakeWindowApi(windows)
    api._windows_by_pid = {info.get("pid", 200): info.get("process", "app.exe")
                           for info in windows.values()}
    return api


def test_visible_application_windows_are_enumerated_including_minimised():
    api = make_api({
        1: {"title": "Editor", "pid": 200, "process": "Code.exe"},
        2: {"title": "Music", "pid": 201, "process": "Spotify.exe", "minimized": True},
    })
    found = enumerate_windows(api=api, own_pid=999)
    assert [window.title for window in found] == ["Editor", "Music"]
    assert found[1].minimized is True


def test_talcon_shell_and_system_windows_are_excluded():
    api = make_api({
        1: {"title": "TALCON", "pid": 999, "process": "pythonw.exe"},
        2: {"title": "Desktop", "class_name": "Progman", "pid": 300},
        3: {"title": "Taskbar", "class_name": "Shell_TrayWnd", "pid": 301},
        4: {"title": "Service host", "pid": 302, "process": "svchost.exe"},
        5: {"title": "Hidden", "visible": False, "pid": 303},
        6: {"title": "", "pid": 304},
        7: {"title": "Tooltip", "ex_style": windows_control.WS_EX_TOOLWINDOW, "pid": 305},
        8: {"title": "Owned dialog", "owner": 1, "pid": 306},
        9: {"title": "Cloaked", "cloaked": True, "pid": 307},
        10: {"title": "Browser", "pid": 308, "process": "chrome.exe"},
    })
    found = enumerate_windows(api=api, own_pid=999)
    assert [window.hwnd for window in found] == [10]
    assert found[0].process == "chrome.exe"


def test_close_all_sends_wm_close_and_reports_closed_windows():
    api = make_api({
        1: {"title": "Editor", "pid": 200, "process": "Code.exe"},
        2: {"title": "Chat", "pid": 201, "process": "Slack.exe"},
    })
    api.closed_on_post = {1}
    windows = enumerate_windows(api=api, own_pid=999)
    result = close_windows(windows, api=api, wait=0.0, sleep=lambda _s: None,
                           now=lambda: 0.0)
    assert api.posted == [1, 2]
    assert [window.hwnd for window in result.closed] == [1]
    assert [window.hwnd for window in result.refused] == [2]
    assert "still open" in result.summary().lower()


def test_unresponsive_applications_are_reported_not_force_closed():
    api = make_api({1: {"title": "Stuck", "pid": 200, "process": "Stuck.exe"}})
    windows = enumerate_windows(api=api, own_pid=999)
    result = close_windows(windows, api=api, wait=0.0, sleep=lambda _s: None,
                           now=lambda: 0.0)
    assert [window.hwnd for window in result.refused] == [1]
    assert result.errors == []
    # Only WM_CLOSE was used; there is no terminate/taskkill path here.
    assert api.posted == [1]


def test_post_failure_is_reported():
    api = make_api({1: {"title": "Locked", "pid": 200, "post_ok": False}})
    windows = enumerate_windows(api=api, own_pid=999)
    result = close_windows(windows, api=api, wait=0.0, sleep=lambda _s: None,
                           now=lambda: 0.0)
    assert result.asked == []
    assert result.errors and "could not ask" in result.errors[0].lower()


def test_preview_lists_windows_without_closing_anything():
    api = make_api({1: {"title": "Editor", "pid": 200, "process": "Code.exe"}})
    listed = preview(api=api, own_pid=999)
    assert listed[0]["title"] == "Editor"
    assert api.posted == []
