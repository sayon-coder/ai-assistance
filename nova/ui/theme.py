"""Solid black professional theme shared by every NOVA window."""
from __future__ import annotations

BG = "#080808"
SIDEBAR = "#111111"
SURFACE = "#181818"
SURFACE_ALT = "#141414"
INPUT = "#101010"
BORDER = "#292929"
TEXT = "#FFFFFF"
MUTED = "#A0A0A0"
ACCENT = "#3B82F6"
ACCENT_DARK = "#2563EB"
GOOD = "#22C55E"
WARN = "#F59E0B"
BAD = "#EF4444"

STYLESHEET = f"""
QWidget {{
    color: {TEXT};
    font-family: "Segoe UI Variable Text", "Segoe UI", sans-serif;
    font-size: 10pt;
}}
QWidget#Root, QWidget#Page, QDialog#Root, QStackedWidget, QStackedWidget > QWidget {{
    background: {BG};
}}
QScrollArea {{
    background: {BG};
    border: none;
}}
QScrollArea > QWidget > QWidget {{
    background: {BG};
}}
QScrollArea > QWidget > QScrollBar {{
    background: {BG};
}}
QFrame#Shell {{
    background: {BG};
    border: 1px solid {BORDER};
    border-radius: 12px;
}}
QFrame#Header {{
    background: {SIDEBAR};
    border: none;
    border-bottom: 1px solid {BORDER};
    border-top-left-radius: 12px;
    border-top-right-radius: 12px;
}}
QFrame#Sidebar {{
    background: {SIDEBAR};
    border: none;
    border-right: 1px solid {BORDER};
    border-bottom-left-radius: 12px;
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
QLabel#Footnote {{ color: #808080; font-size: 8.5pt; }}
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
    background: #1A1A1A;
    border: 1px solid #333333;
    border-radius: 7px;
    padding: 8px 14px;
    color: {TEXT};
}}
QPushButton:hover {{ background: #222222; border-color: #444444; }}
QPushButton:pressed {{ background: #151515; }}
QPushButton:disabled {{ color: #666666; border-color: #262626; background: #121212; }}
QPushButton#Primary {{
    background: {ACCENT_DARK};
    border-color: {ACCENT};
    color: #FFFFFF;
    font-weight: 600;
}}
QPushButton#Primary:hover {{ background: {ACCENT}; }}
QPushButton#Danger {{
    background: #3A1414;
    border-color: #7F1D1D;
    color: #FFDADA;
}}
QPushButton#Danger:hover {{ background: #4A1717; }}
QPushButton#WindowButton {{
    background: transparent;
    border: 1px solid transparent;
    border-radius: 7px;
    padding: 7px 11px;
    color: {MUTED};
    font-size: 9pt;
}}
QPushButton#WindowButton:hover {{
    background: #1C1C1C;
    border-color: #333333;
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
    background: #3A1414;
    border-color: #7F1D1D;
    color: #FFDADA;
}}
QPushButton#Nav {{
    background: transparent;
    border: 1px solid transparent;
    border-radius: 7px;
    padding: 11px 14px;
    text-align: left;
    color: {MUTED};
}}
QPushButton#Nav:hover {{ background: #1A1A1A; color: {TEXT}; }}
QPushButton#Nav:checked {{
    background: #1C2430;
    border-color: #2F4A6B;
    border-left: 3px solid {ACCENT};
    color: {TEXT};
    font-weight: 600;
}}
QLineEdit, QComboBox, QSpinBox, QDoubleSpinBox, QPlainTextEdit, QTextEdit {{
    background: {INPUT};
    border: 1px solid #333333;
    border-radius: 7px;
    padding: 8px 10px;
    color: {TEXT};
    selection-background-color: {ACCENT_DARK};
}}
QLineEdit:focus, QComboBox:focus, QSpinBox:focus, QDoubleSpinBox:focus,
QPlainTextEdit:focus, QTextEdit:focus {{
    border-color: {ACCENT};
}}
QComboBox::drop-down {{ border: none; width: 22px; }}
QComboBox QAbstractItemView {{
    background: {INPUT};
    border: 1px solid #333333;
    selection-background-color: #1E3A5F;
    selection-color: {TEXT};
    outline: none;
}}
QListWidget, QTableWidget, QTreeWidget {{
    background: {INPUT};
    border: 1px solid #2A2A2A;
    border-radius: 7px;
    padding: 5px;
    outline: none;
    color: {TEXT};
}}
QListWidget::item, QTableWidget::item {{ padding: 7px 8px; border-radius: 5px; color: {TEXT}; }}
QListWidget::item:selected, QTableWidget::item:selected {{
    background: #1E3A5F;
    color: #FFFFFF;
}}
QListWidget::item:hover {{ background: #1A1A1A; }}
QHeaderView::section {{
    background: #141414;
    color: {MUTED};
    border: none;
    border-bottom: 1px solid {BORDER};
    padding: 6px;
}}
QCheckBox {{ spacing: 8px; color: {TEXT}; }}
QSlider::groove:horizontal {{ height: 4px; background: #333333; border-radius: 2px; }}
QSlider::sub-page:horizontal {{ background: {ACCENT}; border-radius: 2px; }}
QSlider::handle:horizontal {{
    width: 15px;
    margin: -6px 0;
    background: #DCE8FF;
    border-radius: 7px;
}}
QProgressBar {{
    background: {INPUT};
    border: 1px solid #333333;
    border-radius: 4px;
    height: 8px;
    text-align: center;
    color: transparent;
}}
QProgressBar::chunk {{ background: {ACCENT}; border-radius: 3px; }}
QScrollBar:vertical {{ background: {BG}; width: 11px; margin: 2px; }}
QScrollBar::handle:vertical {{ background: #3A3A3A; border-radius: 5px; min-height: 30px; }}
QScrollBar::handle:vertical:hover {{ background: #4A4A4A; }}
QScrollBar::add-line, QScrollBar::sub-line {{ height: 0; }}
QScrollBar:horizontal {{ background: {BG}; height: 11px; margin: 2px; }}
QScrollBar::handle:horizontal {{ background: #3A3A3A; border-radius: 5px; min-width: 30px; }}
QToolTip {{
    background: #1A1A1A;
    color: {TEXT};
    border: 1px solid #333333;
    padding: 6px;
}}
QMenu {{
    background: {SURFACE};
    border: 1px solid #333333;
    border-radius: 7px;
    padding: 6px;
}}
QMenu::item {{ padding: 7px 22px 7px 14px; border-radius: 5px; color: {TEXT}; }}
QMenu::item:selected {{ background: #1E3A5F; }}
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
