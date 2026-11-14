"""NOVA application shell: tray, face companion and control-center lifecycle."""
from __future__ import annotations

import argparse
import ctypes
import logging
import sys

from PySide6.QtCore import QObject, Qt, QTimer
from PySide6.QtGui import QAction, QGuiApplication, QIcon, QPixmap
from PySide6.QtNetwork import QLocalServer, QLocalSocket
from PySide6.QtWidgets import QApplication, QMenu, QSystemTrayIcon

from .config import ASSETS_DIR, VERSION
from .face_render import render_face_image
from .face_widget import FaceWidget
from .logging_setup import setup_logging
from .runtime import NovaRuntime
from .ui import theme
from .ui.main_window import MainWindow
from .ui.wizard import SetupWizard

log = logging.getLogger("nova.app")

SERVER_NAME = "NOVA_Assistant_2_0"


def _claim_single_instance(app: QApplication) -> bool:
    socket = QLocalSocket()
    socket.connectToServer(SERVER_NAME)
    if socket.waitForConnected(250):
        try:
            socket.write(b"show")
            socket.waitForBytesWritten(250)
        except Exception:
            pass
        socket.disconnectFromServer()
        return False
    QLocalServer.removeServer(SERVER_NAME)
    server = QLocalServer(app)
    if not server.listen(SERVER_NAME):
        log.warning("Could not create the single-instance server: %s", server.errorString())
    app._nova_server = server  # keep the server alive for the process lifetime
    return True


def _app_icon() -> QIcon:
    path = ASSETS_DIR / "nova_icon.png"
    if path.exists():
        return QIcon(str(path))
    icon = QIcon()
    icon.addPixmap(QPixmap.fromImage(render_face_image(64)))
    return icon


