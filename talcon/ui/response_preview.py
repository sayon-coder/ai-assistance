"""Compact spoken-response preview and its expanded reader.

The preview is a tiny, non-activating window that follows the avatar (the
desktop face, or the menu-bar face when the desktop face is hidden).  It
never takes keyboard focus and only ever shows the first two lines of the
response that the speech engine is playing.

Clicking opens :class:`ExpandedResponse`, a separate window with the complete
text, scrolling, a copy button and a close button.  Both views are fed from
the exact same final response text the speech engine received.
"""
from __future__ import annotations

import ctypes
import logging
import time
from ctypes import wintypes

from PySide6.QtCore import QPointF, QRect, QRectF, Qt, QTimer
from PySide6.QtGui import (
    QColor, QCursor, QGuiApplication, QPainter, QPen,
)
from PySide6.QtWidgets import (
    QFrame, QHBoxLayout, QLabel, QMenu, QPushButton, QTextBrowser,
    QVBoxLayout, QWidget,
)

from . import theme

log = logging.getLogger("talcon.preview")

#: Compact box width in logical pixels.  Two short lines of a normal response
#: fit comfortably without covering the desktop.
PREVIEW_WIDTH = 360
PREVIEW_MAX_LINES = 2
PREVIEW_PADDING_X = 12
PREVIEW_PADDING_Y = 9
PREVIEW_SPACING = 10
PREVIEW_ICON = 30
PREVIEW_CHEVRON = 14
PREVIEW_HEIGHT = 58
PREVIEW_GAP = 8
#: The compact box disappears automatically this long after the response
#: appears, but never while TALCON is still speaking.
AUTO_HIDE_SECONDS = 5.0

GWL_EXSTYLE = -20
WS_EX_NOACTIVATE = 0x08000000
WS_EX_TOOLWINDOW = 0x00000080

_user32 = ctypes.windll.user32
_user32.GetWindowLongW.argtypes = [wintypes.HWND, ctypes.c_int]
_user32.GetWindowLongW.restype = ctypes.c_long
_user32.SetWindowLongW.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_long]
_user32.SetWindowLongW.restype = ctypes.c_long


def preview_lines(text: str, measure, width: int,
                  max_lines: int = PREVIEW_MAX_LINES) -> str:
    """Wrap ``text`` into at most ``max_lines`` visual lines.

    ``measure`` is a callable such as ``QFontMetrics.horizontalAdvance``.  Any
    text that does not fit is cut at a word boundary and marked with an
    ellipsis, so the compact box never grows past two lines.
    """
    words = str(text or "").split()
    if not words or width <= 0:
        return ""
    lines: list[str] = []
    current = ""
    index = 0
    while index < len(words) and len(lines) < max_lines:
        word = words[index]
        candidate = f"{current} {word}".strip()
        if current and measure(candidate) > width:
            lines.append(current)
            current = word
        else:
            current = candidate
        index += 1
    truncated = index < len(words)
    if current:
        if len(lines) < max_lines:
            lines.append(current)
        else:
            truncated = True
    if not lines:
        return ""
    if truncated:
        suffix = "…"
        last = lines[-1]
        while last and measure(last + suffix) > width:
            last = last.rsplit(" ", 1)[0] if " " in last else ""
        lines[-1] = (last + suffix) if last else suffix
    return "\n".join(lines[:max_lines])


class _WaveIcon(QWidget):
    """Small waveform tile matching the demo's speaker indicator."""

    def __init__(self, size: int = PREVIEW_ICON, parent=None):
        super().__init__(parent)
        self.setFixedSize(size, size)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(38, 46, 60))
        painter.drawRoundedRect(
            QRectF(0, 0, self.width(), self.height()), 9, 9
        )
        bar_width = 3.0
        gap = 3.0
        heights = (0.34, 0.62, 0.9, 0.52, 0.26)
        total = len(heights) * bar_width + (len(heights) - 1) * gap
        x = (self.width() - total) / 2.0
        painter.setBrush(QColor("#8FC0FF"))
        for fraction in heights:
            height = max(3.0, (self.height() - 10) * fraction)
            y = (self.height() - height) / 2.0
            painter.drawRoundedRect(
                QRectF(x, y, bar_width, height), 1.5, 1.5
            )
            x += bar_width + gap
        painter.end()


