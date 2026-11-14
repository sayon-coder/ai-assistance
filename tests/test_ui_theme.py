"""UI tests for the solid black theme, opacity and the new editor section."""
from __future__ import annotations

import shiboken6
import pytest
from PySide6.QtCore import Qt

from nova.face_widget import FaceWidget
from nova.ui import theme
from nova.ui.main_window import MainWindow, PAGES
from nova.ui.wizard import SetupWizard

from test_ui import StubRuntime


_CREATED: list = []


def make_window(qapp):
    runtime = StubRuntime()
    face = FaceWidget(runtime, lambda: None)
    window = MainWindow(runtime, face)
    _CREATED.extend((window, face))
    return runtime, face, window


@pytest.fixture(autouse=True)
def dispose_widgets(qapp):
    """Delete Qt widgets deterministically so interpreter exit never races Qt."""
    yield
    while _CREATED:
        widget = _CREATED.pop()
        try:
            widget.close()
            if shiboken6.isValid(widget):
                shiboken6.delete(widget)
        except RuntimeError:
            pass
    qapp.processEvents()


def test_palette_is_solid_black_and_opaque():
    assert theme.BG == "#080808"
    assert theme.SIDEBAR == "#111111"
    assert theme.SURFACE == "#181818"
    assert theme.INPUT == "#101010"
    assert theme.BORDER == "#292929"
    assert theme.TEXT == "#FFFFFF"
    assert theme.MUTED == "#A0A0A0"
    assert "QFrame#Shell" in theme.STYLESHEET
    assert f"background: {theme.BG};" in theme.STYLESHEET
    assert f"background: {theme.SURFACE};" in theme.STYLESHEET
    assert f"background: {theme.INPUT};" in theme.STYLESHEET


def test_control_center_is_opaque_and_resizable(qapp):
    runtime, face, window = make_window(qapp)
    try:
        assert not window.testAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        assert window.testAttribute(Qt.WidgetAttribute.WA_StyledBackground)
        assert window.styleSheet() == theme.STYLESHEET
        assert window.minimumWidth() >= 980
        assert window.minimumHeight() >= 660
        assert hasattr(window, "size_grip")
        window.resize(1120, 800)
        qapp.processEvents()
        assert window.width() == 1120
        assert window.height() == 800
    finally:
        window.close()
        face.close()


def test_always_listen_switch_reflects_the_runtime(qapp):
    runtime, face, window = make_window(qapp)
    try:
        runtime.settings["always_listen"] = False
        window.load_from_runtime()
        assert window.always_switch.isChecked() is False

        runtime.settings["always_listen"] = True
        window.load_from_runtime()
        assert window.always_switch.isChecked() is True
    finally:
        window.close()
        face.close()


def test_setup_wizard_is_opaque(qapp):
    runtime = StubRuntime()
    wizard = SetupWizard(runtime)
    _CREATED.append(wizard)
    try:
        assert not wizard.testAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        assert wizard.testAttribute(Qt.WidgetAttribute.WA_StyledBackground)
        assert wizard.objectName() == "Root"
        assert wizard.styleSheet() == theme.STYLESHEET
    finally:
        wizard.close()


def test_face_overlay_keeps_transparency(qapp):
    runtime = StubRuntime()
    face = FaceWidget(runtime, lambda: None)
    _CREATED.append(face)
    try:
        assert face.testAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        assert face.testAttribute(Qt.WidgetAttribute.WA_NoSystemBackground)
    finally:
        face.close()


def test_navigation_contains_custom_commands_section(qapp):
    assert [key for key, _ in PAGES] == [
        "dashboard", "study", "entertainment", "stock", "voice", "commands",
        "appearance", "monitoring", "startup", "about",
    ]
    runtime, face, window = make_window(qapp)
    try:
        window.show_page("commands")
        assert window.stack.currentWidget() is window.pages["commands"]
        assert window.nav_buttons["commands"].isChecked()
    finally:
        window.close()
        face.close()


def test_custom_editor_lists_saved_entries(qapp):
    runtime, face, window = make_window(qapp)
    runtime.custom_data = {
        "version": 1,
        "commands": [{
            "id": "c1", "name": "Open notes", "enabled": True,
            "triggers": ["open my notes"], "action": "open_app",
            "params": {"path": "C:/notes.exe", "args": []},
        }],
        "automations": [{
            "id": "a1", "name": "Study setup", "enabled": False,
            "triggers": ["prepare study"], "steps": [
                {"action": "activate_mode", "params": {"mode": "study"}, "delay": 0.0},
            ],
        }],
    }
    try:
        window.show_page("commands")
        editor = window.custom_editor
        editor.refresh()
        assert editor.command_list.count() == 1
        assert editor.automation_list.count() == 1
        assert "Open notes" in editor.command_list.item(0).text()
        assert "Study setup" in editor.automation_list.item(0).text()
    finally:
        window.close()
        face.close()
