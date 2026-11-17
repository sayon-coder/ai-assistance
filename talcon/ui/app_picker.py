"""Reusable Windows application picker.

The same dialog is used by Study, Entertainment and Stock modes and by the
custom-command and automation editors.  It only discovers applications that
are already installed on this PC; nothing is downloaded or installed.
"""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QObject, Qt, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QComboBox, QDialog, QFileDialog, QHBoxLayout, QLabel, QLineEdit, QListWidget,
    QListWidgetItem, QPushButton, QVBoxLayout,
)

from ..apps import ApplicationLibrary, app_key, normalize_app_entry
from . import theme

KIND_LABELS = {
    "executable": "Program",
    "shortcut": "Shortcut",
    "packaged_app": "Windows app",
    "browser_app": "Web app",
}

CATEGORIES = (
    ("Installed Applications", ""),
    ("Taskbar Pinned", "taskbar"),
    ("Start Menu Shortcuts", "start menu"),
)


class _PickerBridge(QObject):
    """Carries discovery results from the worker thread to the UI thread."""

    loaded = Signal(list, str)


class ApplicationPicker(QDialog):
    """Searchable picker for installed programs, shortcuts and packaged apps."""

    def __init__(self, runtime, existing=None, parent=None):
        super().__init__(parent)
        self.runtime = runtime
        self.library: ApplicationLibrary | None = getattr(runtime, "apps", None)
        self._entries: list = []
        self._selected: dict | None = None
        self._refreshing = False
        self.existing_keys = {
            key for key in (app_key(entry) for entry in (existing or [])) if key is not None
        }

        self.setWindowTitle("Choose an application")
        self.setModal(True)
        self.setObjectName("Root")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setStyleSheet(theme.STYLESHEET)
        self.resize(720, 560)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(22, 20, 22, 18)
        layout.setSpacing(10)

        heading = QLabel("Choose an application")
        heading.setObjectName("PageTitle")
        layout.addWidget(heading)
        note = QLabel(
            "Applications already installed on this PC. Discovery runs in the background "
            "and is cached, so it never blocks the microphone or the interface."
        )
        note.setObjectName("Muted")
        note.setWordWrap(True)
        layout.addWidget(note)

        filters = QHBoxLayout()
        self.category = QComboBox()
        for label, value in CATEGORIES:
            self.category.addItem(label, value)
        self.search = QLineEdit()
        self.search.setPlaceholderText("Search by application name…")
        self.search.setClearButtonEnabled(True)
        self.refresh_button = QPushButton("Refresh")
        self.refresh_button.setToolTip("Re-scan the Start menu, taskbar and Windows app list.")
        filters.addWidget(QLabel("Source"))
        filters.addWidget(self.category)
        filters.addWidget(self.search, 1)
        filters.addWidget(self.refresh_button)
        layout.addLayout(filters)

        self.list = QListWidget()
        self.list.setMinimumHeight(300)
        layout.addWidget(self.list, 1)

        self.status = QLabel("Ready.")
        self.status.setObjectName("Footnote")
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        self.error = QLabel("")
        self.error.setObjectName("Bad")
        self.error.setWordWrap(True)
        self.error.setVisible(False)
        layout.addWidget(self.error)

        buttons = QHBoxLayout()
        browse = QPushButton("Browse Manually…")
        browse.setToolTip("Choose an .exe, .lnk, .url, .bat or .cmd file yourself.")
        cancel = QPushButton("Cancel")
        select = QPushButton("Use this application")
        select.setObjectName("Primary")
        buttons.addWidget(browse)
        buttons.addStretch(1)
        buttons.addWidget(cancel)
        buttons.addWidget(select)
        layout.addLayout(buttons)

        self._bridge = _PickerBridge(self)
        self._bridge.loaded.connect(self._on_loaded)
        self.search.textChanged.connect(self._populate)
        self.category.currentIndexChanged.connect(self._populate)
        self.refresh_button.clicked.connect(self._refresh)
        self.list.itemDoubleClicked.connect(lambda _item: self._accept_selected())
        browse.clicked.connect(self._browse_manually)
        cancel.clicked.connect(self.reject)
        select.clicked.connect(self._accept_selected)

        self._load_initial()

    # -- data --------------------------------------------------------------
    @property
    def selected_app(self) -> dict | None:
        return dict(self._selected) if self._selected else None

    def _load_initial(self) -> None:
        if self.library is None:
            self._show_error("The application library is not available.")
            return
        if self.library.is_loaded:
            self._on_loaded(self.library.cached_entries(), "")
            if self.library.is_stale():
                self._refresh(silent=True)
        else:
            self._refresh()

    def _refresh(self, silent: bool = False) -> None:
        if self.library is None:
            self._show_error("The application library is not available.")
            return
        if self._refreshing:
            return
        self._refreshing = True
        self.refresh_button.setEnabled(False)
        if not silent:
            self.status.setText("Refreshing installed applications…")
        self._clear_error()

        def done(entries, error) -> None:
            self._bridge.loaded.emit(list(entries or []), str(error or ""))

        try:
            self.library.refresh_async(done)
        except Exception as exc:
            self._refreshing = False
            self.refresh_button.setEnabled(True)
            self._show_error(f"Could not refresh applications: {exc}")

    def _on_loaded(self, entries, error: str) -> None:
        self._refreshing = False
        self.refresh_button.setEnabled(True)
        if error:
            self._show_error(f"Application discovery failed: {error}")
            self.status.setText("Showing the last cached list.")
            return
        self._entries = list(entries or [])
        self.status.setText(f"{len(self._entries)} application(s) found.")
        self._populate()

    def _populate(self) -> None:
        query = self.search.text().strip().lower()
        category = str(self.category.currentData() or "").lower()
        self.list.clear()
        shown = 0
        for entry in self._entries:
            if category and entry.source.lower() != category:
                continue
            haystack = f"{entry.name} {entry.path} {entry.aumid}".lower()
            if query and query not in haystack:
                continue
            item = QListWidgetItem(
                f"{entry.name}   ·   {KIND_LABELS.get(entry.kind, entry.kind)}   ·   {entry.source}"
            )
            item.setData(Qt.ItemDataRole.UserRole, entry)
            detail = entry.aumid or entry.path
            item.setToolTip(detail)
            if entry.key() in self.existing_keys:
                item.setText(item.text() + "   (already added)")
                item.setForeground(QColor(theme.MUTED))
            self.list.addItem(item)
            shown += 1
        if not self._entries:
            self.status.setText("No applications discovered yet. Use Refresh or Browse Manually.")
        elif shown == 0:
            self.status.setText("No application matches this search or source.")

    # -- actions -----------------------------------------------------------
    def _accept_selected(self) -> None:
        item = self.list.currentItem()
        if item is None:
            self._show_error("Choose an application first.")
            return
        entry = item.data(Qt.ItemDataRole.UserRole)
        if entry is None:
            self._show_error("Choose an application first.")
            return
        if entry.key() in self.existing_keys:
            self._show_error("That application is already in this list.")
            return
        self._selected = entry.to_dict()
        self.accept()

    def _browse_manually(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Choose an application",
            str(Path.home()),
            "Programs and shortcuts (*.exe *.lnk *.url *.bat *.cmd);;All files (*.*)",
        )
        if not path:
            return
        entry = normalize_app_entry({"path": path, "name": Path(path).stem})
        if entry is None:
            self._show_error("That file is not a supported application.")
            return
        key = app_key(entry)
        if key is not None and key in self.existing_keys:
            self._show_error("That application is already in this list.")
            return
        self._selected = entry
        self.accept()

    # -- messages ----------------------------------------------------------
    def _show_error(self, text: str) -> None:
        self.error.setText(str(text))
        self.error.setVisible(bool(text))

    def _clear_error(self) -> None:
        self.error.clear()
        self.error.setVisible(False)
