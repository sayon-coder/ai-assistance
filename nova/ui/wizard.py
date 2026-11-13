"""First-launch setup: microphone, voice, model availability and startup choice."""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QComboBox, QDialog, QFrame, QHBoxLayout, QLabel, QProgressBar, QPushButton,
    QStackedWidget, QVBoxLayout, QWidget,
)

from .. import startup
from ..model_setup import MODEL_LICENSE, model_is_ready, resolve_model
from ..voice import measure_input
from . import theme
from .components import Card, StatusText, Switch, footnote, muted
from .main_window import run_async


class SetupWizard(QDialog):
    def __init__(self, runtime, parent=None):
        super().__init__(parent)
        self.runtime = runtime
        self.setWindowTitle("Welcome to NOVA")
        self.setWindowFlags(Qt.WindowType.Dialog | Qt.WindowType.FramelessWindowHint)
        self.setObjectName("Root")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setStyleSheet(theme.STYLESHEET)
        self.setModal(True)
        self.setFixedSize(640, 520)
        self._build()
        runtime.bridge.voices_available.connect(self._populate_voices)
        runtime.bridge.model_progress.connect(self._on_model_progress)
        self._populate_microphones()
        self._populate_voices(runtime.voices)
        self._refresh_model()
        self._update_nav()

    def _build(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        shell = QFrame()
        shell.setObjectName("Shell")
        outer.addWidget(shell)
        layout = QVBoxLayout(shell)
        layout.setContentsMargins(26, 22, 26, 20)
        layout.setSpacing(12)

        brand = QLabel("NOVA")
        brand.setObjectName("Brand")
        subtitle = QLabel("FIRST-TIME SETUP")
        subtitle.setObjectName("BrandSub")
        layout.addWidget(brand)
        layout.addWidget(subtitle)

        self.stack = QStackedWidget()
        layout.addWidget(self.stack, 1)
        self.stack.addWidget(self._page_welcome())
        self.stack.addWidget(self._page_microphone())
        self.stack.addWidget(self._page_voice())
        self.stack.addWidget(self._page_ready())

        footer = QHBoxLayout()
        self.skip_button = QPushButton("Skip for now")
        self.skip_button.clicked.connect(self._skip)
        self.back_button = QPushButton("Back")
        self.back_button.clicked.connect(self._back)
        self.next_button = QPushButton("Continue")
        self.next_button.setObjectName("Primary")
        self.next_button.clicked.connect(self._next)
        footer.addWidget(self.skip_button)
        footer.addStretch(1)
        footer.addWidget(self.back_button)
        footer.addWidget(self.next_button)
        layout.addLayout(footer)

    # -- pages -------------------------------------------------------------
    def _page_welcome(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 8, 0, 0)
        layout.setSpacing(14)
        title = QLabel("Your private desktop assistant")
        title.setObjectName("PageTitle")
        layout.addWidget(title)
        layout.addWidget(muted(
            "NOVA stays on this computer. Speech recognition and speech output run locally, "
            "and nothing is uploaded. There is no account, subscription or paid API."
        ))
        points = QLabel(
            "• A small animated face sits below your top bar; click it to open this control center.\n"
            "• Say “NOVA”, then a command such as “activate my study mode”.\n"
            "• NOVA can announce when your coding tasks finish or fail.\n"
            "• Everything is optional and can be changed later."
        )
        points.setWordWrap(True)
        layout.addWidget(points)
        layout.addWidget(footnote(
            "This wizard only checks your microphone, speech voice and offline model. "
            "It does not install anything else."
        ))
        layout.addStretch(1)
        return page

    def _page_microphone(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 8, 0, 0)
        layout.setSpacing(12)
        title = QLabel("Microphone")
        title.setObjectName("PageTitle")
        layout.addWidget(title)
        layout.addWidget(muted("Choose the microphone NOVA listens to. You can change this later."))
        self.mic_combo = QComboBox()
        layout.addWidget(self.mic_combo)
        check = QPushButton("Check microphone")
        check.clicked.connect(self._check_mic)
        layout.addWidget(check)
        self.mic_result = StatusText("Not tested yet.")
        layout.addWidget(self.mic_result)
        layout.addWidget(footnote(
            "A short sample is measured and immediately discarded — nothing is saved or sent."
        ))
        layout.addStretch(1)
        return page

    def _page_voice(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 8, 0, 0)
        layout.setSpacing(12)
        title = QLabel("Windows speech voice")
        title.setObjectName("PageTitle")
        layout.addWidget(title)
        layout.addWidget(muted("NOVA speaks through the voices already installed in Windows."))
        self.voice_combo = QComboBox()
        layout.addWidget(self.voice_combo)
        test = QPushButton("Play a test sentence")
        test.clicked.connect(self.runtime.test_voice)
        layout.addWidget(test)

        self.model_status = StatusText("Checking offline speech model…")
        layout.addWidget(self.model_status)
        self.download_button = QPushButton("Download the free offline speech model (~40 MB)")
        self.download_button.clicked.connect(self._download)
        layout.addWidget(self.download_button)
        self.progress = QProgressBar()
        self.progress.setRange(0, 100)
        self.progress.setVisible(False)
        layout.addWidget(self.progress)
        layout.addWidget(footnote(f"Apache-2.0 model. {MODEL_LICENSE}. Needed only for voice commands."))
        layout.addStretch(1)
        return page

    def _page_ready(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 8, 0, 0)
        layout.setSpacing(12)
        title = QLabel("Ready")
        title.setObjectName("PageTitle")
        layout.addWidget(title)
        layout.addWidget(muted("NOVA can start on its own so the face is waiting after you sign in."))
        startup_row = QHBoxLayout()
        startup_row.addWidget(QLabel("Start NOVA automatically with Windows"))
        startup_row.addStretch(1)
        self.startup_switch = Switch()
        self.startup_switch.set_checked_silently(startup.is_enabled())
        startup_row.addWidget(self.startup_switch)
        layout.addLayout(startup_row)
        layout.addWidget(footnote(
            "Uses the per-user Startup folder and can be turned off at any time in Startup Settings."
        ))
        layout.addStretch(1)
        return page

    # -- data --------------------------------------------------------------
    def _populate_microphones(self) -> None:
        self.mic_combo.clear()
        for index, name in self.runtime.microphones():
            self.mic_combo.addItem(str(name), index)
        selected = self.runtime.settings.get("microphone_device")
        if selected is not None:
            position = self.mic_combo.findData(selected)
            if position >= 0:
                self.mic_combo.setCurrentIndex(position)

    def _populate_voices(self, voices: list) -> None:
        current = self.runtime.settings.get("speech_voice", "")
        self.voice_combo.blockSignals(True)
        self.voice_combo.clear()
        for name in voices or []:
            self.voice_combo.addItem(str(name))
        if current:
            position = self.voice_combo.findText(current)
            if position >= 0:
                self.voice_combo.setCurrentIndex(position)
        self.voice_combo.blockSignals(False)
        if not voices:
            self.voice_combo.addItem("Windows voices unavailable")

    def _refresh_model(self) -> None:
        ready = model_is_ready(resolve_model(self.runtime.settings.get("vosk_model_path")))
        self.model_status.set_text(
            "Offline speech model installed" if ready
            else "Offline speech model not installed · voice commands need it"
        )
        self.download_button.setVisible(not ready)
        self.download_button.setEnabled(not ready)

    def _check_mic(self) -> None:
        device = self.mic_combo.currentData()
        self.mic_result.set_text("Recording a 1.2 s test…")
        run_async(self, lambda: measure_input(1.2, device), self._mic_done)

    def _mic_done(self, result) -> None:
        if isinstance(result, Exception) or not result.get("ok"):
            error = result if isinstance(result, Exception) else result.get("error")
            self.mic_result.set_text(f"Microphone unavailable · {error}")
            return
        peak = int(result.get("peak", 0))
        if peak < 300:
            self.mic_result.set_text("Almost silent — check the Windows input volume, then test again.")
        else:
            self.mic_result.set_text(f"Microphone works · input level {min(100, peak // 328)}%")

    def _download(self) -> None:
        self.download_button.setEnabled(False)
        self.progress.setVisible(True)
        self.progress.setValue(0)
        self.runtime.download_speech_model()

    def _on_model_progress(self, percent: int, text: str) -> None:
        if percent < 0:
            self.progress.setVisible(False)
            self.download_button.setEnabled(True)
            self.model_status.set_text(text, theme.BAD)
            return
        self.progress.setValue(max(0, min(100, percent)))
        if percent >= 100:
            self.progress.setVisible(False)
            self._refresh_model()

    # -- navigation --------------------------------------------------------
    def _update_nav(self) -> None:
        index = self.stack.currentIndex()
        self.back_button.setEnabled(index > 0)
        self.next_button.setText("Finish" if index == self.stack.count() - 1 else "Continue")
        self.skip_button.setVisible(index < self.stack.count() - 1)

    def _next(self) -> None:
        index = self.stack.currentIndex()
        if index < self.stack.count() - 1:
            self.stack.setCurrentIndex(index + 1)
            self._update_nav()
            return
        self._finish()
        self.accept()

    def _back(self) -> None:
        self.stack.setCurrentIndex(max(0, self.stack.currentIndex() - 1))
        self._update_nav()

    def _skip(self) -> None:
        try:
            self.runtime.save_settings({"setup_complete": True, "wizard_version": 2})
        except Exception:
            pass
        self.accept()

    def _finish(self) -> None:
        self.runtime.save_settings({
            "setup_complete": True,
            "wizard_version": 2,
            "microphone_device": self.mic_combo.currentData(),
            "speech_voice": self.voice_combo.currentText() if self.voice_combo.count() else "",
            "start_with_windows": self.startup_switch.isChecked(),
        })
