"""Transparent, circular desktop companion window for the NOVA face."""
from __future__ import annotations

import ctypes
import logging
import math
import random
import time

from PySide6.QtCore import QPoint, Qt, QTimer
from PySide6.QtGui import QCursor, QGuiApplication, QPainter, QRegion
from PySide6.QtWidgets import QApplication, QMenu, QWidget

from .face_render import FaceParams, paint_face

log = logging.getLogger("nova.face")

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
    """The face overlay: left-click opens NOVA, drag repositions, right-click menu."""

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
        self.setObjectName("NovaFaceOverlay")
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
        self.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground, True)
        self.setWindowTitle("NOVA")
        self.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))

        self._size = int(runtime.settings.get("face_size", 66) or 66)
        self._state = "idle"
        self._state_until = 0.0
        self._suspend_pause = False

        self._current = FaceParams()
        self._started = time.monotonic()
        self._last_tick = self._started
        self._next_blink = self._started + random.uniform(2.2, 5.0)
        self._blink_started: float | None = None
        self._gaze_target = (0.0, 0.0)
        self._next_gaze = self._started + random.uniform(2.0, 5.0)

        self._press_global: QPoint | None = None
        self._press_window: QPoint | None = None
        self._dragged = False

        self.setFixedSize(self._size, self._size)
        self._apply_mask()
        self._timer = QTimer(self)
        self._timer.setInterval(FRAME_MS)
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
    def _update_tooltip(self) -> None:
        labels = {
            "idle": "Idle", "listening": "Listening", "working": "Working",
            "completed": "Done", "error": "Needs attention", "speaking": "Speaking",
        }
        self.setToolTip(f"NOVA · {labels.get(self._state, 'Idle')} · click to configure")

    def set_state(self, state: str) -> None:
        if state not in {"idle", "listening", "working", "completed", "error", "speaking"}:
            state = "idle"
        now = time.monotonic()
        self._state = state
        if state in {"completed", "error"}:
            self._state_until = now + 1.9
        self._update_tooltip()
        if self.isVisible() and not self._timer.isActive() and not self._suspend_pause:
            self._timer.start()
        self.update()

    def _tick(self) -> None:
        now = time.monotonic()
        dt = min(0.12, max(0.001, now - self._last_tick))
        self._last_tick = now
        elapsed = now - self._started

        if self._state in {"completed", "error"} and now >= self._state_until:
            self._state = "idle"
            self._update_tooltip()

        state = self._state
        gx, gy = self._gaze_target
        tilt = 0.0
        arc = 0.0
        glow = 1.0

        if state == "idle":
            openness = 1.0
        elif state == "listening":
            openness = 1.13
            gx, gy = 0.12, -0.34
            glow = 1.14
        elif state == "working":
            openness = 0.80
            gx = math.sin(elapsed * 0.65) * 0.34
            gy = 0.28
            glow = 0.94
        elif state == "completed":
            openness = 0.10
            arc = 1.0
            glow = 1.18
        elif state == "error":
            openness = 0.62
            tilt = 9.0
            gy = 0.08
            glow = 1.02
        else:  # speaking
            openness = 0.96 + math.sin(elapsed * 6.4) * 0.05
            gx, gy = 0.10, 0.03
            glow = 1.06

        # Natural blinking, only while genuinely resting.
        blink_factor = 1.0
        if self._blink_started is not None:
            progress = (now - self._blink_started) / 0.19
            if progress >= 1.0:
                self._blink_started = None
                self._next_blink = now + random.uniform(2.8, 7.4)
            else:
                blink_factor = 1.0 - math.sin(math.pi * progress)
        elif state in {"idle", "speaking"} and now >= self._next_blink:
            self._blink_started = now

        if state == "idle":
            if now >= self._next_gaze:
                gx = random.uniform(-0.34, 0.34)
                gy = random.uniform(-0.14, 0.20)
                self._gaze_target = (gx, gy)
                self._next_gaze = now + random.uniform(2.6, 7.0)

        target = FaceParams(
            openness=max(0.0, openness * blink_factor),
            gaze_x=gx, gaze_y=gy, tilt=tilt, arc=arc, glow=glow,
        )
        factor = 1.0 - math.exp(-dt * 15.0)
        self._current = self._current.blended(target, factor)
        self.update()

    # -- painting ----------------------------------------------------------
    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        paint_face(painter, float(self._size), self._current)
        painter.end()

    def showEvent(self, event) -> None:
        super().showEvent(event)
        self._last_tick = time.monotonic()
        if not self._suspend_pause:
            self._timer.start()

    def hideEvent(self, event) -> None:
        self._timer.stop()
        super().hideEvent(event)

    def changeEvent(self, event) -> None:
        from PySide6.QtCore import QEvent

        if event.type() == QEvent.Type.WindowStateChange and self.isMinimized():
            self._timer.stop()
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
                        if self.isVisible():
                            self._last_tick = time.monotonic()
                            self._timer.start()
        except Exception:
            pass
        return super().nativeEvent(event_type, message)

    # -- mouse -------------------------------------------------------------
    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
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
        open_action = menu.addAction("Open NOVA")
        menu.addSeparator()
        face_action = menu.addAction("Hide animated face")
        mute_action = menu.addAction("Unmute voice" if self.runtime.muted else "Mute voice")
        menu.addSeparator()
        study_action = menu.addAction("Activate Study Mode")
        entertain_action = menu.addAction("Activate Entertainment Mode")
        menu.addSeparator()
        tray_action = menu.addAction("Hide control center to tray")
        exit_action = menu.addAction("Exit NOVA completely")
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
        elif chosen == tray_action:
            self.runtime.bridge.hide_to_tray.emit()
        elif chosen == exit_action:
            self.runtime.bridge.exit_requested.emit()
