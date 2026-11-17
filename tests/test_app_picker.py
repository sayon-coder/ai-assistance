"""Application picker UI tests with mocked discovery data."""
from __future__ import annotations

from types import SimpleNamespace

from talcon.apps import AppEntry
from talcon.ui.app_picker import ApplicationPicker


class FakeLibrary:
    def __init__(self, entries, loaded=True, stale=False):
        self._entries = list(entries)
        self.is_loaded = loaded
        self._stale = stale
        self.refresh_calls = 0
        self.callback = None

    def cached_entries(self):
        return list(self._entries)

    def is_stale(self):
        return self._stale

    def refresh_async(self, callback=None):
        self.refresh_calls += 1
        self.callback = callback
        if callback is not None:
            callback(list(self._entries), "")


def entries():
    return [
        AppEntry(name="Editor", kind="shortcut", path="C:/Menu/Editor.lnk", source="Start Menu"),
        AppEntry(name="Chat", kind="shortcut", path="C:/Taskbar/Chat.lnk", source="Taskbar"),
        AppEntry(name="Calculator", kind="packaged_app", aumid="Calc!App", source="Installed"),
    ]


def make_picker(qapp, library, existing=None):
    runtime = SimpleNamespace(apps=library)
    picker = ApplicationPicker(runtime, existing=existing)
    return picker


def test_picker_lists_all_discovered_applications(qapp):
    picker = make_picker(qapp, FakeLibrary(entries()))
    try:
        assert picker.list.count() == 3
        assert picker.status.text().startswith("3 application")
    finally:
        picker.close()


def test_search_filters_by_name(qapp):
    picker = make_picker(qapp, FakeLibrary(entries()))
    try:
        picker.search.setText("calc")
        assert picker.list.count() == 1
        assert "Calculator" in picker.list.item(0).text()
    finally:
        picker.close()


def test_category_filters_by_source(qapp):
    picker = make_picker(qapp, FakeLibrary(entries()))
    try:
        picker.category.setCurrentIndex(1)  # Taskbar Pinned
        assert picker.list.count() == 1
        assert "Chat" in picker.list.item(0).text()
    finally:
        picker.close()


def test_duplicate_application_is_rejected(qapp):
    existing = [entries()[0].to_dict()]
    picker = make_picker(qapp, FakeLibrary(entries()), existing=existing)
    try:
        picker.list.setCurrentRow(0)
        picker._accept_selected()
        assert picker.selected_app is None
        assert "already" in picker.error.text().lower()
    finally:
        picker.close()


def test_same_shortcut_name_from_another_source_is_a_duplicate(qapp):
    existing = [entries()[0].to_dict()]  # Editor, Start Menu
    taskbar_editor = AppEntry(
        name="Editor", kind="shortcut", path="C:/Taskbar/Editor.lnk", source="Taskbar"
    )
    picker = make_picker(qapp, FakeLibrary([taskbar_editor]), existing=existing)
    try:
        picker.list.setCurrentRow(0)
        picker._accept_selected()
        assert picker.selected_app is None
        assert "already" in picker.error.text().lower()
    finally:
        picker.close()


def test_selecting_an_application_returns_its_descriptor(qapp):
    picker = make_picker(qapp, FakeLibrary(entries()))
    try:
        picker.list.setCurrentRow(2)
        picker._accept_selected()
        assert picker.selected_app == {
            "kind": "packaged_app", "name": "Calculator", "path": "",
            "args": [], "aumid": "Calc!App",
        }
    finally:
        picker.close()


def test_refresh_reloads_the_list(qapp):
    library = FakeLibrary(entries(), loaded=False)
    picker = make_picker(qapp, library)
    try:
        assert library.refresh_calls == 1
        assert picker.list.count() == 3
        picker._refresh()
        assert library.refresh_calls == 2
    finally:
        picker.close()


def test_manual_browse_builds_a_descriptor(qapp, monkeypatch, tmp_path):
    exe = tmp_path / "editor.exe"
    exe.write_text("stub", encoding="utf-8")
    picker = make_picker(qapp, FakeLibrary([]))
    try:
        monkeypatch.setattr(
            "talcon.ui.app_picker.QFileDialog.getOpenFileName",
            lambda *args, **kwargs: (str(exe), ""),
        )
        picker._browse_manually()
        assert picker.selected_app["kind"] == "executable"
        assert picker.selected_app["path"] == str(exe)
    finally:
        picker.close()
