"""TALCON application shell: tray, face companion and control-center lifecycle."""
from __future__ import annotations

import argparse
import ctypes
import logging
import os
import sys
import threading

from PySide6.QtCore import QObject, QRect, Qt, QTimer
from PySide6.QtGui import QAction, QGuiApplication, QIcon, QPixmap
from PySide6.QtNetwork import QLocalServer, QLocalSocket
from PySide6.QtWidgets import QApplication, QMenu, QSystemTrayIcon

from .config import ASSETS_DIR, VERSION
from . import startup
from .face_render import render_face_image
from .face_widget import FaceWidget
from .logging_setup import setup_logging
from .menu_bar_face import MenuBarFace
from .runtime import TalconRuntime
from .shell_shortcut import APP_USER_MODEL_ID, ensure_shortcut
from .ui import theme
from .ui.main_window import MainWindow
from .ui.response_preview import ExpandedResponse, ResponsePreview
from .ui.wizard import SetupWizard

log = logging.getLogger("talcon.app")

SERVER_NAME = "TALCON_Assistant_2_0"
_ERROR_ALREADY_EXISTS = 183
#: Held open for the whole process lifetime; closing it releases the lock.
_instance_mutex = None
#: Kept alive for the process lifetime (RegisterHotKey registration).
_hotkey = None


def _claim_instance_mutex() -> bool:
    """Atomically claim the process-wide instance lock (Windows).

    A named kernel mutex cannot be double-acquired by two launches that race
    within the same millisecond, which the connect-then-listen socket check
    alone can do; both callers fail to connect before either is listening.
    Returns False when an already-running instance owns the lock, and True
    when the mutex API is unavailable (the socket check remains the fallback).
    """
    global _instance_mutex
    if sys.platform != "win32":
        return True
    try:
        kernel32 = ctypes.windll.kernel32
        handle = kernel32.CreateMutexW(None, False, f"Local\\{SERVER_NAME}")
        if kernel32.GetLastError() == _ERROR_ALREADY_EXISTS:
            if handle:
                kernel32.CloseHandle(handle)
            return False
        # Keep the handle open until the process exits; never close it here.
        _instance_mutex = handle
        return True
    except Exception:
        log.debug("Named instance mutex unavailable", exc_info=True)
        return True


def _notify_running_instance() -> None:
    socket = QLocalSocket()
    socket.connectToServer(SERVER_NAME)
    if socket.waitForConnected(250):
        try:
            socket.write(b"show")
            socket.waitForBytesWritten(250)
        except Exception:
            pass
        socket.disconnectFromServer()


def _claim_single_instance(app: QApplication) -> bool:
    # A live instance owns the mutex: ask it to show its window and refuse
    # to start a second copy.  Two copies each keep an in-memory settings
    # snapshot and overwrite settings.json wholesale, silently reverting
    # whichever copy saved second.
    if not _claim_instance_mutex():
        _notify_running_instance()
        return False
    server = QLocalServer(app)
    if not server.listen(SERVER_NAME):
        # A stale name from a crashed instance can block the lock; clear it
        # once and retry.  We already hold the mutex, so we remain the only
        # instance either way and a listen failure must not start a second
        # competing copy of anything.
        QLocalServer.removeServer(SERVER_NAME)
        server.listen(SERVER_NAME)
    app._talcon_server = server  # keep the server alive for the process lifetime
    return True


def _app_icon() -> QIcon:
    path = ASSETS_DIR / "talcon.ico"
    if not path.exists():
        path = ASSETS_DIR / "talcon_icon.png"
    if path.exists():
        return QIcon(str(path))
    icon = QIcon()
    icon.addPixmap(QPixmap.fromImage(render_face_image(64)))
    return icon


