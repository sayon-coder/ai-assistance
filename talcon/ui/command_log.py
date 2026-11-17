"""Session-only command log for the TALCON Control Center.

This is the diagnostic the voice assistant needed: what the microphone heard,
what TALCON interpreted it as, which action ran, how long it took and how
confident Vosk was.  It is deliberately in memory only — nothing is written to
disk, and a restart starts empty.
"""
from __future__ import annotations

import logging

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QHBoxLayout, QLabel, QListWidget, QListWidgetItem, QPushButton,
    QTextBrowser, QVBoxLayout, QWidget,
)

from .components import Card, Heading, footnote

log = logging.getLogger("talcon.ui.command_log")

#: Friendly wording for the action codes emitted by the command router.
_ACTION_LABELS = {
    "app_opened": "Opened the application",
    "app_closed": "Closed the application",
    "app_not_running": "That application was not running",
    "app_missing": "The application is no longer installed",
    "app_failed": "Could not open the application",
    "running_list": "Listed running applications",
    "running_yes": "Yes, it is running",
    "running_no": "No, it is not running",
    "running_empty": "No user applications are running",
    "volume_set": "Set the voice volume",
    "volume_up": "Turned the volume up",
    "volume_down": "Turned the volume down",
    "rate_up": "Spoke faster",
    "rate_down": "Spoke slower",
    "rate_reset": "Returned to normal speed",
    "silenced": "Stopped the current answer",
    "repeated": "Repeated the last answer",
    "nothing_to_repeat": "Nothing had been said yet",
    "nothing_to_reopen": "Nothing had been opened yet",
    "greeting": "Greeted you",
    "wellbeing": "Reported its status",
    "introduced": "Introduced itself",
    "introduce": "Introduced itself",
    "time": "Read the current time",
    "status": "Reported the active mode",
    "help": "Listed its commands",
    "thanks": "Answered politely",
    "mute": "Muted speech",
    "unmute": "Unmuted speech",
}


def action_label(action: str) -> str:
    """Turn an internal action code into readable text for the log."""
    code = str(action or "").strip()
    if not code:
        return "unknown action"
    if code in _ACTION_LABELS:
        return _ACTION_LABELS[code]
    if code.startswith("mode__"):
        return f"Activated {code[6:].replace('_', ' ')} mode"
    return code.replace("_", " ").strip().capitalize()


