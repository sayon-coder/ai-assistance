"""Light-grey professional theme shared by every TALCON window."""
from __future__ import annotations

#: Cool, low-saturation light palette.  Cards are true white on a soft
#: blue-grey canvas so panels read as separate planes without heavy outlines.
BG = "#EEF1F5"
SIDEBAR = "#F7F9FC"
SURFACE = "#FFFFFF"
SURFACE_ALT = "#F4F7FB"
INPUT = "#FFFFFF"
BORDER = "#E3E7ED"
WINDOW_BORDER = "#D9DEE6"
TEXT = "#14181F"
MUTED = "#5B6472"
ACCENT = "#4F6EF7"
ACCENT_DARK = "#3B57D6"
GOOD = "#15803D"
WARN = "#B45309"
BAD = "#DC2626"

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
        stop:0 #EBEFF6, stop:0.55 #E4EAF4, stop:1 #DCE4F0);
    border: 1px solid {WINDOW_BORDER};
    border-radius: 16px;
}}
QFrame#Header {{
    background: transparent;
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
    font-weight: 700;
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
QLabel#Footnote {{ color: {MUTED}; font-size: 8.5pt; }}
QFrame#Card {{
    background: {SURFACE};
    border: 1px solid {BORDER};
    border-radius: 12px;
}}
QLabel#CardTitle {{
    font-size: 11.5pt;
    font-weight: 700;
    color: {TEXT};
}}
QLabel#CardSub {{
    color: {MUTED};
    font-size: 9pt;
}}
QLabel#StatValue {{
    font-size: 10.5pt;
    font-weight: 600;
}}
QPushButton {{
    background: {SURFACE};
    border: 1px solid #DCE1E8;
    border-radius: 8px;
    padding: 9px 16px;
    color: {TEXT};
    font-weight: 500;
}}
QPushButton:hover {{ background: #F2F5FB; border-color: #C4CEDC; }}
QPushButton:pressed {{ background: #E7ECF4; }}
QPushButton:focus {{ border-color: {ACCENT}; }}
QPushButton:disabled {{ color: #98A1AE; border-color: #E4E8EE; background: {SURFACE_ALT}; }}
QPushButton#Ghost {{
    background: transparent;
    border: 1px solid #DCE1E8;
    color: {MUTED};
}}
QPushButton#Ghost:hover {{ background: #F2F5FB; color: {TEXT}; border-color: #C4CEDC; }}
QLabel#Section {{
    color: {MUTED};
    font-size: 8pt;
    font-weight: 700;
    letter-spacing: 2px;
}}
QFrame#Card:hover {{ border-color: #C9D3E3; }}
QPushButton#Primary {{
    background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
        stop:0 #5B79FF, stop:1 {ACCENT_DARK});
    border-color: {ACCENT_DARK};
    color: #FFFFFF;
    font-weight: 600;
}}
QPushButton#Primary:hover {{
    background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
        stop:0 #6C87FF, stop:1 {ACCENT});
}}
QPushButton#Primary:pressed {{ background: {ACCENT_DARK}; }}
QPushButton#Danger {{
    background: #FDF1F1;
    border-color: #F1C6C6;
    color: #B3261E;
}}
QPushButton#Danger:hover {{ background: #FBE4E4; border-color: #E9A8A8; }}
QPushButton#WindowButton {{
    background: transparent;
    border: 1px solid transparent;
    border-radius: 8px;
    padding: 7px 11px;
    color: {MUTED};
    font-size: 9pt;
}}
QPushButton#WindowButton:hover {{
    background: #E9EDF4;
    border-color: #DCE1E8;
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
    background: #FBE4E4;
    border-color: #E9A8A8;
    color: #B3261E;
}}
QPushButton#Nav {{
    background: transparent;
    border: 1px solid transparent;
    border-radius: 9px;
    padding: 11px 14px;
    text-align: left;
    color: {MUTED};
    font-weight: 500;
}}
QPushButton#Nav:hover {{ background: #EDF1F8; color: {TEXT}; }}
QPushButton#Nav:checked {{
    background: #E7EDFE;
    border-color: #CFDCFB;
    color: {ACCENT_DARK};
    font-weight: 600;
}}
QLineEdit, QComboBox, QSpinBox, QDoubleSpinBox, QDateEdit, QTimeEdit,
QPlainTextEdit, QTextEdit {{
    background: {INPUT};
    border: 1px solid #DCE1E8;
    border-radius: 8px;
    padding: 9px 11px;
    color: {TEXT};
    selection-background-color: #D6E1FD;
}}
QLineEdit:focus, QComboBox:focus, QSpinBox:focus, QDoubleSpinBox:focus,
QDateEdit:focus, QTimeEdit:focus,
QPlainTextEdit:focus, QTextEdit:focus {{
    border-color: {ACCENT};
}}
QLineEdit:hover, QComboBox:hover, QSpinBox:hover, QDoubleSpinBox:hover,
QDateEdit:hover, QTimeEdit:hover {{ border-color: #C4CEDC; }}
QComboBox::drop-down {{ border: none; width: 22px; }}
QComboBox QAbstractItemView {{
    background: #FFFFFF;
    border: 1px solid #DCE1E8;
    selection-background-color: #E7EDFE;
    selection-color: {TEXT};
    outline: none;
}}
QListWidget, QTableWidget, QTreeWidget {{
    background: {INPUT};
    border: 1px solid #DCE1E8;
    border-radius: 8px;
    padding: 5px;
    outline: none;
    color: {TEXT};
}}
QListWidget::item, QTableWidget::item {{ padding: 8px 9px; border-radius: 6px; color: {TEXT}; }}
QListWidget::item:selected, QTableWidget::item:selected {{
    background: #E7EDFE;
    color: {TEXT};
}}
QListWidget::item:hover, QTableWidget::item:hover {{ background: #F2F5FB; }}
QHeaderView::section {{
    background: {SURFACE_ALT};
    color: {MUTED};
    border: none;
    border-bottom: 1px solid {BORDER};
    border-top-left-radius: 7px;
    border-top-right-radius: 7px;
    padding: 8px 9px;
    font-weight: 600;
}}
QCheckBox {{ spacing: 8px; color: {TEXT}; }}
QSlider::groove:horizontal {{ height: 5px; background: #DCE1E8; border-radius: 3px; }}
QSlider::sub-page:horizontal {{ background: {ACCENT}; border-radius: 3px; }}
QSlider::handle:horizontal {{
    width: 16px;
    margin: -6px 0;
    background: #FFFFFF;
    border: 1px solid #C4CEDC;
    border-radius: 8px;
}}
QSlider::handle:horizontal:hover {{ border-color: {ACCENT}; }}
QProgressBar {{
    background: #E7EBF1;
    border: none;
    border-radius: 5px;
    height: 10px;
    text-align: center;
    color: transparent;
}}
QProgressBar::chunk {{
    background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
        stop:0 {ACCENT}, stop:1 #6C87FF);
    border-radius: 5px;
}}
QScrollBar:vertical {{ background: transparent; width: 12px; margin: 3px; }}
QScrollBar::handle:vertical {{ background: #C7CEDA; border-radius: 6px; min-height: 30px; }}
QScrollBar::handle:vertical:hover {{ background: #A9B4C4; }}
QScrollBar::add-line, QScrollBar::sub-line {{ height: 0; }}
QScrollBar:horizontal {{ background: transparent; height: 12px; margin: 3px; }}
QScrollBar::handle:horizontal {{ background: #C7CEDA; border-radius: 6px; min-width: 30px; }}
QScrollBar::handle:horizontal:hover {{ background: #A9B4C4; }}
QToolTip {{
    background: #1D2430;
    color: #FFFFFF;
    border: none;
    border-radius: 6px;
    padding: 7px 9px;
}}
QMenu {{
    background: #FFFFFF;
    border: 1px solid {BORDER};
    border-radius: 9px;
    padding: 7px;
}}
QMenu::item {{ padding: 8px 22px 8px 14px; border-radius: 6px; color: {TEXT}; }}
QMenu::item:selected {{ background: #E7EDFE; }}
QMenu::separator {{ height: 1px; background: {BORDER}; margin: 6px 9px; }}
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
