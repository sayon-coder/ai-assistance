"""TALCON's animated face, docked inside the Windhawk macOS-style menu bar.

The face is not redrawn by a second engine: the desktop :class:`FaceWidget`
owns the expression controller, blinking and idle timers, and publishes every
blended frame.  This module only paints that frame in a tiny transparent
window positioned inside the menu bar strip.

Positioning is derived from the real bar window (``WindhawkMacIslandDockIsland``
for the Mac Island & Dock mod) and, when possible, from a one-shot capture of
the bar that finds the clock's right edge.  Nothing is captured continuously.
A calibration drag along the bar stores a ratio so the face follows resolution
and DPI changes.
"""
from __future__ import annotations

import ctypes
import logging
import time
from ctypes import wintypes
from dataclasses import dataclass

from PySide6.QtCore import QPoint, QRect, Qt, QTimer
from PySide6.QtGui import QCursor, QGuiApplication, QImage, QPainter, QRegion
from PySide6.QtWidgets import QMenu, QWidget

from .face_render import FaceParams, paint_face
from .face_widget import FaceWidget

log = logging.getLogger("talcon.menu_face")

GWL_EXSTYLE = -20
WS_EX_NOACTIVATE = 0x08000000
WS_EX_TOOLWINDOW = 0x00000080
WS_EX_TOPMOST = 0x00000008
#: The Mac Island & Dock bar re-raises itself over any always-on-top window
#: that overlaps it and is *not* click-through (its own ``IsCovered`` check
#: skips ``WS_EX_TRANSPARENT`` windows).  Marking the face click-through stops
#: that tug-of-war; clicks are handled by a low-level mouse hook instead.
WS_EX_TRANSPARENT = 0x00000020
SWP_NOSIZE = 0x0001
SWP_NOMOVE = 0x0002
SWP_NOACTIVATE = 0x0010
HWND_TOPMOST = wintypes.HWND(-1)

WH_MOUSE_LL = 14
WM_MOUSEMOVE = 0x0200
WM_LBUTTONDOWN = 0x0201
WM_LBUTTONUP = 0x0202
WM_RBUTTONUP = 0x0205

#: Window classes used by the Mac Island & Dock Windhawk mod.  The bar window
#: is a full-width child of explorer.exe; the Dynamic Island panels share the
#: same prefix but are separate windows.
BAR_CLASS_HINTS = ("WindhawkMacIslandDockIsland", "MacIslandDockIsland")

#: Fallback anchor: just right of a centred clock on a 16:9 bar.
DEFAULT_CLOCK_RATIO = 0.553
CLOCK_GAP = 10  # logical pixels between the clock text and the face
GW_HWNDPREV = 3


class _MSLLHOOKSTRUCT(ctypes.Structure):
    _fields_ = [
        ("pt", wintypes.POINT),
        ("mouseData", wintypes.DWORD),
        ("flags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ctypes.c_size_t),
    ]


_HOOK_PROC = ctypes.WINFUNCTYPE(
    ctypes.c_ssize_t, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM
)

_user32 = ctypes.windll.user32
_ENUM_PROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

# Explicit signatures keep HWND values from being truncated on 64-bit Windows.
_user32.EnumWindows.argtypes = [_ENUM_PROC, wintypes.LPARAM]
_user32.EnumWindows.restype = wintypes.BOOL
_user32.GetClassNameW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
_user32.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
_user32.GetWindowRect.restype = wintypes.BOOL
_user32.IsWindow.argtypes = [wintypes.HWND]
_user32.IsWindow.restype = wintypes.BOOL
_user32.IsWindowVisible.argtypes = [wintypes.HWND]
_user32.IsWindowVisible.restype = wintypes.BOOL
_user32.GetWindow.argtypes = [wintypes.HWND, ctypes.c_uint]
_user32.GetWindow.restype = wintypes.HWND
_user32.GetWindowLongW.argtypes = [wintypes.HWND, ctypes.c_int]
_user32.GetWindowLongW.restype = ctypes.c_long
_user32.SetWindowLongW.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_long]
_user32.SetWindowLongW.restype = ctypes.c_long
_user32.SetWindowPos.argtypes = [
    wintypes.HWND, wintypes.HWND, ctypes.c_int, ctypes.c_int,
    ctypes.c_int, ctypes.c_int, ctypes.c_uint,
]
_user32.SetWindowPos.restype = wintypes.BOOL
_user32.SetWindowsHookExW.argtypes = [
    ctypes.c_int, _HOOK_PROC, wintypes.HINSTANCE, wintypes.DWORD,
]
_user32.SetWindowsHookExW.restype = ctypes.c_void_p
_user32.UnhookWindowsHookEx.argtypes = [ctypes.c_void_p]
_user32.UnhookWindowsHookEx.restype = wintypes.BOOL
_user32.CallNextHookEx.argtypes = [
    ctypes.c_void_p, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM,
]
_user32.CallNextHookEx.restype = ctypes.c_ssize_t


