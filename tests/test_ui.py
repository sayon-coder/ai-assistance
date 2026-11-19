"""Interface behaviour tests: eight sections, three window actions, mode editing."""
from types import SimpleNamespace

import pytest
import shiboken6
from PySide6.QtCore import QObject
from PySide6.QtWidgets import QPushButton

from talcon.bridge import UiBridge
from talcon.config import DEFAULT_MODES, DEFAULT_SETTINGS
from talcon.face_widget import FaceWidget
from talcon.ui.main_window import MainWindow, PAGES


class StubRuntime(QObject):
    def __init__(self):
        super().__init__()
        self.bridge = UiBridge()
        self.settings = dict(DEFAULT_SETTINGS)
        self.modes_data = {key: {"greeting": value["greeting"],
                                 "urls": list(value["urls"]), "apps": list(value["apps"])}
                           for key, value in DEFAULT_MODES.items()}
        self.muted = False
        self.voices = ["George"]
        self.calls = []
        self.status = {
            "voice": "Listening for TALCON",
            "speech": "Kokoro voice ready",
            "opencode": "OpenCode not running · retrying",
            "codex": "Codex Desktop connected · watching current session (read-only)",
            "last_event": "TALCON is ready, sir.",
        }
        self.speech = SimpleNamespace(set_rate=lambda rate: self.calls.append(("rate", rate)))
        self.custom_data = {"version": 1, "commands": [], "automations": []}
        self.history = []
        self.command_entries = []

    def command_log(self):
        return [dict(entry) for entry in self.command_entries]

    def clear_command_log(self):
        self.command_entries.clear()
        self.bridge.command_log_changed.emit([])

    def conversation_history(self):
        return [dict(entry) for entry in self.history]

    def clear_conversation_history(self):
        self.history.clear()
        self.bridge.conversation_changed.emit([])

    def add_response(self, text, request="", when="12:00:00"):
        entry = {
            "id": len(self.history) + 1,
            "time": when,
            "request": request,
            "text": text,
        }
        self.history.append(entry)
        self.bridge.response_started.emit(dict(entry))
        self.bridge.conversation_changed.emit(self.conversation_history())
        return entry

    def custom_snapshot(self):
        import copy

        return copy.deepcopy(self.custom_data)

    def save_custom_command(self, entry, entry_id=None):
        self.calls.append(("save_command", entry))
        return entry

    def delete_custom_command(self, entry_id):
        self.calls.append(("delete_command", entry_id))
        return True

    def set_custom_command_enabled(self, entry_id, enabled):
        self.calls.append(("toggle_command", entry_id, enabled))
        return True

    def save_automation(self, entry, entry_id=None):
        self.calls.append(("save_automation", entry))
        return entry

    def delete_automation(self, entry_id):
        self.calls.append(("delete_automation", entry_id))
        return True

    def set_automation_enabled(self, entry_id, enabled):
        self.calls.append(("toggle_automation", entry_id, enabled))
        return True

    def reserved_command_phrases(self):
        return {"study mode", "open settings", "talcon"}

    def test_phrase(self, text):
        return {"matched": False, "phrase": str(text), "reason": "stub runtime"}

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
        self.bridge.modes_changed.emit(dict(modes))
        return modes

    def mode_title(self, name):
        mode = self.modes_data.get(name)
        if isinstance(mode, dict) and mode.get("title"):
            return str(mode["title"])
        return str(name).replace("_", " ").title()

    def mode_names(self):
        return list(self.modes_data)

    def activate_mode(self, name):
        self.calls.append(("mode", name))
        return True

    def set_muted(self, value):
        self.muted = value
        self.calls.append(("mute", value))

    def set_wake_enabled(self, value):
        self.calls.append(("wake", value))

    def set_ai_enabled(self, value):
        self.settings["ai_enabled"] = bool(value)
        self.calls.append(("ai", bool(value)))

    def assistant_backend_status(self):
        return "off"

    def ai_backend_report(self, start_if_down=False):
        return {"installed": False, "listening": False, "reachable": False,
                "home": "", "launcher": "", "host": "127.0.0.1",
                "port": 8642, "detail": "not running"}

    def start_ai_backend(self, wait_seconds=40.0):
        return False, "not running"

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


