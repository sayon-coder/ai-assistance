"""Session-only conversation history page for the TALCON control center.

The entries live in :class:`talcon.runtime.TalconRuntime` memory and are never
written anywhere.  This page only renders that list: a chronological list of
spoken responses, a detail pane showing the selected response in full, and a
guarded clear action.
"""
from __future__ import annotations

import logging

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QHBoxLayout, QLabel, QListWidget, QListWidgetItem, QMessageBox,
    QPushButton, QTextBrowser, QVBoxLayout, QWidget,
)

from .components import Card, Heading, footnote

log = logging.getLogger("talcon.ui.history")


class HistoryPage(QWidget):
    """Chronological, in-memory list of responses from this run."""

    def __init__(self, runtime, parent=None):
        super().__init__(parent)
        self.runtime = runtime
        self._entries: list[dict] = []

        layout = QVBoxLayout(self)
        layout.setContentsMargins(26, 22, 26, 22)
        layout.setSpacing(14)
        layout.addWidget(Heading(
            "History",
            "Every response TALCON has spoken in this session, newest at the bottom. "
            "It is kept in memory only and disappears when TALCON exits.",
        ))

        card = Card()
        toolbar = QHBoxLayout()
        self.count_label = QLabel("No responses yet.")
        self.count_label.setObjectName("Muted")
        toolbar.addWidget(self.count_label)
        toolbar.addStretch(1)
        self.clear_button = QPushButton("Clear history")
        self.clear_button.setObjectName("Danger")
        self.clear_button.setToolTip("Delete this session's history. Speech keeps playing.")
        self.clear_button.setEnabled(False)
        toolbar.addWidget(self.clear_button)
        card.add_layout(toolbar)

        split = QHBoxLayout()
        split.setSpacing(12)
        self.entry_list = QListWidget()
        self.entry_list.setObjectName("HistoryList")
        self.entry_list.setFixedWidth(330)
        self.entry_list.setWordWrap(True)
        self.entry_list.setUniformItemSizes(False)
        split.addWidget(self.entry_list)

        self.detail = QTextBrowser()
        self.detail.setObjectName("HistoryDetail")
        self.detail.setReadOnly(True)
        self.detail.setOpenExternalLinks(False)
        self.detail.setPlaceholderText("Select a response to read it in full.")
        split.addWidget(self.detail, 1)
        card.add_layout(split, 1)

        card.add(footnote(
            "Session memory only — nothing is saved to a file, database or "
            "browser storage, so a restart starts with an empty history."
        ))
        layout.addWidget(card, 1)

        self.clear_button.clicked.connect(self._confirm_clear)
        self.entry_list.currentItemChanged.connect(self._show_selected)
        self.refresh()

    # -- data --------------------------------------------------------------
    def refresh(self, entries=None) -> None:
        """Rebuild from the runtime snapshot (or an explicit emitted list)."""
        if entries is None:
            getter = getattr(self.runtime, "conversation_history", None)
            try:
                entries = getter() if callable(getter) else []
            except Exception:
                log.exception("Could not read the session history")
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
            request = str(entry.get("request") or "").strip()
            when = str(entry.get("time") or "").strip()
            label = f"{when}  ·  {request}" if request else f"{when}  ·  TALCON"
            item = QListWidgetItem(label)
            item.setData(Qt.ItemDataRole.UserRole, entry.get("id"))
            preview = " ".join(str(entry.get("text") or "").split())
            if len(preview) > 160:
                preview = preview[:160].rstrip() + "…"
            item.setToolTip(preview)
            self.entry_list.addItem(item)
        self.entry_list.blockSignals(False)

        if entries:
            self.count_label.setText(
                f"{len(entries)} response{'s' if len(entries) != 1 else ''} "
                "this session"
            )
        else:
            self.count_label.setText("No responses yet.")
            self.detail.setPlainText("")

        target = None
        if was_at_bottom and entries:
            # The reader is following the conversation: keep the newest
            # response selected, at the bottom.
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
        elif entries and current_id is None:
            self.entry_list.setCurrentRow(0)

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
        when = str(entry.get("time") or "").strip()
        request = str(entry.get("request") or "").strip()
        header = []
        if request:
            header.append(f'You said: "{request}"')
        header.append(when or "This session")
        self.detail.setPlainText("\n".join(header) + "\n\n" + str(entry.get("text") or ""))

    # -- actions -----------------------------------------------------------
    def _confirm_clear(self) -> None:
        if not self._entries:
            return
        answer = QMessageBox.question(
            self,
            "Clear history",
            "Clear every response from this session?\n\n"
            "This cannot be undone, and TALCON will keep speaking normally.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        clearer = getattr(self.runtime, "clear_conversation_history", None)
        try:
            if callable(clearer):
                clearer()
        except Exception:
            log.exception("Could not clear the session history")
            return
        self.refresh([])