@dataclass(frozen=True)
class BarInfo:
    """The detected menu bar, in Qt logical pixels."""

    hwnd: int | None
    rect: QRect
    detected: bool


def _enum_bar_window():
    """Return ``(hwnd, left, top, width, height)`` for the bar, in physical px."""
    matches: list[tuple[int, int, int, int, int]] = []

    def callback(hwnd, _lparam):
        if not _user32.IsWindowVisible(hwnd):
            return True
        buffer = ctypes.create_unicode_buffer(256)
        _user32.GetClassNameW(hwnd, buffer, 256)
        name = buffer.value
        if not any(hint in name for hint in BAR_CLASS_HINTS):
            return True
        rect = wintypes.RECT()
        if not _user32.GetWindowRect(hwnd, ctypes.byref(rect)):
            return True
        width = rect.right - rect.left
        height = rect.bottom - rect.top
        if width >= 600 and 12 <= height <= 80 and rect.top <= 8:
            matches.append((hwnd, rect.left, rect.top, width, height))
        return True

    try:
        _user32.EnumWindows(_ENUM_PROC(callback), 0)
    except Exception:
        log.debug("Menu bar enumeration failed", exc_info=True)
        return None
    if not matches:
        return None
    # The bar is the widest candidate (panels are small helper windows).
    return max(matches, key=lambda item: item[3])


def _logical_rect(hwnd: int, dpr: float) -> QRect | None:
    rect = wintypes.RECT()
    try:
        if not _user32.GetWindowRect(hwnd, ctypes.byref(rect)):
            return None
    except Exception:
        return None
    scale = dpr or 1.0
    return QRect(
        round(rect.left / scale),
        round(rect.top / scale),
        max(1, round((rect.right - rect.left) / scale)),
        max(1, round((rect.bottom - rect.top) / scale)),
    )


def locate_menu_bar(screen=None) -> BarInfo:
    """Find the menu bar, falling back to the reserved strip at the screen top."""
    screen = screen or QGuiApplication.primaryScreen()
    if screen is None:
        return BarInfo(None, QRect(0, 0, 800, 25), False)
    dpr = float(screen.devicePixelRatio() or 1.0)
    geometry = screen.geometry()
    found = _enum_bar_window()
    if found is not None:
        hwnd, left, top, width, height = found
        rect = QRect(
            round(left / dpr), round(top / dpr),
            max(1, round(width / dpr)), max(1, round(height / dpr)),
        )
        if rect.width() >= geometry.width() * 0.5:
            return BarInfo(int(hwnd), rect, True)
    # No supported bar window: use the area Windows reports as reserved for
    # the top bar (availableGeometry starts below it), or a 25 px strip.
    reserved = screen.availableGeometry().top() - geometry.top()
    height = reserved if 12 <= reserved <= 60 else 25
    return BarInfo(None, QRect(geometry.x(), geometry.y(), geometry.width(), height), False)


