"""Editor for user-defined voice commands and ordered automation sequences."""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QComboBox, QDialog, QDoubleSpinBox, QFileDialog, QFormLayout, QHBoxLayout,
    QLabel, QLineEdit, QListWidget, QListWidgetItem, QMessageBox, QPlainTextEdit,
    QPushButton, QVBoxLayout, QWidget,
)

from ..custom import ACTION_LABELS, COMMAND_ACTIONS, MODE_NAMES, STEP_ACTIONS
from . import theme
from .components import Card, Heading, Switch, footnote, muted


def _triggers_text(entry: dict) -> str:
    return "\n".join(entry.get("triggers", []))


def _parse_triggers(text: str) -> list[str]:
    return [line.strip() for line in str(text or "").splitlines() if line.strip()]


def _step_summary(step: dict, index: int) -> str:
    action = step.get("action", "")
    params = step.get("params", {}) if isinstance(step.get("params"), dict) else {}
    delay = float(step.get("delay", 0.0) or 0.0)
    if action == "open_app":
        detail = Path(str(params.get("path", ""))).name or "application"
    elif action == "close_app":
        detail = Path(str(params.get("path", ""))).name or "application"
    elif action == "open_url":
        detail = str(params.get("url", ""))
    elif action == "activate_mode":
        detail = f"{str(params.get('mode', '')).title()} mode"
    else:
        detail = f"{float(params.get('seconds', 0) or 0):g} s"
    suffix = f"  · delay {delay:g} s" if delay else ""
    return f"{index}. {ACTION_LABELS.get(action, action)} — {detail}{suffix}"


class _ParamEditor:
    """Builds the parameter widgets for one action inside a dialog."""

    def __init__(self, dialog: QDialog, layout: QVBoxLayout):
        self.dialog = dialog
        self.layout = layout
        self.widgets: dict[str, QWidget] = {}

    def clear(self) -> None:
        while self.layout.count():
            item = self.layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
        self.widgets.clear()

    def build(self, action: str, params: dict) -> None:
        self.clear()
        params = params or {}
        if action in {"open_app", "close_app"}:
            path_row = QHBoxLayout()
            path = QLineEdit(str(params.get("path", "")))
            path.setPlaceholderText(r"C:\Program Files\App\app.exe")
            browse = QPushButton("Browse…")
            browse.clicked.connect(lambda: self._browse(path))
            path_row.addWidget(path, 1)
            path_row.addWidget(browse)
            holder = QWidget()
            holder.setLayout(path_row)
            self.layout.addWidget(holder)
            self.widgets["path"] = path
            if action == "open_app":
                args = QPlainTextEdit()
                args.setPlaceholderText("Optional arguments, one per line")
                args.setPlainText("\n".join(params.get("args", [])))
                args.setFixedHeight(64)
                self.layout.addWidget(QLabel("Arguments (optional)"))
                self.layout.addWidget(args)
                self.widgets["args"] = args
        elif action == "open_url":
            url = QLineEdit(str(params.get("url", "")))
            url.setPlaceholderText("https://example.com")
            self.layout.addWidget(QLabel("Website address"))
            self.layout.addWidget(url)
            self.widgets["url"] = url
        elif action == "activate_mode":
            mode = QComboBox()
            for name in MODE_NAMES:
                mode.addItem(f"{name.title()} mode", name)
            current = str(params.get("mode", "study"))
            index = mode.findData(current)
            if index >= 0:
                mode.setCurrentIndex(index)
            self.layout.addWidget(QLabel("Mode"))
            self.layout.addWidget(mode)
            self.widgets["mode"] = mode
        elif action == "wait":
            seconds = QDoubleSpinBox()
            seconds.setRange(0.0, 30.0)
            seconds.setSingleStep(0.5)
            seconds.setValue(float(params.get("seconds", 1.0) or 1.0))
            seconds.setSuffix(" s")
            self.layout.addWidget(QLabel("Wait duration"))
            self.layout.addWidget(seconds)
            self.widgets["seconds"] = seconds

    def _browse(self, target: QLineEdit) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self.dialog, "Choose an application", str(Path.home()),
            "Programs and shortcuts (*.exe *.lnk *.bat *.cmd);;All files (*.*)",
        )
        if path:
            target.setText(path)

    def params(self) -> dict:
        if "path" in self.widgets:
            result = {"path": self.widgets["path"].text().strip()}
            if "args" in self.widgets:
                result["args"] = _parse_triggers(self.widgets["args"].toPlainText())
            return result
        if "url" in self.widgets:
            return {"url": self.widgets["url"].text().strip()}
        if "mode" in self.widgets:
            return {"mode": self.widgets["mode"].currentData()}
        if "seconds" in self.widgets:
            return {"seconds": float(self.widgets["seconds"].value())}
        return {}


