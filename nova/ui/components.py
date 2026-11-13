"""Small reusable widgets: cards, toggles, status rows and a face preview."""
from __future__ import annotations

import math
import time

from PySide6.QtCore import QEasingCurve, QRectF, Qt, QTimer, QVariantAnimation
from PySide6.QtGui import QColor, QPainter
from PySide6.QtWidgets import (
    QAbstractButton, QFrame, QHBoxLayout, QLabel, QSizePolicy, QVBoxLayout, QWidget,
)

from ..face_render import FaceParams, paint_face
from . import theme


class Card(QFrame):
    def __init__(self, title: str = "", subtitle: str = "", parent=None):
        super().__init__(parent)
        self.setObjectName("Card")
        self.body = QVBoxLayout(self)
        self.body.setContentsMargins(18, 16, 18, 16)
        self.body.setSpacing(10)
        if title:
            label = QLabel(title)
            label.setObjectName("CardTitle")
            self.body.addWidget(label)
        if subtitle:
            sub = QLabel(subtitle)
            sub.setObjectName("CardSub")
            sub.setWordWrap(True)
            self.body.addWidget(sub)

    def add(self, widget: QWidget, stretch: int = 0):
        self.body.addWidget(widget, stretch)
        return widget

    def add_layout(self, layout, stretch: int = 0):
        self.body.addLayout(layout, stretch)
        return layout

    def add_spacing(self, amount: int) -> None:
        self.body.addSpacing(amount)


class Switch(QAbstractButton):
    """Compact animated on/off switch."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setCheckable(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFixedSize(44, 24)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self._pos = 0.0
        self._anim = QVariantAnimation(self)
        self._anim.setDuration(140)
        self._anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._anim.valueChanged.connect(self._animate_to)
        self.toggled.connect(self._start_animation)

    def _animate_to(self, value) -> None:
        self._pos = float(value)
        self.update()

    def _start_animation(self, checked: bool) -> None:
        self._anim.stop()
        self._anim.setStartValue(float(self._pos))
        self._anim.setEndValue(1.0 if checked else 0.0)
        self._anim.start()

    def set_checked_silently(self, checked: bool) -> None:
        was_blocked = self.blockSignals(True)
        self.setChecked(bool(checked))
        self.blockSignals(was_blocked)
        self._pos = 1.0 if checked else 0.0
        self.update()

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setPen(Qt.PenStyle.NoPen)
        off = QColor(38, 54, 75)
        on = QColor(theme.ACCENT)
        track = QColor(
            int(off.red() + (on.red() - off.red()) * self._pos),
            int(off.green() + (on.green() - off.green()) * self._pos),
            int(off.blue() + (on.blue() - off.blue()) * self._pos),
        )
        painter.setBrush(track)
        painter.drawRoundedRect(QRectF(0, 0, self.width(), self.height()), 12, 12)
        diameter = 18.0
        x = 3.0 + self._pos * (self.width() - diameter - 6.0)
        painter.setBrush(QColor(255, 255, 255))
        painter.drawEllipse(QRectF(x, 3.0, diameter, diameter))
        painter.end()


class StatusText(QWidget):
    """Coloured dot plus a short status sentence."""

    def __init__(self, text: str = "", parent=None):
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)
        self.dot = QLabel("●")
        self.dot.setFixedWidth(12)
        self.label = QLabel(text)
        self.label.setWordWrap(True)
        self.label.setObjectName("Muted")
        layout.addWidget(self.dot, 0, Qt.AlignmentFlag.AlignTop)
        layout.addWidget(self.label, 1)
        self._text: str | None = None
        self.set_text(text)

    def set_text(self, text: str, colour: str | None = None) -> None:
        if str(text) == self._text and colour is None:
            return
        self._text = str(text)
        self.label.setText(str(text))
        self.dot.setStyleSheet(f"color: {colour or theme.status_colour(text)}; font-size: 9pt;")


def row(*widgets: QWidget, spacing: int = 10) -> QWidget:
    holder = QWidget()
    layout = QHBoxLayout(holder)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.setSpacing(spacing)
    for widget in widgets:
        layout.addWidget(widget)
    return holder


def muted(text: str, wrap: bool = True) -> QLabel:
    label = QLabel(text)
    label.setObjectName("Muted")
    label.setWordWrap(wrap)
    return label


def footnote(text: str) -> QLabel:
    label = QLabel(text)
    label.setObjectName("Footnote")
    label.setWordWrap(True)
    return label


class Heading(QWidget):
    def __init__(self, title: str, subtitle: str = "", parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)
        heading = QLabel(title)
        heading.setObjectName("PageTitle")
        layout.addWidget(heading)
        if subtitle:
            sub = QLabel(subtitle)
            sub.setObjectName("PageSub")
            sub.setWordWrap(True)
            layout.addWidget(sub)


class Divider(QFrame):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedHeight(1)
        self.setStyleSheet("background: #1E2B3D; border: none;")
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)


class FacePreview(QWidget):
    """Live preview that cycles through the NOVA expressions."""

    STATES = ("idle", "listening", "working", "completed", "error", "speaking")

    def __init__(self, size: int = 148, parent=None):
        super().__init__(parent)
        self.setFixedSize(size, size)
        self._size = size
        self._current = FaceParams()
        self._started = time.monotonic()
        self._last = self._started
        self._index = 0
        self._next_state = self._started + 2.6
        self._timer = QTimer(self)
        self._timer.setInterval(33)
        self._timer.timeout.connect(self._tick)
        self._timer.start()

    def _target(self) -> FaceParams:
        state = self.STATES[self._index]
        elapsed = time.monotonic() - self._started
        if state == "idle":
            return FaceParams(1.0, 0.12, 0.02, 0.0, 0.0, 1.0)
        if state == "listening":
            return FaceParams(1.13, 0.12, -0.34, 0.0, 0.0, 1.14)
        if state == "working":
            return FaceParams(0.80, math.sin(elapsed * 0.65) * 0.34, 0.28, 0.0, 0.0, 0.94)
        if state == "completed":
            return FaceParams(0.10, 0.0, 0.0, 0.0, 1.0, 1.18)
        if state == "error":
            return FaceParams(0.62, 0.0, 0.08, 9.0, 0.0, 1.02)
        return FaceParams(0.96 + math.sin(elapsed * 6.4) * 0.05, 0.10, 0.03, 0.0, 0.0, 1.06)

    def _tick(self) -> None:
        now = time.monotonic()
        dt = min(0.1, max(0.001, now - self._last))
        self._last = now
        if now >= self._next_state:
            self._index = (self._index + 1) % len(self.STATES)
            self._next_state = now + 2.6
        self._current = self._current.blended(self._target(), 1.0 - math.exp(-dt * 12.0))
        self.update()

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        paint_face(painter, float(self._size), self._current)
        painter.end()
