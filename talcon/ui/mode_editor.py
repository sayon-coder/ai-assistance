"""Editable Study / Entertainment / Stock mode configuration without JSON."""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QHBoxLayout, QLabel, QLineEdit, QListWidget, QListWidgetItem,
    QPlainTextEdit, QPushButton, QSizePolicy, QVBoxLayout, QWidget,
)

from ..apps import app_key, normalize_app_entry
from ..commands import normalize_phrase
from ..modes import normalize_url
from . import theme
from .app_picker import ApplicationPicker, KIND_LABELS
from .components import Card, Heading, footnote, muted, row


class ModeEditor(QWidget):
    def __init__(self, runtime, mode_key: str, title: str, subtitle: str,
                 custom: bool = False, parent=None):
        super().__init__(parent)
        self.runtime = runtime
        self.mode_key = mode_key
        self.custom = bool(custom)
        self.display_title = title
        self.setObjectName("Page")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(14)

        layout.addWidget(Heading(title, subtitle))

        if self.custom:
            identity = Card(
                "Mode name and spoken command",
                "These are the words you say after “TALCON” to open this mode.",
            )
            self.name_input = QLineEdit()
            self.name_input.setPlaceholderText("Cinema night")
            self.name_input.editingFinished.connect(self._save)
            identity.add(muted("Mode name"))
            identity.add(self.name_input)

            self.trigger_input = QPlainTextEdit()
            self.trigger_input.setPlaceholderText("cinema mode\nmovie time")
            self.trigger_input.setFixedHeight(82)
            identity.add(muted("Spoken command(s), one per line"))
            identity.add(self.trigger_input)
            save_triggers = QPushButton("Save spoken commands")
            save_triggers.setObjectName("Primary")
            save_triggers.clicked.connect(self._save)
            identity.add(row(save_triggers, _stretch(), spacing=8))
            identity.add(footnote(
                "A phrase already used by TALCON, another mode, or a custom command is rejected."
            ))
            layout.addWidget(identity)

        greeting_card = Card("Spoken greeting", "TALCON says this when the mode finishes opening.")
        self.greeting = QLineEdit()
        self.greeting.setPlaceholderText("Welcome back, sir. Your study environment is ready.")
        self.greeting.editingFinished.connect(self._save)
        greeting_card.add(self.greeting)
        layout.addWidget(greeting_card)

        websites = Card("Websites", "Opened in your default browser. Only http:// and https:// addresses are accepted.")
        self.website_list = QListWidget()
        self.website_list.setMinimumHeight(110)
        websites.add(self.website_list)
        self.website_input = QLineEdit()
        self.website_input.setPlaceholderText("example.com  or  https://example.com/page")
        self.website_input.returnPressed.connect(self._add_website)
        add_site = QPushButton("Add website")
        add_site.setObjectName("Primary")
        add_site.clicked.connect(self._add_website)
        remove_site = QPushButton("Remove selected")
        remove_site.clicked.connect(self._remove_website)
        websites.add(row(self.website_input, add_site))
        websites.add(row(remove_site, _stretch(), spacing=8))
        self.website_error = QLabel("")
        self.website_error.setObjectName("Bad")
        self.website_error.setVisible(False)
        websites.add(self.website_error)
        layout.addWidget(websites)

        apps = Card("Applications", "Windows programs or shortcuts to start with this mode.")
        self.app_list = QListWidget()
        self.app_list.setMinimumHeight(110)
        apps.add(self.app_list)
        add_app = QPushButton("Add application…")
        add_app.setObjectName("Primary")
        add_app.clicked.connect(self._add_app)
        remove_app = QPushButton("Remove selected")
        remove_app.clicked.connect(self._remove_app)
        apps.add(row(add_app, remove_app, _stretch(), spacing=8))
        apps.add(footnote(
            "Tip: you can add a shortcut (.lnk) from your Start menu so the program keeps "
            "its normal working folder and settings."
        ))
        layout.addWidget(apps)

        footer = QHBoxLayout()
        self.saved_label = QLabel("Saved automatically on this PC.")
        self.saved_label.setObjectName("Footnote")
        open_now = QPushButton(f"Open {title} now")
        open_now.clicked.connect(lambda: self.runtime.activate_mode(self.mode_key))
        footer.addWidget(self.saved_label)
        footer.addStretch(1)
        footer.addWidget(open_now)
        layout.addLayout(footer)
        layout.addStretch(1)

        self.refresh()

    # -- data --------------------------------------------------------------
    def _mode(self) -> dict:
        mode = self.runtime.modes_data.get(self.mode_key)
        return mode if isinstance(mode, dict) else {"greeting": "", "urls": [], "apps": []}

    def refresh(self) -> None:
        mode = self._mode()
        if self.custom:
            self.name_input.setText(str(mode.get("title", "")))
            self.trigger_input.setPlainText("\n".join(mode.get("triggers", [])))
        self.greeting.setText(str(mode.get("greeting", "")))
        self.website_list.clear()
        for url in mode.get("urls", []):
            item = QListWidgetItem(str(url))
            item.setData(Qt.ItemDataRole.UserRole, str(url))
            item.setToolTip(str(url))
            self.website_list.addItem(item)
        self.app_list.clear()
        for app in mode.get("apps", []):
            descriptor = normalize_app_entry(app)
            if descriptor is None:
                continue
            item = QListWidgetItem(
                f"{descriptor['name']}   ·   {KIND_LABELS.get(descriptor['kind'], descriptor['kind'])}"
            )
            item.setData(Qt.ItemDataRole.UserRole, descriptor)
            item.setToolTip(descriptor["aumid"] or descriptor["path"])
            self.app_list.addItem(item)

    def _save(self) -> None:
        mode = self._mode()
        urls = [self.website_list.item(i).data(Qt.ItemDataRole.UserRole)
                for i in range(self.website_list.count())]
        apps = [self.app_list.item(i).data(Qt.ItemDataRole.UserRole)
                for i in range(self.app_list.count())]
        entry = dict(mode)
        entry["greeting"] = self.greeting.text().strip() or mode.get("greeting", "")
        entry["urls"] = urls
        entry["apps"] = apps
        if self.custom:
            name = " ".join(self.name_input.text().split())
            triggers: list[str] = []
            for line in self.trigger_input.toPlainText().splitlines():
                phrase = normalize_phrase(line)
                if phrase and phrase not in triggers:
                    triggers.append(phrase)
            if not name:
                self._show_save_error("Enter a name for the mode.")
                return
            if not triggers:
                self._show_save_error("Add at least one spoken command, one per line.")
                return
            entry["title"] = name
            entry["triggers"] = triggers
        modes = dict(self.runtime.modes_data)
        modes[self.mode_key] = entry
        try:
            self.runtime.save_modes(modes)
            self.saved_label.setText("Saved.")
            self.saved_label.setStyleSheet(f"color: {theme.GOOD}; font-size: 8.5pt;")
        except Exception as exc:
            self._show_save_error(f"Could not save: {exc}")

    def _show_save_error(self, message: str) -> None:
        self.saved_label.setText(message)
        self.saved_label.setStyleSheet(f"color: {theme.BAD}; font-size: 8.5pt;")

    # -- websites ----------------------------------------------------------
    def _add_website(self) -> None:
        url = normalize_url(self.website_input.text())
        if url is None:
            self.website_error.setText("Enter a valid website address, for example example.com")
            self.website_error.setVisible(True)
            return
        self.website_error.setVisible(False)
        existing = [self.website_list.item(i).data(Qt.ItemDataRole.UserRole)
                    for i in range(self.website_list.count())]
        if url not in existing:
            item = QListWidgetItem(url)
            item.setData(Qt.ItemDataRole.UserRole, url)
            item.setToolTip(url)
            self.website_list.addItem(item)
            self._save()
        self.website_input.clear()

    def _remove_website(self) -> None:
        for item in self.website_list.selectedItems():
            self.website_list.takeItem(self.website_list.row(item))
        self._save()

    # -- applications ------------------------------------------------------
    def _add_app(self) -> None:
        existing = [self.app_list.item(i).data(Qt.ItemDataRole.UserRole)
                    for i in range(self.app_list.count())]
        picker = ApplicationPicker(self.runtime, existing=existing, parent=self)
        if not picker.exec() or not picker.selected_app:
            return
        entry = picker.selected_app
        keys = {key for key in (app_key(a) for a in existing) if key is not None}
        if app_key(entry) in keys:
            return
        item = QListWidgetItem(
            f"{entry['name']}   ·   {KIND_LABELS.get(entry['kind'], entry['kind'])}"
        )
        item.setData(Qt.ItemDataRole.UserRole, entry)
        item.setToolTip(entry.get("aumid") or entry.get("path") or "")
        self.app_list.addItem(item)
        self._save()

    def _remove_app(self) -> None:
        for item in self.app_list.selectedItems():
            self.app_list.takeItem(self.app_list.row(item))
        self._save()


def _stretch() -> QWidget:
    widget = QWidget()
    widget.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
    return widget