class TalconApplication(QObject):
    def __init__(self, qt: QApplication, startup_mode: bool = False):
        super().__init__()
        self.qt = qt
        self.startup_mode = bool(startup_mode)
        self.window: MainWindow | None = None
        self._exiting = False
        self._hidden_once = False

        self.runtime = TalconRuntime()
        self.face = FaceWidget(self.runtime, self.show_settings)
        self.menu_face = MenuBarFace(self.runtime, self.face, self.show_settings)
        # Same final response feeds the compact preview and the full reader;
        # both follow whichever avatar is currently visible.
        self.preview = ResponsePreview(on_expand=self._expand_response)
        self.preview.set_anchor_provider(self._avatar_anchor)
        self.expanded = ExpandedResponse()
        self._build_tray()
        self._connect()

    # -- tray --------------------------------------------------------------
    def _build_tray(self) -> None:
        self.tray = QSystemTrayIcon(_app_icon(), self.qt)
        self.tray.setToolTip("TALCON · local desktop assistant")
        self.tray_menu = QMenu()
        self.tray_menu.setStyleSheet("""
            QMenu { background: #181818; color: #FFFFFF; border: 1px solid #333333; padding: 6px; }
            QMenu::item { padding: 7px 24px 7px 16px; border-radius: 5px; }
            QMenu::item:selected { background: #1E3A5F; }
            QMenu::separator { height: 1px; background: #292929; margin: 5px 8px; }
        """)

        self.open_action = QAction("Open TALCON Control Center", self)
        self.open_action.triggered.connect(self.show_settings)
        self.tray_menu.addAction(self.open_action)

        self.face_action = QAction("Hide animated face", self)
        self.face_action.triggered.connect(self._toggle_face)
        self.tray_menu.addAction(self.face_action)

        self.menu_face_action = QAction("Hide menu bar face", self)
        self.menu_face_action.triggered.connect(self._toggle_menu_face)
        self.tray_menu.addAction(self.menu_face_action)

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

        self.tray_menu.addSeparator()
        exit_action = QAction("Exit TALCON completely", self)
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
        bridge.response_started.connect(self._on_response_started)
        bridge.speech_state.connect(self.preview.note_speaking)
        bridge.assistant_state.connect(self._on_assistant_state)
        self.qt.aboutToQuit.connect(self.runtime.shutdown)

        server = getattr(self.qt, "_talcon_server", None)
        if server is not None:
            server.newConnection.connect(self._incoming_connection)

    def _incoming_connection(self) -> None:
        server = getattr(self.qt, "_talcon_server", None)
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
        # Repair the logon task/shortcut in the background so a missing or
        # stale startup entry is fixed at every launch.
        if self.runtime.settings.get("start_with_windows", False):
            threading.Thread(
                target=startup.ensure, name="TALCON-StartupRepair", daemon=True
            ).start()
        self._apply_face_settings(self.runtime.settings)
        menu_face = getattr(self, "menu_face", None)
        if menu_face is not None:
            menu_face.start()
        self._refresh_menu()
        if self.startup_mode:
            log.info("Started quietly in the background")
            # Only the automatic Windows startup path speaks the greeting, and
            # only once.  A manual launch stays silent.
            self.runtime.speak_startup_greeting()
        else:
            self.show_settings()
            if not self.runtime.settings.get("setup_complete", False):
                QTimer.singleShot(280, self.show_wizard)
        return self.qt.exec()

    def show_settings(self, page: str = "dashboard") -> None:
        if self.window is None:
            self.window = MainWindow(self.runtime, self.face, menu_face=self.menu_face)
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
        self._hidden_once = True

    def set_face_visible(self, visible: bool) -> None:
        visible = bool(visible)
        try:
            self.runtime.save_settings({"show_face": visible})
        except Exception:
            log.exception("Could not save face visibility")
        self._apply_face_settings(self.runtime.settings)
        self._refresh_menu()

    def set_all_faces_visible(self, visible: bool) -> None:
        """Apply a voice show/hide request to both face surfaces only."""
        visible = bool(visible)
        try:
            self.runtime.save_settings({
                "show_face": visible,
                "menu_bar_face_enabled": visible,
            })
        except Exception:
            log.exception("Could not save companion visibility")
        self._apply_face_settings(self.runtime.settings)
        self._refresh_menu()

    def _apply_face_settings(self, settings: dict) -> None:
        self.face.apply_settings(settings)
        visible = bool(settings.get("show_face", True))
        # Use the explicit QWidget operations rather than relying on a stale
        # visibility flag while Windows is restoring a Tool window.  This does
        # not interact with the Control Center at all.
        if visible:
            self.face.show()
        else:
            self.face.hide()
        try:
            menu_face = getattr(self, "menu_face", None)
            if menu_face is not None:
                menu_face.apply_settings(settings)
        except Exception:
            log.exception("Could not apply the menu bar face settings")
        self._refresh_menu()

    def _on_settings_changed(self, settings: dict) -> None:
        self._apply_face_settings(settings)

    # -- response preview --------------------------------------------------
    def _avatar_anchor(self) -> QRect | None:
        """The visible avatar's rectangle, used to park the preview below it.

        The larger desktop face wins when both faces are visible; the menu-bar
        face is used when the desktop face is hidden.  ``None`` means no
        avatar is on screen, so the preview hides instead of floating alone.
        """
        face = getattr(self, "face", None)
        menu_face = getattr(self, "menu_face", None)
        candidates = [
            widget for widget in (face, menu_face)
            if widget is not None and widget.isVisible()
        ]
        if not candidates:
            return None
        # Prefer an avatar that the user can actually see in full.  A desktop
        # face half-hidden behind the taskbar would park the preview in a spot
        # that looks unrelated to the visible menu-bar face.
        for widget in candidates:
            rect = QRect(widget.geometry())
            screen = QGuiApplication.screenAt(rect.center()) or QGuiApplication.primaryScreen()
            if screen is None:
                continue
            if screen.availableGeometry().adjusted(-10, -10, 4, 4).contains(rect):
                return rect
        return QRect(candidates[0].geometry())

    def _on_response_started(self, entry: dict) -> None:
        try:
            self.preview.show_response(entry)
        except Exception:
            log.exception("Could not show the response preview")

    def _on_assistant_state(self, payload: dict) -> None:
        """Reflect AI request progress in the compact preview line."""
        kind = str((payload or {}).get("kind") or "")
        text = str((payload or {}).get("text") or "")
        try:
            if kind in ("accepted", "working"):
                self.preview.show_progress("Thinking…")
            elif kind == "progress":
                self.preview.show_progress(text or "Working…")
            elif kind == "approval":
                self.preview.show_progress("Waiting for your yes or no…")
            elif kind in ("answered", "error", "cancelled", "cache_hit"):
                self.preview.clear_progress()
        except Exception:
            log.debug("Could not update the preview progress", exc_info=True)

    def _expand_response(self, entry: dict) -> None:
        try:
            self.expanded.show_entry(entry, anchor=self.preview.geometry())
        except Exception:
            log.exception("Could not open the full response")

    def _on_status(self, status: dict) -> None:
        voice = str(status.get("voice", ""))
        tip = f"TALCON · {voice[:90]}" if voice else "TALCON · local desktop assistant"
        self.tray.setToolTip(tip[:120])
        self._refresh_menu()

    def _refresh_menu(self) -> None:
        self.face_action.setText(
            "Hide animated face" if self.face.isVisible() else "Show animated face"
        )
        self.menu_face_action.setText(
            "Hide menu bar face"
            if bool(self.runtime.settings.get("menu_bar_face_enabled", True))
            else "Show menu bar face"
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

    def _toggle_menu_face(self) -> None:
        enabled = not bool(self.runtime.settings.get("menu_bar_face_enabled", True))
        try:
            self.runtime.save_settings({"menu_bar_face_enabled": enabled})
        except Exception:
            log.exception("Could not change the menu bar face setting")
        self._refresh_menu()

    def _toggle_startup(self, checked: bool) -> None:
        try:
            self.runtime.save_settings({"start_with_windows": bool(checked)})
        except Exception:
            log.exception("Could not change startup setting")
        self._refresh_menu()

    def _notification(self, title: str, body: str) -> None:
        try:
            self.tray.showMessage(title, body, QSystemTrayIcon.MessageIcon.Information, 4000)
            self.tray.setToolTip(f"TALCON · {body[:90]}")
        except Exception:
            log.exception("Tray notification failed")

    def exit_completely(self) -> None:
        if self._exiting:
            return
        self._exiting = True
        log.info("Full shutdown requested")
        # Persist any debounced slider change the user made moments before
        # quitting (tray Quit never passes through the window close event).
        if self.window is not None:
            try:
                self.window._flush_pending_saves()
            except Exception:
                log.debug("Could not flush pending setting saves", exc_info=True)
        try:
            self.face.hide()
        except Exception:
            pass
        try:
            self.menu_face.shutdown()
        except Exception:
            pass
        for window in (getattr(self, "preview", None), getattr(self, "expanded", None)):
            try:
                if window is not None:
                    window.close()
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
        server = getattr(self.qt, "_talcon_server", None)
        if server is not None:
            try:
                server.close()
            except Exception:
                pass
        log.info("TALCON shut down")
        self.qt.quit()


def main() -> None:
    parser = argparse.ArgumentParser(description="TALCON local desktop assistant")
    parser.add_argument("--startup", action="store_true", help="start quietly in the background")
    parser.add_argument("--version", action="store_true", help="print the version and exit")
    parser.add_argument(
        "--speech-diagnostic", action="store_true",
        help="report the real Kokoro voice path and synthesise silently, then exit",
    )
    parser.add_argument(
        "--speech-test", action="store_true",
        help="synthesise an audible verification sentence and exit",
    )
    parser.add_argument(
        "--voice-id", default="",
        help="voice id to verify with --speech-test / --speech-diagnostic",
    )
    args = parser.parse_args()

    if args.version:
        print(f"TALCON {VERSION}")
        return

    if args.speech_diagnostic or args.speech_test:
        from .speech import voice_diagnostic

        setup_logging()
        print(
            voice_diagnostic(
                audible=bool(args.speech_test),
                voice_id=args.voice_id or "",
            )
        )
        return

    setup_logging()
    from .config import migrate_legacy_data

    migrate_legacy_data()
    try:
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(APP_USER_MODEL_ID)
    except Exception:
        pass
    ensure_shortcut()

    QGuiApplication.setHighDpiScaleFactorRoundingPolicy(
        Qt.HighDpiScaleFactorRoundingPolicy.PassThrough
    )
    qt = QApplication(sys.argv[:1])
    qt.setApplicationName("TALCON")
    qt.setApplicationDisplayName("TALCON")
    qt.setOrganizationName("TALCON Local Assistant")
    qt.setStyle("Fusion")
    qt.setStyleSheet(theme.STYLESHEET)
    qt.setQuitOnLastWindowClosed(False)
    qt.setWindowIcon(_app_icon())

    if not _claim_single_instance(qt):
        log.info("TALCON is already running; asked the existing instance to show its window")
        # The Qt objects created before the check can keep the interpreter
        # alive without an event loop running; exit explicitly so a refused
        # duplicate never lingers as a zombie process.
        for handler in logging.getLogger().handlers:
            handler.flush()
        os._exit(0)
        return

    log.info("Launching TALCON (startup=%s)", args.startup)
    application = TalconApplication(qt, startup_mode=args.startup)
    global _hotkey
    try:
        from .hotkey import install

        _hotkey = install(qt, application.runtime.listen_now)
    except Exception:
        log.debug("Global listen hotkey is unavailable", exc_info=True)
    sys.exit(application.run())