class _BaseDialog(QDialog):
    def __init__(self, runtime, title: str, parent=None):
        super().__init__(parent)
        self.runtime = runtime
        self.setWindowTitle(title)
        self.setModal(True)
        self.setObjectName("Root")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setStyleSheet(theme.STYLESHEET)
        self.setMinimumWidth(560)


class CommandDialog(_BaseDialog):
    def __init__(self, runtime, entry: dict | None = None, parent=None):
        super().__init__(runtime, "Custom voice command", parent)
        self.entry = dict(entry or {})
        self.resize(580, 560)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(22, 20, 22, 18)
        layout.setSpacing(12)
        heading = QLabel("Custom voice command")
        heading.setObjectName("PageTitle")
        layout.addWidget(heading)
        layout.addWidget(muted(
            "A trigger phrase runs exactly one supported action. Built-in command "
            "phrases and other custom triggers cannot be reused."
        ))

        form = QFormLayout()
        form.setSpacing(9)
        self.name = QLineEdit(str(self.entry.get("name", "")))
        self.name.setPlaceholderText("Open Visual Studio Code")
        form.addRow("Name", self.name)
        layout.addLayout(form)

        layout.addWidget(QLabel("Spoken trigger phrases (one per line)"))
        self.triggers = QPlainTextEdit(_triggers_text(self.entry))
        self.triggers.setPlaceholderText("Open Visual Studio Code\nStart VS Code\nLaunch my code editor")
        self.triggers.setFixedHeight(110)
        layout.addWidget(self.triggers)

        action_row = QHBoxLayout()
        action_row.addWidget(QLabel("Action"))
        self.action = QComboBox()
        for key in COMMAND_ACTIONS:
            self.action.addItem(ACTION_LABELS[key], key)
        index = self.action.findData(self.entry.get("action", "open_app"))
        if index >= 0:
            self.action.setCurrentIndex(index)
        action_row.addWidget(self.action, 1)
        layout.addLayout(action_row)

        self.param_box = QVBoxLayout()
        self.param_box.setSpacing(8)
        layout.addLayout(self.param_box)
        self.params = _ParamEditor(self, self.param_box)
        self.action.currentIndexChanged.connect(self._rebuild)
        self._rebuild()

        enabled_row = QHBoxLayout()
        enabled_row.addWidget(QLabel("Enabled"))
        enabled_row.addStretch(1)
        self.enabled = Switch()
        self.enabled.set_checked_silently(bool(self.entry.get("enabled", True)))
        enabled_row.addWidget(self.enabled)
        layout.addLayout(enabled_row)
        layout.addStretch(1)

        buttons = QHBoxLayout()
        buttons.addStretch(1)
        cancel = QPushButton("Cancel")
        cancel.clicked.connect(self.reject)
        save = QPushButton("Save command")
        save.setObjectName("Primary")
        save.clicked.connect(self._save)
        buttons.addWidget(cancel)
        buttons.addWidget(save)
        layout.addLayout(buttons)

    def _rebuild(self) -> None:
        self.params.build(self.action.currentData(), self.entry.get("params", {}))

    def _save(self) -> None:
        entry = {
            "name": self.name.text().strip(),
            "enabled": self.enabled.isChecked(),
            "triggers": _parse_triggers(self.triggers.toPlainText()),
            "action": self.action.currentData(),
            "params": self.params.params(),
        }
        try:
            self.runtime.save_custom_command(entry, self.entry.get("id"))
        except ValueError as exc:
            QMessageBox.warning(self, "Cannot save command", str(exc))
            return
        except Exception as exc:
            QMessageBox.critical(self, "Cannot save command", str(exc))
            return
        self.accept()