def test_window_has_all_required_sections(qapp):
    runtime, face, window = make_window(qapp)
    try:
        assert [key for key, _ in PAGES] == [
            "dashboard", "history", "commands", "study", "entertainment",
            "stock", "modes", "voice", "appearance", "monitoring", "cron",
            "startup",
        ]
        assert window.stack.count() == 12
        assert set(window.nav_buttons) == {key for key, _ in PAGES}
        assert window.nav_buttons["history"].text() == "History"
        assert window.nav_buttons["commands"].text() == "Commands"
        window.show_page("commands")
        assert window.stack.currentWidget() is window.pages["commands"]
        window.show_page("history")
        assert window.stack.currentWidget() is window.pages["history"]
        window.show_page("voice")
        assert window.stack.currentWidget() is window.pages["voice"]
        window.show_page("appearance")
        assert window.stack.currentWidget() is window.pages["appearance"]
        window.show_page("modes")
        assert window.stack.currentWidget() is window.pages["modes"]
        window.show_page("cron")
        assert window.stack.currentWidget() is window.pages["cron"]
        assert window.nav_buttons["cron"].text() == "Cron Jobs"
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


def test_switches_call_runtime_and_dashboard_has_quick_actions(qapp):
    runtime, face, window = make_window(qapp)
    try:
        window.speech_switch.setChecked(False)
        assert ("mute", True) in runtime.calls
        window.speech_switch.setChecked(True)
        assert ("mute", False) in runtime.calls

        dashboard = window.pages["dashboard"]
        labels = {button.text() for button in dashboard.findChildren(QPushButton)}
        assert "Study Mode" not in labels
        assert "Listen now" in labels
        assert "Mute voice" in labels
        assert "Voice settings" in labels
        assert "Hide face" not in labels
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


def test_custom_mode_can_be_created_without_ai(qapp):
    runtime, face, window = make_window(qapp)
    try:
        window.show_page("modes")
        page = window.custom_modes_page
        page.name_input.setText("Cinema night")
        page.trigger_input.setPlainText("cinema mode\nmovie time")
        page.greeting_input.setText("Lights down, sir.")
        page.website_input.setText("example.com")
        next(
            b for b in page.findChildren(QPushButton) if b.text() == "Add website"
        ).click()
        create = next(
            b for b in page.findChildren(QPushButton) if b.text() == "Create mode"
        )
        create.click()

        assert "cinema_night" in runtime.modes_data
        mode = runtime.modes_data["cinema_night"]
        assert mode["title"] == "Cinema night"
        assert mode["triggers"] == ["cinema mode", "movie time"]
        assert mode["greeting"] == "Lights down, sir."
        assert mode["urls"] == ["https://example.com"]

        assert window.stack.currentWidget() is window.pages["cinema_night"]
        assert window.nav_buttons["cinema_night"].text() == "Cinema night"
        entries = [key for key, _label in window._nav_entries()]
        assert entries.index("cinema_night") < entries.index("modes")

        editor = window.mode_editors["cinema_night"]
        assert editor.custom is True
        assert editor.trigger_input.toPlainText() == "cinema mode\nmovie time"
    finally:
        window.close()
        face.close()


def test_custom_mode_rejects_a_phrase_already_in_use(qapp):
    runtime, face, window = make_window(qapp)
    try:
        page = window.custom_modes_page
        page.name_input.setText("Study copy")
        page.trigger_input.setPlainText("study mode")
        create = next(
            b for b in page.findChildren(QPushButton) if b.text() == "Create mode"
        )
        create.click()

        assert "study_copy" not in runtime.modes_data
        assert "already used" in page.error_label.text()

        page.name_input.setText("Dashboard")
        page.trigger_input.setPlainText("dash mode")
        create.click()
        assert "dashboard" not in runtime.modes_data
        assert "reserved" in page.error_label.text()
    finally:
        window.close()
        face.close()


