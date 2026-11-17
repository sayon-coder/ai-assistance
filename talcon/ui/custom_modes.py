"""Create user-defined modes without any AI or cloud service.

A mode is just a name, one or more spoken commands, a greeting, and the
websites and applications it should open.  Everything is stored in the same
``config/modes.json`` file as the built-in modes, so the existing offline
command grammar picks the new triggers up immediately.
"""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QLabel, QLineEdit, QListWidget, QListWidgetItem, QMessageBox,
    QPlainTextEdit, QPushButton, QSizePolicy, QVBoxLayout, QWidget,
)

from ..apps import app_key, normalize_app_entry
from ..commands import normalize_phrase
from ..config import RESERVED_MODE_KEYS, mode_key
from ..modes import normalize_url
from .app_picker import ApplicationPicker, KIND_LABELS
from .components import Card, Heading, footnote, muted, row


class CustomModesPage(QWidget):
    """The "Add your own mode" section of the control center."""

    def __init__(self, runtime, on_open=None, parent=None):
        super().__init__(parent)
        self.runtime = runtime
        self.on_open = on_open or (lambda _key: None)
        self.setObjectName("Page")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(14)

        layout.addWidget(Heading(
            "Add your own mode",
            "Create a mode yourself — no AI, no account, no cloud. Choose the words you "
            "will say, what TALCON answers, and what opens.",
        ))

        form = Card(
            "New mode",
            "The spoken command is what you say after “TALCON”. Use one line per phrase.",
        )
        self.name_input = QLineEdit()
        self.name_input.setPlaceholderText("Mode name, for example Cinema night")
        form.add(_labelled("Mode name", self.name_input))

        self.trigger_input = QPlainTextEdit()
        self.trigger_input.setPlaceholderText(
            "cinema mode\nmovie time\nopen my cinema setup"
        )
        self.trigger_input.setFixedHeight(82)
        form.add(_labelled("Spoken command(s)", self.trigger_input))

        self.greeting_input = QLineEdit()
        self.greeting_input.setPlaceholderText(
            "Certainly, sir. Your cinema environment is ready."
        )
        form.add(_labelled("Spoken greeting", self.greeting_input))

        form.add(muted("Websites to open (optional)"))
        self.website_list = QListWidget()
        self.website_list.setMinimumHeight(90)
        form.add(self.website_list)
        self.website_input = QLineEdit()
        self.website_input.setPlaceholderText("example.com  or  https://example.com/page")
        self.website_input.returnPressed.connect(self._add_website)
        add_site = QPushButton("Add website")
        add_site.clicked.connect(self._add_website)
        remove_site = QPushButton("Remove selected")
        remove_site.clicked.connect(self._remove_website)
        form.add(row(self.website_input, add_site))
        form.add(row(remove_site, _stretch(), spacing=8))
        self.website_error = QLabel("")
        self.website_error.setObjectName("Bad")
        self.website_error.setVisible(False)
        form.add(self.website_error)

        form.add(muted("Applications to open (optional)"))
        self.app_list = QListWidget()
        self.app_list.setMinimumHeight(90)
        form.add(self.app_list)
        add_app = QPushButton("Add application…")
        add_app.clicked.connect(self._add_app)
        remove_app = QPushButton("Remove selected")
        remove_app.clicked.connect(self._remove_app)
        form.add(row(add_app, remove_app, _stretch(), spacing=8))

        create = QPushButton("Create mode")
        create.setObjectName("Primary")
        create.clicked.connect(self._create)
        form.add(create)

        self.error_label = QLabel("")
        self.error_label.setObjectName("Bad")
        self.error_label.setWordWrap(True)
        self.error_label.setVisible(False)
        form.add(self.error_label)
        form.add(footnote(
            "The mode appears in the sidebar after it is created, where you can change "
            "these details at any time. Phrases already used by TALCON or another mode are "
            "rejected."
        ))
        layout.addWidget(form)

        existing = Card(
            "Your modes",
            "Open a mode to edit its greeting, websites and applications.",
        )
        self.mode_list = QListWidget()
        self.mode_list.setMinimumHeight(140)
        self.mode_list.itemDoubleClicked.connect(lambda _item: self._open_selected())
        existing.add(self.mode_list)

        open_button = QPushButton("Open selected")
        open_button.setObjectName("Primary")
        open_button.clicked.connect(self._open_selected)
        delete_button = QPushButton("Delete selected")
        delete_button.setObjectName("Danger")
        delete_button.clicked.connect(self._delete_selected)
        existing.add(row(open_button, delete_button, spacing=8))
        existing.add(muted(
            "Built-in Study, Entertainment and Stock modes cannot be deleted here."
        ))
        layout.addWidget(existing)
        layout.addStretch(1)

        self.refresh()

    # -- helpers -----------------------------------------------------------
    def _modes(self) -> dict:
        return dict(getattr(self.runtime, "modes_data", {}) or {})

    def _custom_keys(self) -> list[str]:
        return [key for key in self._modes() if key not in RESERVED_MODE_KEYS]

    def _title(self, key: str) -> str:
        mode = self._modes().get(key)
        if isinstance(mode, dict) and mode.get("title"):
            return str(mode["title"])
        return str(key).replace("_", " ").title()

    def _show_error(self, message: str) -> None:
        self.error_label.setText(message)
        self.error_label.setVisible(True)

    def _clear_error(self) -> None:
        self.error_label.clear()
        self.error_label.setVisible(False)

    # -- data --------------------------------------------------------------
    def refresh(self) -> None:
        self.mode_list.clear()
        for key in self._custom_keys():
            mode = self._modes().get(key, {})
            triggers = mode.get("triggers") or []
            detail = " · ".join(triggers) if triggers else "no spoken command yet"
            item = QListWidgetItem(f"{self._title(key)}   —   {detail}")
            item.setData(Qt.ItemDataRole.UserRole, key)
            self.mode_list.addItem(item)

    # -- actions -----------------------------------------------------------
    def _create(self) -> None:
        self._clear_error()
        name = " ".join(self.name_input.text().split())
        if not name:
            self._show_error("Enter a name for the mode.")
            return
        key = mode_key(name)
        if not key:
            self._show_error("Use letters or numbers in the mode name.")
            return
        modes = self._modes()
        if key in modes:
            self._show_error(f"A mode named “{self._title(key)}” already exists.")
            return
        if key in RESERVED_MODE_KEYS:
            self._show_error(
                f"“{name}” is reserved for an existing TALCON section. Choose another name."
            )
            return

        triggers: list[str] = []
        for line in self.trigger_input.toPlainText().splitlines():
            phrase = normalize_phrase(line)
            if phrase and phrase not in triggers:
                triggers.append(phrase)
        if not triggers:
            self._show_error("Add at least one spoken command, one per line.")
            return

        try:
            reserved = set(self.runtime.reserved_command_phrases())
        except Exception:
            reserved = set()
        clash = next((phrase for phrase in triggers if phrase in reserved), None)
        if clash is not None:
            self._show_error(
                f"“{clash}” is already used by TALCON or another mode. Choose another phrase."
            )
            return

        greeting = self.greeting_input.text().strip() or (
            f"Certainly, sir. Your {name.lower()} environment is ready."
        )
        urls = [self.website_list.item(i).data(Qt.ItemDataRole.UserRole)
                for i in range(self.website_list.count())]
        apps = [self.app_list.item(i).data(Qt.ItemDataRole.UserRole)
                for i in range(self.app_list.count())]
        modes[key] = {
            "title": name,
            "greeting": greeting,
            "triggers": triggers,
            "urls": urls,
            "apps": apps,
        }
        try:
            self.runtime.save_modes(modes)
        except ValueError as exc:
            self._show_error(str(exc))
            return
        except Exception as exc:
            self._show_error(f"Could not save the mode: {exc}")
            return

        self.name_input.clear()
        self.trigger_input.clear()
        self.greeting_input.clear()
        self.website_list.clear()
        self.app_list.clear()
        self.refresh()
        self.on_open(key)

    # -- websites and applications ----------------------------------------
    def _add_website(self) -> None:
        url = normalize_url(self.website_input.text())
        if url is None:
            self.website_error.setText(
                "Enter a valid website address, for example example.com"
            )
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
        self.website_input.clear()

    def _remove_website(self) -> None:
        for item in self.website_list.selectedItems():
            self.website_list.takeItem(self.website_list.row(item))

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

    def _remove_app(self) -> None:
        for item in self.app_list.selectedItems():
            self.app_list.takeItem(self.app_list.row(item))

    def _selected_key(self) -> str | None:
        item = self.mode_list.currentItem()
        if item is None:
            self._show_error("Select a mode first.")
            return None
        return str(item.data(Qt.ItemDataRole.UserRole))

    def _open_selected(self) -> None:
        key = self._selected_key()
        if key is not None:
            self.on_open(key)

    def _delete_selected(self) -> None:
        key = self._selected_key()
        if key is None:
            return
        title = self._title(key)
        answer = QMessageBox.question(
            self,
            "Delete mode",
            f"Delete “{title}” mode? Its spoken command will stop working.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        modes = self._modes()
        modes.pop(key, None)
        try:
            self.runtime.save_modes(modes)
        except Exception as exc:
            self._show_error(f"Could not delete the mode: {exc}")
            return
        self.refresh()


def _labelled(text: str, widget: QWidget) -> QWidget:
    holder = QWidget()
    layout = QVBoxLayout(holder)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.setSpacing(4)
    label = QLabel(text)
    label.setObjectName("CardSub")
    layout.addWidget(label)
    layout.addWidget(widget)
    return holder


def _stretch() -> QWidget:
    widget = QWidget()
    widget.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
    return widget