def clock_right_ratio_from_image(image: QImage) -> float | None:
    """Return the clock's right edge as a fraction of bar width, or ``None``.

    Works for dark and light bars: the background is sampled from an empty
    area, and columns that differ from it by more than a fixed contrast are
    treated as content.  Wide content near the middle of the bar is the clock;
    narrow groups are icons and are ignored.
    """
    width, height = image.width(), image.height()
    if width < 200 or height < 6:
        return None
    background = image.pixelColor(int(width * 0.72), max(1, height // 2))

    def contrast(x: int, y: int) -> int:
        colour = image.pixelColor(x, y)
        return (
            abs(colour.red() - background.red())
            + abs(colour.green() - background.green())
            + abs(colour.blue() - background.blue())
        )

    columns: list[bool] = []
    for x in range(width):
        hit = False
        for y in range(1, height - 2):
            if contrast(x, y) > 40:
                hit = True
                break
        columns.append(hit)

    runs: list[tuple[int, int]] = []
    start = None
    for x, on in enumerate(columns + [False]):
        if on and start is None:
            start = x
        elif not on and start is not None:
            runs.append((start, x - 1))
            start = None

    gap = max(8, int(width * 0.007))
    merged: list[tuple[int, int]] = []
    for run in runs:
        if merged and run[0] - merged[-1][1] <= gap:
            merged[-1] = (merged[-1][0], run[1])
        else:
            merged.append(run)

    min_width = max(40, int(width * 0.03))
    candidates = [
        run for run in merged
        if run[1] - run[0] + 1 >= min_width
        and 0.30 * width <= (run[0] + run[1]) / 2 <= 0.92 * width
        and run[1] <= 0.93 * width
    ]
    if not candidates:
        return None
    # The clock is the widest text group; ties go to the one nearest centre.
    best = max(
        candidates,
        key=lambda run: (run[1] - run[0], -abs((run[0] + run[1]) / 2 - width / 2)),
    )
    return (best[1] + 1) / width


def detect_clock_ratio(bar: QRect) -> float | None:
    """Capture the bar once and locate the clock text inside it."""
    screen = QGuiApplication.primaryScreen()
    if screen is None or bar.width() < 200:
        return None
    try:
        pixmap = screen.grabWindow(0, bar.x(), bar.y(), bar.width(), bar.height())
    except Exception:
        log.debug("Menu bar capture failed", exc_info=True)
        return None
    if pixmap.isNull():
        return None
    return clock_right_ratio_from_image(pixmap.toImage())


class MenuBarFace(QWidget):
    """A click-through-except-the-sphere face docked in the menu bar."""

    def __init__(self, runtime, source: FaceWidget, on_open):
        super().__init__(
            None,
            Qt.WindowType.Tool
            | Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.NoDropShadowWindowHint
            | Qt.WindowType.WindowDoesNotAcceptFocus,
        )
        self.runtime = runtime
        self.source = source
        self.on_open = on_open
        self.setObjectName("TalconMenuBarFace")
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
        self.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground, True)
        self.setWindowTitle("TALCON menu bar face")
        self.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))

        self._settings: dict = dict(getattr(runtime, "settings", {}) or {})
        self._size = self._clamp_size(self._settings.get("menu_bar_face_size", 18))
        self._params: FaceParams = source.current_params
        self._animate = bool(self._settings.get("menu_bar_face_animate", True))
        self._frame_until = 0.0
        self._bar: QRect | None = None
        self._bar_hwnd: int | None = None
        self._clock_ratio: float | None = None
        self._observing = False
        self._native_ready = False
        self._started = False
        self._ticks = 0
        self._watched_screens: set[int] = set()

        self._press_global: QPoint | None = None
        self._press_x = 0
        self._dragged = False

        self._mouse_hook = None
        self._mouse_proc = None
        self._hook_press: tuple[int, int] | None = None
        self._hook_dragged = False
        self._hook_start_x = 0

        self.setFixedSize(self._size, self._size)
        self._apply_mask()

        self._refresh_timer = QTimer(self)
        self._refresh_timer.setSingleShot(True)
        self._refresh_timer.setInterval(250)
        self._refresh_timer.timeout.connect(lambda: self.refresh_position(redetect=True))

        self._watch = QTimer(self)
        self._watch.setInterval(1500)
        self._watch.timeout.connect(self._watchdog)

        source.frame_changed.connect(self._on_frame)
        source.expression_changed.connect(self._on_expression)
        self._watch_application()
        self._update_tooltip()

    # -- configuration -----------------------------------------------------
    @staticmethod
    def _clamp_size(value) -> int:
        try:
            return max(12, min(32, int(value)))
        except (TypeError, ValueError):
            return 18

    def _position_key(self) -> tuple:
        s = self._settings
        return (
            self._size,
            int(s.get("menu_bar_face_x_offset", 0) or 0),
            int(s.get("menu_bar_face_y_offset", 0) or 0),
            int(s.get("menu_bar_face_height", 0) or 0),
            float(s.get("menu_bar_face_x_ratio", 0.0) or 0.0),
            bool(s.get("menu_bar_face_enabled", True)),
        )

    def apply_settings(self, settings: dict, *, start: bool = False) -> None:
        before = self._position_key()
        self._settings = dict(settings or {})
        size = self._clamp_size(self._settings.get("menu_bar_face_size", 18))
        if size != self._size:
            self._size = size
            self.setFixedSize(size, size)
            self._apply_mask()
        self._animate = bool(self._settings.get("menu_bar_face_animate", True))
        self._update_tooltip()

        if not bool(self._settings.get("menu_bar_face_enabled", True)):
            self.hide()
            return

        if not self._started and not start:
            return
        if start:
            self._started = True

        if start or not self.isVisible():
            self.refresh_position(redetect=True)
            self.show()
            self._watch.start()
            return

        if self._position_key() != before:
            self.refresh_position(redetect=False)
        self.update()

    def start(self) -> None:
        """Show the overlay once the bar has had a moment to appear."""
        self.apply_settings(self._settings, start=True)
        # The bar can be recreated while Windhawk reloads; two cheap retries
        # cover a slow login without a polling loop.
        QTimer.singleShot(900, self._late_refresh)
        QTimer.singleShot(2600, self._late_refresh)

    def _late_refresh(self) -> None:
        if not self.isVisible():
            return
        if self._bar_hwnd is None or self._clock_ratio is None:
            self.refresh_position(redetect=True)

    def shutdown(self) -> None:
        """Stop timers, release the shared animator and destroy the window."""
        self._watch.stop()
        self._refresh_timer.stop()
        self._detach_observer()
        self._remove_mouse_hook()
        self.hide()
        self.close()

    def recalibrate(self) -> None:
        """Forget the dragged/manual anchor and detect the clock again."""
        reset = {
            "menu_bar_face_x_ratio": 0.0,
            "menu_bar_face_x_offset": 0,
            "menu_bar_face_y_offset": 0,
        }
        self._settings.update(reset)
        try:
            self.runtime.save_settings(reset)
        except Exception:
            log.exception("Could not reset the menu bar face position")
        self._clock_ratio = None
        self.refresh_position(redetect=True)

    # -- geometry ----------------------------------------------------------
    def _apply_mask(self) -> None:
        self.setMask(QRegion(0, 0, self.width(), self.height(), QRegion.RegionType.Ellipse))

    def refresh_position(self, redetect: bool = False) -> None:
        screen = QGuiApplication.primaryScreen()
        info = locate_menu_bar(screen)
        self._bar = QRect(info.rect)
        self._bar_hwnd = info.hwnd
        if redetect or self._clock_ratio is None:
            ratio = detect_clock_ratio(info.rect)
            if ratio is not None:
                self._clock_ratio = ratio
            elif info.detected:
                log.debug("Menu bar clock not found; using the default anchor")
        self._place()

    def _place(self) -> None:
        bar = self._bar
        if bar is None or bar.width() <= 0:
            return
        settings = self._settings
        size = self._size
        try:
            bar_height = int(settings.get("menu_bar_face_height", 0) or 0)
        except (TypeError, ValueError):
            bar_height = 0
        if not 12 <= bar_height <= 64:
            bar_height = bar.height()
        x_offset = int(settings.get("menu_bar_face_x_offset", 0) or 0)
        y_offset = int(settings.get("menu_bar_face_y_offset", 0) or 0)
        manual = float(settings.get("menu_bar_face_x_ratio", 0.0) or 0.0)

        if manual > 0:
            x = bar.x() + round(bar.width() * manual) + x_offset
        else:
            ratio = self._clock_ratio
            if ratio is None:
                ratio = DEFAULT_CLOCK_RATIO
            x = bar.x() + round(bar.width() * ratio) + CLOCK_GAP + x_offset
        y = bar.y() + max(0, (bar_height - size) // 2) + y_offset

        x = max(bar.x(), min(x, bar.x() + bar.width() - size))
        y = max(bar.y() - 8, min(y, bar.y() + bar_height))
        self.move(x, y)
        self._stack_above_bar()

    def _watch_application(self) -> None:
        app = QGuiApplication.instance()
        if app is not None:
            for name in ("primaryScreenChanged", "screenAdded", "screenRemoved"):
                signal = getattr(app, name, None)
                if signal is not None:
                    signal.connect(self._schedule_refresh)
        for screen in QGuiApplication.screens():
            self._watch_screen(screen)

    def _watch_screen(self, screen) -> None:
        key = id(screen)
        if key in self._watched_screens:
            return
        self._watched_screens.add(key)
        for name in (
            "geometryChanged", "availableGeometryChanged",
            "logicalDotsPerInchChanged",
        ):
            signal = getattr(screen, name, None)
            if signal is not None:
                signal.connect(self._schedule_refresh)

    def _schedule_refresh(self, *_args) -> None:
        if not self.isVisible():
            return
        self._refresh_timer.start()

    def _watchdog(self) -> None:
        """Recover if Windhawk reloads the bar; a few cheap Win32 calls every 1.5 s."""
        if not self.isVisible():
            return
        self._ticks += 1
        if self._bar_hwnd is not None and not _user32.IsWindow(self._bar_hwnd):
            log.info("Menu bar window disappeared; looking for it again")
            self._bar_hwnd = None
            self._clock_ratio = None
            self.refresh_position(redetect=True)
            return
        if self._bar_hwnd is None:
            # No bar was found yet: retry every ~7 s, not every tick.
            if self._ticks % 5 == 0:
                self.refresh_position(redetect=True)
            return
        screen = QGuiApplication.primaryScreen()
        if screen is not None:
            current = _logical_rect(self._bar_hwnd, float(screen.devicePixelRatio() or 1.0))
            if current is not None and current != self._bar:
                self.refresh_position(redetect=False)
                return
        # Windhawk re-raises its topmost bar whenever it repaints, which would
        # leave the face painted underneath it.  Re-insert the overlay just above
        # the bar (a no-op when it is already there).
        self._stack_above_bar()

    # -- animation ---------------------------------------------------------
    def _on_frame(self, params) -> None:
        self._params = params
        if self._animate or time.monotonic() < self._frame_until:
            self.update()

    def _on_expression(self, name: str) -> None:
        self._expression_name = str(name or "neutral")
        self._update_tooltip()
        # When animation is off, still run a short blend after each change so
        # the eyes transition instead of jumping.
        if not self._animate:
            self._frame_until = time.monotonic() + 0.6
        self.update()

    def _update_tooltip(self) -> None:
        labels = {
            "neutral": "Idle", "blinking": "Idle", "sleepy": "Sleepy",
            "sleeping": "Sleeping", "attentive": "Listening",
            "speaking": "Speaking", "thinking": "Working", "happy": "Done",
            "excited": "Excited", "surprised": "Surprised", "curious": "Curious",
            "confused": "Needs attention", "focused": "Focused",
            "concerned": "Needs attention",
        }
        name = getattr(self, "_expression_name", self.source.expression_name)
        self.setToolTip(f"TALCON · {labels.get(name, 'Idle')} · click to open")

    # -- painting ----------------------------------------------------------
    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        paint_face(painter, float(self._size), self._params)
        painter.end()

    def showEvent(self, event) -> None:
        super().showEvent(event)
        self._attach_observer()
        self._apply_native_styles()
        self._install_mouse_hook()
        if not self._watch.isActive():
            self._watch.start()

    def hideEvent(self, event) -> None:
        self._detach_observer()
        self._remove_mouse_hook()
        if not bool(self._settings.get("menu_bar_face_enabled", True)):
            self._watch.stop()
        super().hideEvent(event)

    def _attach_observer(self) -> None:
        if self._observing:
            return
        self.source.add_observer()
        self._observing = True
        self._params = self.source.current_params
        self._expression_name = self.source.expression_name
        self._update_tooltip()

    def _detach_observer(self) -> None:
        if not self._observing:
            return
        self.source.remove_observer()
        self._observing = False

    def _apply_native_styles(self) -> None:
        """Keep the overlay out of Alt-Tab and make it clickable without focus."""
        if self._native_ready:
            return
        try:
            hwnd = int(self.winId())
            style = _user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
            _user32.SetWindowLongW(
                hwnd, GWL_EXSTYLE,
                style | WS_EX_NOACTIVATE | WS_EX_TOOLWINDOW | WS_EX_TRANSPARENT,
            )
            _user32.SetWindowPos(
                wintypes.HWND(hwnd), HWND_TOPMOST, 0, 0, 0, 0,
                SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE,
            )
            self._native_ready = True
        except Exception:
            log.debug("Could not apply menu bar face window styles", exc_info=True)
            return
        self._stack_above_bar()

    def _stack_above_bar(self) -> None:
        """Keep the face above the bar's frosted strip.

        The bar and its glass strip are always-on-top; the face has to be
        raised after them.  Because the overlay is click-through
        (``WS_EX_TRANSPARENT``), the bar's own ``IsCovered`` check ignores it
        and does not raise itself back over the face, so there is no z-order
        tug-of-war.  When the face is already the bar's immediate predecessor
        nothing is changed, which keeps the watchdog almost free.
        """
        try:
            hwnd = int(self.winId())
            bar = self._bar_hwnd
            if bar is not None:
                if not _user32.IsWindow(bar):
                    return
                if _user32.GetWindow(wintypes.HWND(bar), GW_HWNDPREV) == hwnd:
                    return
            _user32.SetWindowPos(
                wintypes.HWND(hwnd), HWND_TOPMOST, 0, 0, 0, 0,
                SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE,
            )
        except Exception:
            log.debug("Could not restack the menu bar face", exc_info=True)

    # -- mouse -------------------------------------------------------------
    def _drag_to(self, x: int) -> None:
        bar = self._bar
        if bar is None:
            return
        self.move(
            max(bar.x(), min(x, bar.x() + bar.width() - self._size)),
            self.y(),
        )

    def _commit_drag(self) -> None:
        if self._bar is None or self._bar.width() <= 0:
            return
        ratio = (self.x() - self._bar.x()) / self._bar.width()
        ratio = max(0.02, min(0.97, ratio))
        values = {
            "menu_bar_face_x_ratio": round(ratio, 4),
            "menu_bar_face_x_offset": 0,
        }
        self._settings.update(values)
        try:
            self.runtime.save_settings(values)
        except Exception:
            log.exception("Could not save the menu bar face position")

    def _face_hit(self, point) -> bool:
        """Whether a physical screen point is inside the circular face."""
        try:
            hwnd = int(self.winId())
            rect = wintypes.RECT()
            if not _user32.GetWindowRect(wintypes.HWND(hwnd), ctypes.byref(rect)):
                return False
        except Exception:
            return False
        cx = (rect.left + rect.right) / 2.0
        cy = (rect.top + rect.bottom) / 2.0
        radius = (rect.right - rect.left) / 2.0
        dx = point[0] - cx
        dy = point[1] - cy
        return dx * dx + dy * dy <= radius * radius

    def _install_mouse_hook(self) -> None:
        """Watch for clicks on the click-through face (WH_MOUSE_LL)."""
        if self._mouse_hook is not None:
            return
        try:
            self._mouse_proc = _HOOK_PROC(self._on_global_mouse)
            self._mouse_hook = _user32.SetWindowsHookExW(
                WH_MOUSE_LL, self._mouse_proc, None, 0
            )
        except Exception:
            log.debug("Could not install the menu bar face mouse hook", exc_info=True)
            self._mouse_hook = None
            self._mouse_proc = None

    def _remove_mouse_hook(self) -> None:
        if self._mouse_hook is not None:
            try:
                _user32.UnhookWindowsHookEx(self._mouse_hook)
            except Exception:
                log.debug("Could not remove the mouse hook", exc_info=True)
        self._mouse_hook = None
        self._mouse_proc = None
        self._hook_press = None
        self._hook_dragged = False

    def _on_global_mouse(self, code, wparam, lparam):
        if code < 0:
            return _user32.CallNextHookEx(self._mouse_hook, code, wparam, lparam)
        try:
            if wparam in (WM_LBUTTONDOWN, WM_LBUTTONUP, WM_MOUSEMOVE, WM_RBUTTONUP):
                info = ctypes.cast(
                    lparam, ctypes.POINTER(_MSLLHOOKSTRUCT)
                ).contents
                point = (int(info.pt.x), int(info.pt.y))
                if self._face_hit(point):
                    if wparam == WM_LBUTTONDOWN:
                        self._hook_press = point
                        self._hook_dragged = False
                        self._hook_start_x = self.x()
                        return 1
                    if wparam == WM_MOUSEMOVE and self._hook_press is not None:
                        dpr = float(self.devicePixelRatioF() or 1.0)
                        delta = point[0] - self._hook_press[0]
                        if not self._hook_dragged and abs(delta) > 3 * dpr:
                            self._hook_dragged = True
                        if self._hook_dragged:
                            self._drag_to(self._hook_start_x + round(delta / dpr))
                        return 1
                    if wparam == WM_LBUTTONUP and self._hook_press is not None:
                        dragged = self._hook_dragged
                        self._hook_press = None
                        self._hook_dragged = False
                        if dragged:
                            self._commit_drag()
                        else:
                            QTimer.singleShot(0, self.on_open)
                        return 1
                    if wparam == WM_RBUTTONUP:
                        pos = QCursor.pos()
                        QTimer.singleShot(0, lambda: self._show_menu(pos))
                        return 1
        except Exception:
            log.debug("Menu bar face mouse hook failed", exc_info=True)
        return _user32.CallNextHookEx(self._mouse_hook, code, wparam, lparam)

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self._press_global = event.globalPosition().toPoint()
            self._press_x = self.x()
            self._dragged = False
            event.accept()
        elif event.button() == Qt.MouseButton.RightButton:
            self._show_menu(event.globalPosition().toPoint())
            event.accept()
        else:
            super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:
        if self._press_global is not None and event.buttons() & Qt.MouseButton.LeftButton:
            delta = event.globalPosition().toPoint().x() - self._press_global.x()
            if not self._dragged and abs(delta) > 3:
                self._dragged = True
            if self._dragged:
                self._drag_to(self._press_x + delta)
            event.accept()
        else:
            super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton and self._press_global is not None:
            if self._dragged:
                self._commit_drag()
            else:
                self.on_open()
            self._press_global = None
            self._dragged = False
            event.accept()
        else:
            super().mouseReleaseEvent(event)

    def _show_menu(self, point: QPoint) -> None:
        menu = QMenu(self)
        open_action = menu.addAction("Open TALCON")
        menu.addSeparator()
        recalibrate = menu.addAction("Recalibrate position")
        hide_action = menu.addAction("Hide menu bar face")
        chosen = menu.exec(point)
        if chosen is None:
            return
        if chosen == open_action:
            self.on_open()
        elif chosen == recalibrate:
            self.recalibrate()
        elif chosen == hide_action:
            try:
                self.runtime.save_settings({"menu_bar_face_enabled": False})
            except Exception:
                log.exception("Could not hide the menu bar face")
            self.hide()