class _Chevron(QWidget):
    """Thin down-chevron that hints the box opens a larger view."""

    def __init__(self, size: int = PREVIEW_CHEVRON, parent=None):
        super().__init__(parent)
        self.setFixedSize(size, size)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        pen = QPen(QColor("#A9B6C9"), 1.6)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        painter.setPen(pen)
        width, height = self.width(), self.height()
        painter.drawLine(
            QPointF(width * 0.22, height * 0.40),
            QPointF(width * 0.5, height * 0.62),
        )
        painter.drawLine(
            QPointF(width * 0.5, height * 0.62),
            QPointF(width * 0.78, height * 0.40),
        )
        painter.end()


class ResponsePreview(QWidget):
    """Two-line, clickable response box anchored below the avatar."""

    def __init__(self, on_expand=None):
        super().__init__(
            None,
            Qt.WindowType.Tool
            | Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.NoDropShadowWindowHint
            | Qt.WindowType.WindowDoesNotAcceptFocus,
        )
        self.setObjectName("TalconResponsePreview")
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
        self.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground, True)
        self.setWindowTitle("TALCON response preview")
        self.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self.setToolTip("Click to read the full response · right-click to dismiss")

        self._entry: dict = {}
        self._text = ""
        self._has_response = False
        self._progress = ""
        self._shown_at = 0.0
        self._speaking = False
        self._anchor_provider = None
        self._on_expand = on_expand or (lambda entry: None)

        self.icon = _WaveIcon(parent=self)
        self.label = QLabel(self)
        self.label.setTextFormat(Qt.TextFormat.PlainText)
        self.label.setWordWrap(False)
        self.label.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.label.setStyleSheet(
            "color: #E8F0FF; font-size: 9pt; background: transparent;"
        )
        self.label.setAlignment(
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter
        )
        self.chevron = _Chevron(parent=self)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(
            PREVIEW_PADDING_X, PREVIEW_PADDING_Y,
            PREVIEW_PADDING_X, PREVIEW_PADDING_Y,
        )
        layout.setSpacing(PREVIEW_SPACING)
        layout.addWidget(self.icon, 0, Qt.AlignmentFlag.AlignVCenter)
        layout.addWidget(self.label, 1)
        layout.addWidget(self.chevron, 0, Qt.AlignmentFlag.AlignVCenter)
        self.setFixedWidth(PREVIEW_WIDTH)
        self.setFixedHeight(PREVIEW_HEIGHT)
        self.hide()

        # Cheap follow timer: keeps the box under the avatar when the face is
        # dragged, the bar moves or the avatar is hidden/shown.
        self._follow = QTimer(self)
        self._follow.setInterval(300)
        self._follow.timeout.connect(self._sync_visibility)
        self._follow.start()

    # -- public API --------------------------------------------------------
    def set_anchor_provider(self, provider) -> None:
        """``provider()`` returns the avatar's ``QRect`` or ``None``."""
        self._anchor_provider = provider

    def current_entry(self) -> dict:
        return dict(self._entry)

    def show_response(self, entry: dict) -> None:
        """Show the two-line preview for one completed response."""
        if not entry:
            return
        text = str(entry.get("text") or "").strip()
        if not text:
            return
        self._entry = dict(entry)
        self._text = text
        self._has_response = True
        self._progress = ""
        self._shown_at = time.monotonic()
        self._apply_text()
        self._sync_visibility()

    def dismiss(self) -> None:
        """Hide the preview until another response arrives."""
        self._has_response = False
        self._progress = ""
        self.hide()

    # -- assistant progress --------------------------------------------------
    def show_progress(self, text: str) -> None:
        """Line of status while a long AI request is running.

        It replaces the last response until the new answer arrives; the full
        text of previous responses always stays available in History.
        """
        self._progress = str(text or "").strip() or "Thinking…"
        self._has_response = False
        self._shown_at = time.monotonic()
        self._apply_text()
        self._sync_visibility()

    def clear_progress(self) -> None:
        if not self._progress:
            return
        self._progress = ""
        self._apply_text()
        self._sync_visibility()

    def note_speaking(self, speaking: bool) -> None:
        """Track playback so the auto-hide never cuts a response short."""
        self._speaking = bool(speaking)
        if not self._speaking:
            self._sync_visibility()

    # -- rendering ---------------------------------------------------------
    def _apply_text(self) -> None:
        metrics = self.label.fontMetrics()
        width = (
            PREVIEW_WIDTH
            - PREVIEW_PADDING_X * 2
            - PREVIEW_ICON
            - PREVIEW_CHEVRON
            - PREVIEW_SPACING * 2
        )
        source = self._progress if (self._progress and not self._has_response) \
            else self._text
        lines = preview_lines(source, metrics.horizontalAdvance, width)
        self.label.setText(lines)

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        rect = self.rect().adjusted(1, 1, -1, -1)
        painter.setPen(QPen(QColor("#33415C"), 1))
        painter.setBrush(QColor(18, 21, 27, 245))
        painter.drawRoundedRect(rect, 14, 14)
        # A slim blue edge ties the box to the avatar's accent without adding
        # any new visual language.
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(theme.ACCENT))
        painter.drawRoundedRect(
            QRect(rect.left() + 1, rect.top() + 11, 2, max(0, rect.height() - 22)),
            1, 1,
        )
        painter.end()

    # -- geometry ----------------------------------------------------------
    def _anchor_rect(self) -> QRect | None:
        if self._anchor_provider is None:
            return None
        try:
            rect = self._anchor_provider()
        except Exception:
            log.debug("Preview anchor lookup failed", exc_info=True)
            return None
        return QRect(rect) if rect is not None else None

    def _sync_visibility(self) -> None:
        anchor = self._anchor_rect()
        if anchor is None or not (self._has_response or self._progress):
            if self.isVisible():
                self.hide()
            return
        if (
            not self._speaking
            and not self._progress
            and self._shown_at
            and time.monotonic() - self._shown_at >= AUTO_HIDE_SECONDS
        ):
            self.dismiss()
            return
        self._place(anchor)
        if not self.isVisible():
            self.show()
            self._apply_native_styles()
        self.raise_()

    def _place(self, anchor: QRect) -> None:
        screen = QGuiApplication.screenAt(anchor.center()) or QGuiApplication.primaryScreen()
        if screen is None:
            return
        area = screen.availableGeometry()
        width, height = self.width(), self.height()
        x = anchor.center().x() - width // 2
        y = anchor.bottom() + PREVIEW_GAP
        if y + height > area.bottom() - 4:
            # No room below (avatar near the bottom): sit above it instead.
            y = anchor.top() - height - PREVIEW_GAP
        x = max(area.left() + 6, min(x, area.right() - width - 6))
        y = max(area.top() + 4, min(y, area.bottom() - height - 4))
        self.move(int(x), int(y))

    def _apply_native_styles(self) -> None:
        """Clickable but never focused: no activation, no Alt-Tab entry."""
        try:
            hwnd = int(self.winId())
            style = _user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
            _user32.SetWindowLongW(
                hwnd, GWL_EXSTYLE,
                style | WS_EX_NOACTIVATE | WS_EX_TOOLWINDOW,
            )
        except Exception:
            log.debug("Could not apply preview window styles", exc_info=True)

    # -- interaction -------------------------------------------------------
    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self._open_full()
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def contextMenuEvent(self, event) -> None:
        menu = QMenu(self)
        open_action = menu.addAction("Read full response")
        menu.addSeparator()
        dismiss_action = menu.addAction("Dismiss preview")
        chosen = menu.exec(event.globalPos())
        if chosen is open_action:
            self._open_full()
        elif chosen is dismiss_action:
            self.dismiss()

    def _open_full(self) -> None:
        if not self._entry:
            return
        try:
            self._on_expand(dict(self._entry))
        except Exception:
            log.exception("Could not open the full response")

    def closeEvent(self, event) -> None:
        self._follow.stop()
        super().closeEvent(event)