class CommandLogPage(QWidget):
    """Chronological list of commands this session actually executed."""

    def __init__(self, runtime, parent=None):
        super().__init__(parent)
        self.runtime = runtime
        self._entries: list[dict] = []

        layout = QVBoxLayout(self)
        layout.setContentsMargins(26, 22, 26, 22)
        layout.setSpacing(14)
        layout.addWidget(Heading(
            "Commands",
            "What TALCON heard, how it interpreted the words, what it actually "
            "did and how long that took.  This makes a misunderstood command "
            "obvious instead of mysterious.",
        ))

        card = Card()
        toolbar = QHBoxLayout()
        self.count_label = QLabel("No commands yet.")
        self.count_label.setObjectName("Muted")
        toolbar.addWidget(self.count_label)
        toolbar.addStretch(1)
        self.clear_button = QPushButton("Clear log")
        self.clear_button.setObjectName("Danger")
        self.clear_button.setToolTip("Delete this session's command log.")
        self.clear_button.setEnabled(False)
        toolbar.addWidget(self.clear_button)
        card.add_layout(toolbar)

        split = QHBoxLayout()
        split.setSpacing(12)
        self.entry_list = QListWidget()
        self.entry_list.setObjectName("CommandList")
        self.entry_list.setFixedWidth(360)
        self.entry_list.setWordWrap(True)
        self.entry_list.setUniformItemSizes(False)
        split.addWidget(self.entry_list)

        self.detail = QTextBrowser()
        self.detail.setObjectName("CommandDetail")
        self.detail.setReadOnly(True)
        self.detail.setOpenExternalLinks(False)
        self.detail.setPlaceholderText("Select a command to inspect it.")
        split.addWidget(self.detail, 1)
        card.add_layout(split, 1)

        card.add(footnote(
            "Session memory only. No audio is stored, and the transcript shown "
            "here is the text that was matched, not a recording."
        ))
        layout.addWidget(card, 1)

        self.clear_button.clicked.connect(self._clear)
        self.entry_list.currentItemChanged.connect(self._show_selected)
        changed = getattr(getattr(runtime, "bridge", None), "command_log_changed", None)
        if changed is not None:
            try:
                changed.connect(self.refresh)
            except Exception:
                log.exception("Could not connect the command log signal")
        self.refresh()

    # -- data --------------------------------------------------------------
    def refresh(self, entries=None) -> None:
        if entries is None:
            getter = getattr(self.runtime, "command_log", None)
            try:
                entries = getter() if callable(getter) else []
            except Exception:
                log.exception("Could not read the command log")
                entries = []
        entries = [dict(item) for item in (entries or [])]

        ids = [item.get("id") for item in entries]
        if ids == [item.get("id") for item in self._entries]:
            return

        current_id = None
        current = self.entry_list.currentItem()
        if current is not None:
            current_id = current.data(Qt.ItemDataRole.UserRole)
        bar = self.entry_list.verticalScrollBar()
        was_at_bottom = bar.value() >= bar.maximum() - 4

        self._entries = entries
        self.entry_list.blockSignals(True)
        self.entry_list.clear()
        for entry in entries:
            when = str(entry.get("time") or "")
            phrase = str(entry.get("phrase") or entry.get("heard") or "")
            action = str(entry.get("action") or "")
            label = f"{when}  ·  {phrase}  →  {action_label(action)}"
            item = QListWidgetItem(label)
            item.setData(Qt.ItemDataRole.UserRole, entry.get("id"))
            heard = str(entry.get("heard") or "")
            if heard and heard != phrase:
                item.setToolTip(f"Heard: {heard}")
            self.entry_list.addItem(item)
        self.entry_list.blockSignals(False)

        if entries:
            self.count_label.setText(
                f"{len(entries)} command{'s' if len(entries) != 1 else ''} this session"
            )
        else:
            self.count_label.setText("No commands yet.")
            self.detail.setPlainText("")

        target = None
        if was_at_bottom and entries:
            target = self.entry_list.item(self.entry_list.count() - 1)
        elif current_id is not None:
            for row in range(self.entry_list.count()):
                candidate = self.entry_list.item(row)
                if candidate.data(Qt.ItemDataRole.UserRole) == current_id:
                    target = candidate
                    break
        if target is None and entries and current_id is None:
            target = self.entry_list.item(self.entry_list.count() - 1)
        if target is not None:
            self.entry_list.setCurrentItem(target)
            if was_at_bottom or current_id is None:
                self.entry_list.scrollToBottom()

        self.clear_button.setEnabled(bool(entries))

    def _show_selected(self, current, _previous=None) -> None:
        if current is None:
            return
        entry_id = current.data(Qt.ItemDataRole.UserRole)
        entry = next(
            (item for item in self._entries if item.get("id") == entry_id), None
        )
        if entry is None:
            return
        heard = str(entry.get("heard") or "").strip()
        phrase = str(entry.get("phrase") or "").strip()
        confidence = entry.get("confidence")
        confidence_text = (
            "not supplied" if confidence is None else f"{float(confidence):.2f}"
        )
        lines = [
            f"Time: {entry.get('time') or 'this session'}",
            f"What I heard: {heard or '(microphone transcript unavailable)'}",
            f"Interpreted as: {phrase or '(no registered phrase)'}",
            f"Action: {action_label(entry.get('action'))} "
            f"({entry.get('action') or 'unknown'})",
            f"Execution time: {int(entry.get('ms') or 0)} ms",
            f"Recognition confidence: {confidence_text}",
        ]
        if heard and phrase and heard != phrase:
            lines.append("")
            lines.append(
                "The words did not match exactly, so TALCON used the "
                "interpretation above. If it picked the wrong command, add "
                "the exact wording as a command alias."
            )
        self.detail.setPlainText("\n".join(lines))

    # -- actions -----------------------------------------------------------
    def _clear(self) -> None:
        clearer = getattr(self.runtime, "clear_command_log", None)
        try:
            if callable(clearer):
                clearer()
        except Exception:
            log.exception("Could not clear the command log")
            return
        self.refresh([])