class StepDialog(_BaseDialog):
    def __init__(self, runtime, step: dict | None = None, parent=None):
        super().__init__(runtime, "Automation step", parent)
        self.step = dict(step or {})
        self.result_step: dict | None = None
        self.resize(520, 430)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(22, 20, 22, 18)
        layout.setSpacing(12)
        heading = QLabel("Automation step")
        heading.setObjectName("PageTitle")
        layout.addWidget(heading)

        action_row = QHBoxLayout()
        action_row.addWidget(QLabel("Action"))
        self.action = QComboBox()
        for key in STEP_ACTIONS:
            self.action.addItem(ACTION_LABELS[key], key)
        index = self.action.findData(self.step.get("action", "open_app"))
        if index >= 0:
            self.action.setCurrentIndex(index)
        action_row.addWidget(self.action, 1)
        layout.addLayout(action_row)

        delay_row = QHBoxLayout()
        delay_row.addWidget(QLabel("Delay before this step"))
        self.delay = QDoubleSpinBox()
        self.delay.setRange(0.0, 10.0)
        self.delay.setSingleStep(0.5)
        self.delay.setValue(float(self.step.get("delay", 0.0) or 0.0))
        self.delay.setSuffix(" s")
        delay_row.addWidget(self.delay)
        delay_row.addStretch(1)
        layout.addLayout(delay_row)

        self.param_box = QVBoxLayout()
        self.param_box.setSpacing(8)
        layout.addLayout(self.param_box)
        self.params = _ParamEditor(self, self.param_box)
        self.action.currentIndexChanged.connect(self._rebuild)
        self._rebuild()
        layout.addStretch(1)

        buttons = QHBoxLayout()
        buttons.addStretch(1)
        cancel = QPushButton("Cancel")
        cancel.clicked.connect(self.reject)
        save = QPushButton("Save step")
        save.setObjectName("Primary")
        save.clicked.connect(self._save)
        buttons.addWidget(cancel)
        buttons.addWidget(save)
        layout.addLayout(buttons)

    def _rebuild(self) -> None:
        self.params.build(self.action.currentData(), self.step.get("params", {}))

    def _save(self) -> None:
        action = self.action.currentData()
        params = self.params.params()
        if action in {"open_app", "close_app"} and not params.get("path"):
            QMessageBox.warning(self, "Cannot save step", "Choose an application for this step.")
            return
        if action == "open_url" and not params.get("url"):
            QMessageBox.warning(self, "Cannot save step", "Enter a website address.")
            return
        self.result_step = {
            "action": action,
            "params": params,
            "delay": float(self.delay.value()),
        }
        self.accept()