def test_custom_mode_editor_updates_spoken_triggers(qapp):
    runtime, face, window = make_window(qapp)
    try:
        page = window.custom_modes_page
        page.name_input.setText("Gaming")
        page.trigger_input.setPlainText("game mode")
        next(
            b for b in page.findChildren(QPushButton) if b.text() == "Create mode"
        ).click()

        editor = window.mode_editors["gaming"]
        editor.trigger_input.setPlainText("play time\nstart gaming")
        editor._save()
        assert runtime.modes_data["gaming"]["triggers"] == ["play time", "start gaming"]
    finally:
        window.close()
        face.close()


def test_custom_mode_can_be_deleted_from_the_page(qapp, monkeypatch):
    import talcon.ui.custom_modes as custom_modes

    runtime, face, window = make_window(qapp)
    try:
        page = window.custom_modes_page
        page.name_input.setText("Temporary")
        page.trigger_input.setPlainText("temporary mode")
        next(
            b for b in page.findChildren(QPushButton) if b.text() == "Create mode"
        ).click()
        assert "temporary" in runtime.modes_data

        monkeypatch.setattr(
            custom_modes.QMessageBox, "question",
            lambda *args, **kwargs: custom_modes.QMessageBox.StandardButton.Yes,
        )
        page.mode_list.setCurrentRow(0)
        next(
            b for b in page.findChildren(QPushButton) if b.text() == "Delete selected"
        ).click()

        assert "temporary" not in runtime.modes_data
        assert "temporary" not in window.pages
        assert "temporary" not in window.nav_buttons
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


def test_closing_the_window_terminates_talcon(qapp):
    runtime, face, window = make_window(qapp)
    try:
        window.close()
        assert ("exit",) in runtime.calls
    finally:
        face.close()


def test_app_hide_in_tray_keeps_face_visible_and_minimise_does_not_exit(qapp):
    """Exercise the real TalconApplication methods with passive stand-ins."""
    from talcon.app import TalconApplication

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

    app = TalconApplication.__new__(TalconApplication)
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
    assert not app.tray.messages, "only mute and unmute may show a notification"
    assert "shutdown_called" not in app.__dict__

    app.exit_completely()
    assert app.shutdown_called is True
    assert app.quit_called is True
    assert app.face.hidden == 1


# ---------------------------------------------- AI status panel updates
@pytest.mark.parametrize("payload", [
    {"kind": "accepted"},
    {"kind": "working"},
    {"kind": "progress", "text": "Reading the folder"},
    {"kind": "approval"},
    {"kind": "cache_hit"},
    {"kind": "answered"},
    {"kind": "cancelled"},
    {"kind": "error", "text": "Hermes is not reachable."},
    {"kind": "unknown-kind"},
    {},
])
def test_every_assistant_state_updates_the_ai_card(qapp, payload):
    """Regression: StatusText has no setText, so these updates crashed."""
    _runtime, _face, window = make_window(qapp)
    try:
        window._on_assistant_state(dict(payload))
    finally:
        window.close()
    assert window.ai_status._text

def test_commands_page_shows_heard_interpretation_and_timing(qapp):
    runtime, face, window = make_window(qapp)
    try:
        runtime.command_entries.append({
            "id": 1,
            "time": "18:04:12",
            "heard": "open crate",
            "phrase": "open krite",
            "action": "app_opened",
            "ms": 42,
            "confidence": 0.61,
        })
        runtime.bridge.command_log_changed.emit(runtime.command_log())
        page = window.command_log_page
        assert page.entry_list.count() == 1
        assert "open krite" in page.entry_list.item(0).text()
        page.entry_list.setCurrentRow(0)
        detail = page.detail.toPlainText()
        assert "open crate" in detail
        assert "42 ms" in detail
        assert "0.61" in detail
        assert page.clear_button.isEnabled()
        page._clear()
        assert runtime.command_entries == []
        assert page.clear_button.isEnabled() is False
    finally:
        window.close()
        face.close()
