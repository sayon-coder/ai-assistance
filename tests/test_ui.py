"""Interface behaviour tests: eight sections, three window actions, mode editing."""
from types import SimpleNamespace

import pytest
import shiboken6
from PySide6.QtCore import QObject
from PySide6.QtWidgets import QPushButton

from nova.bridge import UiBridge
from nova.config import DEFAULT_MODES, DEFAULT_SETTINGS
from nova.face_widget import FaceWidget
from nova.ui.main_window import MainWindow, PAGES


class StubRuntime(QObject):
    def __init__(self):
        super().__init__()
        self.bridge = UiBridge()
        self.settings = dict(DEFAULT_SETTINGS)
        self.modes_data = {key: {"greeting": value["greeting"],
                                 "urls": list(value["urls"]), "apps": list(value["apps"])}
                           for key, value in DEFAULT_MODES.items()}
        self.muted = False
        self.voices = ["Microsoft David"]
        self.calls = []
        self.status = {
            "voice": "Listening for NOVA",
            "speech": "Windows speech ready",
            "opencode": "OpenCode not running · retrying",
            "codex": "Codex Desktop connected · watching current session (read-only)",
            "last_event": "NOVA is ready, sir.",
        }
        self.speech = SimpleNamespace(set_rate=lambda rate: self.calls.append(("rate", rate)))

    def status_snapshot(self):
        return dict(self.status)

    def microphones(self):
        return [(None, "System default microphone")]

    def save_settings(self, values):
        self.settings.update(values)
        self.calls.append(("save", dict(values)))
        self.bridge.settings_changed.emit(dict(self.settings))
        return self.settings

    def save_modes(self, modes):
        self.modes_data = modes
        self.calls.append(("modes", dict(modes)))
        return modes

    def activate_mode(self, name):
        self.calls.append(("mode", name))
        return True

    def set_muted(self, value):
        self.muted = value
        self.calls.append(("mute", value))

    def set_wake_enabled(self, value):
        self.calls.append(("wake", value))

    def hide_to_tray(self):
        self.calls.append(("hide_to_tray",))

    def exit_completely(self):
        self.calls.append(("exit",))

    def listen_now(self):
        self.calls.append(("listen",))

    def test_voice(self):
        self.calls.append(("test_voice",))

    def refresh_voices(self):
        self.calls.append(("refresh_voices",))

    def download_speech_model(self):
        self.calls.append(("download",))


_CREATED: list = []


def make_window(qapp):
    runtime = StubRuntime()
    face = FaceWidget(runtime, lambda: None)
    window = MainWindow(runtime, face)
    _CREATED.append((window, face))
    return runtime, face, window


@pytest.fixture(autouse=True)
def dispose_widgets(qapp):
    """Delete Qt widgets deterministically so the interpreter exit stays clean."""
    yield
    while _CREATED:
        window, face = _CREATED.pop()
        for widget in (window, face):
            try:
                widget.close()
                if shiboken6.isValid(widget):
                    shiboken6.delete(widget)
            except RuntimeError:
                pass
    qapp.processEvents()


def test_window_has_all_eight_sections(qapp):
    runtime, face, window = make_window(qapp)
    try:
        assert [key for key, _ in PAGES] == [
            "dashboard", "study", "entertainment", "voice",
            "appearance", "monitoring", "startup", "about",
        ]
        assert window.stack.count() == 8
        assert set(window.nav_buttons) == {key for key, _ in PAGES}
        window.show_page("voice")
        assert window.stack.currentWidget() is window.pages["voice"]
        window.show_page("about")
        assert window.stack.currentWidget() is window.pages["about"]
    finally:
        window.close()
        face.close()