class NovaApplication(QObject):
    def __init__(self, qt: QApplication, startup_mode: bool = False):
        super().__init__()
        self.qt = qt
        self.startup_mode = bool(startup_mode)
        self.window: MainWindow | None = None
        self._exiting = False
        self._hidden_once = False

        self.runtime = NovaRuntime()
        self.face = FaceWidget(self.runtime, self.show_settings)
        self._build_tray()
        self._connect()

    # -- tray --------------------------------------------------------------
    def _build_tray(self) -> None:
        self.tray = QSystemTrayIcon(_app_icon(), self.qt)
        self.tray.setToolTip("NOVA · local desktop assistant")
        self.tray_menu = QMenu()
        self.tray_menu.setStyleSheet("""
            QMenu { background: #181818; color: #FFFFFF; border: 1px solid #333333; padding: 6px; }
            QMenu::item { padding: 7px 24px 7px 16px; border-radius: 5px; }
            QMenu::item:selected { background: #1E3A5F; }
            QMenu::separator { height: 1px; background: #292929; margin: 5px 8px; }
        """)

        self.open_action = QAction("Open NOVA Control Center", self)
        self.open_action.triggered.connect(self.show_settings)
        self.tray_menu.addAction(self.open_action)

        self.face_action = QAction("Hide animated face", self)
        self.face_action.triggered.connect(self._toggle_face)
        self.tray_menu.addAction(self.face_action)

        self.mute_action = QAction("Mute voice", self)
        self.mute_action.triggered.connect(lambda: self.runtime.set_muted(not self.runtime.muted))
        self.tray_menu.addAction(self.mute_action)

        self.listen_action = QAction("Listen now", self)
        self.listen_action.triggered.connect(self.runtime.listen_now)
        self.tray_menu.addAction(self.listen_action)

        self.tray_menu.addSeparator()
        study = QAction("Activate Study Mode", self)
        study.triggered.connect(lambda: self.runtime.activate_mode("study"))
        self.tray_menu.addAction(study)
        entertain = QAction("Activate Entertainment Mode", self)
        entertain.triggered.connect(lambda: self.runtime.activate_mode("entertainment"))
        self.tray_menu.addAction(entertain)
        stock = QAction("Activate Stock Mode", self)
        stock.triggered.connect(lambda: self.runtime.activate_mode("stock"))
        self.tray_menu.addAction(stock)

        self.tray_menu.addSeparator()
        self.startup_action = QAction("Start with Windows", self)
        self.startup_action.setCheckable(True)
        self.startup_action.triggered.connect(self._toggle_startup)
        self.tray_menu.addAction(self.startup_action)

        about_action = QAction("About NOVA", self)
        about_action.triggered.connect(lambda: self.show_settings("about"))
        self.tray_menu.addAction(about_action)

        self.tray_menu.addSeparator()
        exit_action = QAction("Exit NOVA completely", self)
        exit_action.triggered.connect(self.exit_completely)
        self.tray_menu.addAction(exit_action)

        self.tray.setContextMenu(self.tray_menu)
        self.tray.activated.connect(self._tray_activated)
        self.tray.show()

    def _tray_activated(self, reason) -> None:
        if reason in (
            QSystemTrayIcon.ActivationReason.Trigger,
            QSystemTrayIcon.ActivationReason.DoubleClick,
        ):
            self.show_settings()

    # -- wiring ------------------------------------------------------------
    def _connect(self) -> None:
        bridge = self.runtime.bridge
        bridge.open_settings.connect(self.show_settings)
        bridge.hide_to_tray.connect(self.hide_to_tray)
        bridge.face_visibility_requested.connect(self.set_face_visible)
        bridge.exit_requested.connect(self.exit_completely)
        bridge.notification.connect(self._notification)
        bridge.status_changed.connect(self._on_status)
        bridge.face_state_changed.connect(self.face.set_state)
        bridge.settings_changed.connect(self._on_settings_changed)
        self.qt.aboutToQuit.connect(self.runtime.shutdown)

        server = getattr(self.qt, "_nova_server", None)
        if server is not None:
            server.newConnection.connect(self._incoming_connection)

    def _incoming_connection(self) -> None:
        server = getattr(self.qt, "_nova_server", None)
        if server is None:
            return
        while server.hasPendingConnections():
            connection = server.nextPendingConnection()
            message = b""
            try:
                connection.waitForReadyRead(200)
                message = bytes(connection.readAll()).strip()
                connection.disconnectFromServer()
            except Exception:
                pass
            if message == b"show":
                # A second launch asks this instance to reopen its window.
                log.info("Second launch requested the control center")
                self.show_settings()
        self.show_settings()

    # -- lifecycle ---------------------------------------------------------
    def run(self) -> int:
        self.runtime.start()
        self._apply_face_settings(self.runtime.settings)
        self._refresh_menu()
        if self.startup_mode:
            log.info("Started quietly in the background")
        else:
            self.show_settings()
            if not self.runtime.settings.get("setup_complete", False):
                QTimer.singleShot(280, self.show_wizard)
        return self.qt.exec()

    def show_settings(self, page: str = "dashboard") -> None:
        if self.window is None:
            self.window = MainWindow(self.runtime, self.face)
        if page:
            self.window.show_page(str(page))
        self.window.show()
        self.window.raise_()
        self.window.activateWindow()
        self.window.sync_controls()

    def show_wizard(self) -> None:
        if self.window is not None:
            self.window.show()
        wizard = SetupWizard(self.runtime, self.window)
        wizard.exec()
        if self.window is not None:
            self.window.sync_controls()
            self.window.load_from_runtime()

    def hide_to_tray(self) -> None:
        """Hide the control center only. The face and all services keep running."""
        if self.window is not None:
            self.window.hide()
        if not self.tray.isVisible():
            self.tray.show()
        if not self._hidden_once:
            self._hidden_once = True
            self.tray.showMessage(
                "NOVA",
                "Control center hidden. NOVA and the face keep running — reopen from this tray icon.",
                QSystemTrayIcon.MessageIcon.Information,
                3000,
            )

    def set_face_visible(self, visible: bool) -> None:
        visible = bool(visible)
        try:
            self.runtime.save_settings({"show_face": visible})
        except Exception:
            log.exception("Could not save face visibility")
        self._apply_face_settings(self.runtime.settings)
        self._refresh_menu()

    def _apply_face_settings(self, settings: dict) -> None:
        self.face.apply_settings(settings)
        visible = bool(settings.get("show_face", True))
        if self.face.isVisible() != visible:
            self.face.setVisible(visible)
        self._refresh_menu()

    def _on_settings_changed(self, settings: dict) -> None:
        self._apply_face_settings(settings)

    def _on_status(self, status: dict) -> None:
        voice = str(status.get("voice", ""))
        tip = f"NOVA · {voice[:90]}" if voice else "NOVA · local desktop assistant"
        self.tray.setToolTip(tip[:120])
        self._refresh_menu()

    def _refresh_menu(self) -> None:
        self.face_action.setText(
            "Hide animated face" if self.face.isVisible() else "Show animated face"
        )
        self.mute_action.setText("Unmute voice" if self.runtime.muted else "Mute voice")
        try:
            from .startup import is_enabled

            enabled = is_enabled()
            self.startup_action.blockSignals(True)
            self.startup_action.setChecked(enabled)
            self.startup_action.blockSignals(False)
        except Exception:
            pass

    def _toggle_face(self) -> None:
        self.set_face_visible(not self.face.isVisible())

    def _toggle_startup(self, checked: bool) -> None:
        try:
            self.runtime.save_settings({"start_with_windows": bool(checked)})
        except Exception:
            log.exception("Could not change startup setting")
        self._refresh_menu()

    def _notification(self, title: str, body: str) -> None:
        try:
            self.tray.showMessage(title, body, QSystemTrayIcon.MessageIcon.Information, 4000)
            self.tray.setToolTip(f"NOVA · {body[:90]}")
        except Exception:
            log.exception("Tray notification failed")

    def exit_completely(self) -> None:
        if self._exiting:
            return
        self._exiting = True
        log.info("Full shutdown requested")
        try:
            self.face.hide()
        except Exception:
            pass
        try:
            self.tray.hide()
        except Exception:
            pass
        self.runtime.shutdown()
        if self.window is not None:
            try:
                self.window.blockSignals(True)
                self.window.hide()
            except Exception:
                pass
        server = getattr(self.qt, "_nova_server", None)
        if server is not None:
            try:
                server.close()
            except Exception:
                pass
        log.info("NOVA shut down")
        self.qt.quit()


def main() -> None:
    parser = argparse.ArgumentParser(description="NOVA local desktop assistant")
    parser.add_argument("--startup", action="store_true", help="start quietly in the background")
    parser.add_argument("--version", action="store_true", help="print the version and exit")
    args = parser.parse_args()

    if args.version:
        print(f"NOVA {VERSION}")
        return

    setup_logging()
    try:
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("NOVA.LocalAssistant")
    except Exception:
        pass

    QGuiApplication.setHighDpiScaleFactorRoundingPolicy(
        Qt.HighDpiScaleFactorRoundingPolicy.PassThrough
    )
    qt = QApplication(sys.argv[:1])
    qt.setApplicationName("NOVA")
    qt.setApplicationDisplayName("NOVA")
    qt.setOrganizationName("NOVA Local Assistant")
    qt.setStyle("Fusion")
    qt.setStyleSheet(theme.STYLESHEET)
    qt.setQuitOnLastWindowClosed(False)
    qt.setWindowIcon(_app_icon())

    if not _claim_single_instance(qt):
        log.info("NOVA is already running; asked the existing instance to show its window")
        return

    log.info("Launching NOVA (startup=%s)", args.startup)
    application = NovaApplication(qt, startup_mode=args.startup)
    sys.exit(application.run())
