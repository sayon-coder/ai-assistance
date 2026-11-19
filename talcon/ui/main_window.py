"""TALCON control center: minimal chrome, three distinct window controls."""
from __future__ import annotations

import datetime as _dt
import logging
import os
import subprocess
import threading

from PySide6.QtCore import QEvent, QObject, QRect, Qt, QTimer, Signal
from PySide6.QtGui import (
    QColor, QGuiApplication, QIcon, QKeySequence, QPixmap, QShortcut,
)
from PySide6.QtWidgets import (
    QButtonGroup, QComboBox, QDoubleSpinBox, QFrame, QGraphicsDropShadowEffect,
    QGridLayout, QHBoxLayout, QLabel, QLineEdit, QMessageBox, QProgressBar,
    QPushButton, QScrollArea, QSizeGrip, QSlider, QSpinBox, QStackedWidget,
    QVBoxLayout, QWidget,
)

from .. import startup
from ..asr import (
    ENGINE_CHOICES,
    ENGINE_LABELS,
    engine_model_ready,
    engine_reason,
)
from ..config import BUILTIN_MODES, LOGS_DIR, MODELS_DIR, RESERVED_MODE_KEYS, ROOT
from ..face_render import render_face_image
from ..runtime import TalconRuntime
from ..speech import BACKEND_AUTO, SPEECH_BACKEND_OPTIONS
from ..audio import calibrate_microphone
from ..voice import measure_input
from . import theme
from .components import Card, FacePreview, Heading, StatusText, Switch, footnote, muted
from .cron_jobs import CronJobsPage
from .custom_modes import CustomModesPage
from .history import HistoryPage
from .command_log import CommandLogPage
from .mode_editor import ModeEditor

log = logging.getLogger("talcon.ui")

PAGES = [
    ("dashboard", "Dashboard"),
    ("history", "History"),
    ("commands", "Commands"),
    ("study", "Study Mode"),
    ("entertainment", "Entertainment Mode"),
    ("stock", "Stock Mode"),
    ("modes", "Add your own mode"),
    ("voice", "Voice Settings"),
    ("appearance", "Appearance"),
    ("monitoring", "Application Monitoring"),
    ("cron", "Cron Jobs"),
    ("startup", "Startup Settings"),
]


class _Task(QObject):
    finished = Signal(object)


def run_async(parent: QObject, work, callback) -> None:
    task = _Task(parent)
    task.finished.connect(callback)

    def runner():
        try:
            result = work()
        except Exception as exc:  # report instead of crashing the UI
            result = exc
        task.finished.emit(result)

    threading.Thread(target=runner, daemon=True).start()


