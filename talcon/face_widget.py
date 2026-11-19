"""Transparent, circular desktop companion window for the TALCON face."""
from __future__ import annotations

import ctypes
import logging
import math
import time

from PySide6.QtCore import QPoint, Qt, QTimer, Signal
from PySide6.QtGui import QCursor, QGuiApplication, QPainter, QRegion
from PySide6.QtWidgets import QApplication, QMenu, QWidget

from .expressions import ActivityMonitor, ExpressionController, normalize_expression
from .face_render import FaceParams, paint_face

log = logging.getLogger("talcon.face")

FRAME_MS = 33  # ~30 FPS
WM_POWERBROADCAST = 0x0218
PBT_APMSUSPEND = 0x0004
PBT_APMRESUMEAUTOMATIC = 0x0012
PBT_APMRESUMESUSPEND = 0x0007


class _MSG(ctypes.Structure):
    _fields_ = [
        ("hWnd", ctypes.c_void_p),
        ("message", ctypes.c_uint),
        ("wParam", ctypes.c_size_t),
        ("lParam", ctypes.c_ssize_t),
    ]


class FaceWidget(QWidget):
    """The face overlay: left-click opens TALCON, drag repositions, right-click menu."""

    #: Emitted on every animation frame so other views (the menu bar face) can
    #: paint the exact same blended parameters without a second animator.
    frame_changed = Signal(object)
    #: Emitted when the resolved expression name changes.
    expression_changed = Signal(str)

    def __init__(self, runtime, on_open):
        super().__init__(
            None,
            Qt.WindowType.Tool
            | Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.NoDropShadowWindowHint,
        )
        self.runtime = runtime
        self.on_open = on_open
        self.setObjectName("TalconFaceOverlay")
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
        self.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground, True)
        self.setWindowTitle("TALCON")
        self.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))

        self._size = int(runtime.settings.get("face_size", 66) or 66)
        self._state = "idle"
        self._expression_name = "neutral"
        self._suspend_pause = False
        self._expression = ExpressionController(
            sleepy_after=float(runtime.settings.get("face_sleepy_after", 90) or 90),
            sleep_after=float(runtime.settings.get("face_sleep_after", 300) or 300),
            enabled=bool(runtime.settings.get("face_expressions", True)),
        )
        self._activity = ActivityMonitor()
        self._idle_seconds = 0.0
        self._activity_accum = 0.0

        self._current = FaceParams()
        self._last_tick = time.monotonic()
        self._observers = 0

        self._press_global: QPoint | None = None
        self._press_window: QPoint | None = None
        self._dragged = False

        self.setFixedSize(self._size, self._size)
        self._apply_mask()
        self._timer = QTimer(self)
        self._timer.setTimerType(Qt.TimerType.CoarseTimer)
        self._timer.setInterval(self._frame_interval())
        self._timer.timeout.connect(self._tick)
        self._update_tooltip()
        self._restore_position()

    # -- geometry ----------------------------------------------------------
    def _apply_mask(self) -> None:
        # A real window mask makes everything outside the circle click-through,
        # so the face never steals clicks from other applications.
        self.setMask(QRegion(0, 0, self.width(), self.height(), QRegion.RegionType.Ellipse))

    def _default_position(self) -> QPoint:
        screen = QGuiApplication.primaryScreen()
        if screen is None:
            return QPoint(100, 60)
        area = screen.availableGeometry()
        size = self._size
        # Just below the top bar, on the right beside the clock area, without
        # covering the clock, date or system icons.
        x = area.x() + area.width() - size - 170
        y = area.y() + 42
        return QPoint(max(area.x(), x), y)

    def _restore_position(self) -> None:
        x = self.runtime.settings.get("face_x")
        y = self.runtime.settings.get("face_y")
        if isinstance(x, int) and isinstance(y, int):
            point = QPoint(x, y)
            if self._point_visible(point):
                self.move(point)
                return
        self.move(self._default_position())

    def _point_visible(self, point: QPoint) -> bool:
        for screen in QGuiApplication.screens():
            if screen.availableGeometry().adjusted(-4, -4, 4, 4).contains(point):
                return True
        return False

    def _clamp_move(self, point: QPoint) -> QPoint:
        for screen in QGuiApplication.screens():
            area = screen.availableGeometry()
            if area.adjusted(-20, -20, 20, 20).contains(point + QPoint(self._size // 2, self._size // 2)):
                return QPoint(
                    max(area.x() - self._size // 3, min(point.x(), area.x() + area.width() - self._size * 2 // 3)),
                    max(area.y(), min(point.y(), area.y() + area.height() - self._size)),
                )
        return point

    def apply_settings(self, settings: dict) -> None:
        size = max(52, min(128, int(settings.get("face_size", 66) or 66)))
        if size != self._size:
            self._size = size
            self.setFixedSize(size, size)
            self._apply_mask()
        self._expression.configure(
            sleepy_after=float(settings.get("face_sleepy_after", self._expression.sleepy_after)),
            sleep_after=float(settings.get("face_sleep_after", self._expression.sleep_after)),
            enabled=bool(settings.get("face_expressions", self._expression.enabled)),
        )
        self._restore_position()
        self.update()

    def reset_position(self) -> None:
        point = self._default_position()
        self.move(point)
        self._save_position()

    def _save_position(self) -> None:
        screen = QGuiApplication.screenAt(self.geometry().center()) or QGuiApplication.primaryScreen()
        name = screen.name() if screen else ""
        try:
            self.runtime.set_face_position(self.x(), self.y(), name)
        except Exception:
            log.exception("Could not save face position")

    # -- animation ---------------------------------------------------------
    def _frame_interval(self) -> int:
        """30 FPS only while something is happening; slower when idle/asleep.

        A constant 30 FPS repaint while the face is asleep keeps the CPU (and
        the fan) busy for no visible benefit on a laptop.
        """
        name = getattr(self, "_expression_name", "neutral")
        if name == "sleeping":
            return 250
        if name == "sleepy":
            return 120
        if name in {"neutral", "blinking", "look_left", "look_right", "look_up"}:
            return 66
        return FRAME_MS

    def _sync_frame_interval(self) -> None:
        interval = self._frame_interval()
        if self._timer.interval() != interval:
            self._timer.setInterval(interval)

    def _update_tooltip(self) -> None:
        labels = {
            "neutral": "Idle", "blinking": "Idle", "sleepy": "Sleepy",
            "sleeping": "Sleeping", "attentive": "Listening",
            "speaking": "Speaking", "thinking": "Working", "happy": "Done",
            "excited": "Excited", "surprised": "Surprised", "curious": "Curious",
            "confused": "Needs attention", "focused": "Focused",
            "concerned": "Needs attention", "look_left": "Looking left",
            "look_right": "Looking right", "look_up": "Looking up",
        }
        label = labels.get(self._expression_name, "Idle")
        self.setToolTip(f"TALCON · {label} · click to configure")

    @property
    def current_params(self) -> FaceParams:
        """The most recent blended frame (used to paint the menu bar face)."""
        return self._current

    @property
    def expression_name(self) -> str:
        return self._expression_name

    def add_observer(self) -> None:
        """Keep the shared animator running while another view paints it."""
        self._observers += 1
        if self._suspend_pause:
            return
        if not self._timer.isActive():
            self._last_tick = time.monotonic()
            self._timer.start()

    def remove_observer(self) -> None:
        self._observers = max(0, self._observers - 1)
        if self._observers == 0 and not self.isVisible():
            self._timer.stop()

    def _start_timer(self) -> None:
        if self._suspend_pause or self._timer.isActive():
            return
        if self.isVisible() or self._observers:
            self._last_tick = time.monotonic()
            self._timer.start()

    def set_state(self, state: str) -> None:
        """Accept the assistant's coarse event names (idle, working, …)."""
        text = str(state or "").strip().lower()
        known = {
            "idle", "listening", "working", "completed", "error", "speaking",
            "thinking", "success", "failure", "excited", "surprised",
            "curious", "confused", "focused", "concerned", "sleepy",
            "sleeping", "neutral", "look_left", "look_right", "look_up",
            "heard", "command", "blinking",
        }
        if text not in known:
            text = "idle"
        self._state = text
        self._expression.set_event(text)
        self._expression_name = (
            self._expression.active_name
            if self._expression.active_priority > 20
            else normalize_expression(text)
        )
        self._update_tooltip()
        self.expression_changed.emit(self._expression_name)
        self._sync_frame_interval()
        self._start_timer()
        self.update()

    def set_expression(self, name: str, duration: float | None = None) -> None:
        """Direct expression control, used by tests and future integrations."""
        key = normalize_expression(name)
        self._expression.set_expression(key, duration=duration)
        self._state = key
        self._expression_name = (
            self._expression.active_name
            if self._expression.active_priority > 20
            else key
        )
        self._update_tooltip()
        self.expression_changed.emit(self._expression_name)
        self._sync_frame_interval()
        self._start_timer()
        self.update()

    def _tick(self) -> None:
        now = time.monotonic()
        dt = min(0.12, max(0.001, now - self._last_tick))
        self._last_tick = now

        # Poll the cheap Windows idle counter at ~2 Hz, not every frame.
        self._activity_accum += dt
        if self._activity_accum >= 0.5:
            self._idle_seconds = self._activity.idle_seconds(now)
            self._activity_accum = 0.0

        target = self._expression.update(
            now, idle_seconds=self._idle_seconds, dt=dt
        )
        if self._expression.current_name != self._expression_name:
            self._expression_name = self._expression.current_name
            self._update_tooltip()
            self._sync_frame_interval()
            self.expression_changed.emit(self._expression_name)

        factor = 1.0 - math.exp(-dt * 12.0)
        self._current = self._current.blended(target, factor)
        self.update()
        if self._observers:
            self.frame_changed.emit(self._current)

    # -- painting ----------------------------------------------------------
    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        paint_face(painter, float(self._size), self._current)
        painter.end()

    def showEvent(self, event) -> None:
        super().showEvent(event)
        now = time.monotonic()
        self._last_tick = now
        self._activity.mark_active(now)
        self._idle_seconds = 0.0
        self._activity_accum = 0.0
        if not self._suspend_pause:
            self._timer.start()

    def hideEvent(self, event) -> None:
        if self._observers == 0:
            self._timer.stop()
        super().hideEvent(event)

    def changeEvent(self, event) -> None:
        from PySide6.QtCore import QEvent

        if event.type() == QEvent.Type.WindowStateChange:
            if self.isMinimized():
                if self._observers == 0:
                    self._timer.stop()
            elif self.isVisible() and not self._suspend_pause:
                self._last_tick = time.monotonic()
                self._timer.start()
        super().changeEvent(event)

    def nativeEvent(self, event_type, message):
        # Pause animation while Windows is suspended to avoid needless wake-ups.
        try:
            if event_type in (b"windows_generic_MSG", b"windows_dispatcher_MSG"):
                msg = ctypes.cast(int(message), ctypes.POINTER(_MSG)).contents
                if msg.message == WM_POWERBROADCAST:
                    if msg.wParam == PBT_APMSUSPEND:
                        self._suspend_pause = True
                        self._timer.stop()
                    elif msg.wParam in (PBT_APMRESUMEAUTOMATIC, PBT_APMRESUMESUSPEND):
                        self._suspend_pause = False
                        if self.isVisible() or self._observers:
                            self._last_tick = time.monotonic()
                            self._activity.mark_active()
                            self._timer.start()
        except Exception:
            pass
        return super().nativeEvent(event_type, message)

    # -- mouse -------------------------------------------------------------
    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self._activity.mark_active()
            self._press_global = event.globalPosition().toPoint()
            self._press_window = self.pos()
            self._dragged = False
            event.accept()
        elif event.button() == Qt.MouseButton.RightButton:
            self._show_menu(event.globalPosition().toPoint())
            event.accept()
        else:
            super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:
        if self._press_global is not None and event.buttons() & Qt.MouseButton.LeftButton:
            delta = event.globalPosition().toPoint() - self._press_global
            if not self._dragged and abs(delta.x()) + abs(delta.y()) > 4:
                self._dragged = True
            if self._dragged:
                self.move(self._clamp_move(self._press_window + delta))
            event.accept()
        else:
            super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton and self._press_global is not None:
            if self._dragged:
                self._save_position()
            else:
                self.on_open()
            self._press_global = None
            self._press_window = None
            event.accept()
        else:
            super().mouseReleaseEvent(event)

    def _show_menu(self, point: QPoint) -> None:
        menu = QMenu(self)
        open_action = menu.addAction("Open TALCON")
        menu.addSeparator()
        face_action = menu.addAction("Hide animated face")
        mute_action = menu.addAction("Unmute voice" if self.runtime.muted else "Mute voice")
        menu.addSeparator()
        study_action = menu.addAction("Activate Study Mode")
        entertain_action = menu.addAction("Activate Entertainment Mode")
        stock_action = menu.addAction("Activate Stock Mode")
        menu.addSeparator()
        tray_action = menu.addAction("Hide control center to tray")
        exit_action = menu.addAction("Exit TALCON completely")
        chosen = menu.exec(point)
        if chosen is None:
            return
        if chosen == open_action:
            self.on_open()
        elif chosen == face_action:
            self.runtime.bridge.face_visibility_requested.emit(False)
        elif chosen == mute_action:
            self.runtime.set_muted(not self.runtime.muted)
        elif chosen == study_action:
            self.runtime.activate_mode("study")
        elif chosen == entertain_action:
            self.runtime.activate_mode("entertainment")
        elif chosen == stock_action:
            self.runtime.activate_mode("stock")
        elif chosen == tray_action:
            self.runtime.bridge.hide_to_tray.emit()
        elif chosen == exit_action:
            self.runtime.bridge.exit_requested.emit()