class AutomationDialog(_BaseDialog):
    def __init__(self, runtime, entry: dict | None = None, parent=None):
        super().__init__(runtime, "Automation sequence", parent)
        self.entry = dict(entry or {})
        self.steps = [dict(step) for step in self.entry.get("steps", []) if isinstance(step, dict)]
        self.resize(640, 660)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(22, 20, 22, 18)
        layout.setSpacing(12)
        heading = QLabel("Automation sequence")
        heading.setObjectName("PageTitle")
        layout.addWidget(heading)
        layout.addWidget(muted(
            "One spoken trigger runs the steps below in order. Closing an application "
            "only affects programs NOVA started itself; nothing is force-terminated."
        ))

        form = QFormLayout()
        form.setSpacing(9)
        self.name = QLineEdit(str(self.entry.get("name", "")))
        self.name.setPlaceholderText("Start my study session")
        form.addRow("Name", self.name)
        layout.addLayout(form)

        layout.addWidget(QLabel("Spoken trigger phrases (one per line)"))
        self.triggers = QPlainTextEdit(_triggers_text(self.entry))
        self.triggers.setPlaceholderText("Prepare my study session\nStart my study setup")
        self.triggers.setFixedHeight(90)
        layout.addWidget(self.triggers)

        layout.addWidget(QLabel("Ordered steps"))
        self.step_list = QListWidget()
        self.step_list.setMinimumHeight(150)
        layout.addWidget(self.step_list)

        step_buttons = QHBoxLayout()
        add = QPushButton("Add step…")
        add.setObjectName("Primary")
        add.clicked.connect(self._add_step)
        edit = QPushButton("Edit")
        edit.clicked.connect(self._edit_step)
        remove = QPushButton("Remove")
        remove.clicked.connect(self._remove_step)
        up = QPushButton("Move up")
        up.clicked.connect(lambda: self._move(-1))
        down = QPushButton("Move down")
        down.clicked.connect(lambda: self._move(1))
        for widget in (add, edit, remove, up, down):
            step_buttons.addWidget(widget)
        step_buttons.addStretch(1)
        layout.addLayout(step_buttons)

        enabled_row = QHBoxLayout()
        enabled_row.addWidget(QLabel("Enabled"))
        enabled_row.addStretch(1)
        self.enabled = Switch()
        self.enabled.set_checked_silently(bool(self.entry.get("enabled", True)))
        enabled_row.addWidget(self.enabled)
        layout.addLayout(enabled_row)
        layout.addStretch(1)

        buttons = QHBoxLayout()
        buttons.addStretch(1)
        cancel = QPushButton("Cancel")
        cancel.clicked.connect(self.reject)
        save = QPushButton("Save automation")
        save.setObjectName("Primary")
        save.clicked.connect(self._save)
        buttons.addWidget(cancel)
        buttons.addWidget(save)
        layout.addLayout(buttons)
        self._refresh_steps()

    def _refresh_steps(self, select: int | None = None) -> None:
        self.step_list.clear()
        for index, step in enumerate(self.steps):
            item = QListWidgetItem(_step_summary(step, index + 1))
            self.step_list.addItem(item)
        if select is not None and 0 <= select < self.step_list.count():
            self.step_list.setCurrentRow(select)

    def _add_step(self) -> None:
        dialog = StepDialog(self.runtime, parent=self)
        if dialog.exec() and dialog.result_step:
            self.steps.append(dialog.result_step)
            self._refresh_steps(len(self.steps) - 1)

    def _edit_step(self) -> None:
        row = self.step_list.currentRow()
        if row < 0:
            return
        dialog = StepDialog(self.runtime, self.steps[row], parent=self)
        if dialog.exec() and dialog.result_step:
            self.steps[row] = dialog.result_step
            self._refresh_steps(row)

    def _remove_step(self) -> None:
        row = self.step_list.currentRow()
        if row < 0:
            return
        self.steps.pop(row)
        self._refresh_steps(min(row, len(self.steps) - 1))

    def _move(self, offset: int) -> None:
        row = self.step_list.currentRow()
        target = row + offset
        if row < 0 or target < 0 or target >= len(self.steps):
            return
        self.steps[row], self.steps[target] = self.steps[target], self.steps[row]
        self._refresh_steps(target)

    def _save(self) -> None:
        entry = {
            "name": self.name.text().strip(),
            "enabled": self.enabled.isChecked(),
            "triggers": _parse_triggers(self.triggers.toPlainText()),
            "steps": self.steps,
        }
        try:
            self.runtime.save_automation(entry, self.entry.get("id"))
        except ValueError as exc:
            QMessageBox.warning(self, "Cannot save automation", str(exc))
            return
        except Exception as exc:
            QMessageBox.critical(self, "Cannot save automation", str(exc))
            return
        self.accept()


