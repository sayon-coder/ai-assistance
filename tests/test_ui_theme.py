"""UI tests for the solid black theme, opacity and the new editor section."""
from __future__ import annotations

import shiboken6
import pytest
from PySide6.QtCore import Qt

from talcon.face_widget import FaceWidget
from talcon.ui import theme
from talcon.ui.main_window import MainWindow, PAGES
from talcon.ui.wizard import SetupWizard

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


def test_navigation_contains_expected_sections(qapp):
    assert [key for key, _ in PAGES] == [
        "dashboard", "history", "commands", "study", "entertainment", "stock",
        "modes", "voice", "appearance", "monitoring", "cron", "startup",
    ]
    runtime, face, window = make_window(qapp)
    try:
        window.show_page("appearance")
        assert window.stack.currentWidget() is window.pages["appearance"]
        assert window.nav_buttons["appearance"].isChecked()
    finally:
        window.close()
        face.close()
