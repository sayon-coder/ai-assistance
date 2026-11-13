"""Qt signals used to move work off the UI thread safely."""
from PySide6.QtCore import QObject, Signal


class UiBridge(QObject):
    status_changed = Signal(dict)
    face_state_changed = Signal(str)
    face_visibility_requested = Signal(bool)
    open_settings = Signal(str)
    hide_to_tray = Signal()
    exit_requested = Signal()
    notification = Signal(str, str)
    voices_available = Signal(list)
    model_progress = Signal(int, str)
    settings_changed = Signal(dict)
    speech_state = Signal(bool)