class MainWindow(QWidget):
    def __init__(self, runtime: TalconRuntime, face, parent=None, menu_face=None):
        super().__init__(parent)
        self.runtime = runtime
        self.face = face
        self.menu_face = menu_face
        self._status: dict = {}
        self._drag_offset = None
        self._drag_widgets: list[QWidget] = []
        self._mic_result = None
        self._download_active = False
        self._voice_lookup: dict = {}

        self.setWindowTitle("TALCON Control Center")
        icon_file = ROOT / "assets" / "talcon.ico"
        if not icon_file.exists():
            icon_file = ROOT / "assets" / "talcon_icon.png"
        self.setWindowIcon(QIcon(str(icon_file)))
        self.setObjectName("Root")
        self.setWindowFlags(Qt.WindowType.Window | Qt.WindowType.FramelessWindowHint)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        # Let the desktop show through the rounded corners instead of painting
        # an opaque square behind the shell.
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setMinimumSize(980, 660)
        self.resize(1060, 740)
        self._current_page = self._initial_page()
        self._build()
        self._restore_geometry()
        self._install_shortcuts()
        self._connect()
        self.load_from_runtime()
        self.update_statuses(self.runtime.status_snapshot())
        self._center_on_screen()

    # =====================================================================
    # window state
    # =====================================================================
    def _initial_page(self) -> str:
        known = {key for key, _label in PAGES}
        page = str(self.runtime.settings.get("window_page") or "dashboard")
        return page if page in known else "dashboard"

    def _restore_geometry(self) -> None:
        """Reopen at the size and place the user left it, if still on screen."""
        s = self.runtime.settings
        width = int(s.get("window_width", 1060) or 1060)
        height = int(s.get("window_height", 740) or 740)
        x = s.get("window_x")
        y = s.get("window_y")
        if not isinstance(x, int) or not isinstance(y, int):
            return
        rect = QRect(x, y, width, height)
        for screen in QGuiApplication.screens():
            if screen.availableGeometry().intersects(rect):
                self.setGeometry(rect)
                return

    def _remember_geometry(self) -> None:
        geometry = self.geometry()
        values = {
            "window_width": max(720, geometry.width()),
            "window_height": max(520, geometry.height()),
            "window_x": geometry.x(),
            "window_y": geometry.y(),
            "window_page": str(self._current_page or "dashboard"),
        }
        if values == {key: self.runtime.settings.get(key) for key in values}:
            return
        try:
            self.runtime.save_settings(values)
        except Exception:
            log.debug("Could not save the window geometry", exc_info=True)

    def _install_shortcuts(self) -> None:
        """Ctrl+1…Ctrl+9 jump to a section; Esc returns to the tray."""
        for index, (key, label) in enumerate(PAGES[:9], start=1):
            shortcut = QShortcut(QKeySequence(f"Ctrl+{index}"), self)
            shortcut.activated.connect(lambda k=key: self.show_page(k))
            shortcut.setWhatsThis(f"Open {label}")
        escape = QShortcut(QKeySequence("Escape"), self)
        escape.activated.connect(self.runtime.hide_to_tray)

    # =====================================================================
    # construction
    # =====================================================================
    def _build(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(22, 22, 22, 22)
        shell = QFrame()
        shell.setObjectName("Shell")
        shadow = QGraphicsDropShadowEffect(shell)
        shadow.setBlurRadius(40)
        shadow.setOffset(0, 10)
        shadow.setColor(QColor(0, 0, 0, 75))
        shell.setGraphicsEffect(shadow)
        outer.addWidget(shell)

        shell_layout = QVBoxLayout(shell)
        shell_layout.setContentsMargins(0, 0, 0, 0)
        shell_layout.setSpacing(0)
        shell_layout.addWidget(self._build_header())

        body = QHBoxLayout()
        body.setContentsMargins(0, 0, 0, 0)
        body.setSpacing(0)
        body.addWidget(self._build_sidebar())

        self.stack = QStackedWidget()
        self.stack.setObjectName("PageStack")
        body.addWidget(self.stack, 1)
        shell_layout.addLayout(body, 1)

        self.pages = {
            "dashboard": self._page_dashboard(),
            "history": self._page_history(),
            "commands": self._page_commands(),
            "study": self._page_mode(
                "study", "Study Mode",
                "Websites and applications opened by “TALCON, activate my study mode”.",
            ),
            "entertainment": self._page_mode(
                "entertainment", "Entertainment Mode",
                "Everything opened by “TALCON, get ready for my entertainment mode”.",
            ),
            "stock": self._page_mode(
                "stock", "Stock Mode",
                "Everything opened by “TALCON, open news” or “TALCON, stock mode”.",
            ),
            "modes": self._page_custom_modes(),
            "voice": self._page_voice(),
            "appearance": self._page_appearance(),
            "monitoring": self._page_monitoring(),
            "cron": self._page_cron_jobs(),
            "startup": self._page_startup(),
        }
        for key, _label in PAGES:
            self.stack.addWidget(self.pages[key])
        self._sync_custom_mode_pages()
        self._populate_nav()
        self.show_page(self._current_page)

        self.size_grip = QSizeGrip(self)
        self.size_grip.setFixedSize(16, 16)
        self.size_grip.raise_()

    def _build_header(self) -> QWidget:
        header = QFrame()
        header.setObjectName("Header")
        header.setFixedHeight(72)
        layout = QHBoxLayout(header)
        layout.setContentsMargins(18, 10, 14, 10)
        layout.setSpacing(12)

        dpr = QGuiApplication.primaryScreen().devicePixelRatio() if QGuiApplication.primaryScreen() else 1.0
        icon = QLabel()
        image = render_face_image(38, ratio=dpr)
        icon.setPixmap(QPixmap.fromImage(image))
        icon.setFixedSize(38, 38)
        layout.addWidget(icon)

        brand = QVBoxLayout()
        brand.setSpacing(1)
        name = QLabel("TALCON")
        name.setObjectName("Brand")
        subtitle = QLabel("LOCAL DESKTOP ASSISTANT")
        subtitle.setObjectName("BrandSub")
        brand.addWidget(name)
        brand.addWidget(subtitle)
        layout.addLayout(brand)
        layout.addStretch(1)

        self.header_status = QLabel("Starting…")
        self.header_status.setObjectName("Muted")
        self.header_status.setStyleSheet("font-size: 8.8pt;")
        layout.addWidget(self.header_status)

        self.min_button = QPushButton("—  Minimise")
        self.min_button.setObjectName("WindowButton")
        self.min_button.setToolTip("Minimise this window to the taskbar. TALCON keeps running and the face stays visible.")
        self.hide_button = QPushButton("↓  Hide in tray")
        self.hide_button.setObjectName("WindowButton")
        self.hide_button.setToolTip("Hide this window to the system tray. TALCON and the face keep running.")
        self.close_button = QPushButton("✕  Close")
        self.close_button.setObjectName("CloseButton")
        self.close_button.setToolTip("Close TALCON completely and stop all background services.")
        layout.addWidget(self.min_button)
        layout.addWidget(self.hide_button)
        layout.addWidget(self.close_button)

        self._drag_widgets = [header, icon, name, subtitle, self.header_status]
        for widget in self._drag_widgets:
            widget.installEventFilter(self)
        return header

    def _build_sidebar(self) -> QWidget:
        sidebar = QFrame()
        sidebar.setObjectName("Sidebar")
        sidebar.setFixedWidth(212)
        layout = QVBoxLayout(sidebar)
        layout.setContentsMargins(12, 18, 12, 16)
        layout.setSpacing(4)
        self.nav_group = QButtonGroup(self)
        self.nav_group.setExclusive(True)
        self.nav_buttons: dict[str, QPushButton] = {}
        nav_holder = QWidget()
        self.nav_layout = QVBoxLayout(nav_holder)
        self.nav_layout.setContentsMargins(0, 0, 0, 0)
        self.nav_layout.setSpacing(4)
        layout.addWidget(nav_holder)
        layout.addStretch(1)
        return sidebar

    def _custom_mode_keys(self) -> list[str]:
        data = getattr(self.runtime, "modes_data", {}) or {}
        return [
            str(key) for key in data if str(key) not in RESERVED_MODE_KEYS
        ]

    def _mode_title(self, key: str) -> str:
        getter = getattr(self.runtime, "mode_title", None)
        if callable(getter):
            try:
                return str(getter(key))
            except Exception:
                pass
        mode = (getattr(self.runtime, "modes_data", {}) or {}).get(key)
        if isinstance(mode, dict) and mode.get("title"):
            return str(mode["title"])
        return str(key).replace("_", " ").title()

    def _nav_entries(self) -> list[tuple[str, str]]:
        entries: list[tuple[str, str]] = []
        for key, label in PAGES:
            if key == "modes":
                entries.extend((name, self._mode_title(name))
                               for name in self._custom_mode_keys())
            entries.append((key, label))
        return entries

    def _populate_nav(self) -> None:
        current = None
        if hasattr(self, "stack"):
            widget = self.stack.currentWidget()
            current = next(
                (key for key, page in self.pages.items() if page is widget), None
            )
        for button in list(self.nav_buttons.values()):
            self.nav_group.removeButton(button)
            self.nav_layout.removeWidget(button)
            button.setParent(None)
            button.deleteLater()
        self.nav_buttons = {}
        for index, (key, label) in enumerate(self._nav_entries()):
            button = QPushButton(label)
            button.setObjectName("Nav")
            button.setCheckable(True)
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            button.clicked.connect(lambda _checked=False, k=key: self.show_page(k))
            self.nav_group.addButton(button, index)
            self.nav_buttons[key] = button
            self.nav_layout.addWidget(button)
        if current:
            for name, button in self.nav_buttons.items():
                button.setChecked(name == current)

    def _page(self) -> tuple[QWidget, QVBoxLayout]:
        page = QWidget()
        page.setObjectName("Page")
        layout = QVBoxLayout(page)
        layout.setContentsMargins(26, 22, 26, 22)
        layout.setSpacing(14)
        return page, layout

    def _scroll(self, page: QWidget) -> QScrollArea:
        area = QScrollArea()
        area.setWidgetResizable(True)
        area.setFrameShape(QFrame.Shape.NoFrame)
        area.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        area.setWidget(page)
        return area

    # =====================================================================
    # dashboard
    # =====================================================================
    def _page_dashboard(self) -> QWidget:
        page, layout = self._page()
        hour = _dt.datetime.now().hour
        greeting = "Good morning" if hour < 12 else ("Good afternoon" if hour < 18 else "Good evening")
        layout.addWidget(Heading("Dashboard", f"{greeting}, sir. TALCON is running quietly in the background."))

        actions = QHBoxLayout()
        actions.setSpacing(10)
        self.dash_listen = QPushButton("Listen now")
        self.dash_listen.setObjectName("Primary")
        self.dash_listen.clicked.connect(self.runtime.listen_now)
        self.dash_mute = QPushButton("Mute voice")
        self.dash_mute.setObjectName("Ghost")
        self.dash_mute.clicked.connect(
            lambda: self.runtime.set_muted(not self.runtime.muted)
        )
        voice_button = QPushButton("Voice settings")
        voice_button.setObjectName("Ghost")
        voice_button.clicked.connect(lambda: self.show_page("voice"))
        commands_button = QPushButton("Recent commands")
        commands_button.setObjectName("Ghost")
        commands_button.clicked.connect(lambda: self.show_page("commands"))
        for widget in (self.dash_listen, self.dash_mute,
                       voice_button, commands_button):
            actions.addWidget(widget)
        actions.addStretch(1)
        layout.addLayout(actions)

        grid = QGridLayout()
        grid.setSpacing(12)
        self.stat_widgets: dict[str, StatusText] = {}
        cards = (
            ("Listening", "wake"),
            ("OpenCode", "opencode"),
            ("Codex Desktop", "codex"),
            ("DeepAI Code", "deepai"),
        )
        for position, (title, key) in enumerate(cards):
            card = Card(title)
            status = StatusText("…")
            card.add(status)
            self.stat_widgets[key] = status
            grid.addWidget(card, position // 3, position % 3)
        layout.addLayout(grid)

        recent = Card("Recent activity")
        self.activity_label = QLabel("No activity yet.")
        self.activity_label.setWordWrap(True)
        self.activity_label.setStyleSheet("font-size: 9.5pt; color: #3A3A3A;")
        recent.add(self.activity_label)
        self.last_event = QLabel("TALCON is ready.")
        self.last_event.setWordWrap(True)
        self.last_event.setObjectName("Muted")
        recent.add(self.last_event)
        recent.add(footnote(
            "Coding notifications come only from real OpenCode events and recorded Codex turns."
        ))
        layout.addWidget(recent)
        layout.addStretch(1)
        return self._scroll(page)

    # =====================================================================
    # history
    # =====================================================================
    def _page_history(self) -> QWidget:
        self.history_page = HistoryPage(self.runtime)
        return self.history_page

    def _page_commands(self) -> QWidget:
        self.command_log_page = CommandLogPage(self.runtime)
        return self.command_log_page

    # =====================================================================
    # modes
    # =====================================================================
    def _page_mode(self, key: str, title: str, subtitle: str,
                   custom: bool = False) -> QWidget:
        page, layout = self._page()
        self.mode_editors = getattr(self, "mode_editors", {})
        editor = ModeEditor(self.runtime, key, title, subtitle, custom=custom)
        self.mode_editors[key] = editor
        layout.addWidget(editor)
        return self._scroll(page)

    def _page_custom_modes(self) -> QWidget:
        page, layout = self._page()
        self.custom_modes_page = CustomModesPage(
            self.runtime, on_open=self.show_page, parent=page
        )
        layout.addWidget(self.custom_modes_page)
        return self._scroll(page)

    def _custom_mode_subtitle(self, key: str) -> str:
        mode = (getattr(self.runtime, "modes_data", {}) or {}).get(key)
        triggers = mode.get("triggers") if isinstance(mode, dict) else None
        if triggers:
            quoted = ", ".join(f"“{phrase}”" for phrase in triggers)
            return f"Everything opened by {quoted}."
        return "Add a spoken command below, then choose the websites and applications to open."

    def _sync_custom_mode_pages(self) -> None:
        """Create, refresh or remove the pages for user-created modes."""
        keys = self._custom_mode_keys()
        for key in list(getattr(self, "mode_editors", {})):
            if key in BUILTIN_MODES or key in keys:
                continue
            editor = self.mode_editors.pop(key, None)
            page = self.pages.pop(key, None)
            if page is not None:
                self.stack.removeWidget(page)
                page.deleteLater()
            if editor is not None:
                editor.deleteLater()
        for key in keys:
            if key in self.pages:
                continue
            page = self._page_mode(
                key, self._mode_title(key), self._custom_mode_subtitle(key),
                custom=True,
            )
            self.pages[key] = page
            index = self.stack.indexOf(self.pages["modes"])
            self.stack.insertWidget(index, page)
        for key in keys:
            editor = getattr(self, "mode_editors", {}).get(key)
            if editor is not None:
                editor.refresh()
        if hasattr(self, "custom_modes_page"):
            self.custom_modes_page.refresh()

    def _on_modes_changed(self, modes: dict) -> None:
        if hasattr(self.runtime, "modes_data"):
            self.runtime.modes_data = dict(modes or {})
        self._sync_custom_mode_pages()
        self._populate_nav()
        if self.stack.currentWidget() not in self.pages.values():
            self.show_page("modes")

    def _on_conversation_changed(self, entries) -> None:
        """Render a fresh in-memory history snapshot; no storage involved."""
        if hasattr(self, "history_page"):
            self.history_page.refresh(entries)

    # =====================================================================
    # voice
    # =====================================================================
    def _page_voice(self) -> QWidget:
        page, layout = self._page()
        layout.addWidget(Heading(
            "Voice Settings",
            "Continuous listening, microphone and the local Kokoro voice. "
            "All recognition and speech happen on this PC.",
        ))

        output = Card(
            "Speech engine",
            "TALCON speaks only through the free Kokoro-82M neural voice, run "
            "locally on the CPU. No Microsoft voice is used and nothing is sent "
            "to the cloud.",
        )
        output_row = QHBoxLayout()
        output_row.addWidget(QLabel("Enable spoken announcements"))
        output_row.addStretch(1)
        self.speech_switch = Switch()
        output_row.addWidget(self.speech_switch)
        output.add_layout(output_row)

        engine_row = QHBoxLayout()
        engine_row.addWidget(QLabel("Engine"))
        self.engine_combo = QComboBox()
        self.engine_combo.setMinimumWidth(260)
        for key, label in SPEECH_BACKEND_OPTIONS:
            self.engine_combo.addItem(label, key)
        engine_row.addWidget(self.engine_combo, 1)
        preview_voice = QPushButton("Preview Kokoro voice")
        preview_voice.clicked.connect(self._preview_voice)
        engine_row.addWidget(preview_voice)
        output.add_layout(engine_row)

        self.engine_status = StatusText("Checking the Kokoro voice…")
        output.add(self.engine_status)

        meta = QGridLayout()
        meta.setHorizontalSpacing(18)
        meta.setVerticalSpacing(6)
        self.engine_requested_value = QLabel("—")
        self.engine_active_value = QLabel("—")
        self.voice_name_value = QLabel("—")
        self.voice_id_value = QLabel("—")
        self.voice_language_value = QLabel("—")
        self.voice_gender_value = QLabel("—")
        self.voice_offline_value = QLabel("—")
        self.voice_availability_value = QLabel("—")
        self.synthesis_value = QLabel("Not spoken yet")
        meta_rows = (
            ("Engine", self.engine_active_value, "Voice model", self.engine_requested_value),
            ("Selected voice", self.voice_name_value, "Voice ID", self.voice_id_value),
            ("Language / accent", self.voice_language_value, "Gender", self.voice_gender_value),
            ("Offline", self.voice_offline_value, "Availability", self.voice_availability_value),
            ("Last synthesis", self.synthesis_value, None, None),
        )
        for row_index, (left_label, left_value, right_label, right_value) in enumerate(meta_rows):
            for column, (text, widget) in enumerate(
                ((left_label, left_value), (right_label, right_value))
            ):
                if widget is None:
                    continue
                label = QLabel(text)
                label.setObjectName("Muted")
                widget.setWordWrap(True)
                widget.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
                meta.addWidget(label, row_index, column * 2)
                meta.addWidget(widget, row_index, column * 2 + 1, 1, 1)
        output.add_layout(meta)

        voice_row = QHBoxLayout()
        voice_row.addWidget(QLabel("Installed voice"))
        self.voice_combo = QComboBox()
        self.voice_combo.setMinimumWidth(300)
        voice_row.addWidget(self.voice_combo, 1)
        self.test_voice_button = QPushButton("Test voice")
        self.test_voice_button.clicked.connect(self.runtime.test_voice)
        refresh_voices = QPushButton("Refresh")
        refresh_voices.clicked.connect(self.runtime.refresh_voices)
        voice_row.addWidget(self.test_voice_button)
        voice_row.addWidget(refresh_voices)
        output.add_layout(voice_row)

        self.engine_note = QLabel("")
        self.engine_note.setObjectName("Muted")
        self.engine_note.setWordWrap(True)
        output.add(self.engine_note)

        rate_row = QHBoxLayout()
        rate_row.addWidget(QLabel("Speaking rate"))
        self.rate_slider = QSlider(Qt.Orientation.Horizontal)
        self.rate_slider.setRange(-10, 10)
        self.rate_slider.setMinimumWidth(220)
        self.rate_slider.valueChanged.connect(self._on_rate_changed)
        self.rate_slider.sliderReleased.connect(self._save_rate)
        self.rate_slider.valueChanged.connect(
            lambda _v: self._debounced_save("rate", self._save_rate)
        )
        self.rate_label = QLabel("Normal")
        self.rate_label.setObjectName("Muted")
        self.rate_label.setFixedWidth(70)
        rate_row.addWidget(self.rate_slider, 1)
        rate_row.addWidget(self.rate_label)
        output.add_layout(rate_row)

        volume_row = QHBoxLayout()
        volume_row.addWidget(QLabel("Volume"))
        self.volume_slider = QSlider(Qt.Orientation.Horizontal)
        self.volume_slider.setRange(0, 100)
        self.volume_slider.setMinimumWidth(220)
        self.volume_slider.valueChanged.connect(self._on_volume_changed)
        self.volume_slider.sliderReleased.connect(self._save_volume)
        self.volume_slider.valueChanged.connect(
            lambda _v: self._debounced_save("volume", self._save_volume)
        )
        self.volume_label = QLabel("90%")
        self.volume_label.setObjectName("Muted")
        self.volume_label.setFixedWidth(70)
        volume_row.addWidget(self.volume_slider, 1)
        volume_row.addWidget(self.volume_label)
        output.add_layout(volume_row)

        pause_row = QHBoxLayout()
        pause_row.addWidget(QLabel("Pause after a full stop"))
        self.pause_slider = QSlider(Qt.Orientation.Horizontal)
        # Stored in tenths of a second (0-3 s); one second is the default beat.
        self.pause_slider.setRange(0, 30)
        self.pause_slider.setMinimumWidth(220)
        self.pause_slider.valueChanged.connect(self._on_pause_changed)
        self.pause_slider.sliderReleased.connect(self._save_pause)
        self.pause_slider.valueChanged.connect(
            lambda _v: self._debounced_save("pause", self._save_pause)
        )
        self.pause_label = QLabel("1.0 s")
        self.pause_label.setObjectName("Muted")
        self.pause_label.setFixedWidth(70)
        pause_row.addWidget(self.pause_slider, 1)
        pause_row.addWidget(self.pause_label)
        output.add_layout(pause_row)
        output.add(footnote(
            "Muting stops speech only. Wake-word listening and application monitoring keep running."
        ))
        layout.addWidget(output)

        install = Card(
            "Kokoro neural voice",
            "Kokoro-82M is free, Apache-2.0 and runs offline on the CPU. "
            "The model is about 118 MB and is installed once.",
        )
        install.add(muted(
            "Install or repair the voice from a terminal:\n"
            "python -m talcon.model_setup --kokoro\n"
            "The files live in models/kokoro. If they are missing, TALCON says so "
            "instead of falling back to a Microsoft voice."
        ))
        diagnose = QPushButton("Check Kokoro voice")
        diagnose.clicked.connect(self._run_voice_diagnostic)
        install_row = QHBoxLayout()
        install_row.addWidget(diagnose)
        install_row.addStretch(1)
        install.add_layout(install_row)
        install.add(footnote(
            "The same check is available from a terminal: "
            "python main.py --speech-diagnostic (silent) or --speech-test (audible)."
        ))
        layout.addWidget(install)

        wake = Card(
            "Listening and microphone",
            "TALCON can listen continuously, or wait for the word “TALCON”. Commands are matched exactly.",
        )
        wake_row = QHBoxLayout()
        wake_row.addWidget(QLabel("Enable voice input"))
        wake_row.addStretch(1)
        self.wake_switch = Switch()
        wake_row.addWidget(self.wake_switch)
        wake.add_layout(wake_row)

        always_row = QHBoxLayout()
        always_row.addWidget(QLabel("Listen continuously (no wake word)"))
        always_row.addStretch(1)
        self.always_switch = Switch()
        always_row.addWidget(self.always_switch)
        wake.add_layout(always_row)

        asr_row = QHBoxLayout()
        asr_row.addWidget(QLabel("Recognition engine"))
        self.asr_engine_combo = QComboBox()
        self.asr_engine_combo.setMinimumWidth(300)
        for key in ENGINE_CHOICES:
            self.asr_engine_combo.addItem(ENGINE_LABELS[key], key)
        asr_row.addWidget(self.asr_engine_combo, 1)
        wake.add_layout(asr_row)
        self.asr_status = StatusText("Checking recognition engine…")
        wake.add(self.asr_status)

        frontend = QGridLayout()
        frontend.setHorizontalSpacing(18)
        frontend.setVerticalSpacing(6)
        self.agc_switch = Switch()
        self.noise_switch = Switch()
        self.aec_switch = Switch()
        frontend.addWidget(QLabel("Automatic gain (AGC)"), 0, 0)
        frontend.addWidget(self.agc_switch, 0, 1)
        frontend.addWidget(QLabel("Noise suppression"), 0, 2)
        frontend.addWidget(self.noise_switch, 0, 3)
        frontend.addWidget(QLabel("Echo suppression (requires playback tap)"), 1, 0)
        frontend.addWidget(self.aec_switch, 1, 1)
        wake.add_layout(frontend)

        self.voice_status = StatusText("Starting…")
        wake.add(self.voice_status)

        mic_row = QHBoxLayout()
        mic_row.addWidget(QLabel("Microphone"))
        self.mic_combo = QComboBox()
        self.mic_combo.setMinimumWidth(260)
        mic_row.addWidget(self.mic_combo, 1)
        refresh_mics = QPushButton("Refresh")
        refresh_mics.clicked.connect(self._populate_microphones)
        mic_row.addWidget(refresh_mics)
        wake.add_layout(mic_row)

        mic_actions = QHBoxLayout()
        listen_now = QPushButton("Listen now")
        listen_now.clicked.connect(self.runtime.listen_now)
        check_mic = QPushButton("Check microphone")
        check_mic.clicked.connect(self._check_microphone)
        calibrate_mic = QPushButton("Calibrate microphone")
        calibrate_mic.clicked.connect(self._calibrate_microphone)
        self.mic_level = QLabel("")
        self.mic_level.setObjectName("Muted")
        mic_actions.addWidget(listen_now)
        mic_actions.addWidget(check_mic)
        mic_actions.addWidget(calibrate_mic)
        mic_actions.addWidget(self.mic_level, 1)
        wake.add_layout(mic_actions)

        timing = QGridLayout()
        timing.setHorizontalSpacing(18)
        timing.setVerticalSpacing(6)
        self.timing_start = QDoubleSpinBox()
        self.timing_start.setRange(2.0, 15.0)
        self.timing_start.setSingleStep(0.5)
        self.timing_start.setSuffix(" s")
        self.timing_silence = QDoubleSpinBox()
        self.timing_silence.setRange(0.6, 4.0)
        self.timing_silence.setSingleStep(0.1)
        self.timing_silence.setSuffix(" s")
        self.timing_max = QDoubleSpinBox()
        self.timing_max.setRange(6.0, 45.0)
        self.timing_max.setSingleStep(1.0)
        self.timing_max.setSuffix(" s")
        self.timing_confidence = QDoubleSpinBox()
        self.timing_confidence.setRange(0.0, 1.0)
        self.timing_confidence.setSingleStep(0.05)
        self.timing_confidence.setDecimals(2)
        for widget in (self.timing_start, self.timing_silence,
                       self.timing_max, self.timing_confidence):
            widget.editingFinished.connect(self._save_speech_timing)
            widget.valueChanged.connect(
                lambda _v: self._debounced_save("speech_timing", self._save_speech_timing)
            )
        timing.addWidget(QLabel("Speech-start timeout"), 0, 0)
        timing.addWidget(self.timing_start, 0, 1)
        timing.addWidget(QLabel("Trailing silence"), 0, 2)
        timing.addWidget(self.timing_silence, 0, 3)
        timing.addWidget(QLabel("Maximum utterance"), 1, 0)
        timing.addWidget(self.timing_max, 1, 1)
        timing.addWidget(QLabel("Minimum confidence"), 1, 2)
        timing.addWidget(self.timing_confidence, 1, 3)
        wake.add_layout(timing)
        wake.add(footnote(
            "Adaptive noise tracking runs continuously. Longer trailing silence helps slow "
            "speech; the maximum is a safety bound. Calibration is saved locally and never uploads audio."
        ))

        self.model_status = StatusText("Checking speech model…")
        wake.add(self.model_status)
        model_row = QHBoxLayout()
        self.download_button = QPushButton("Download offline speech model (~40 MB)")
        self.download_button.setObjectName("Primary")
        self.download_button.clicked.connect(self._download_model)
        model_row.addWidget(self.download_button)
        model_row.addStretch(1)
        wake.add_layout(model_row)
        self.model_progress = QProgressBar()
        self.model_progress.setRange(0, 100)
        self.model_progress.setVisible(False)
        wake.add(self.model_progress)
        self.model_note = QLabel("")
        self.model_note.setObjectName("Footnote")
        self.model_note.setWordWrap(True)
        wake.add(self.model_note)
        layout.addWidget(wake)

        diagnostics = Card(
            "Voice diagnostics",
            "Real recognition state only. Partial results are not shown and no audio is stored.",
        )
        self.diag_wake = StatusText("Checking…")
        diagnostics.add(QLabel("Listening state"))
        diagnostics.add(self.diag_wake)
        self.diag_heard = QLabel("Nothing recognised yet.")
        self.diag_match = QLabel("No command matched yet.")
        self.diag_command = QLabel("No command executed yet.")
        self.diag_result = QLabel("No result yet.")
        self.diag_failure = QLabel("No recognition failures.")
        self.diag_error = QLabel("No microphone or recognition errors.")
        for label in (self.diag_heard, self.diag_match, self.diag_command,
                      self.diag_result, self.diag_failure, self.diag_error):
            label.setWordWrap(True)
            label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        diagnostics.add(QLabel("Last recognised sentence"))
        diagnostics.add(self.diag_heard)
        diagnostics.add(QLabel("Last matched command"))
        diagnostics.add(self.diag_match)
        diagnostics.add(QLabel("Last executed command"))
        diagnostics.add(self.diag_command)
        diagnostics.add(QLabel("Last command result"))
        diagnostics.add(self.diag_result)
        diagnostics.add(QLabel("Last recognition failure"))
        diagnostics.add(self.diag_failure)
        diagnostics.add(QLabel("Microphone and recognition errors"))
        diagnostics.add(self.diag_error)
        self.diag_signal = QLabel("No audio measured yet.")
        self.diag_clipping = QLabel("Clipping: not measured.")
        self.diag_confidence = QLabel("Confidence: not measured.")
        self.diag_endpoint = QLabel("Endpoint: not measured.")
        self.diag_reason = QLabel("Match decision: no recognition attempt yet.")
        for label in (self.diag_signal, self.diag_clipping,
                      self.diag_confidence, self.diag_endpoint, self.diag_reason):
            label.setWordWrap(True)
            label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            diagnostics.add(label)
        diag_actions = QHBoxLayout()
        diag_listen = QPushButton("Listen now")
        diag_listen.clicked.connect(self.runtime.listen_now)
        diag_test_voice = QPushButton("Test voice")
        diag_test_voice.clicked.connect(self.runtime.test_voice)
        open_log = QPushButton("Open TALCON log")
        open_log.clicked.connect(self._open_log)
        diag_actions.addWidget(diag_listen)
        diag_actions.addWidget(diag_test_voice)
        diag_actions.addWidget(open_log)
        diag_actions.addStretch(1)
        diagnostics.add_layout(diag_actions)
        diagnostics.add(footnote(
            "The log records recognition text and errors only. Microphone audio is never written to disk."
        ))
        layout.addWidget(diagnostics)

        ai = Card(
            "Natural-language AI (optional)",
            "Questions and requests that are not TALCON commands can be "
            "answered by the locally-installed Hermes Agent. Local commands "
            "always keep priority, audio is never sent to the AI, and the "
            "feature is off until you switch it on.",
        )
        ai_row = QHBoxLayout()
        ai_row.addWidget(QLabel("Enable AI answers"))
        ai_row.addStretch(1)
        self.ai_switch = Switch()
        ai_row.addWidget(self.ai_switch)
        ai.add_layout(ai_row)

        ai.add(muted(
            "If Hermes is installed but its gateway is not running, TALCON "
            "starts it automatically. Everything stays on this PC."
        ))
        endpoint = QGridLayout()
        endpoint.setHorizontalSpacing(12)
        endpoint.setVerticalSpacing(6)
        self.ai_host = QLineEdit()
        self.ai_host.setPlaceholderText("127.0.0.1")
        self.ai_port = QSpinBox()
        self.ai_port.setRange(1, 65535)
        self.ai_model = QLineEdit()
        self.ai_model.setPlaceholderText("hermes-agent")
        endpoint.addWidget(QLabel("Host"), 0, 0)
        endpoint.addWidget(self.ai_host, 0, 1)
        endpoint.addWidget(QLabel("Port"), 0, 2)
        endpoint.addWidget(self.ai_port, 0, 3)
        endpoint.addWidget(QLabel("Model"), 1, 0)
        endpoint.addWidget(self.ai_model, 1, 1, 1, 3)
        ai.add_layout(endpoint)

        auto_row = QHBoxLayout()
        auto_row.addWidget(QLabel("Start the gateway automatically"))
        auto_row.addStretch(1)
        self.ai_autostart_switch = Switch()
        auto_row.addWidget(self.ai_autostart_switch)
        ai.add_layout(auto_row)

        stream_row = QHBoxLayout()
        stream_row.addWidget(QLabel("Speak while the answer streams in"))
        stream_row.addStretch(1)
        self.ai_stream_switch = Switch()
        stream_row.addWidget(self.ai_stream_switch)
        ai.add_layout(stream_row)
        ai_status_row = QHBoxLayout()
        self.ai_status = StatusText("AI is off — TALCON answers with local commands only.")
        ai_status_row.addWidget(self.ai_status, 1)
        self.ai_start_button = QPushButton("Start Hermes")
        self.ai_start_button.setToolTip(
            "Start the Hermes gateway installed on this PC and wait for its API."
        )
        self.ai_start_button.clicked.connect(self._start_ai_backend)
        ai_status_row.addWidget(self.ai_start_button)
        self.ai_check_button = QPushButton("Test connection")
        self.ai_check_button.clicked.connect(self._check_ai_backend)
        ai_status_row.addWidget(self.ai_check_button)
        ai.add_layout(ai_status_row)

        ai_actions = QHBoxLayout()
        save_backend = QPushButton("Save AI address")
        save_backend.clicked.connect(self._save_ai_backend)
        ai_actions.addWidget(save_backend)
        ai_actions.addStretch(1)
        self.ai_detail = QLabel("")
        self.ai_detail.setObjectName("Footnote")
        self.ai_detail.setWordWrap(True)
        self.ai_detail.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
        )
        ai.add_layout(ai_actions)
        ai.add(self.ai_detail)
        ai.add(footnote(
            "TALCON only ever talks to the Hermes server on 127.0.0.1, uses the "
            "API key already in your local Hermes configuration, and keeps AI "
            "conversation in memory for the current run."
        ))
        layout.addWidget(ai)

        privacy = Card("Privacy")
        privacy.add(muted(
            "Microphone audio is processed locally by the Vosk model and is never recorded, "
            "stored or uploaded. TALCON has no account system and makes no paid API calls. "
            "Recognition text is only ever sent to the local Hermes AI server on this PC, "
            "and only after you enable AI answers. "
            "The only network request it ever makes is the one-time speech-model download you start."
        ))
        layout.addWidget(privacy)
        layout.addStretch(1)
        return self._scroll(page)

    # =====================================================================
    # appearance
    # =====================================================================
    def _page_appearance(self) -> QWidget:
        page, layout = self._page()
        layout.addWidget(Heading(
            "Appearance",
            "A separate transparent companion window — Mac Island & Dock is never modified.",
        ))

        card = Card("Desktop face", "Click the face to open this window. Drag it anywhere; the position is remembered.")
        content = QHBoxLayout()
        content.setSpacing(22)
        controls = QVBoxLayout()
        controls.setSpacing(12)

        show_row = QHBoxLayout()
        show_row.addWidget(QLabel("Show the face on the desktop"))
        show_row.addStretch(1)
        self.face_switch = Switch()
        show_row.addWidget(self.face_switch)
        controls.addLayout(show_row)

        size_row = QHBoxLayout()
        size_row.addWidget(QLabel("Face size"))
        self.size_slider = QSlider(Qt.Orientation.Horizontal)
        self.size_slider.setRange(52, 128)
        self.size_slider.setMinimumWidth(180)
        self.size_value = QLabel("66 px")
        self.size_value.setObjectName("Muted")
        self.size_value.setFixedWidth(52)
        self.size_slider.valueChanged.connect(lambda v: self.size_value.setText(f"{v} px"))
        self.size_slider.valueChanged.connect(
            lambda _v: self._debounced_save("face_size", self._save_size)
        )
        self.size_slider.sliderReleased.connect(self._save_size)
        size_row.addWidget(self.size_slider, 1)
        size_row.addWidget(self.size_value)
        controls.addLayout(size_row)

        expressions_row = QHBoxLayout()
        expressions_row.addWidget(QLabel("Eye expressions and idle blinking"))
        expressions_row.addStretch(1)
        self.expressions_switch = Switch()
        expressions_row.addWidget(self.expressions_switch)
        controls.addLayout(expressions_row)

        sleepy_row = QHBoxLayout()
        sleepy_row.addWidget(QLabel("Look sleepy after"))
        self.sleepy_slider = QSlider(Qt.Orientation.Horizontal)
        self.sleepy_slider.setRange(20, 900)
        self.sleepy_slider.setMinimumWidth(180)
        self.sleepy_value = QLabel("90 s")
        self.sleepy_value.setObjectName("Muted")
        self.sleepy_value.setFixedWidth(52)
        self.sleepy_slider.valueChanged.connect(
            lambda v: self.sleepy_value.setText(f"{v} s")
        )
        self.sleepy_slider.valueChanged.connect(
            lambda _v: self._debounced_save("sleep_timers", self._save_sleep_timers)
        )
        self.sleepy_slider.sliderReleased.connect(self._save_sleep_timers)
        sleepy_row.addWidget(self.sleepy_slider, 1)
        sleepy_row.addWidget(self.sleepy_value)
        controls.addLayout(sleepy_row)

        sleep_row = QHBoxLayout()
        sleep_row.addWidget(QLabel("Close eyes after"))
        self.sleep_slider = QSlider(Qt.Orientation.Horizontal)
        self.sleep_slider.setRange(60, 3600)
        self.sleep_slider.setMinimumWidth(180)
        self.sleep_value = QLabel("300 s")
        self.sleep_value.setObjectName("Muted")
        self.sleep_value.setFixedWidth(52)
        self.sleep_slider.valueChanged.connect(
            lambda v: self.sleep_value.setText(f"{v} s")
        )
        self.sleep_slider.valueChanged.connect(
            lambda _v: self._debounced_save("sleep_timers", self._save_sleep_timers)
        )
        self.sleep_slider.sliderReleased.connect(self._save_sleep_timers)
        sleep_row.addWidget(self.sleep_slider, 1)
        sleep_row.addWidget(self.sleep_value)
        controls.addLayout(sleep_row)

        reset = QPushButton("Reset position (below the top bar, near the clock)")
        reset.clicked.connect(self._reset_face_position)
        controls.addWidget(reset)
        controls.addWidget(footnote(
            "The face is a 30 FPS circular overlay with transparent corners and a circular input mask, "
            "so clicks pass through everything outside the sphere. Animation pauses when it is hidden."
        ))
        controls.addStretch(1)

        content.addLayout(controls, 1)
        preview_box = QVBoxLayout()
        preview_box.setSpacing(6)
        preview = FacePreview(150)
        preview_box.addWidget(preview, 0, Qt.AlignmentFlag.AlignHCenter)
        caption = QLabel("Preview")
        caption.setObjectName("Footnote")
        caption.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        preview_box.addWidget(caption)
        content.addLayout(preview_box)
        card.add_layout(content)
        layout.addWidget(card)

        menu_card = Card(
            "Menu bar face",
            "The same animated face, docked beside the clock in the Mac Island & Dock bar. "
            "Drag the face along the bar to calibrate; the position is remembered.",
        )
        menu_controls = QVBoxLayout()
        menu_controls.setSpacing(12)

        menu_show_row = QHBoxLayout()
        menu_show_row.addWidget(QLabel("Show the face in the menu bar"))
        menu_show_row.addStretch(1)
        self.menu_face_switch = Switch()
        self.menu_face_switch.toggled.connect(
            lambda checked: self._save({"menu_bar_face_enabled": checked})
        )
        menu_show_row.addWidget(self.menu_face_switch)
        menu_controls.addLayout(menu_show_row)

        menu_size_row = QHBoxLayout()
        menu_size_row.addWidget(QLabel("Menu bar face size"))
        self.menu_size_slider = QSlider(Qt.Orientation.Horizontal)
        self.menu_size_slider.setRange(12, 32)
        self.menu_size_slider.setMinimumWidth(180)
        self.menu_size_value = QLabel("18 px")
        self.menu_size_value.setObjectName("Muted")
        self.menu_size_value.setFixedWidth(52)
        self.menu_size_slider.valueChanged.connect(
            lambda v: self.menu_size_value.setText(f"{v} px")
        )
        self.menu_size_slider.valueChanged.connect(
            lambda _v: self._debounced_save("menu_size", self._save_menu_size)
        )
        self.menu_size_slider.sliderReleased.connect(self._save_menu_size)
        menu_size_row.addWidget(self.menu_size_slider, 1)
        menu_size_row.addWidget(self.menu_size_value)
        menu_controls.addLayout(menu_size_row)

        offset_row = QHBoxLayout()
        offset_row.addWidget(QLabel("Horizontal offset"))
        self.menu_x_spin = QSpinBox()
        self.menu_x_spin.setRange(-400, 800)
        self.menu_x_spin.setSuffix(" px")
        self.menu_x_spin.valueChanged.connect(self._save_menu_offsets)
        offset_row.addWidget(self.menu_x_spin)
        offset_row.addSpacing(14)
        offset_row.addWidget(QLabel("Vertical offset"))
        self.menu_y_spin = QSpinBox()
        self.menu_y_spin.setRange(-12, 12)
        self.menu_y_spin.setSuffix(" px")
        self.menu_y_spin.valueChanged.connect(self._save_menu_offsets)
        offset_row.addWidget(self.menu_y_spin)
        offset_row.addStretch(1)
        menu_controls.addLayout(offset_row)

        height_row = QHBoxLayout()
        height_row.addWidget(QLabel("Menu bar height (0 = detect automatically)"))
        self.menu_height_spin = QSpinBox()
        self.menu_height_spin.setRange(0, 60)
        self.menu_height_spin.setSuffix(" px")
        self.menu_height_spin.valueChanged.connect(self._save_menu_offsets)
        height_row.addWidget(self.menu_height_spin)
        height_row.addStretch(1)
        menu_controls.addLayout(height_row)

        menu_animate_row = QHBoxLayout()
        menu_animate_row.addWidget(QLabel("Animate in the menu bar"))
        menu_animate_row.addStretch(1)
        self.menu_animate_switch = Switch()
        self.menu_animate_switch.toggled.connect(
            lambda checked: self._save({"menu_bar_face_animate": checked})
        )
        menu_animate_row.addWidget(self.menu_animate_switch)
        menu_controls.addLayout(menu_animate_row)

        menu_reset = QPushButton("Reset menu bar position")
        menu_reset.clicked.connect(self._reset_menu_position)
        menu_controls.addWidget(menu_reset)
        menu_controls.addWidget(footnote(
            "A transparent circular overlay with a circular input mask: clicks pass through "
            "everything outside the face, so the clock and neighbouring icons stay usable."
        ))
        menu_card.add_layout(menu_controls)
        layout.addWidget(menu_card)

        layout.addStretch(1)
        return self._scroll(page)

    # =====================================================================
    # monitoring
    # =====================================================================
    def _page_monitoring(self) -> QWidget:
        page, layout = self._page()
        layout.addWidget(Heading(
            "Application Monitoring",
            "Announcements are driven by real events only. Monitoring keeps running when this window is hidden.",
        ))

        opencode = Card("OpenCode", "Reads OpenCode's own local event stream. Keep its server on 127.0.0.1.")
        open_row = QHBoxLayout()
        open_row.addWidget(QLabel("Monitor OpenCode tasks"))
        open_row.addStretch(1)
        self.opencode_switch = Switch()
        open_row.addWidget(self.opencode_switch)
        opencode.add_layout(open_row)
        self.opencode_status = StatusText("Starting…")
        opencode.add(self.opencode_status)

        url_row = QHBoxLayout()
        self.opencode_url = QLineEdit()
        self.opencode_url.setPlaceholderText("http://127.0.0.1:4096")
        apply_url = QPushButton("Apply")
        apply_url.clicked.connect(self._save_opencode_url)
        test_connection = QPushButton("Test connection")
        test_connection.clicked.connect(self._test_opencode)
        url_row.addWidget(QLabel("Local server"))
        url_row.addWidget(self.opencode_url, 1)
        url_row.addWidget(apply_url)
        url_row.addWidget(test_connection)
        opencode.add_layout(url_row)
        self.opencode_test = QLabel("")
        self.opencode_test.setObjectName("Muted")
        opencode.add(self.opencode_test)
        opencode.add(footnote(
            "Start it with:  opencode serve --hostname 127.0.0.1 --port 4096.  "
            "A task is reported complete only after OpenCode reports the session busy and then idle."
        ))
        layout.addWidget(opencode)

        codex = Card("Codex Desktop", "Read-only observation of the session logs Codex Desktop writes locally.")
        codex_row = QHBoxLayout()
        codex_row.addWidget(QLabel("Monitor Codex Desktop turns"))
        codex_row.addStretch(1)
        self.codex_switch = Switch()
        codex_row.addWidget(self.codex_switch)
        codex.add_layout(codex_row)
        self.codex_status = StatusText("Checking…")
        codex.add(self.codex_status)
        self.codex_path = QLabel("")
        self.codex_path.setObjectName("Footnote")
        self.codex_path.setWordWrap(True)
        codex.add(self.codex_path)
        codex_actions = QHBoxLayout()
        open_codex = QPushButton("Open Codex sessions folder")
        open_codex.clicked.connect(self._open_codex_folder)
        codex_actions.addWidget(open_codex)
        codex_actions.addStretch(1)
        codex.add_layout(codex_actions)
        codex.add(footnote(
            "TALCON never starts its own Codex app-server and never treats a closed app as a finished task. "
            "It announces success or failure only from recorded task_started / task_complete / turn_aborted events. "
            "If those logs cannot be read, the status above says so and nothing is announced."
        ))
        layout.addWidget(codex)

        deepai = Card(
            "DeepAI Code",
            "Read-only observation of DeepAI Code's own local task database. "
            "No API key, account or network connection is used.",
        )
        deepai_row = QHBoxLayout()
        deepai_row.addWidget(QLabel("Monitor DeepAI Code tasks"))
        deepai_row.addStretch(1)
        self.deepai_switch = Switch()
        deepai_row.addWidget(self.deepai_switch)
        deepai.add_layout(deepai_row)
        self.deepai_status = StatusText("Checking…")
        deepai.add(self.deepai_status)
        self.deepai_path = QLabel("")
        self.deepai_path.setObjectName("Footnote")
        self.deepai_path.setWordWrap(True)
        deepai.add(self.deepai_path)
        deepai.add(footnote(
            "TALCON watches DeepAI Code's local run records and announces completion or failure "
            "only after the run itself reaches that state. Existing history is never replayed."
        ))
        layout.addWidget(deepai)

        announcements = Card("Announcements")
        announce_row = QHBoxLayout()
        announce_row.addWidget(QLabel("Also announce interrupted tasks"))
        announce_row.addStretch(1)
        self.interrupt_switch = Switch()
        announce_row.addWidget(self.interrupt_switch)
        announcements.add_layout(announce_row)
        announcements.add(footnote(
            "Completion and failure are always announced when the voice is unmuted. "
            "Muting speech never stops monitoring."
        ))
        layout.addWidget(announcements)
        layout.addStretch(1)
        return self._scroll(page)

    # =====================================================================
    # startup
    # =====================================================================
    def _page_cron_jobs(self) -> QWidget:
        self.cron_page = CronJobsPage(self.runtime)
        return self._scroll(self.cron_page)

    def _page_startup(self) -> QWidget:
        page, layout = self._page()
        layout.addWidget(Heading("Startup Settings", "TALCON can start quietly when you sign in to Windows."))

        card = Card("Start with Windows")
        start_row = QHBoxLayout()
        start_row.addWidget(QLabel("Start TALCON automatically after I sign in"))
        start_row.addStretch(1)
        self.startup_switch = Switch()
        start_row.addWidget(self.startup_switch)
        card.add_layout(start_row)
        self.startup_status = StatusText("Checking…")
        card.add(self.startup_status)
        card.add(muted(
            "Uses the normal per-user Startup folder. No administrator rights and no registry changes. "
            "TALCON starts with the face and tray only — the control center stays closed."
        ))
        actions = QHBoxLayout()
        open_startup = QPushButton("Open Startup folder")
        open_startup.clicked.connect(lambda: self._open_path(startup.startup_folder()))
        actions.addWidget(open_startup)
        actions.addStretch(1)
        card.add_layout(actions)
        layout.addWidget(card)

        closing = Card("Closing and uninstalling")
        closing.add(muted(
            "Closing TALCON (×) stops the microphone listener, speech, application monitoring and the face, "
            "but leaves this startup setting exactly as it is, so TALCON returns at the next sign-in."
        ))
        uninstall = QPushButton("Uninstall TALCON…")
        uninstall.setObjectName("Danger")
        uninstall.clicked.connect(self._uninstall)
        closing.add(uninstall)
        layout.addWidget(closing)
        layout.addStretch(1)
        return self._scroll(page)

    # =====================================================================
    # wiring
    # =====================================================================
    def _connect(self) -> None:
        self.min_button.clicked.connect(self.showMinimized)
        self.hide_button.clicked.connect(self.runtime.hide_to_tray)
        self.close_button.clicked.connect(self.runtime.exit_completely)

        self.speech_switch.toggled.connect(lambda checked: self.runtime.set_muted(not checked))
        self.ai_switch.toggled.connect(self.runtime.set_ai_enabled)
        self.ai_autostart_switch.toggled.connect(
            lambda checked: self._save({"ai_autostart_backend": bool(checked)})
        )
        self.ai_stream_switch.toggled.connect(
            lambda checked: self._save({"ai_streaming": bool(checked)})
        )
        self.wake_switch.toggled.connect(self.runtime.set_wake_enabled)
        self.always_switch.toggled.connect(
            lambda checked: self._save({"always_listen": bool(checked)})
        )
        self.asr_engine_combo.currentIndexChanged.connect(self._on_asr_engine_chosen)
        self.agc_switch.toggled.connect(
            lambda checked: self._save_audio_frontend()
        )
        self.noise_switch.toggled.connect(
            lambda checked: self._save_audio_frontend()
        )
        self.aec_switch.toggled.connect(
            lambda checked: self._save_audio_frontend()
        )
        self.deepai_switch.toggled.connect(
            lambda checked: self._save({"deepai_enabled": checked})
        )
        self.opencode_switch.toggled.connect(
            lambda checked: self._save({"opencode_enabled": checked})
        )
        self.codex_switch.toggled.connect(lambda checked: self._save({"codex_enabled": checked}))
        self.interrupt_switch.toggled.connect(
            lambda checked: self._save({"announce_interruptions": checked})
        )
        self.face_switch.toggled.connect(lambda checked: self._save({"show_face": checked}))
        self.expressions_switch.toggled.connect(
            lambda checked: self._save({"face_expressions": checked})
        )
        self.startup_switch.toggled.connect(self._on_startup_toggled)
        self.engine_combo.currentIndexChanged.connect(self._on_engine_chosen)
        self.voice_combo.currentIndexChanged.connect(self._on_voice_chosen)
        self.mic_combo.currentIndexChanged.connect(self._on_mic_chosen)

        bridge = self.runtime.bridge
        bridge.status_changed.connect(self._queue_status)
        bridge.speech_voices_changed.connect(self._populate_speech_voices)
        bridge.model_progress.connect(self._on_model_progress)
        bridge.settings_changed.connect(self._on_settings_changed)
        bridge.modes_changed.connect(self._on_modes_changed)
        bridge.conversation_changed.connect(self._on_conversation_changed)
        bridge.assistant_state.connect(self._on_assistant_state)

    # =====================================================================
    # data loading
    # =====================================================================
    def load_from_runtime(self) -> None:
        s = self.runtime.settings
        self.speech_switch.set_checked_silently(bool(s.get("voice_enabled", True)))
        self.ai_switch.set_checked_silently(bool(s.get("ai_enabled", False)))
        self.wake_switch.set_checked_silently(bool(s.get("wake_enabled", True)))
        self.always_switch.set_checked_silently(bool(s.get("always_listen", True)))
        self._sync_asr_controls(s)
        self.opencode_switch.set_checked_silently(bool(s.get("opencode_enabled", True)))
        self.codex_switch.set_checked_silently(bool(s.get("codex_enabled", True)))
        self.deepai_switch.set_checked_silently(bool(s.get("deepai_enabled", True)))
        try:
            from ..monitors.deepai import state_db_path
            self.deepai_path.setText(f"Local database: {state_db_path()}")
        except Exception:
            self.deepai_path.setText("")
        self.interrupt_switch.set_checked_silently(bool(s.get("announce_interruptions", False)))
        self.face_switch.set_checked_silently(bool(s.get("show_face", True)))
        self.expressions_switch.set_checked_silently(bool(s.get("face_expressions", True)))
        self.startup_switch.set_checked_silently(startup.is_enabled())
        for widget, key, default in (
            (self.timing_start, "speech_start_timeout", 7.0),
            (self.timing_silence, "speech_trailing_silence", 1.0),
            (self.timing_max, "speech_max_utterance", 20.0),
            (self.timing_confidence, "speech_min_confidence", 0.30),
        ):
            widget.blockSignals(True)
            widget.setValue(float(s.get(key, default)))
            widget.blockSignals(False)
        self.startup_status.set_text(
            "Enabled · TALCON starts quietly at sign-in" if startup.is_enabled()
            else "Disabled · TALCON only starts when you launch it"
        )

        self.rate_slider.blockSignals(True)
        self.rate_slider.setValue(int(s.get("speech_rate", 0)))
        self.rate_slider.blockSignals(False)
        self._on_rate_changed(int(s.get("speech_rate", 0)))

        self.volume_slider.blockSignals(True)
        self.volume_slider.setValue(int(s.get("speech_volume", 90)))
        self.volume_slider.blockSignals(False)
        self._on_volume_changed(int(s.get("speech_volume", 90)))

        pause = float(s.get("speech_sentence_pause", 1.0) or 0.0)
        self.pause_slider.blockSignals(True)
        self.pause_slider.setValue(int(round(max(0.0, min(3.0, pause)) * 10)))
        self.pause_slider.blockSignals(False)
        self._on_pause_changed(self.pause_slider.value())

        self._sync_engine_combo(s.get("speech_backend", BACKEND_AUTO))

        self.size_slider.blockSignals(True)
        self.size_slider.setValue(int(s.get("face_size", 66)))
        self.size_slider.blockSignals(False)
        self.size_value.setText(f"{int(s.get('face_size', 66))} px")

        self.sleepy_slider.blockSignals(True)
        self.sleepy_slider.setValue(int(s.get("face_sleepy_after", 90)))
        self.sleepy_slider.blockSignals(False)
        self.sleepy_value.setText(f"{int(s.get('face_sleepy_after', 90))} s")

        self.sleep_slider.blockSignals(True)
        self.sleep_slider.setValue(int(s.get("face_sleep_after", 300)))
        self.sleep_slider.blockSignals(False)
        self.sleep_value.setText(f"{int(s.get('face_sleep_after', 300))} s")

        self._sync_menu_face_controls(s)
        self._sync_ai_controls(s)

        self.opencode_url.setText(s.get("opencode_base_url", "http://127.0.0.1:4096"))
        details = []
        if hasattr(self.runtime, "speech_voice_details"):
            details = self.runtime.speech_voice_details()
        engine = (
            self.runtime.speech_engine_info()
            if hasattr(self.runtime, "speech_engine_info")
            else {"requested": s.get("speech_backend", BACKEND_AUTO)}
        )
        self._populate_speech_voices(details, engine)
        self._populate_microphones()
        self._refresh_model_status()
        self._update_mute_labels()

        for editor in getattr(self, "mode_editors", {}).values():
            editor.refresh()
        if hasattr(self, "custom_modes_page"):
            self.custom_modes_page.refresh()

        path = startup.startup_folder()
        self.startup_status.setToolTip(str(path))
        from ..monitors.codex import sessions_root

        root = sessions_root()
        self.codex_path.setText(f"Session logs: {root}" + ("" if root.is_dir() else "  (not found)"))
        self.codex_path.setToolTip(str(root))

    def show_page(self, key: str) -> None:
        if key not in self.pages:
            key = "dashboard"
        self._current_page = key
        self.stack.setCurrentWidget(self.pages[key])
        for name, button in self.nav_buttons.items():
            button.setChecked(name == key)
        if key == "cron" and hasattr(self, "cron_page"):
            self.cron_page.refresh()
        if key == "history" and hasattr(self, "history_page"):
            self.history_page.refresh()
        if key == "commands" and hasattr(self, "command_log_page"):
            self.command_log_page.refresh()

    def _sync_menu_face_controls(self, s: dict) -> None:
        self.menu_face_switch.set_checked_silently(bool(s.get("menu_bar_face_enabled", True)))
        self.menu_animate_switch.set_checked_silently(bool(s.get("menu_bar_face_animate", True)))
        size = int(s.get("menu_bar_face_size", 18) or 18)
        self.menu_size_slider.blockSignals(True)
        self.menu_size_slider.setValue(size)
        self.menu_size_slider.blockSignals(False)
        self.menu_size_value.setText(f"{size} px")
        for spin, key in (
            (self.menu_x_spin, "menu_bar_face_x_offset"),
            (self.menu_y_spin, "menu_bar_face_y_offset"),
            (self.menu_height_spin, "menu_bar_face_height"),
        ):
            spin.blockSignals(True)
            spin.setValue(int(s.get(key, 0) or 0))
            spin.blockSignals(False)

    def sync_controls(self) -> None:
        s = self.runtime.settings
        self.speech_switch.set_checked_silently(bool(s.get("voice_enabled", True)))
        self.ai_switch.set_checked_silently(bool(s.get("ai_enabled", False)))
        self.wake_switch.set_checked_silently(bool(s.get("wake_enabled", True)))
        self.always_switch.set_checked_silently(bool(s.get("always_listen", True)))
        self._sync_asr_controls(s)
        self.face_switch.set_checked_silently(bool(s.get("show_face", True)))
        self.expressions_switch.set_checked_silently(bool(s.get("face_expressions", True)))
        self.startup_switch.set_checked_silently(startup.is_enabled())
        for widget, key, default in (
            (self.timing_start, "speech_start_timeout", 7.0),
            (self.timing_silence, "speech_trailing_silence", 1.0),
            (self.timing_max, "speech_max_utterance", 20.0),
            (self.timing_confidence, "speech_min_confidence", 0.30),
        ):
            # Programmatic sync must not look like a user edit (the spin
            # boxes auto-save on valueChanged).
            widget.blockSignals(True)
            widget.setValue(float(s.get(key, default)))
            widget.blockSignals(False)
        self._sync_engine_combo(s.get("speech_backend", BACKEND_AUTO))
        self._sync_menu_face_controls(s)
        self._update_mute_labels()
        self._sync_ai_controls(s)

    def _sync_ai_controls(self, s: dict) -> None:
        for widget, value in (
            (self.ai_host, str(s.get("hermes_host", "127.0.0.1"))),
            (self.ai_port, int(s.get("hermes_port", 8642) or 8642)),
            (self.ai_model, str(s.get("hermes_model", "hermes-agent"))),
        ):
            widget.blockSignals(True)
            if isinstance(widget, QSpinBox):
                widget.setValue(value)
            else:
                widget.setText(value)
            widget.blockSignals(False)
        self.ai_autostart_switch.set_checked_silently(
            bool(s.get("ai_autostart_backend", True))
        )
        self.ai_stream_switch.set_checked_silently(
            bool(s.get("ai_streaming", True))
        )

    def _on_settings_changed(self, settings: dict) -> None:
        self.sync_controls()
        for editor in getattr(self, "mode_editors", {}).values():
            editor.refresh()
        if hasattr(self, "custom_modes_page"):
            self.custom_modes_page.refresh()
        self.startup_status.set_text(
            "Enabled · TALCON starts quietly at sign-in" if startup.is_enabled()
            else "Disabled · TALCON only starts when you launch it"
        )
        self._refresh_model_status()

    def _update_mute_labels(self) -> None:
        if self.runtime.muted:
            self.header_status.setText("Voice muted · monitoring active")
        dash = getattr(self, "dash_mute", None)
        if dash is not None:
            dash.setText("Unmute voice" if self.runtime.muted else "Mute voice")

    # =====================================================================
    # status
    # =====================================================================
    def _queue_status(self, status: dict) -> None:
        """Coalesce bursts of status updates into one repaint.

        One command publishes five to eight snapshots in a row (heard, match,
        command, result, activity, speech).  Rendering each one writes ~25
        labels and a few stylesheets; a 100 ms window keeps the UI smooth and
        loses nothing a person can perceive.
        """
        self._pending_status = dict(status or {})
        timer = getattr(self, "_status_timer", None)
        if timer is None:
            timer = self._status_timer = QTimer(self)
            timer.setSingleShot(True)
            timer.setInterval(100)
            timer.timeout.connect(self._flush_status)
        timer.start()

    def _flush_status(self) -> None:
        pending = getattr(self, "_pending_status", None)
        if pending:
            self.update_statuses(pending)

    def update_statuses(self, status: dict) -> None:
        self._status = dict(status or {})
        for key, widget in getattr(self, "stat_widgets", {}).items():
            widget.set_text(self._status.get(key, "—"))
        self.voice_status.set_text(self._status.get("voice", "—"))
        self._update_speech_engine_status()
        self.opencode_status.set_text(self._status.get("opencode", "—"))
        self.codex_status.set_text(self._status.get("codex", "—"))
        self.diag_wake.set_text(self._status.get("wake", "—"))
        self.last_event.setText(self._status.get("last_event", "TALCON is ready."))

        heard = str(self._status.get("last_heard", "") or "").strip()
        matched = str(self._status.get("last_match", "") or "").strip()
        command = str(self._status.get("last_command", "") or "").strip()
        result = str(self._status.get("last_result", "") or "").strip()
        failure = str(self._status.get("last_failure", "") or "").strip()
        error = str(self._status.get("mic_error", "") or "").strip()
        self.diag_heard.setText(heard or "Nothing recognised yet.")
        self.diag_match.setText(matched or "No command matched yet.")
        self.diag_command.setText(command or "No command executed yet.")
        self.diag_result.setText(result or "No result yet.")
        self.diag_failure.setText(failure or "No recognition failures.")
        self.diag_error.setText(error or "No microphone or recognition errors.")
        noise = self._status.get("speech_noise", "—")
        rms = self._status.get("speech_rms", "—")
        gate = self._status.get("speech_threshold", "—")
        self.diag_signal.setText(f"Noise floor: {noise} · speech RMS: {rms} · adaptive gate: {gate}")
        self.diag_clipping.setText(f"Clipping: {self._status.get('speech_clipping', 'Not detected')}")
        self.diag_confidence.setText(f"Recognition confidence: {self._status.get('speech_confidence', '—')}")
        self.diag_endpoint.setText(f"Trailing-silence endpoint: {self._status.get('speech_endpoint', '—')}")
        self.diag_reason.setText(f"Match decision: {self._status.get('speech_reject_reason', '—')}")
        activity = self._status.get("recent_activity") or []
        self.activity_label.setText("\n".join(str(item) for item in activity) or "No activity yet.")

        header = self._status.get("wake") or self._status.get("voice", "")
        if self.runtime.muted:
            header = "Voice muted · monitoring active"
        self.header_status.setText(str(header)[:70])
        self.header_status.setStyleSheet(
            f"font-size: 8.8pt; color: {theme.status_colour(header)};"
        )
        self._update_mute_labels()

    # =====================================================================
    # actions
    # =====================================================================
    def _save(self, values: dict) -> None:
        try:
            self.runtime.save_settings(values)
        except Exception as exc:
            QMessageBox.warning(self, "TALCON", f"Could not save settings: {exc}")

    def _debounced_save(self, key: str, save, delay_ms: int = 400) -> None:
        """Coalesce rapid slider changes into one write after the value settles.

        A click on the slider groove or an arrow-key nudge changes the value
        without ever emitting ``sliderReleased``; without this path those
        changes were shown but never persisted across restarts.
        """
        timers = getattr(self, "_pending_saves", None)
        if timers is None:
            timers = self._pending_saves = {}
        existing = timers.get(key)
        if existing is not None:
            existing.stop()
        timer = QTimer(self)
        timer.setSingleShot(True)

        def fire() -> None:
            timers.pop(key, None)
            save()

        timer.timeout.connect(fire)
        timer.start(delay_ms)
        timers[key] = timer

    def _flush_pending_saves(self) -> None:
        """Persist every debounced change immediately (window closing/quit)."""
        self._remember_geometry()
        pending = list(getattr(self, "_pending_saves", {}).values())
        self._pending_saves = {}
        for timer in pending:
            timer.stop()
            timer.timeout.emit()

    def _reset_face_position(self) -> None:
        self.face.reset_position()

    def _save_size(self) -> None:
        self._save({"face_size": self.size_slider.value()})

    def _save_menu_size(self) -> None:
        self._save({"menu_bar_face_size": self.menu_size_slider.value()})

    def _save_menu_offsets(self, *_args) -> None:
        self._save({
            "menu_bar_face_x_offset": self.menu_x_spin.value(),
            "menu_bar_face_y_offset": self.menu_y_spin.value(),
            "menu_bar_face_height": self.menu_height_spin.value(),
        })

    def _reset_menu_position(self) -> None:
        if self.menu_face is not None:
            self.menu_face.recalibrate()
            return
        self._save({
            "menu_bar_face_x_ratio": 0.0,
            "menu_bar_face_x_offset": 0,
            "menu_bar_face_y_offset": 0,
        })

    def _save_sleep_timers(self) -> None:
        sleepy = self.sleepy_slider.value()
        sleep = max(sleepy + 10, self.sleep_slider.value())
        self._save({"face_sleepy_after": sleepy, "face_sleep_after": sleep})

    def _on_rate_changed(self, value: int) -> None:
        self.rate_label.setText("Slow" if value < -2 else ("Fast" if value > 2 else "Normal"))
        try:
            self.runtime.speech.set_rate(int(value))
        except Exception:
            pass

    def _save_rate(self) -> None:
        self._save({"speech_rate": self.rate_slider.value()})

    def _on_volume_changed(self, value: int) -> None:
        self.volume_label.setText(f"{int(value)}%")
        try:
            self.runtime.speech.set_volume(int(value))
        except Exception:
            pass

    def _save_volume(self) -> None:
        self._save({"speech_volume": self.volume_slider.value()})

    def _on_pause_changed(self, value: int) -> None:
        seconds = int(value) / 10.0
        self.pause_label.setText(f"{seconds:.1f} s")
        try:
            self.runtime.speech.set_sentence_pause(seconds)
        except Exception:
            pass

    def _save_pause(self) -> None:
        self._save({"speech_sentence_pause": self.pause_slider.value() / 10.0})

    def _preview_voice(self) -> None:
        preview = getattr(self.runtime, "preview_voice", None)
        if callable(preview):
            preview()
        else:
            self.runtime.test_voice()

    def _sync_engine_combo(self, value: str) -> None:
        index = self.engine_combo.findData(value)
        if index < 0:
            index = self.engine_combo.findData(BACKEND_AUTO)
        if index >= 0 and index != self.engine_combo.currentIndex():
            self.engine_combo.blockSignals(True)
            self.engine_combo.setCurrentIndex(index)
            self.engine_combo.blockSignals(False)

    def _on_engine_chosen(self, index: int) -> None:
        key = self.engine_combo.itemData(index)
        if not key:
            return
        if hasattr(self.runtime, "set_speech_backend"):
            self.runtime.set_speech_backend(key)
        else:
            self._save({"speech_backend": key})

    def _on_voice_chosen(self, index: int) -> None:
        voice_id = self.voice_combo.itemData(index)
        if not voice_id:
            return
        detail = self._voice_lookup.get(voice_id, {})
        name = str(detail.get("name") or self.voice_combo.itemText(index))
        if voice_id == self.runtime.settings.get("speech_voice_id"):
            return
        if hasattr(self.runtime, "choose_speech_voice"):
            self.runtime.choose_speech_voice(voice_id, name)
        else:
            self._save({"speech_voice": name, "speech_voice_id": voice_id})

    def _populate_speech_voices(self, voices: list, engine: dict | None = None) -> None:
        current_id = self.runtime.settings.get("speech_voice_id", "")
        current_name = self.runtime.settings.get("speech_voice", "")
        self._voice_lookup = {}
        self.voice_combo.blockSignals(True)
        self.voice_combo.clear()
        for entry in voices or []:
            if isinstance(entry, dict):
                voice_id = str(entry.get("id") or entry.get("name") or "")
                name = str(entry.get("name") or voice_id)
                label = str(entry.get("label") or entry.get("engine_label") or "Kokoro voice")
                display = f"{name} — {label}"
                self._voice_lookup[voice_id] = dict(entry)
            else:
                voice_id = str(entry)
                name = voice_id
                display = voice_id
                self._voice_lookup[voice_id] = {"id": voice_id, "name": name}
            self.voice_combo.addItem(display, voice_id)
        position = self.voice_combo.findData(current_id) if current_id else -1
        if position < 0 and current_name:
            for candidate in range(self.voice_combo.count()):
                if self._voice_lookup.get(
                    self.voice_combo.itemData(candidate), {}
                ).get("name") == current_name:
                    position = candidate
                    break
        if position >= 0:
            self.voice_combo.setCurrentIndex(position)
        elif current_id:
            # Never pretend the saved voice is active.  Show it as missing and
            # keep the saved preference until the user chooses again.
            self.voice_combo.insertItem(
                0, f"Saved voice unavailable — {current_name or current_id}"
            )
            self.voice_combo.setItemData(0, "")
            self.voice_combo.setCurrentIndex(0)
        self.voice_combo.blockSignals(False)
        if not voices:
            self.voice_combo.addItem("No Kokoro voices found — install the model")
            self.voice_combo.setEnabled(False)
        else:
            self.voice_combo.setEnabled(True)
        if engine:
            self._sync_engine_combo(engine.get("requested", BACKEND_AUTO))
        self._update_speech_engine_status()

    def _update_speech_engine_status(self) -> None:
        requested = str(self._status.get("speech_engine", "") or "")
        active = str(self._status.get("speech_engine_active", "") or "")
        note = str(self._status.get("speech_engine_note", "") or "")
        voice = str(self._status.get("speech_voice_name", "") or "")
        voice_id = str(self._status.get("speech_voice_id", "") or "")
        language = str(self._status.get("speech_voice_language", "") or "")
        gender = str(self._status.get("speech_voice_gender", "") or "")
        offline = str(self._status.get("speech_offline", "") or "")
        synthesis = str(self._status.get("speech_synthesis", "") or "")
        muted = bool(getattr(self.runtime, "muted", False))

        if muted:
            self.engine_status.set_text("Muted · speech output is paused")
        elif active:
            self.engine_status.set_text(f"Active engine: {active}")
        else:
            self.engine_status.set_text("Checking the Kokoro voice…")

        self.engine_requested_value.setText(requested or "Kokoro Neural Voice")
        self.engine_active_value.setText(active or "Not started yet")
        self.voice_name_value.setText(voice or "No voice selected")
        self.voice_id_value.setText(voice_id or "—")
        self.voice_language_value.setText(language or "—")
        self.voice_gender_value.setText(gender or "Not reported")
        self.voice_offline_value.setText(offline or "On-device")
        self.voice_availability_value.setText(
            "Installed and selectable" if voice else "No usable voice"
        )
        self.synthesis_value.setText(synthesis or "Not spoken yet")
        self.engine_note.setText(note)
        self.engine_note.setVisible(bool(note))

    def _run_voice_diagnostic(self) -> None:
        """Run the real Kokoro voice check off the UI thread."""
        from ..speech import voice_diagnostic

        self.engine_status.set_text("Checking the Kokoro voice…")

        def work():
            return voice_diagnostic(audible=False)

        def done(result) -> None:
            if isinstance(result, Exception):
                self.engine_status.set_text("Voice diagnostic failed")
                QMessageBox.warning(
                    self, "Voice diagnostic", f"Could not read the Kokoro speech path:\n{result}"
                )
                return
            self._update_speech_engine_status()
            dialog = QMessageBox(self)
            dialog.setWindowTitle("TALCON voice diagnostic")
            dialog.setIcon(QMessageBox.Icon.Information)
            dialog.setText(
                "This reports the real Kokoro speech path, not a mock. "
                "Expand “Show Details” to read it."
            )
            dialog.setDetailedText(str(result))
            dialog.exec()

        run_async(self, work, done)

    def _sync_asr_controls(self, s: dict) -> None:
        combo = getattr(self, "asr_engine_combo", None)
        if combo is not None:
            combo.blockSignals(True)
            position = combo.findData(str(s.get("asr_engine", "auto")))
            combo.setCurrentIndex(max(0, position))
            combo.blockSignals(False)
        for widget, key, default in (
            (self.agc_switch, "audio_agc", True),
            (self.noise_switch, "audio_noise_suppression", True),
            (self.aec_switch, "audio_aec", False),
        ):
            widget.set_checked_silently(bool(s.get(key, default)))
        status = getattr(self, "asr_status", None)
        if status is not None:
            reason = engine_reason(self.runtime.settings)
            if not engine_model_ready(self.runtime.settings):
                reason += " · model not installed"
            status.set_text(reason)

    def _on_asr_engine_chosen(self, _index: int) -> None:
        key = self.asr_engine_combo.currentData()
        self._save({"asr_engine": str(key or "auto")})
        self._sync_asr_controls(self.runtime.settings)

    def _save_audio_frontend(self) -> None:
        self._save({
            "audio_agc": bool(self.agc_switch.isChecked()),
            "audio_noise_suppression": bool(self.noise_switch.isChecked()),
            "audio_aec": bool(self.aec_switch.isChecked()),
        })
        self._sync_asr_controls(self.runtime.settings)

    def _populate_microphones(self) -> None:
        selected = self.runtime.settings.get("microphone_device")
        self.mic_combo.blockSignals(True)
        self.mic_combo.clear()
        for index, name in self.runtime.microphones():
            self.mic_combo.addItem(str(name), index)
        if selected is not None:
            position = self.mic_combo.findData(selected)
            if position >= 0:
                self.mic_combo.setCurrentIndex(position)
        self.mic_combo.blockSignals(False)

    def _on_mic_chosen(self, _index: int) -> None:
        self._save({"microphone_device": self.mic_combo.currentData()})

    def _check_microphone(self) -> None:
        device = self.mic_combo.currentData()
        self.mic_level.setText("Recording a 1.2 s test…")
        self._mic_result = None

        def work():
            return measure_input(1.2, device)

        run_async(self, work, self._on_mic_result)

    def _on_mic_result(self, result) -> None:
        if isinstance(result, Exception):
            self.mic_level.setText(f"Microphone error: {result}")
            return
        if not result.get("ok"):
            self.mic_level.setText(f"Microphone unavailable: {result.get('error')}")
            return
        peak = int(result.get("peak", 0))
        percent = min(100, int(peak / 327.68))
        if peak < 300:
            self.mic_level.setText("Almost silent — check the Windows input level.")
        else:
            self.mic_level.setText(f"Microphone works · level {percent}%")

    def _calibrate_microphone(self) -> None:
        device = self.mic_combo.currentData()
        self.mic_level.setText("Stay quiet for 2 s, then speak normally for 3 s…")
        self._mic_result = None

        def work():
            return calibrate_microphone(2.0, 3.0, device)

        run_async(self, work, self._on_calibration_result)

    def _on_calibration_result(self, result) -> None:
        if isinstance(result, Exception):
            self.mic_level.setText(f"Calibration error: {result}")
            return
        if not result.get("ok"):
            self.mic_level.setText(f"Calibration unavailable: {result.get('error')}")
            return
        noise = float(result.get("noise_floor", 0))
        speech = float(result.get("speech_rms", 0))
        snr = float(result.get("snr", 0))
        clipping = "clipping detected" if result.get("clipping") else "no clipping"
        self.mic_level.setText(
            f"Noise {noise:g} · speech {speech:g} · SNR {snr:g}× · {clipping}"
        )
        try:
            self.runtime.save_settings({"vad_noise_floor": noise})
            voice = getattr(self.runtime, "voice", None)
            if voice is not None and hasattr(voice, "set_noise_floor"):
                voice.set_noise_floor(noise)
        except Exception as exc:
            self.mic_level.setText(f"Calibrated, but could not save: {exc}")

    def _save_speech_timing(self) -> None:
        self.runtime.save_settings({
            "speech_start_timeout": self.timing_start.value(),
            "speech_trailing_silence": self.timing_silence.value(),
            "speech_max_utterance": self.timing_max.value(),
            "speech_min_confidence": self.timing_confidence.value(),
        })

    def _refresh_model_status(self) -> None:
        settings = self.runtime.settings
        if engine_model_ready(settings):
            self.model_status.set_text(f"Installed · {engine_reason(settings)}")
            self.download_button.setEnabled(False)
            self.download_button.setText("Speech model installed")
            self.model_note.setText("Apache-2.0 model. Runs entirely offline.")
            return
        sherpa_selected = str(settings.get("asr_engine") or "auto").lower() == "sherpa"
        self.model_status.set_text("Not installed · download the free model to use voice")
        self.download_button.setEnabled(not self._download_active)
        if sherpa_selected:
            self.download_button.setText("Download sherpa-onnx model (~250 MB)")
            self.model_note.setText(
                f"Apache-2.0 streaming model from the sherpa-onnx project. "
                f"Saved to {MODELS_DIR}. Vosk remains the fallback."
            )
        else:
            self.download_button.setText("Download offline speech model (~40 MB)")
            self.model_note.setText(
                f"Apache-2.0 model from alphacephei.com. Saved to {MODELS_DIR}."
            )

    def _download_model(self) -> None:
        if self._download_active:
            return
        self._download_active = True
        self.download_button.setEnabled(False)
        self.model_progress.setVisible(True)
        self.model_progress.setValue(0)
        self.model_note.setText("Starting download…")
        self.runtime.download_speech_model()

    def _on_model_progress(self, percent: int, text: str) -> None:
        if percent < 0:
            self._download_active = False
            self.model_progress.setVisible(False)
            self.model_note.setText(text)
            self.model_note.setStyleSheet(f"color: {theme.BAD}; font-size: 8.5pt;")
            self.download_button.setEnabled(True)
            return
        self.model_progress.setVisible(percent < 100)
        self.model_progress.setValue(max(0, min(100, percent)))
        self.model_note.setText(text)
        if percent >= 100:
            self._download_active = False
            self.model_note.setStyleSheet("")
            self._refresh_model_status()

    def _opencode_base(self) -> str:
        return self.opencode_url.text().strip().rstrip("/")

    def _save_opencode_url(self) -> None:
        self._save({"opencode_base_url": self._opencode_base()})
        self.opencode_test.setText("Saved. Reconnecting…")

    def _test_opencode(self) -> None:
        url = self._opencode_base()

        def work():
            import requests

            from ..monitors.opencode import (
                _auth_header,
                discover_service_password,
            )

            headers = {
                "Accept": "text/event-stream",
                **_auth_header(discover_service_password()),
            }
            last: Exception = RuntimeError("no response")
            for suffix in ("/api/event", "/event"):
                try:
                    response = requests.get(
                        url + suffix, timeout=(3, 5), stream=True, headers=headers,
                    )
                    status = response.status_code
                    ctype = response.headers.get("content-type", "")
                    response.close()
                    if status == 401:
                        return "Authentication required · service password not found"
                    if status == 200 and "text/event-stream" in ctype:
                        auth = "authenticated" if headers.get("Authorization") else "local"
                        return f"Event stream OK ({auth}) · {suffix}"
                    last = RuntimeError(f"{suffix} → HTTP {status}")
                except Exception as exc:
                    last = exc
            raise last

        self.opencode_test.setText("Checking…")
        run_async(self, work, lambda result: self.opencode_test.setText(
            f"Not reachable · {result}" if isinstance(result, Exception) else str(result)
        ))

    def _open_codex_folder(self) -> None:
        from ..monitors.codex import sessions_root

        self._open_path(sessions_root())

    def _on_startup_toggled(self, checked: bool) -> None:
        try:
            self.runtime.save_settings({"start_with_windows": bool(checked)})
        except Exception as exc:
            QMessageBox.warning(self, "TALCON", f"Could not change startup: {exc}")
        enabled = startup.is_enabled()
        if enabled != bool(checked):
            self.startup_switch.set_checked_silently(enabled)
        self.startup_status.set_text(
            "Enabled · TALCON starts quietly at sign-in" if enabled
            else "Disabled · TALCON only starts when you launch it"
        )

    def _uninstall(self) -> None:
        answer = QMessageBox.question(
            self,
            "Uninstall TALCON",
            "This opens the TALCON uninstaller and closes the assistant.\n\n"
            "You will be asked whether to delete the downloaded speech model and settings. "
            "Your Windows startup entry is always removed.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Cancel,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        script = ROOT / "Uninstall TALCON.bat"
        if not script.exists():
            QMessageBox.warning(self, "TALCON", "Uninstall TALCON.bat was not found next to the application.")
            return
        try:
            subprocess.Popen(["cmd.exe", "/c", "start", "", str(script)], cwd=str(ROOT))
        except Exception as exc:
            QMessageBox.critical(self, "TALCON", f"Could not start the uninstaller: {exc}")
            return
        self.runtime.exit_completely()

    def _open_path(self, path) -> None:
        try:
            os.startfile(str(path))  # noqa: S606 - opening a local folder/readme
        except Exception as exc:
            QMessageBox.warning(self, "TALCON", f"Could not open {path}: {exc}")

    def _open_log(self) -> None:
        log_file = LOGS_DIR / "talcon.log"
        self._open_path(log_file if log_file.exists() else LOGS_DIR)

    # =====================================================================
    # AI assistant
    # =====================================================================
    def _on_assistant_state(self, payload: dict) -> None:
        kind = str((payload or {}).get("kind") or "")
        text = str((payload or {}).get("text") or "")
        if kind == "progress" and text:
            self.ai_status.set_text(f"Hermes: {text}")
        elif kind in ("accepted", "working"):
            self.ai_status.set_text("Hermes is working on that request…")
        elif kind == "approval":
            self.ai_status.set_text("Waiting for your spoken yes/no…")
        elif kind == "cache_hit":
            self.ai_status.set_text(
                "AI is on — answered instantly from this session's cache."
            )
        elif kind == "error" and text:
            self.ai_status.set_text(text)
        elif kind in ("answered", "cancelled"):
            self.ai_status.set_text(
                "AI is on — ready for natural-language questions."
            )
        if kind in ("answered", "cache_hit"):
            self._show_cache_hit_rate()

    def _show_cache_hit_rate(self) -> None:
        """Append a plain hit-rate sentence while answers are flowing."""
        stats = getattr(self.runtime, "ai_cache_stats", lambda: {})()
        lookups = int(stats.get("lookups") or 0)
        if not lookups:
            return
        hits = int(stats.get("hits") or 0)
        self.ai_status.set_text(
            f"AI is on — {hits} of {lookups} questions answered from cache "
            f"({stats.get('hit_rate', 0):.0%}), {stats.get('entries', 0)} "
            "stored in memory."
        )

    def _check_ai_backend(self) -> None:
        """Probe Hermes without blocking the UI thread.

        A stopped-but-installed gateway is started automatically, so this
        button repairs the common "nothing is listening" case instead of only
        reporting it.
        """
        self.ai_check_button.setEnabled(False)
        self.ai_status.set_text("Checking the local Hermes server…")

        def work():
            return self.runtime.ai_backend_report(start_if_down=True)

        run_async(self, work, self._ai_report_done)

    def _start_ai_backend(self) -> None:
        self.ai_start_button.setEnabled(False)
        self.ai_status.set_text("Starting the local Hermes gateway…")

        def work():
            return self.runtime.start_ai_backend()

        def done(result) -> None:
            self.ai_start_button.setEnabled(True)
            if isinstance(result, Exception):
                self.ai_status.set_text(f"Could not start Hermes: {result}")
                return
            ok, detail = result
            if ok:
                self.ai_status.set_text("Hermes is starting — checking it…")
                QTimer.singleShot(600, self._check_ai_backend)
            else:
                self.ai_status.set_text(f"Hermes did not start: {detail}")

        run_async(self, work, done)

    def _save_ai_backend(self) -> None:
        self._save({
            "hermes_host": self.ai_host.text().strip() or "127.0.0.1",
            "hermes_port": int(self.ai_port.value()),
            "hermes_model": self.ai_model.text().strip() or "hermes-agent",
        })
        self.ai_status.set_text("AI address saved — testing it now…")
        self._check_ai_backend()

    def _ai_report_done(self, report) -> None:
        self.ai_check_button.setEnabled(True)
        try:
            if isinstance(report, Exception):
                self.ai_status.set_text(f"Could not check the AI backend: {report}")
                return
            report = dict(report or {})
            listening = bool(report.get("listening"))
            reachable = bool(report.get("reachable"))
            if not bool(self.runtime.settings.get("ai_enabled", False)):
                self.ai_status.set_text("AI is off — turn it on to test the connection.")
            elif reachable:
                version = str(report.get("version") or "").strip()
                models = ", ".join(report.get("models") or []) or "hermes-agent"
                suffix = f" (v{version})" if version else ""
                self.ai_status.set_text(
                    f"Connected to Hermes on {report.get('host')}:"
                    f"{report.get('port')}{suffix} — model {models}."
                )
            elif listening:
                self.ai_status.set_text(
                    "Something is listening, but Hermes rejected the request: "
                    f"{report.get('detail') or 'unknown reason'}"
                )
            elif bool(report.get("installed")):
                self.ai_status.set_text(
                    "Hermes is installed but its gateway is not running."
                )
            else:
                self.ai_status.set_text(
                    "No Hermes installation was found on this PC."
                )
            home = str(report.get("home") or "")
            self.ai_detail.setText(
                f"Install: {home or 'not found'} · Launcher: "
                f"{report.get('launcher') or 'none'}"
            )
        except RuntimeError:
            # The window (or its labels) may already be gone.
            pass

    # =====================================================================
    # window behaviour
    # =====================================================================
    def _center_on_screen(self) -> None:
        screen = QGuiApplication.primaryScreen()
        if screen is None:
            return
        area = screen.availableGeometry()
        self.move(area.center() - self.rect().center())

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        grip = getattr(self, "size_grip", None)
        if grip is not None:
            grip.move(self.width() - grip.width() - 4, self.height() - grip.height() - 4)
            grip.raise_()

    def eventFilter(self, obj, event) -> bool:
        if obj in self._drag_widgets:
            if event.type() == QEvent.Type.MouseButtonPress and event.button() == Qt.MouseButton.LeftButton:
                self._drag_offset = event.globalPosition().toPoint() - self.frameGeometry().topLeft()
            elif event.type() == QEvent.Type.MouseMove and self._drag_offset is not None:
                if event.buttons() & Qt.MouseButton.LeftButton:
                    self.move(event.globalPosition().toPoint() - self._drag_offset)
            elif event.type() == QEvent.Type.MouseButtonRelease:
                self._drag_offset = None
        return super().eventFilter(obj, event)

    def showEvent(self, event) -> None:
        super().showEvent(event)
        self.sync_controls()

    def closeEvent(self, event) -> None:
        # The window's Close button and Alt+F4 both terminate TALCON completely.
        self.runtime.exit_completely()
        event.accept()
