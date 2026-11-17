"""Response preview, expanded reader and session-only conversation history."""
from __future__ import annotations

import threading
import time
from types import SimpleNamespace

import pytest
from PySide6.QtCore import QRect, Qt
from PySide6.QtGui import QGuiApplication
from PySide6.QtTest import QTest

import talcon.runtime as runtime_module
from talcon.runtime import TalconRuntime
from talcon.speech import SpeechOutput
from talcon.ui.history import HistoryPage
from talcon.ui.response_preview import (
    ExpandedResponse, ResponsePreview, preview_lines,
)
from tests.test_ui import make_window


# ---------------------------------------------------------------- helpers
class FakeSignal:
    def __init__(self):
        self.events = []

    def emit(self, *args):
        self.events.append(args)


class RecordingBackend:
    def __init__(self):
        self.said = []

    def ensure_started(self, timeout=25.0):
        return True

    def say(self, text):
        self.said.append(text)
        return True

    def voices(self, timeout=15.0):
        return []

    def set_voice(self, *_args, **_kwargs):
        pass

    def set_rate(self, *_args, **_kwargs):
        pass

    def set_volume(self, *_args, **_kwargs):
        pass

    def stop_speaking(self):
        pass

    def stop(self):
        pass


def wait_for(predicate, timeout=3.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.02)
    return False


def memory_runtime():
    runtime = TalconRuntime.__new__(TalconRuntime)
    runtime._conversation = []
    runtime._conversation_lock = threading.Lock()
    runtime._next_response_id = 1
    runtime._pending_request = None
    runtime.bridge = SimpleNamespace(
        response_started=FakeSignal(), conversation_changed=FakeSignal()
    )
    return runtime


ENTRY = {
    "id": 3,
    "time": "10:42:11",
    "request": "what time is it",
    "text": "It is 10:42 AM, sir.",
}


# ---------------------------------------------------------------- speech
def test_utterance_callback_receives_the_complete_text():
    seen = []
    backend = RecordingBackend()
    output = SpeechOutput(
        enabled=True, backend=backend, on_utterance=seen.append
    )
    try:
        long_text = "A" * 900
        output.say(long_text)
        assert wait_for(lambda: seen)
        assert seen == [long_text]
        assert wait_for(lambda: backend.said)
        # The engine still gets its bounded form, so playback is unchanged.
        assert len(backend.said[0]) == 700
    finally:
        output.stop()


def test_short_utterance_is_unchanged_for_engine_and_callback():
    seen = []
    backend = RecordingBackend()
    output = SpeechOutput(
        enabled=True, backend=backend, on_utterance=seen.append
    )
    try:
        output.say("Study mode activated, sir.")
        assert wait_for(lambda: backend.said)
        assert backend.said == ["Study mode activated, sir."]
        assert wait_for(lambda: seen)
        assert seen == ["Study mode activated, sir."]
    finally:
        output.stop()


# ---------------------------------------------------------------- runtime
def test_one_utterance_creates_exactly_one_history_entry():
    runtime = memory_runtime()
    runtime._on_utterance("Only one entry, sir.")
    entries = runtime.conversation_history()
    assert len(entries) == 1
    assert entries[0]["text"] == "Only one entry, sir."
    assert entries[0]["id"] == 1
    assert runtime.bridge.response_started.events[-1][0]["text"] == "Only one entry, sir."
    assert len(runtime.bridge.conversation_changed.events[-1][0]) == 1


def test_response_is_paired_with_the_recent_request_only_once():
    runtime = memory_runtime()
    runtime._pending_request = ("what time is it", time.monotonic())
    runtime._on_utterance("It is 10:42 AM, sir.")
    runtime._on_utterance("A background announcement.")
    entries = runtime.conversation_history()
    assert entries[0]["request"] == "what time is it"
    assert entries[1]["request"] == ""
    assert [entry["id"] for entry in entries] == [1, 2]


