"""Tests for the menu bar face overlay and its clock detection."""
from __future__ import annotations

from types import SimpleNamespace

from PySide6.QtCore import QPoint, QRect, Qt
from PySide6.QtGui import QColor, QImage, QPainter
from PySide6.QtTest import QTest

from talcon import menu_bar_face as menu
from talcon.face_render import FaceParams
from talcon.face_widget import FaceWidget
from talcon.menu_bar_face import (
    BarInfo, MenuBarFace, clock_right_ratio_from_image, locate_menu_bar,
)


class _Signal:
    def emit(self, *args):
        pass


class _Bridge:
    face_visibility_requested = _Signal()
    hide_to_tray = _Signal()
    exit_requested = _Signal()


def make_runtime(settings=None):
    saved = []
    state = dict(settings or {})

    def save_settings(values):
        saved.append(dict(values))
        state.update(values)

    runtime = SimpleNamespace(
        settings=state,
        bridge=_Bridge(),
        muted=False,
        set_face_position=lambda x, y, screen="": None,
        set_muted=lambda value: None,
        activate_mode=lambda name: None,
        save_settings=save_settings,
    )
    runtime.saved = saved
    return runtime


def _bar_image(width=1000, height=20, background=(49, 50, 54), ink=(250, 250, 250)):
    image = QImage(width, height, QImage.Format.Format_ARGB32)
    image.fill(QColor(*background))
    painter = QPainter(image)
    painter.fillRect(20, 5, 22, 10, QColor(*ink))      # left status icon
    painter.fillRect(430, 4, 110, 12, QColor(*ink))    # clock text
    painter.fillRect(950, 5, 22, 10, QColor(*ink))     # tray icon
    painter.end()
    return image


def test_clock_right_edge_is_found_on_a_dark_bar():
    ratio = clock_right_ratio_from_image(_bar_image())
    assert ratio is not None
    assert abs(ratio - 540 / 1000) < 0.01


def test_clock_right_edge_is_found_on_a_light_bar():
    ratio = clock_right_ratio_from_image(
        _bar_image(background=(240, 240, 242), ink=(20, 20, 22))
    )
    assert ratio is not None
    assert abs(ratio - 540 / 1000) < 0.01


def test_narrow_icons_are_not_mistaken_for_the_clock():
    image = QImage(1000, 20, QImage.Format.Format_ARGB32)
    image.fill(QColor(40, 40, 44))
    painter = QPainter(image)
    for x in (20, 120, 220, 900):
        painter.fillRect(x, 5, 18, 10, QColor(240, 240, 240))
    painter.end()
    assert clock_right_ratio_from_image(image) is None


def test_menu_bar_fallback_uses_the_reserved_top_strip(qapp, monkeypatch):
    monkeypatch.setattr(menu, "_enum_bar_window", lambda: None)
    screen = qapp.primaryScreen()
    info = locate_menu_bar(screen)
    assert info.detected is False
    assert info.hwnd is None
    assert info.rect.top() == screen.geometry().top()
    assert info.rect.width() == screen.geometry().width()


