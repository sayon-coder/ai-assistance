"""NOVA control center: eight sections, minimal chrome, three distinct window controls."""
from __future__ import annotations

import datetime as _dt
import logging
import os
import subprocess
import sys
import threading

from PySide6.QtCore import QEvent, QObject, Qt, Signal
from PySide6.QtGui import QGuiApplication, QIcon, QPixmap
from PySide6.QtWidgets import (
    QButtonGroup, QComboBox, QFrame, QGridLayout, QHBoxLayout, QLabel, QLineEdit,
    QMessageBox, QProgressBar, QPushButton, QScrollArea, QSizeGrip, QSlider,
    QStackedWidget, QVBoxLayout, QWidget,
)

from .. import startup
from ..config import CONFIG_DIR, LOGS_DIR, MODELS_DIR, ROOT, VERSION
from ..face_render import render_face_image
from ..model_setup import MODEL_LICENSE, MODEL_URL, model_is_ready, resolve_model
from ..runtime import NovaRuntime
from ..voice import measure_input
from . import theme
from .components import Card, Divider, FacePreview, Heading, StatusText, Switch, footnote, muted
from .custom_editor import CustomEditor
from .mode_editor import ModeEditor

log = logging.getLogger("nova.ui")

PAGES = [
    ("dashboard", "Dashboard"),
    ("study", "Study Mode"),
    ("entertainment", "Entertainment Mode"),
    ("stock", "Stock Mode"),
    ("voice", "Voice Settings"),
    ("commands", "Custom Commands & Automations"),
    ("appearance", "Appearance"),
    ("monitoring", "Application Monitoring"),
    ("startup", "Startup Settings"),
    ("about", "About NOVA"),
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
    def __init__(self, runtime: NovaRuntime, face, parent=None):
        super().__init__(parent)
        self.runtime = runtime
        self.face = face
        self._status: dict = {}
        self._drag_offset = None
        self._drag_widgets: list[QWidget] = []
        self._mic_result = None
        self._download_active = False

        self.setWindowTitle("NOVA Control Center")
        self.setWindowIcon(QIcon(str(ROOT / "assets" / "nova_icon.png")))
        self.setObjectName("Root")
        self.setWindowFlags(Qt.WindowType.Window | Qt.WindowType.FramelessWindowHint)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setStyleSheet(theme.STYLESHEET)
        self.setMinimumSize(980, 660)
        self.resize(1060, 740)
        self._build()
        self._connect()
        self.load_from_runtime()
        self.update_statuses(self.runtime.status_snapshot())
        self._center_on_screen()

    # =====================================================================
    # construction
    # =====================================================================
    def _build(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(10, 10, 10, 10)
        shell = QFrame()
        shell.setObjectName("Shell")
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
            "study": self._page_mode(
                "study", "Study Mode",
                "Websites and applications opened by “NOVA, activate my study mode”.",
            ),
            "entertainment": self._page_mode(
                "entertainment", "Entertainment Mode",
                "Everything opened by “NOVA, get ready for my entertainment mode”.",
            ),
            "stock": self._page_mode(
                "stock", "Stock Mode",
                "Everything opened by “NOVA, open news” or “NOVA, stock mode”.",
            ),
            "voice": self._page_voice(),
            "commands": self._page_commands(),
            "appearance": self._page_appearance(),
            "monitoring": self._page_monitoring(),
            "startup": self._page_startup(),
            "about": self._page_about(),
        }
        for key, _label in PAGES:
            self.stack.addWidget(self.pages[key])
        self.show_page("dashboard")

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
        name = QLabel("NOVA")
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
        self.min_button.setToolTip("Minimise this window to the taskbar. NOVA keeps running and the face stays visible.")
        self.hide_button = QPushButton("↓  Hide in tray")
        self.hide_button.setObjectName("WindowButton")
        self.hide_button.setToolTip("Hide this window to the system tray. NOVA and the face keep running.")
        self.close_button = QPushButton("✕  Close")
        self.close_button.setObjectName("CloseButton")
        self.close_button.setToolTip("Close NOVA completely and stop all background services.")
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
        for index, (key, label) in enumerate(PAGES):
            button = QPushButton(label)
            button.setObjectName("Nav")
            button.setCheckable(True)
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            button.clicked.connect(lambda _checked=False, k=key: self.show_page(k))
            self.nav_group.addButton(button, index)
            self.nav_buttons[key] = button
            layout.addWidget(button)
        layout.addStretch(1)
        layout.addWidget(Divider())
        layout.addSpacing(10)
        note = QLabel("100% local\nNo accounts · no cloud")
        note.setObjectName("Footnote")
        layout.addWidget(note)
        return sidebar

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
        layout.addWidget(Heading("Dashboard", f"{greeting}, sir. NOVA is running quietly in the background."))

        grid = QGridLayout()
        grid.setSpacing(12)
        self.stat_widgets: dict[str, StatusText] = {}
        cards = (
            ("NOVA", "running"),
            ("Listening", "wake"),
            ("Microphone", "microphone"),
            ("Spoken output", "speech"),
            ("Active mode", "active_mode"),
            ("OpenCode", "opencode"),
            ("Codex Desktop", "codex"),
        )
        for position, (title, key) in enumerate(cards):
            card = Card(title)
            status = StatusText("…")
            card.add(status)
            self.stat_widgets[key] = status
            grid.addWidget(card, position // 3, position % 3)
        layout.addLayout(grid)

        actions = Card("Quick actions", "Every button below uses the real runtime service.")
        first = QHBoxLayout()
        first.setSpacing(8)
        study = QPushButton("Study Mode")
        study.setObjectName("Primary")
        study.clicked.connect(lambda: self.runtime.activate_mode("study"))
        entertain = QPushButton("Entertainment Mode")
        entertain.clicked.connect(lambda: self.runtime.activate_mode("entertainment"))
        stock = QPushButton("Stock Mode")
        stock.clicked.connect(lambda: self.runtime.activate_mode("stock"))
        listen = QPushButton("Listen now")
        listen.setToolTip("Capture one spoken command without saying the wake word.")
        listen.clicked.connect(self.runtime.listen_now)
        first.addWidget(study)
        first.addWidget(entertain)
        first.addWidget(stock)
        first.addWidget(listen)
        first.addStretch(1)
        actions.add_layout(first)

        second = QHBoxLayout()
        second.setSpacing(8)
        self.mute_button = QPushButton("Mute voice")
        self.mute_button.clicked.connect(self._toggle_mute)
        self.test_mic_button = QPushButton("Test microphone")
        self.test_mic_button.clicked.connect(self._check_microphone)
        voice_settings = QPushButton("Open Voice Settings")
        voice_settings.clicked.connect(lambda: self.show_page("voice"))
        second.addWidget(self.mute_button)
        second.addWidget(self.test_mic_button)
        second.addWidget(voice_settings)
        self.face_quick = QPushButton("Hide face")
        self.face_quick.clicked.connect(self._toggle_face)
        second.addWidget(self.face_quick)
        second.addStretch(1)
        actions.add_layout(second)
        layout.addWidget(actions)

        interaction = Card(
            "Last voice interaction",
            "Only real transcripts and executed actions are shown here.",
        )
        self.last_heard_label = QLabel("Nothing recognised yet.")
        self.last_command_label = QLabel("No command executed yet.")
        self.last_result_label = QLabel("No result yet.")
        for label in (self.last_heard_label, self.last_command_label, self.last_result_label):
            label.setWordWrap(True)
            label.setStyleSheet("font-size: 10pt; color: #E8E8E8;")
        interaction.add(QLabel("Recognised sentence"))
        interaction.add(self.last_heard_label)
        interaction.add(QLabel("Executed command"))
        interaction.add(self.last_command_label)
        interaction.add(QLabel("Result"))
        interaction.add(self.last_result_label)
        layout.addWidget(interaction)

        recent = Card("Recent activity")
        self.activity_label = QLabel("No activity yet.")
        self.activity_label.setWordWrap(True)
        self.activity_label.setStyleSheet("font-size: 9.5pt; color: #D8D8D8;")
        recent.add(self.activity_label)
        self.last_event = QLabel("NOVA is ready.")
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
    # modes
    # =====================================================================
    def _page_mode(self, key: str, title: str, subtitle: str) -> QWidget:
        page, layout = self._page()
        self.mode_editors = getattr(self, "mode_editors", {})
        editor = ModeEditor(self.runtime, key, title, subtitle)
        self.mode_editors[key] = editor
        layout.addWidget(editor)
        return self._scroll(page)

    # =====================================================================
    # voice
    # =====================================================================
    def _page_voice(self) -> QWidget:
        page, layout = self._page()
        layout.addWidget(Heading(
            "Voice Settings",
            "Continuous listening, microphone and Windows speech. All recognition and speech happen on this PC.",
        ))

        output = Card("Spoken output", "Windows SAPI voices. No cloud text-to-speech is used.")
        output_row = QHBoxLayout()
        output_row.addWidget(QLabel("Enable spoken announcements"))
        output_row.addStretch(1)
        self.speech_switch = Switch()
        output_row.addWidget(self.speech_switch)
        output.add_layout(output_row)

        voice_row = QHBoxLayout()
        voice_row.addWidget(QLabel("Voice"))
        self.voice_combo = QComboBox()
        self.voice_combo.setMinimumWidth(240)
        voice_row.addWidget(self.voice_combo, 1)
        self.test_voice_button = QPushButton("Test voice")
        self.test_voice_button.clicked.connect(self.runtime.test_voice)
        refresh_voices = QPushButton("Refresh")
        refresh_voices.clicked.connect(self.runtime.refresh_voices)
        voice_row.addWidget(self.test_voice_button)
        voice_row.addWidget(refresh_voices)
        output.add_layout(voice_row)

        rate_row = QHBoxLayout()
        rate_row.addWidget(QLabel("Speaking rate"))
        self.rate_slider = QSlider(Qt.Orientation.Horizontal)
        self.rate_slider.setRange(-10, 10)
        self.rate_slider.setMinimumWidth(220)
        self.rate_slider.valueChanged.connect(self._on_rate_changed)
        self.rate_slider.sliderReleased.connect(self._save_rate)
        self.rate_label = QLabel("Normal")
        self.rate_label.setObjectName("Muted")
        self.rate_label.setFixedWidth(70)
        rate_row.addWidget(self.rate_slider, 1)
        rate_row.addWidget(self.rate_label)
        output.add_layout(rate_row)
        output.add(footnote(
            "Muting stops speech only. Wake-word listening and application monitoring keep running."
        ))
        layout.addWidget(output)

        wake = Card(
            "Listening and microphone",
            "NOVA can listen continuously, or wait for the word “NOVA”. Commands are matched exactly.",
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
        self.mic_level = QLabel("")
        self.mic_level.setObjectName("Muted")
        mic_actions.addWidget(listen_now)
        mic_actions.addWidget(check_mic)
        mic_actions.addWidget(self.mic_level, 1)
        wake.add_layout(mic_actions)

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
        diag_actions = QHBoxLayout()
        diag_listen = QPushButton("Listen now")
        diag_listen.clicked.connect(self.runtime.listen_now)
        diag_test_voice = QPushButton("Test voice")
        diag_test_voice.clicked.connect(self.runtime.test_voice)
        open_log = QPushButton("Open NOVA log")
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

        privacy = Card("Privacy")
        privacy.add(muted(
            "Microphone audio is processed locally by the Vosk model and is never recorded, "
            "stored or uploaded. NOVA has no account system and makes no paid API calls. "
            "The only network request it ever makes is the one-time speech-model download you start."
        ))
        layout.addWidget(privacy)
        layout.addStretch(1)
        return self._scroll(page)

    # =====================================================================
    # custom commands and automations
    # =====================================================================
    def _page_commands(self) -> QWidget:
        page, layout = self._page()
        self.custom_editor = CustomEditor(self.runtime)
        layout.addWidget(self.custom_editor)
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
        self.size_slider.sliderReleased.connect(self._save_size)
        size_row.addWidget(self.size_slider, 1)
        size_row.addWidget(self.size_value)
        controls.addLayout(size_row)

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

        expressions = Card("Expressions", "Restrained, calm movement — no flashing, bouncing or decorative symbols.")
        expressions.add(muted(
            "Idle: natural blinking and occasional gaze changes.  Listening: attentive eyes.  "
            "Working: focused, slightly lowered gaze.  Completed: a brief satisfied expression.  "
            "Error: a brief concerned expression.  Speaking: a subtle conversational rhythm."
        ))
        layout.addWidget(expressions)
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
            "NOVA never starts its own Codex app-server and never treats a closed app as a finished task. "
            "It announces success or failure only from recorded task_started / task_complete / turn_aborted events. "
            "If those logs cannot be read, the status above says so and nothing is announced."
        ))
        layout.addWidget(codex)

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
    def _page_startup(self) -> QWidget:
        page, layout = self._page()
        layout.addWidget(Heading("Startup Settings", "NOVA can start quietly when you sign in to Windows."))

        card = Card("Start with Windows")
        start_row = QHBoxLayout()
        start_row.addWidget(QLabel("Start NOVA automatically after I sign in"))
        start_row.addStretch(1)
        self.startup_switch = Switch()
        start_row.addWidget(self.startup_switch)
        card.add_layout(start_row)
        self.startup_status = StatusText("Checking…")
        card.add(self.startup_status)
        card.add(muted(
            "Uses the normal per-user Startup folder. No administrator rights and no registry changes. "
            "NOVA starts with the face and tray only — the control center stays closed."
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
            "Closing NOVA (×) stops the microphone listener, speech, application monitoring and the face, "
            "but leaves this startup setting exactly as it is, so NOVA returns at the next sign-in."
        ))
        uninstall = QPushButton("Uninstall NOVA…")
        uninstall.setObjectName("Danger")
        uninstall.clicked.connect(self._uninstall)
        closing.add(uninstall)
        layout.addWidget(closing)
        layout.addStretch(1)
        return self._scroll(page)

    # =====================================================================
    # about
    # =====================================================================
    def _page_about(self) -> QWidget:
        page, layout = self._page()
        layout.addWidget(Heading("About NOVA", f"Version {VERSION} · a private, local-first Windows assistant."))

        about = Card("What NOVA is")
        about.add(muted(
            "A lightweight desktop companion with an animated face, nine-section control center, "
            "editable study, entertainment and stock modes, fully offline wake-word and command recognition, "
            "Windows SAPI speech, and honest local monitoring for OpenCode and Codex Desktop."
        ))
        layout.addWidget(about)

        privacy = Card("Cost and privacy")
        privacy.add(muted(
            "Zero cost: no paid APIs, subscriptions, keys or accounts. No cloud speech recognition "
            "or text-to-speech. Microphone audio and local files never leave this computer. "
            "No telemetry, no analytics, no background updater."
        ))
        layout.addWidget(privacy)

        licenses = Card("Components and licences")
        licenses.add(footnote(
            "PySide6 / Qt — LGPL-3.0 · Vosk — Apache-2.0 · vosk-model-small-en-us-0.15 — Apache-2.0 · "
            "zstandard — BSD-3-Clause · sounddevice — MIT · requests — Apache-2.0 · "
            "Windows SAPI — built into Windows. Speech model source: " + MODEL_URL
        ))
        layout.addWidget(licenses)

        diagnostics = Card("Diagnostics")
        info = QLabel(
            f"Version: {VERSION}\n"
            f"Python: {sys.version.split()[0]}\n"
            f"Configuration: {CONFIG_DIR}\n"
            f"Logs: {LOGS_DIR}\n"
            f"Models: {MODELS_DIR}"
        )
        info.setObjectName("Muted")
        info.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        diagnostics.add(info)
        buttons = QHBoxLayout()
        logs = QPushButton("Open logs")
        logs.clicked.connect(lambda: self._open_path(LOGS_DIR))
        config = QPushButton("Open data folder")
        config.clicked.connect(lambda: self._open_path(ROOT))
        guide = QPushButton("Open user guide")
        guide.clicked.connect(lambda: self._open_path(ROOT / "README.md"))
        buttons.addWidget(logs)
        buttons.addWidget(config)
        buttons.addWidget(guide)
        buttons.addStretch(1)
        diagnostics.add_layout(buttons)
        layout.addWidget(diagnostics)
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
        self.wake_switch.toggled.connect(self.runtime.set_wake_enabled)
        self.always_switch.toggled.connect(
            lambda checked: self._save({"always_listen": bool(checked)})
        )
        self.opencode_switch.toggled.connect(
            lambda checked: self._save({"opencode_enabled": checked})
        )
        self.codex_switch.toggled.connect(lambda checked: self._save({"codex_enabled": checked}))
        self.interrupt_switch.toggled.connect(
            lambda checked: self._save({"announce_interruptions": checked})
        )
        self.face_switch.toggled.connect(lambda checked: self._save({"show_face": checked}))
        self.startup_switch.toggled.connect(self._on_startup_toggled)
        self.voice_combo.currentTextChanged.connect(self._on_voice_chosen)
        self.mic_combo.currentIndexChanged.connect(self._on_mic_chosen)

        bridge = self.runtime.bridge
        bridge.status_changed.connect(self.update_statuses)
        bridge.voices_available.connect(self._populate_voices)
        bridge.model_progress.connect(self._on_model_progress)
        bridge.settings_changed.connect(self._on_settings_changed)

    # =====================================================================
    # data loading
    # =====================================================================
    def load_from_runtime(self) -> None:
        s = self.runtime.settings
        self.speech_switch.set_checked_silently(bool(s.get("voice_enabled", True)))
        self.wake_switch.set_checked_silently(bool(s.get("wake_enabled", True)))
        self.always_switch.set_checked_silently(bool(s.get("always_listen", True)))
        self.opencode_switch.set_checked_silently(bool(s.get("opencode_enabled", True)))
        self.codex_switch.set_checked_silently(bool(s.get("codex_enabled", True)))
        self.interrupt_switch.set_checked_silently(bool(s.get("announce_interruptions", False)))
        self.face_switch.set_checked_silently(bool(s.get("show_face", True)))
        self.startup_switch.set_checked_silently(startup.is_enabled())
        self.startup_status.set_text(
            "Enabled · NOVA starts quietly at sign-in" if startup.is_enabled()
            else "Disabled · NOVA only starts when you launch it"
        )
        self.face_quick.setText("Hide face" if self.face.isVisible() else "Show face")

        self.rate_slider.blockSignals(True)
        self.rate_slider.setValue(int(s.get("speech_rate", 0)))
        self.rate_slider.blockSignals(False)
        self._on_rate_changed(int(s.get("speech_rate", 0)))

        self.size_slider.blockSignals(True)
        self.size_slider.setValue(int(s.get("face_size", 66)))
        self.size_slider.blockSignals(False)
        self.size_value.setText(f"{int(s.get('face_size', 66))} px")

        self.opencode_url.setText(s.get("opencode_base_url", "http://127.0.0.1:4096"))
        self._populate_voices(self.runtime.voices)
        self._populate_microphones()
        self._refresh_model_status()
        self._update_mute_labels()

        for editor in getattr(self, "mode_editors", {}).values():
            editor.refresh()
        if hasattr(self, "custom_editor"):
            self.custom_editor.refresh()

        path = startup.startup_folder()
        self.startup_status.setToolTip(str(path))
        from ..monitors.codex import sessions_root

        root = sessions_root()
        self.codex_path.setText(f"Session logs: {root}" + ("" if root.is_dir() else "  (not found)"))
        self.codex_path.setToolTip(str(root))

    def show_page(self, key: str) -> None:
        if key not in self.pages:
            key = "dashboard"
        self.stack.setCurrentWidget(self.pages[key])
        for name, button in self.nav_buttons.items():
            button.setChecked(name == key)

    def sync_controls(self) -> None:
        s = self.runtime.settings
        self.speech_switch.set_checked_silently(bool(s.get("voice_enabled", True)))
        self.wake_switch.set_checked_silently(bool(s.get("wake_enabled", True)))
        self.always_switch.set_checked_silently(bool(s.get("always_listen", True)))
        self.face_switch.set_checked_silently(bool(s.get("show_face", True)))
        self.face_quick.setText("Hide face" if self.face.isVisible() else "Show face")
        self.startup_switch.set_checked_silently(startup.is_enabled())
        self._update_mute_labels()

    def _on_settings_changed(self, settings: dict) -> None:
        self.sync_controls()
        for editor in getattr(self, "mode_editors", {}).values():
            editor.refresh()
        if hasattr(self, "custom_editor"):
            self.custom_editor.refresh()
        self.startup_status.set_text(
            "Enabled · NOVA starts quietly at sign-in" if startup.is_enabled()
            else "Disabled · NOVA only starts when you launch it"
        )
        self._refresh_model_status()

    def _update_mute_labels(self) -> None:
        if self.runtime.muted:
            self.mute_button.setText("Unmute voice")
            self.header_status.setText("Voice muted · monitoring active")
        else:
            self.mute_button.setText("Mute voice")

    # =====================================================================
    # status
    # =====================================================================
    def update_statuses(self, status: dict) -> None:
        self._status = dict(status or {})
        for key, widget in getattr(self, "stat_widgets", {}).items():
            widget.set_text(self._status.get(key, "—"))
        self.voice_status.set_text(self._status.get("voice", "—"))
        self.opencode_status.set_text(self._status.get("opencode", "—"))
        self.codex_status.set_text(self._status.get("codex", "—"))
        self.diag_wake.set_text(self._status.get("wake", "—"))
        self.last_event.setText(self._status.get("last_event", "NOVA is ready."))

        heard = str(self._status.get("last_heard", "") or "").strip()
        matched = str(self._status.get("last_match", "") or "").strip()
        command = str(self._status.get("last_command", "") or "").strip()
        result = str(self._status.get("last_result", "") or "").strip()
        failure = str(self._status.get("last_failure", "") or "").strip()
        error = str(self._status.get("mic_error", "") or "").strip()
        self.last_heard_label.setText(heard or "Nothing recognised yet.")
        self.last_command_label.setText(command or "No command executed yet.")
        self.last_result_label.setText(result or "No result yet.")
        self.diag_heard.setText(heard or "Nothing recognised yet.")
        self.diag_match.setText(matched or "No command matched yet.")
        self.diag_command.setText(command or "No command executed yet.")
        self.diag_result.setText(result or "No result yet.")
        self.diag_failure.setText(failure or "No recognition failures.")
        self.diag_error.setText(error or "No microphone or recognition errors.")
        activity = self._status.get("recent_activity") or []
        self.activity_label.setText("\n".join(str(item) for item in activity) or "No activity yet.")

        header = self._status.get("wake") or self._status.get("voice", "")
        if self.runtime.muted:
            header = "Voice muted · monitoring active"
        self.header_status.setText(str(header)[:70])
        self.header_status.setStyleSheet(
            f"font-size: 8.8pt; color: {theme.status_colour(header)};"
        )
        self.face_quick.setText("Hide face" if self.face.isVisible() else "Show face")
        self._update_mute_labels()

    # =====================================================================
    # actions
    # =====================================================================
    def _save(self, values: dict) -> None:
        try:
            self.runtime.save_settings(values)
        except Exception as exc:
            QMessageBox.warning(self, "NOVA", f"Could not save settings: {exc}")

    def _toggle_mute(self) -> None:
        self.runtime.set_muted(not self.runtime.muted)

    def _toggle_face(self) -> None:
        target = not self.face.isVisible()
        self.runtime.save_settings({"show_face": target})
        self.face.setVisible(target)
        self.face_quick.setText("Hide face" if target else "Show face")
        self.face_switch.set_checked_silently(target)

    def _reset_face_position(self) -> None:
        self.face.reset_position()

    def _save_size(self) -> None:
        self._save({"face_size": self.size_slider.value()})

    def _on_rate_changed(self, value: int) -> None:
        self.rate_label.setText("Slow" if value < -2 else ("Fast" if value > 2 else "Normal"))
        try:
            self.runtime.speech.set_rate(int(value))
        except Exception:
            pass

    def _save_rate(self) -> None:
        self._save({"speech_rate": self.rate_slider.value()})

    def _on_voice_chosen(self, name: str) -> None:
        if not name or name not in self.runtime.voices:
            return
        if name == self.runtime.settings.get("speech_voice"):
            return
        try:
            self.runtime.speech.set_voice(name)
        except Exception:
            pass
        self._save({"speech_voice": name})

    def _populate_voices(self, voices: list) -> None:
        current = self.runtime.settings.get("speech_voice", "")
        self.voice_combo.blockSignals(True)
        self.voice_combo.clear()
        for name in voices or []:
            self.voice_combo.addItem(str(name))
        if current:
            index = self.voice_combo.findText(current)
            if index >= 0:
                self.voice_combo.setCurrentIndex(index)
        self.voice_combo.blockSignals(False)
        if not voices:
            self.voice_combo.addItem("Windows voices unavailable")
            self.voice_combo.setEnabled(False)
        else:
            self.voice_combo.setEnabled(True)

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

    def _refresh_model_status(self) -> None:
        path = resolve_model(self.runtime.settings.get("vosk_model_path"))
        if model_is_ready(path):
            self.model_status.set_text(f"Installed · {path.name}")
            self.download_button.setEnabled(False)
            self.download_button.setText("Speech model installed")
            self.model_note.setText(f"Licence: {MODEL_LICENSE}. Runs entirely offline.")
        else:
            self.model_status.set_text("Not installed · download the free model to use voice")
            self.download_button.setEnabled(not self._download_active)
            self.download_button.setText("Download offline speech model (~40 MB)")
            self.model_note.setText(f"Apache-2.0 model from alphacephei.com. Saved to {MODELS_DIR}.")

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
            QMessageBox.warning(self, "NOVA", f"Could not change startup: {exc}")
        enabled = startup.is_enabled()
        if enabled != bool(checked):
            self.startup_switch.set_checked_silently(enabled)
        self.startup_status.set_text(
            "Enabled · NOVA starts quietly at sign-in" if enabled
            else "Disabled · NOVA only starts when you launch it"
        )

    def _uninstall(self) -> None:
        answer = QMessageBox.question(
            self,
            "Uninstall NOVA",
            "This opens the NOVA uninstaller and closes the assistant.\n\n"
            "You will be asked whether to delete the downloaded speech model and settings. "
            "Your Windows startup entry is always removed.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Cancel,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        script = ROOT / "Uninstall NOVA.bat"
        if not script.exists():
            QMessageBox.warning(self, "NOVA", "Uninstall NOVA.bat was not found next to the application.")
            return
        try:
            subprocess.Popen(["cmd.exe", "/c", "start", "", str(script)], cwd=str(ROOT))
        except Exception as exc:
            QMessageBox.critical(self, "NOVA", f"Could not start the uninstaller: {exc}")
            return
        self.runtime.exit_completely()

    def _open_path(self, path) -> None:
        try:
            os.startfile(str(path))  # noqa: S606 - opening a local folder/readme
        except Exception as exc:
            QMessageBox.warning(self, "NOVA", f"Could not open {path}: {exc}")

    def _open_log(self) -> None:
        log_file = LOGS_DIR / "nova.log"
        self._open_path(log_file if log_file.exists() else LOGS_DIR)

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
        # The window's Close button and Alt+F4 both terminate NOVA completely.
        self.runtime.exit_completely()
        event.accept()