class ExpandedResponse(QWidget):
    """Complete, scrollable response with copy and close actions."""

    def __init__(self):
        super().__init__(
            None,
            Qt.WindowType.Tool
            | Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint,
        )
        self.setObjectName("ExpandedResponse")
        self.setStyleSheet(theme.STYLESHEET)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setWindowTitle("TALCON response")
        self.setMinimumSize(420, 280)
        self.resize(540, 360)

        self._entry: dict = {}
        self._text = ""

        outer = QVBoxLayout(self)
        outer.setContentsMargins(10, 10, 10, 10)
        frame = QFrame()
        frame.setObjectName("Card")
        outer.addWidget(frame)
        layout = QVBoxLayout(frame)
        layout.setContentsMargins(16, 14, 16, 14)
        layout.setSpacing(10)

        header = QHBoxLayout()
        title = QLabel("TALCON response")
        title.setObjectName("CardTitle")
        header.addWidget(title)
        header.addStretch(1)
        self.close_button = QPushButton("✕")
        self.close_button.setObjectName("WindowButton")
        self.close_button.setFixedWidth(34)
        self.close_button.setToolTip("Close this panel (history is kept)")
        header.addWidget(self.close_button)
        layout.addLayout(header)

        self.meta = QLabel("")
        self.meta.setObjectName("Muted")
        self.meta.setWordWrap(True)
        layout.addWidget(self.meta)

        self.body = QTextBrowser()
        self.body.setReadOnly(True)
        self.body.setOpenExternalLinks(False)
        self.body.setObjectName("ResponseBody")
        layout.addWidget(self.body, 1)

        footer = QHBoxLayout()
        self.copy_button = QPushButton("Copy response")
        self.copy_button.setObjectName("Primary")
        footer.addWidget(self.copy_button)
        self.copy_status = QLabel("")
        self.copy_status.setObjectName("Muted")
        footer.addWidget(self.copy_status, 1)
        done = QPushButton("Close")
        footer.addWidget(done)
        layout.addLayout(footer)

        self.close_button.clicked.connect(self.hide)
        done.clicked.connect(self.hide)
        self.copy_button.clicked.connect(self._copy)

    def show_entry(self, entry: dict, anchor: QRect | None = None) -> None:
        text = str((entry or {}).get("text") or "")
        if not text:
            return
        self._entry = dict(entry or {})
        self._text = text
        when = str(self._entry.get("time") or "").strip()
        request = str(self._entry.get("request") or "").strip()
        if request:
            self.meta.setText(f'{when} · You said: "{request}"' if when
                              else f'You said: "{request}"')
        else:
            self.meta.setText(f"{when} · Spoken by TALCON" if when
                              else "Spoken by TALCON")
        self.body.setPlainText(text)
        self.body.verticalScrollBar().setValue(0)
        self.copy_status.setText("")
        self._place(anchor)
        self.show()
        self.raise_()
        self.activateWindow()

    def _place(self, anchor: QRect | None) -> None:
        screen = (
            QGuiApplication.screenAt(anchor.center()) if anchor is not None else None
        ) or QGuiApplication.primaryScreen()
        if screen is None:
            return
        area = screen.availableGeometry()
        width, height = self.width(), self.height()
        if anchor is not None:
            x = anchor.center().x() - width // 2
            y = anchor.bottom() + 10
            if y + height > area.bottom() - 8:
                y = anchor.top() - height - 10
        else:
            x = area.center().x() - width // 2
            y = area.center().y() - height // 2
        x = max(area.left() + 8, min(x, area.right() - width - 8))
        y = max(area.top() + 8, min(y, area.bottom() - height - 8))
        self.move(int(x), int(y))

    def _copy(self) -> None:
        if not self._text:
            return
        try:
            QGuiApplication.clipboard().setText(self._text)
            self.copy_status.setText("Copied to clipboard.")
        except Exception:
            self.copy_status.setText("Could not copy to the clipboard.")

    def keyPressEvent(self, event) -> None:
        if event.key() == Qt.Key.Key_Escape:
            self.hide()
            event.accept()
            return
        super().keyPressEvent(event)