def test_three_top_right_controls_are_labelled_and_distinct(qapp):
    runtime, face, window = make_window(qapp)
    try:
        assert "Minimise" in window.min_button.text()
        assert "Hide in tray" in window.hide_button.text()
        assert "Close" in window.close_button.text()
        assert window.min_button is not window.hide_button
        assert window.close_button is not window.hide_button

        window.show()
        qapp.processEvents()
        window.min_button.click()
        qapp.processEvents()
        assert window.isMinimized()
        window.showNormal()

        window.hide_button.click()
        assert ("hide_to_tray",) in runtime.calls
        window.close_button.click()
        assert ("exit",) in runtime.calls
    finally:
        face.close()


def test_switches_and_quick_actions_call_runtime(qapp):
    runtime, face, window = make_window(qapp)
    try:
        window.speech_switch.setChecked(False)
        assert ("mute", True) in runtime.calls
        window.speech_switch.setChecked(True)
        assert ("mute", False) in runtime.calls

        window.mute_button.click()
        assert runtime.muted is True

        dashboard = window.pages["dashboard"]
        study = next(
            b for b in dashboard.findChildren(QPushButton)
            if b.text() == "Study Mode" and b.objectName() == "Primary"
        )
        study.click()
        assert ("mode", "study") in runtime.calls
    finally:
        window.close()
        face.close()


def test_mode_editor_adds_website_without_json(qapp):
    runtime, face, window = make_window(qapp)
    try:
        window.show_page("study")
        editor = window.mode_editors["study"]
        editor.website_input.setText("example.org")
        add = next(b for b in editor.findChildren(QPushButton) if b.text() == "Add website")
        add.click()
        assert "https://example.org" in runtime.modes_data["study"]["urls"]
        assert any(kind == "modes" for kind, _ in runtime.calls)
    finally:
        window.close()
        face.close()


def test_startup_and_face_controls_persist_settings(qapp):
    runtime, face, window = make_window(qapp)
    try:
        window.face_switch.setChecked(False)
        assert runtime.settings["show_face"] is False
        window.show_page("study")
        window.face_switch.setChecked(True)
        assert runtime.settings["show_face"] is True
    finally:
        window.close()
        face.close()


def test_closing_the_window_terminates_nova(qapp):
    runtime, face, window = make_window(qapp)
    try:
        window.close()
        assert ("exit",) in runtime.calls
    finally:
        face.close()


def test_app_hide_in_tray_keeps_face_visible_and_minimise_does_not_exit(qapp):
    """Exercise the real NovaApplication methods with passive stand-ins."""
    from nova.app import NovaApplication

    class FakeFace:
        def __init__(self):
            self.visible = True
            self.hidden = 0

        def hide(self):
            self.hidden += 1
            self.visible = False

        def setVisible(self, value):
            self.visible = bool(value)

        def isVisible(self):
            return self.visible

    class FakeWindow:
        def __init__(self):
            self.visible = True

        def hide(self):
            self.visible = False

    class FakeTray:
        def __init__(self):
            self.messages = []
            self.visible = True

        def isVisible(self):
            return self.visible

        def show(self):
            self.visible = True

        def showMessage(self, *args):
            self.messages.append(args)

        def hide(self):
            self.visible = False

    app = NovaApplication.__new__(NovaApplication)
    QObject.__init__(app)
    app._hidden_once = False
    app._exiting = False
    app.face = FakeFace()
    app.window = FakeWindow()
    app.tray = FakeTray()
    app.runtime = SimpleNamespace(shutdown=lambda: app.__dict__.__setitem__("shutdown_called", True))
    app.qt = SimpleNamespace(quit=lambda: app.__dict__.__setitem__("quit_called", True))

    app.hide_to_tray()
    assert app.window.visible is False
    assert app.face.visible is True, "hiding to the tray must keep the desktop face visible"
    assert app.tray.messages, "the tray should confirm NOVA is still running"
    assert "shutdown_called" not in app.__dict__

    app.exit_completely()
    assert app.shutdown_called is True
    assert app.quit_called is True
    assert app.face.hidden == 1