def test_overlay_is_frameless_topmost_and_circular(qapp):
    runtime = make_runtime({"face_size": 66})
    source = FaceWidget(runtime, lambda: None)
    overlay = MenuBarFace(runtime, source, lambda: None)
    try:
        flags = overlay.windowFlags()
        for flag in (
            Qt.WindowType.Tool,
            Qt.WindowType.FramelessWindowHint,
            Qt.WindowType.WindowStaysOnTopHint,
            Qt.WindowType.NoDropShadowWindowHint,
            Qt.WindowType.WindowDoesNotAcceptFocus,
        ):
            assert flags & flag
        assert overlay.testAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        assert overlay.testAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        assert overlay.mask().contains(QPoint(overlay.width() // 2, overlay.height() // 2))
        assert not overlay.mask().contains(QPoint(1, 1))
    finally:
        overlay.shutdown()
        source.close()


def test_click_opens_the_existing_assistant(qapp):
    runtime = make_runtime({"face_size": 66})
    source = FaceWidget(runtime, lambda: None)
    opened = []
    overlay = MenuBarFace(runtime, source, lambda: opened.append(True))
    try:
        QTest.mouseClick(
            overlay, Qt.MouseButton.LeftButton,
            pos=QPoint(overlay.width() // 2, overlay.height() // 2),
        )
        assert opened == [True]
    finally:
        overlay.shutdown()
        source.close()


def test_overlay_paints_the_same_frame_as_the_desktop_face(qapp):
    runtime = make_runtime({"face_size": 66})
    source = FaceWidget(runtime, lambda: None)
    overlay = MenuBarFace(runtime, source, lambda: None)
    try:
        params = FaceParams(openness=0.35, arc=0.7, tilt=4.0)
        overlay._on_frame(params)
        assert overlay._params is params
        source.set_state("completed")
        assert "Done" in overlay.toolTip()
        source.set_state("failure")
        assert "attention" in overlay.toolTip().lower()
    finally:
        overlay.shutdown()
        source.close()


def test_overlay_shares_one_animator_and_releases_it(qapp):
    runtime = make_runtime({"face_size": 66})
    source = FaceWidget(runtime, lambda: None)
    overlay = MenuBarFace(runtime, source, lambda: None)
    try:
        overlay._attach_observer()
        overlay._attach_observer()
        assert source._observers == 1
        assert source._timer.isActive()
        overlay.shutdown()
        assert source._observers == 0
    finally:
        source.close()


def test_position_is_immediately_right_of_the_clock(qapp, monkeypatch):
    runtime = make_runtime({"face_size": 66, "menu_bar_face_size": 18})
    source = FaceWidget(runtime, lambda: None)
    overlay = MenuBarFace(runtime, source, lambda: None)
    try:
        monkeypatch.setattr(
            menu, "locate_menu_bar",
            lambda screen=None: BarInfo(7, QRect(0, 0, 1000, 20), True),
        )
        monkeypatch.setattr(menu, "detect_clock_ratio", lambda bar: 0.5)
        overlay.refresh_position(redetect=True)
        assert overlay._clock_ratio == 0.5
        assert overlay.pos() == QPoint(500 + menu.CLOCK_GAP, 1)
    finally:
        overlay.shutdown()
        source.close()


def test_dragged_ratio_is_saved_and_used(qapp, monkeypatch):
    runtime = make_runtime({"face_size": 66, "menu_bar_face_size": 18})
    source = FaceWidget(runtime, lambda: None)
    overlay = MenuBarFace(runtime, source, lambda: None)
    try:
        monkeypatch.setattr(
            menu, "locate_menu_bar",
            lambda screen=None: BarInfo(7, QRect(0, 0, 1000, 20), True),
        )
        monkeypatch.setattr(menu, "detect_clock_ratio", lambda bar: 0.5)
        overlay.refresh_position(redetect=True)
        overlay._settings["menu_bar_face_x_ratio"] = 0.25
        overlay._place()
        assert overlay.pos().x() == 250
    finally:
        overlay.shutdown()
        source.close()


def test_recalibrate_clears_the_manual_anchor(qapp, monkeypatch):
    runtime = make_runtime({
        "face_size": 66,
        "menu_bar_face_size": 18,
        "menu_bar_face_x_ratio": 0.4,
        "menu_bar_face_x_offset": 12,
    })
    source = FaceWidget(runtime, lambda: None)
    overlay = MenuBarFace(runtime, source, lambda: None)
    try:
        monkeypatch.setattr(
            menu, "locate_menu_bar",
            lambda screen=None: BarInfo(7, QRect(0, 0, 1000, 20), True),
        )
        monkeypatch.setattr(menu, "detect_clock_ratio", lambda bar: 0.5)
        overlay.recalibrate()
        assert runtime.saved[-1]["menu_bar_face_x_ratio"] == 0.0
        assert runtime.saved[-1]["menu_bar_face_x_offset"] == 0
        assert overlay.pos() == QPoint(500 + menu.CLOCK_GAP, 1)
    finally:
        overlay.shutdown()
        source.close()


def test_disabling_hides_the_overlay(qapp):
    runtime = make_runtime({"face_size": 66, "menu_bar_face_size": 18})
    source = FaceWidget(runtime, lambda: None)
    overlay = MenuBarFace(runtime, source, lambda: None)
    try:
        overlay.apply_settings({"menu_bar_face_enabled": False})
        assert overlay.isVisible() is False
        assert source._observers == 0
    finally:
        overlay.shutdown()
        source.close()


def _hwnd_int(handle):
    """Return an HWND's integer value (``int(c_void_p)`` changed in Python 3.14)."""
    if isinstance(handle, int):
        return handle
    return int(handle.value)


def test_overlay_is_raised_into_the_topmost_band(qapp, monkeypatch):
    calls = []

    class _FakeUser32:
        @staticmethod
        def IsWindow(hwnd):
            return True

        @staticmethod
        def GetWindow(hwnd, command):
            assert command == menu.GW_HWNDPREV
            return 0

        @staticmethod
        def SetWindowPos(hwnd, after, x, y, cx, cy, flags):
            calls.append((_hwnd_int(hwnd), _hwnd_int(after), flags))
            return True

    runtime = make_runtime({"face_size": 66, "menu_bar_face_size": 18})
    source = FaceWidget(runtime, lambda: None)
    overlay = MenuBarFace(runtime, source, lambda: None)
    try:
        monkeypatch.setattr(menu, "_user32", _FakeUser32)
        monkeypatch.setattr(
            menu, "locate_menu_bar",
            lambda screen=None: BarInfo(7, QRect(0, 0, 1000, 20), True),
        )
        monkeypatch.setattr(menu, "detect_clock_ratio", lambda bar: 0.5)
        overlay.refresh_position(redetect=True)
        assert calls
        hwnd, after, flags = calls[-1]
        assert after == menu.HWND_TOPMOST.value
        assert flags & menu.SWP_NOMOVE
        assert flags & menu.SWP_NOSIZE
        assert flags & menu.SWP_NOACTIVATE
        assert hwnd == int(overlay.winId())
    finally:
        overlay.shutdown()
        source.close()


def test_stacking_is_skipped_when_already_above_the_bar(qapp, monkeypatch):
    calls = []

    class _FakeUser32:
        @staticmethod
        def IsWindow(hwnd):
            return True

        @staticmethod
        def GetWindow(hwnd, command):
            return int(overlay.winId())

        @staticmethod
        def SetWindowPos(*args):
            calls.append(args)
            return True

    runtime = make_runtime({"face_size": 66, "menu_bar_face_size": 18})
    source = FaceWidget(runtime, lambda: None)
    overlay = MenuBarFace(runtime, source, lambda: None)
    try:
        monkeypatch.setattr(menu, "_user32", _FakeUser32)
        overlay._bar_hwnd = 7
        overlay._stack_above_bar()
        assert calls == []
    finally:
        overlay.shutdown()
        source.close()


def test_watchdog_reasserts_the_topmost_stacking(qapp, monkeypatch):
    calls = []

    class _FakeUser32:
        @staticmethod
        def IsWindow(hwnd):
            return True

    runtime = make_runtime({"face_size": 66, "menu_bar_face_size": 18})
    source = FaceWidget(runtime, lambda: None)
    overlay = MenuBarFace(runtime, source, lambda: None)
    try:
        monkeypatch.setattr(menu, "_user32", _FakeUser32)
        monkeypatch.setattr(
            menu, "_logical_rect", lambda hwnd, dpr: QRect(0, 0, 1000, 20)
        )
        monkeypatch.setattr(overlay, "_stack_above_bar", lambda: calls.append(True))
        overlay._bar = QRect(0, 0, 1000, 20)
        overlay._bar_hwnd = 7
        overlay.show()
        try:
            overlay._watchdog()
            assert calls == [True]
        finally:
            overlay.hide()
    finally:
        overlay.shutdown()
        source.close()