def test_stale_request_is_not_attached_to_a_later_response():
    runtime = memory_runtime()
    runtime._pending_request = ("old question", time.monotonic() - 60)
    runtime._on_utterance("A much later announcement.")
    assert runtime.conversation_history()[0]["request"] == ""


def test_clearing_history_keeps_speech_untouched():
    runtime = memory_runtime()
    runtime._on_utterance("First response.")
    runtime._on_utterance("Second response.")
    runtime.clear_conversation_history()
    assert runtime.conversation_history() == []
    assert runtime.bridge.conversation_changed.events[-1] == ([],)


def test_history_is_never_persisted_and_restart_is_empty(monkeypatch, qapp):
    persisted = []

    class FakeSpeech:
        def __init__(self, **_kwargs):
            pass

        def stop(self):
            pass

    monkeypatch.setattr(runtime_module, "SpeechOutput", FakeSpeech)
    monkeypatch.setattr(
        runtime_module, "persist_settings",
        lambda settings: persisted.append(dict(settings)) or dict(settings),
    )

    first = TalconRuntime()
    try:
        before = len(persisted)
        first._on_utterance("This response must stay in RAM.")
        assert first.conversation_history()[0]["text"] == "This response must stay in RAM."
        assert len(persisted) == before, "history must not trigger a settings write"
    finally:
        first.shutdown()

    second = TalconRuntime()
    try:
        assert second.conversation_history() == [], "a restart starts empty"
    finally:
        second.shutdown()


# ---------------------------------------------------------------- preview
def test_preview_lines_wraps_and_truncates_with_an_ellipsis():
    measure = lambda text: len(text) * 7  # noqa: E731 - tiny deterministic meter
    text = "one two three four five six seven eight nine ten eleven twelve"
    lines = preview_lines(text, measure, 100, 2).split("\n")
    assert len(lines) <= 2
    assert lines[-1].endswith("…")
    assert text.startswith(lines[0])


def test_short_text_fits_without_an_ellipsis():
    lines = preview_lines("It is 10:42 AM, sir.", lambda t: len(t) * 7, 300, 2)
    assert lines == "It is 10:42 AM, sir."


def test_preview_shows_two_lines_but_keeps_the_complete_entry(qapp):
    opened = []
    preview = ResponsePreview(on_expand=opened.append)
    try:
        preview.set_anchor_provider(lambda: QRect(400, 40, 66, 66))
        long_text = "word " * 120
        preview.show_response({"id": 1, "time": "10:00", "request": "", "text": long_text})
        assert preview.isVisible()
        shown = preview.label.text()
        assert "\n" in shown and shown.count("\n") <= 1
        assert "…" in shown
        assert preview.current_entry()["text"] == long_text
        assert preview.windowFlags() & Qt.WindowType.WindowDoesNotAcceptFocus

        preview.dismiss()
        assert not preview.isVisible()
    finally:
        preview.close()


def test_preview_auto_hides_five_seconds_after_speaking(qapp):
    import time as _time

    preview = ResponsePreview()
    try:
        preview.set_anchor_provider(lambda: QRect(300, 40, 66, 66))
        preview.show_response(ENTRY)
        assert preview.isVisible()

        preview.note_speaking(True)
        preview._shown_at = _time.monotonic() - 10
        preview._sync_visibility()
        assert preview.isVisible(), "never hide while TALCON is still speaking"

        preview.note_speaking(False)
        preview._sync_visibility()
        assert not preview.isVisible(), "hide five seconds after the response"

        preview.show_response(ENTRY)
        preview._shown_at = _time.monotonic() - 4
        preview.note_speaking(False)
        preview._sync_visibility()
        assert preview.isVisible(), "still visible inside the five-second window"
    finally:
        preview.close()


def test_clicking_the_preview_opens_the_full_response(qapp):
    opened = []
    preview = ResponsePreview(on_expand=opened.append)
    try:
        preview.set_anchor_provider(lambda: QRect(300, 40, 66, 66))
        preview.show_response(ENTRY)
        assert preview.isVisible()
        QTest.mouseClick(preview, Qt.MouseButton.LeftButton)
        assert opened and opened[-1]["text"] == ENTRY["text"]
    finally:
        preview.close()