class CustomEditor(QWidget):
    """The Custom Commands & Automations section of the Control Center."""

    def __init__(self, runtime, parent=None):
        super().__init__(parent)
        self.runtime = runtime
        self.setObjectName("Page")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(14)
        layout.addWidget(Heading(
            "Custom Commands & Automations",
            "Create your own offline voice actions without editing Python. "
            "No arbitrary terminal or PowerShell commands are accepted.",
        ))

        commands = Card(
            "Custom voice commands",
            "One spoken phrase, one supported action. Built-in phrases are protected.",
        )
        self.command_list = QListWidget()
        self.command_list.setMinimumHeight(150)
        commands.add(self.command_list)
        command_buttons = QHBoxLayout()
        new_command = QPushButton("New command…")
        new_command.setObjectName("Primary")
        new_command.clicked.connect(self._new_command)
        edit_command = QPushButton("Edit")
        edit_command.clicked.connect(self._edit_command)
        delete_command = QPushButton("Delete")
        delete_command.clicked.connect(self._delete_command)
        toggle_command = QPushButton("Enable / disable")
        toggle_command.clicked.connect(self._toggle_command)
        for widget in (new_command, edit_command, delete_command, toggle_command):
            command_buttons.addWidget(widget)
        command_buttons.addStretch(1)
        commands.add_layout(command_buttons)
        layout.addWidget(commands)

        automations = Card(
            "Automation sequences",
            "Run several configured actions in order, with optional short delays.",
        )
        self.automation_list = QListWidget()
        self.automation_list.setMinimumHeight(150)
        automations.add(self.automation_list)
        automation_buttons = QHBoxLayout()
        new_automation = QPushButton("New automation…")
        new_automation.setObjectName("Primary")
        new_automation.clicked.connect(self._new_automation)
        edit_automation = QPushButton("Edit")
        edit_automation.clicked.connect(self._edit_automation)
        delete_automation = QPushButton("Delete")
        delete_automation.clicked.connect(self._delete_automation)
        toggle_automation = QPushButton("Enable / disable")
        toggle_automation.clicked.connect(self._toggle_automation)
        for widget in (new_automation, edit_automation, delete_automation, toggle_automation):
            automation_buttons.addWidget(widget)
        automation_buttons.addStretch(1)
        automations.add_layout(automation_buttons)
        layout.addWidget(automations)

        layout.addWidget(footnote(
            "Saved in config/custom_commands.json. Closing an application only affects "
            "programs NOVA launched during this session, and uses a graceful Windows close request."
        ))
        layout.addStretch(1)
        self.refresh()

    # -- data --------------------------------------------------------------
    def refresh(self) -> None:
        data = self.runtime.custom_snapshot()
        self.command_list.clear()
        for entry in data.get("commands", []):
            self.command_list.addItem(self._item_text(entry))
        self.automation_list.clear()
        for entry in data.get("automations", []):
            count = len(entry.get("steps", []))
            item = self._item_text(entry)
            item.setText(item.text() + f"  · {count} step{'s' if count != 1 else ''}")
            self.automation_list.addItem(item)

    @staticmethod
    def _item_text(entry: dict) -> QListWidgetItem:
        marker = "●" if entry.get("enabled", True) else "○"
        triggers = ", ".join(entry.get("triggers", [])[:3])
        item = QListWidgetItem(f"{marker}  {entry.get('name', 'Untitled')}  —  {triggers}")
        item.setData(Qt.ItemDataRole.UserRole, entry.get("id"))
        return item

    def _selected(self, widget: QListWidget) -> str | None:
        item = widget.currentItem()
        return item.data(Qt.ItemDataRole.UserRole) if item else None

    def _entry(self, kind: str, entry_id: str) -> dict | None:
        data = self.runtime.custom_snapshot()
        for entry in data.get(kind, []):
            if entry.get("id") == entry_id:
                return entry
        return None

    # -- commands ----------------------------------------------------------
    def _new_command(self) -> None:
        if CommandDialog(self.runtime, parent=self).exec():
            self.refresh()

    def _edit_command(self) -> None:
        entry_id = self._selected(self.command_list)
        if not entry_id:
            return
        entry = self._entry("commands", entry_id)
        if entry and CommandDialog(self.runtime, entry, parent=self).exec():
            self.refresh()

    def _delete_command(self) -> None:
        entry_id = self._selected(self.command_list)
        if not entry_id:
            return
        if QMessageBox.question(self, "Delete command", "Delete this custom command?") != QMessageBox.StandardButton.Yes:
            return
        self.runtime.delete_custom_command(entry_id)
        self.refresh()

    def _toggle_command(self) -> None:
        entry_id = self._selected(self.command_list)
        entry = self._entry("commands", entry_id) if entry_id else None
        if entry:
            self.runtime.set_custom_command_enabled(entry_id, not entry.get("enabled", True))
            self.refresh()

    # -- automations -------------------------------------------------------
    def _new_automation(self) -> None:
        if AutomationDialog(self.runtime, parent=self).exec():
            self.refresh()

    def _edit_automation(self) -> None:
        entry_id = self._selected(self.automation_list)
        if not entry_id:
            return
        entry = self._entry("automations", entry_id)
        if entry and AutomationDialog(self.runtime, entry, parent=self).exec():
            self.refresh()

    def _delete_automation(self) -> None:
        entry_id = self._selected(self.automation_list)
        if not entry_id:
            return
        if QMessageBox.question(self, "Delete automation", "Delete this automation?") != QMessageBox.StandardButton.Yes:
            return
        self.runtime.delete_automation(entry_id)
        self.refresh()

    def _toggle_automation(self) -> None:
        entry_id = self._selected(self.automation_list)
        entry = self._entry("automations", entry_id) if entry_id else None
        if entry:
            self.runtime.set_automation_enabled(entry_id, not entry.get("enabled", True))
            self.refresh()
