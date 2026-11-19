"""Light-grey professional theme shared by every TALCON window."""
from __future__ import annotations

BG = "#A9A9A9"
SIDEBAR = "#B3B3B3"
SURFACE = "#D8D8D8"
SURFACE_ALT = "#CACACA"
INPUT = "#F1F1F1"
BORDER = "#9A9A9A"
WINDOW_BORDER = "#7A7A7A"
TEXT = "#1C1C1C"
MUTED = "#4F4F4F"
ACCENT = "#2F6FDB"
ACCENT_DARK = "#2559BC"
GOOD = "#1F7A45"
WARN = "#A45B00"
BAD = "#B3261E"

STYLESHEET = f"""
QWidget {{
    color: {TEXT};
    font-family: "Segoe UI Variable Text", "Segoe UI", sans-serif;
    font-size: 10pt;
}}
QWidget#Root {{
    background: transparent;
}}
QDialog#Root {{
    background: {BG};
}}
QWidget#Page {{
    background: transparent;
}}
QStackedWidget, QStackedWidget > QWidget {{
    background: transparent;
}}
QScrollArea {{
    background: transparent;
    border: none;
}}
QScrollArea > QWidget > QWidget {{
    background: transparent;
}}
QScrollArea > QWidget > QScrollBar {{
    background: transparent;
}}
QFrame#Shell {{
    background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
        stop:0 #B0B0B0, stop:0.5 #A4A4A4, stop:1 #939393);
    border: 1px solid {WINDOW_BORDER};
    border-radius: 16px;
}}
QFrame#Header {{
    background: {SIDEBAR};
    border: none;
    border-bottom: 1px solid {BORDER};
    border-top-left-radius: 16px;
    border-top-right-radius: 16px;
}}
QFrame#Sidebar {{
    background: {SIDEBAR};
    border: none;
    border-right: 1px solid {BORDER};
    border-bottom-left-radius: 16px;
}}
QLabel#Brand {{
    font-size: 16pt;
    font-weight: 700;
    letter-spacing: 3px;
    color: {TEXT};
}}
QLabel#BrandSub {{
    color: {MUTED};
    font-size: 7.5pt;
    letter-spacing: 1.4px;
}}
QLabel#PageTitle {{
    font-size: 18pt;
    font-weight: 600;
    color: {TEXT};
}}
QLabel#PageSub {{
    color: {MUTED};
    font-size: 9.5pt;
}}
QLabel#Muted {{ color: {MUTED}; }}
QLabel#Good {{ color: {GOOD}; }}
QLabel#Warn {{ color: {WARN}; }}
QLabel#Bad {{ color: {BAD}; }}
QLabel#Footnote {{ color: #5A5A5A; font-size: 8.5pt; }}
QFrame#Card {{
    background: {SURFACE};
    border: 1px solid {BORDER};
    border-radius: 10px;
}}
QLabel#CardTitle {{
    font-size: 11pt;
    font-weight: 600;
    color: {TEXT};
}}
QLabel#CardSub {{
    color: {MUTED};
    font-size: 8.8pt;
}}
QLabel#StatValue {{
    font-size: 10.5pt;
    font-weight: 600;
}}
QPushButton {{
    background: #E4E4E4;
    border: 1px solid #9A9A9A;
    border-radius: 7px;
    padding: 8px 14px;
    color: {TEXT};
}}
QPushButton:hover {{ background: #EFEFEF; border-color: #7C7C7C; }}
QPushButton:pressed {{ background: #CFCFCF; }}
QPushButton:focus {{ border-color: {ACCENT}; }}
QPushButton:disabled {{ color: #7C7C7C; border-color: #A8A8A8; background: #C4C4C4; }}
QPushButton#Ghost {{
    background: transparent;
    border: 1px solid #9A9A9A;
    color: {MUTED};
}}
QPushButton#Ghost:hover {{ background: #E4E4E4; color: {TEXT}; border-color: #7C7C7C; }}
QLabel#Section {{
    color: #5A5A5A;
    font-size: 8pt;
    font-weight: 600;
    letter-spacing: 2px;
}}
QFrame#Card:hover {{ border-color: #7C7C7C; }}
QPushButton#Primary {{
    background: {ACCENT_DARK};
    border-color: {ACCENT};
    color: #FFFFFF;
    font-weight: 600;
}}
QPushButton#Primary:hover {{ background: {ACCENT}; }}
QPushButton#Danger {{
    background: #F6D6D6;
    border-color: #C98A8A;
    color: #7F1D1D;
}}
QPushButton#Danger:hover {{ background: #F1C3C3; }}
QPushButton#WindowButton {{
    background: transparent;
    border: 1px solid transparent;
    border-radius: 7px;
    padding: 7px 11px;
    color: {MUTED};
    font-size: 9pt;
}}
QPushButton#WindowButton:hover {{
    background: #D6D6D6;
    border-color: #9A9A9A;
    color: {TEXT};
}}
QPushButton#CloseButton {{
    background: transparent;
    border: 1px solid transparent;
    border-radius: 7px;
    padding: 7px 11px;
    color: {MUTED};
    font-size: 9pt;
}}
QPushButton#CloseButton:hover {{
    background: #F1C3C3;
    border-color: #C98A8A;
    color: #7F1D1D;
}}
QPushButton#Nav {{
    background: transparent;
    border: 1px solid transparent;
    border-radius: 7px;
    padding: 11px 14px;
    text-align: left;
    color: {MUTED};
}}
QPushButton#Nav:hover {{ background: #E4E4E4; color: {TEXT}; }}
QPushButton#Nav:checked {{
    background: #D5E1F5;
    border-color: #A9BFE0;
    border-left: 3px solid {ACCENT};
    color: {TEXT};
    font-weight: 600;
}}
QLineEdit, QComboBox, QSpinBox, QDoubleSpinBox, QDateEdit, QTimeEdit,
QPlainTextEdit, QTextEdit {{
    background: {INPUT};
    border: 1px solid #9A9A9A;
    border-radius: 7px;
    padding: 8px 10px;
    color: {TEXT};
    selection-background-color: #C7DBF7;
}}
QLineEdit:focus, QComboBox:focus, QSpinBox:focus, QDoubleSpinBox:focus,
QDateEdit:focus, QTimeEdit:focus,
QPlainTextEdit:focus, QTextEdit:focus {{
    border-color: {ACCENT};
}}
QComboBox::drop-down {{ border: none; width: 22px; }}
QComboBox QAbstractItemView {{
    background: #F7F7F7;
    border: 1px solid #9A9A9A;
    selection-background-color: #C7DBF7;
    selection-color: {TEXT};
    outline: none;
}}
QListWidget, QTableWidget, QTreeWidget {{
    background: {INPUT};
    border: 1px solid #9A9A9A;
    border-radius: 7px;
    padding: 5px;
    outline: none;
    color: {TEXT};
}}
QListWidget::item, QTableWidget::item {{ padding: 7px 8px; border-radius: 5px; color: {TEXT}; }}
QListWidget::item:selected, QTableWidget::item:selected {{
    background: #C7DBF7;
    color: {TEXT};
}}
QListWidget::item:hover {{ background: #E4E4E4; }}
QHeaderView::section {{
    background: #C4C4C4;
    color: {MUTED};
    border: none;
    border-bottom: 1px solid {BORDER};
    padding: 6px;
}}
QCheckBox {{ spacing: 8px; color: {TEXT}; }}
QSlider::groove:horizontal {{ height: 4px; background: #9A9A9A; border-radius: 2px; }}
QSlider::sub-page:horizontal {{ background: {ACCENT}; border-radius: 2px; }}
QSlider::handle:horizontal {{
    width: 15px;
    margin: -6px 0;
    background: #FFFFFF;
    border: 1px solid #9A9A9A;
    border-radius: 7px;
}}
QProgressBar {{
    background: {INPUT};
    border: 1px solid #9A9A9A;
    border-radius: 4px;
    height: 8px;
    text-align: center;
    color: transparent;
}}
QProgressBar::chunk {{ background: {ACCENT}; border-radius: 3px; }}
QScrollBar:vertical {{ background: {BG}; width: 11px; margin: 2px; }}
QScrollBar::handle:vertical {{ background: #8A8A8A; border-radius: 5px; min-height: 30px; }}
QScrollBar::handle:vertical:hover {{ background: #6E6E6E; }}
QScrollBar::add-line, QScrollBar::sub-line {{ height: 0; }}
QScrollBar:horizontal {{ background: {BG}; height: 11px; margin: 2px; }}
QScrollBar::handle:horizontal {{ background: #8A8A8A; border-radius: 5px; min-width: 30px; }}
QToolTip {{
    background: #F5F5F5;
    color: {TEXT};
    border: 1px solid #9A9A9A;
    padding: 6px;
}}
QMenu {{
    background: {SURFACE};
    border: 1px solid #9A9A9A;
    border-radius: 7px;
    padding: 6px;
}}
QMenu::item {{ padding: 7px 22px 7px 14px; border-radius: 5px; color: {TEXT}; }}
QMenu::item:selected {{ background: #C7DBF7; }}
QMenu::separator {{ height: 1px; background: {BORDER}; margin: 5px 8px; }}
QMessageBox, QDialog {{ background: {BG}; }}
QSizeGrip {{ background: transparent; width: 14px; height: 14px; }}
"""


def status_colour(text: str) -> str:
    lowered = str(text or "").lower()
    if any(word in lowered for word in (
        "unavailable", "missing", "error", "blocked", "failed", "not found",
        "could not", "invalid",
    )):
        return BAD
    if any(word in lowered for word in (
        "not running", "retry", "disconnected", "waiting", "no session",
        "no speech", "no reliable", "not installed", "starting",
    )):
        return WARN
    if any(word in lowered for word in (
        "disabled", "off", "muted", "stopped", "idle", "no command yet",
    )):
        return MUTED
    if any(word in lowered for word in (
        "listening", "connected", "ready", "installed", "enabled", "watching",
        "running", "speaking", "executed", "completed", "activated", "opened",
        "capturing", "closed",
    )):
        return GOOD
    return MUTED
