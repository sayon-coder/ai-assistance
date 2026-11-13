"""Dark visual theme shared by every NOVA window."""
from __future__ import annotations

BG = "#0A0E15"
SURFACE = "#111823"
SURFACE_ALT = "#0D141E"
BORDER = "#1F2C3F"
TEXT = "#E9EFFA"
MUTED = "#8FA0B8"
ACCENT = "#4D8DFF"
GOOD = "#4FCB8B"
WARN = "#E2B45C"
BAD = "#E06C75"

STYLESHEET = f"""
QWidget {{
    color: {TEXT};
    font-family: "Segoe UI Variable Text", "Segoe UI", sans-serif;
    font-size: 10pt;
}}
QWidget#Root, QWidget#Page, QScrollArea, QScrollArea > QWidget > QWidget {{
    background: transparent;
}}
QFrame#Shell {{
    background: {BG};
    border: 1px solid #1D2939;
    border-radius: 16px;
}}
QFrame#Header {{
    background: #0D141E;
    border: none;
    border-bottom: 1px solid #1B2636;
    border-top-left-radius: 16px;
    border-top-right-radius: 16px;
}}
QFrame#Sidebar {{
    background: #0B111A;
    border: none;
    border-right: 1px solid #1B2636;
}}
QLabel#Brand {{
    font-size: 16pt;
    font-weight: 700;
    letter-spacing: 3px;
}}
QLabel#BrandSub {{
    color: {MUTED};
    font-size: 7.5pt;
    letter-spacing: 1.4px;
}}
QLabel#PageTitle {{
    font-size: 18pt;
    font-weight: 600;
}}
QLabel#PageSub {{
    color: {MUTED};
    font-size: 9.5pt;
}}
QLabel#Muted {{ color: {MUTED}; }}
QLabel#Good {{ color: {GOOD}; }}
QLabel#Warn {{ color: {WARN}; }}
QLabel#Bad {{ color: {BAD}; }}
QLabel#Footnote {{ color: #6F8098; font-size: 8.5pt; }}
QFrame#Card {{
    background: {SURFACE};
    border: 1px solid {BORDER};
    border-radius: 13px;
}}
QLabel#CardTitle {{
    font-size: 11pt;
    font-weight: 600;
    color: #EDF3FD;
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
    background: #16202D;
    border: 1px solid #27374C;
    border-radius: 8px;
    padding: 8px 14px;
    color: #DCE7F7;
}}
QPushButton:hover {{ background: #1D2A3A; border-color: #33475F; }}
QPushButton:pressed {{ background: #121A25; }}
QPushButton:disabled {{ color: #5D6D83; border-color: #1E2A3A; }}
QPushButton#Primary {{
    background: #2A63C4;
    border-color: #4C86E0;
    color: #FFFFFF;
    font-weight: 600;
}}
QPushButton#Primary:hover {{ background: #3572D8; }}
QPushButton#Danger {{
    background: #4E1D26;
    border-color: #7E3341;
    color: #FFD9DE;
}}
QPushButton#Danger:hover {{ background: #602430; }}
QPushButton#WindowButton {{
    background: transparent;
    border: 1px solid transparent;
    border-radius: 8px;
    padding: 7px 11px;
    color: {MUTED};
    font-size: 9pt;
}}
QPushButton#WindowButton:hover {{
    background: #182333;
    border-color: #27374C;
    color: {TEXT};
}}
QPushButton#CloseButton {{
    background: transparent;
    border: 1px solid transparent;
    border-radius: 8px;
    padding: 7px 11px;
    color: {MUTED};
    font-size: 9pt;
}}
QPushButton#CloseButton:hover {{
    background: #4E1D26;
    border-color: #7E3341;
    color: #FFD9DE;
}}
QPushButton#Nav {{
    background: transparent;
    border: 1px solid transparent;
    border-radius: 9px;
    padding: 11px 14px;
    text-align: left;
    color: #9DB0C9;
}}
QPushButton#Nav:hover {{ background: #121B27; color: #E7EEF9; }}
QPushButton#Nav:checked {{
    background: #16263C;
    border-color: #27405F;
    color: #FFFFFF;
    font-weight: 600;
}}
QLineEdit, QComboBox, QSpinBox, QDoubleSpinBox {{
    background: #0B111A;
    border: 1px solid #26364B;
    border-radius: 8px;
    padding: 8px 10px;
    selection-background-color: #2C5DA8;
}}
QLineEdit:focus, QComboBox:focus, QSpinBox:focus {{ border-color: #3E6FB8; }}
QComboBox::drop-down {{ border: none; width: 22px; }}
QComboBox QAbstractItemView {{
    background: #0D141E;
    border: 1px solid #26364B;
    selection-background-color: #1B2C42;
    outline: none;
}}
QListWidget {{
    background: #0B111A;
    border: 1px solid #202D3F;
    border-radius: 9px;
    padding: 5px;
    outline: none;
}}
QListWidget::item {{ padding: 7px 8px; border-radius: 6px; color: #D7E2F2; }}
QListWidget::item:selected {{ background: #1B2C42; color: #FFFFFF; }}
QListWidget::item:hover {{ background: #141F2C; }}
QCheckBox {{ spacing: 8px; color: {TEXT}; }}
QSlider::groove:horizontal {{ height: 4px; background: #26364B; border-radius: 2px; }}
QSlider::sub-page:horizontal {{ background: #3F76D0; border-radius: 2px; }}
QSlider::handle:horizontal {{
    width: 15px;
    margin: -6px 0;
    background: #7FB0FF;
    border-radius: 7px;
}}
QProgressBar {{
    background: #0B111A;
    border: 1px solid #26364B;
    border-radius: 5px;
    height: 8px;
    text-align: center;
    color: transparent;
}}
QProgressBar::chunk {{ background: #3F76D0; border-radius: 4px; }}
QScrollBar:vertical {{ background: transparent; width: 10px; margin: 2px; }}
QScrollBar::handle:vertical {{ background: #24344A; border-radius: 5px; min-height: 30px; }}
QScrollBar::handle:vertical:hover {{ background: #31465F; }}
QScrollBar::add-line, QScrollBar::sub-line {{ height: 0; }}
QToolTip {{
    background: #121A26;
    color: {TEXT};
    border: 1px solid #2A3A50;
    padding: 6px;
}}
QMenu {{
    background: #0F1621;
    border: 1px solid #27374C;
    border-radius: 8px;
    padding: 6px;
}}
QMenu::item {{ padding: 7px 22px 7px 14px; border-radius: 5px; }}
QMenu::item:selected {{ background: #1B2C42; }}
QMenu::separator {{ height: 1px; background: #1F2C3F; margin: 5px 8px; }}
QMessageBox {{ background: {BG}; }}
"""


def status_colour(text: str) -> str:
    lowered = str(text or "").lower()
    if any(word in lowered for word in ("unavailable", "missing", "error", "blocked", "failed")):
        return BAD
    if any(word in lowered for word in ("not running", "retry", "disconnected", "waiting", "no session")):
        return WARN
    if any(word in lowered for word in ("disabled", "off", "muted", "stopped")):
        return MUTED
    if any(word in lowered for word in ("listening", "connected", "ready", "installed", "enabled", "watching")):
        return GOOD
    return MUTED