def test_preview_hides_when_no_avatar_is_visible(qapp):
    preview = ResponsePreview()
    try:
        preview.set_anchor_provider(lambda: None)
        preview.show_response(ENTRY)
        assert not preview.isVisible()
        preview.set_anchor_provider(lambda: QRect(100, 10, 66, 66))
        preview._sync_visibility()
        assert preview.isVisible()
    finally:
        preview.close()


# ---------------------------------------------------------------- expanded
def test_expanded_panel_shows_full_text_and_copies_it(qapp):
    panel = ExpandedResponse()
    try:
        long_text = ("Full answer sentence. " * 120).strip()
        panel.show_entry({**ENTRY, "text": long_text}, anchor=QRect(300, 100, 340, 50))
        assert panel.isVisible()
        assert panel.body.toPlainText() == long_text
        panel.copy_button.click()
        assert QGuiApplication.clipboard().text() == long_text
        assert "copied" in panel.copy_status.text().lower()
        panel.close_button.click()
        assert not panel.isVisible()
    finally:
        panel.close()


# ---------------------------------------------------------------- history UI
def test_history_page_lists_responses_and_shows_full_text(qapp):
    runtime, face, window = make_window(qapp)
    try:
        window.show()
        qapp.processEvents()
        window.show_page("history")
        page = window.history_page
        runtime.add_response("Short reply, sir.", "what time is it", "10:00:01")
        long_text = "This is the complete response. " * 90
        runtime.add_response(long_text, "tell me a long story", "10:00:02")

        assert page.entry_list.count() == 2
        assert page.count_label.text().startswith("2 responses")
        # Newest entry is last (bottom) and selected by default.
        assert page.entry_list.item(1).data(Qt.ItemDataRole.UserRole) == 2
        assert page.entry_list.currentRow() == 1
        assert long_text in page.detail.toPlainText()
        assert "tell me a long story" in page.detail.toPlainText()

        # The detail pane can scroll long answers.
        assert page.detail.verticalScrollBar().maximum() > 0

        # Clicking the first entry shows its complete text.
        page.entry_list.setCurrentRow(0)
        assert page.detail.toPlainText().strip().endswith("Short reply, sir.")
    finally:
        window.close()
        face.close()


def test_history_survives_hiding_restoring_and_clears_with_confirmation(
    qapp, monkeypatch
):
    runtime, face, window = make_window(qapp)
    try:
        runtime.add_response("Remember me, sir.", "hello", "11:00:00")
        window.hide()
        qapp.processEvents()
        window.show()
        qapp.processEvents()
        assert window.history_page.entry_list.count() == 1

        import talcon.ui.history as history_module

        answers = iter([
            history_module.QMessageBox.StandardButton.No,
            history_module.QMessageBox.StandardButton.Yes,
        ])
        monkeypatch.setattr(
            history_module.QMessageBox, "question",
            lambda *args, **kwargs: next(answers),
        )
        window.history_page.clear_button.click()
        assert window.history_page.entry_list.count() == 1, "No must keep history"

        spoken_before = list(runtime.history)
        window.history_page.clear_button.click()
        assert runtime.history == []
        assert spoken_before  # clearing only removed the in-memory list
        assert window.history_page.entry_list.count() == 0
        assert window.history_page.count_label.text() == "No responses yet."
    finally:
        window.close()
        face.close()


def test_history_page_created_later_still_shows_the_session(qapp):
    runtime, face, window = make_window(qapp)
    try:
        runtime.add_response("Already spoken, sir.", "hello", "09:00:00")
        late_page = HistoryPage(runtime)
        try:
            assert late_page.entry_list.count() == 1
            assert "Already spoken, sir." in late_page.detail.toPlainText()
        finally:
            late_page.close()
            late_page.deleteLater()
    finally:
        window.close()
        face.close()
