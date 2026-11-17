"""Application discovery, migration and safe launching tests.

All discovery uses temporary folders and mocked Windows data; nothing on the
real computer is enumerated, installed or launched.
"""
from __future__ import annotations

from pathlib import Path

from talcon import apps
from talcon.apps import (
    AppEntry, ApplicationLibrary, app_key, deduplicate, discover_packaged_apps,
    discover_shortcuts, launch_app, normalize_app_entry,
)
from talcon.config import _normalize_mode


def touch(path: Path, text: str = "stub") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


# -- discovery --------------------------------------------------------------
def test_start_menu_shortcuts_and_web_apps_are_discovered(tmp_path):
    touch(tmp_path / "Editor.lnk")
    touch(tmp_path / "YouTube.url")
    touch(tmp_path / "Uninstall Editor.lnk")
    touch(tmp_path / "Nested" / "Word.lnk")
    found = discover_shortcuts([tmp_path], "Start Menu")
    names = {entry.name: entry.kind for entry in found}
    assert names == {"Editor": "shortcut", "YouTube": "browser_app", "Word": "shortcut"}


def test_taskbar_and_implicit_taskbar_shortcuts_are_discovered(tmp_path):
    touch(tmp_path / "TaskBar" / "Chat.lnk")
    touch(tmp_path / "ImplicitAppShortcuts" / "hash" / "Music.lnk")
    found = discover_shortcuts([tmp_path / "TaskBar", tmp_path / "ImplicitAppShortcuts"], "Taskbar")
    assert {entry.name for entry in found} == {"Chat", "Music"}
    assert all(entry.source == "Taskbar" for entry in found)


def test_packaged_application_ids_are_discovered():
    raw = (
        '[{"Name":"Calculator","AppID":"Microsoft.WindowsCalculator_8wekyb3d8bbwe!App"},'
        '{"Name":"Command Prompt","AppID":"cmd"},'
        '{"Name":"Photos","AppID":"Microsoft.Windows.Photos_8wekyb3d8bbwe!App"}]'
    )
    found = discover_packaged_apps(raw)
    assert [entry.name for entry in found] == ["Calculator", "Photos"]
    assert all(entry.kind == "packaged_app" for entry in found)
    assert found[0].aumid.startswith("Microsoft.WindowsCalculator")


def test_duplicate_sources_are_deduplicated(tmp_path):
    path = str(tmp_path / "Editor.lnk")
    entries = [
        AppEntry(name="Editor", kind="shortcut", path=path, source="Start Menu"),
        AppEntry(name="Editor", kind="shortcut", path=path, source="Taskbar"),
        AppEntry(name="Calculator", kind="packaged_app", aumid="Calc!App", source="Installed"),
    ]
    result = deduplicate(entries)
    assert len(result) == 2
    assert {entry.name for entry in result} == {"Editor", "Calculator"}


def test_library_search_filters_names_and_sources(tmp_path):
    start = tmp_path / "start"
    taskbar = tmp_path / "taskbar"
    touch(start / "Editor.lnk")
    touch(start / "Word.lnk")
    touch(taskbar / "Chat.lnk")
    library = ApplicationLibrary(
        start_dirs=[start], pinned_dirs=[taskbar],
        packaged_loader=lambda: [
            AppEntry(name="Calculator", kind="packaged_app", aumid="Calc!App", source="Installed")
        ],
    )
    assert [entry.name for entry in library.search("edit")] == ["Editor"]
    assert [entry.name for entry in library.search("", "Taskbar")] == ["Chat"]
    assert [entry.name for entry in library.search("", "Installed")] == ["Calculator"]
    assert library.cached_entries(), "the cache is populated after discovery"


def test_refresh_discovers_new_applications(tmp_path):
    start = tmp_path / "start"
    start.mkdir()
    library = ApplicationLibrary(start_dirs=[start], pinned_dirs=[], refresh_interval=300)
    assert library.entries() == []
    touch(start / "NewApp.lnk")
    assert [entry.name for entry in library.refresh()] == ["NewApp"]


def test_cache_avoids_repeated_discovery(tmp_path):
    start = tmp_path / "start"
    touch(start / "Editor.lnk")
    calls = []

    class Clock:
        now = 0.0

        def __call__(self):
            return self.now

    clock = Clock()
    library = ApplicationLibrary(
        start_dirs=[start], pinned_dirs=[],
        packaged_loader=lambda: calls.append(1) or [],
        refresh_interval=300, clock=clock,
    )
    library.entries()
    library.entries()
    assert calls == [1]
    clock.now = 301
    library.entries()
    assert calls == [1, 1]


# -- launching --------------------------------------------------------------
def test_packaged_app_launches_through_the_appsfolder_namespace():
    calls = []

    def fake_popen(args, **kwargs):
        calls.append((args, kwargs))
        return object()

    result = launch_app(
        {"kind": "packaged_app", "name": "Calculator", "aumid": "Calc!App"},
        popen=fake_popen,
    )
    assert result is not None
    assert calls[0][0] == ["explorer.exe", "shell:AppsFolder\\Calc!App"]
    assert "shell" not in calls[0][1]


def test_shortcut_launch_preserves_the_shortcut_itself(tmp_path):
    link = touch(tmp_path / "Editor.lnk")
    opened = []
    result = launch_app(
        {"kind": "shortcut", "name": "Editor", "path": str(link)},
        startfile=opened.append,
        popen=lambda *a, **k: (_ for _ in ()).throw(AssertionError("Popen must not run")),
    )
    assert result is None
    assert opened == [str(link)]


def test_executable_launch_uses_an_argument_list_without_a_shell(tmp_path):
    exe = touch(tmp_path / "editor.exe")
    calls = []

    def fake_popen(args, **kwargs):
        calls.append((args, kwargs))
        return object()

    launch_app(
        {"kind": "executable", "name": "Editor", "path": str(exe), "args": ["--safe"]},
        popen=fake_popen,
    )
    assert calls[0][0] == [str(exe), "--safe"]
    assert "shell" not in calls[0][1]


def test_batch_launch_uses_cmd_exe_without_a_shell(tmp_path):
    batch = touch(tmp_path / "start.bat")
    calls = []

    def fake_popen(args, **kwargs):
        calls.append((args, kwargs))
        return object()

    launch_app({"kind": "executable", "path": str(batch)}, popen=fake_popen)
    assert calls[0][0] == ["cmd.exe", "/c", str(batch)]
    assert "shell" not in calls[0][1]


def test_missing_application_is_reported(tmp_path):
    try:
        launch_app({"kind": "executable", "path": str(tmp_path / "missing.exe")},
                   popen=lambda *a, **k: None)
    except FileNotFoundError as exc:
        assert "not found" in str(exc).lower()
    else:  # pragma: no cover - the assertion below explains the failure
        raise AssertionError("A missing application must not be launched")


# -- migration --------------------------------------------------------------
def test_old_path_and_args_entries_are_migrated():
    assert normalize_app_entry({"path": "C:/Tools/editor.exe", "args": ["--a"]}) == {
        "kind": "executable", "name": "editor", "path": "C:/Tools/editor.exe",
        "args": ["--a"], "aumid": "",
    }
    assert normalize_app_entry({"path": "C:/Menu/Word.lnk"})["kind"] == "shortcut"
    assert normalize_app_entry({"path": "C:/Menu/YouTube.url"})["kind"] == "browser_app"
    assert normalize_app_entry({"path": "C:/Menu/start.bat"})["kind"] == "executable"
    assert normalize_app_entry({"aumid": "Calc!App", "name": "Calculator"})["kind"] == "packaged_app"


def test_existing_modes_keep_every_application_after_migration():
    mode = _normalize_mode("study", {
        "greeting": "Ready.",
        "urls": ["https://example.com"],
        "apps": [
            {"path": "C:/Tools/editor.exe", "args": ["--a"]},
            {"kind": "shortcut", "name": "Word", "path": "C:/Menu/Word.lnk"},
            {"kind": "packaged_app", "name": "Calculator", "aumid": "Calc!App"},
        ],
    })
    assert [app["kind"] for app in mode["apps"]] == [
        "executable", "shortcut", "packaged_app",
    ]
    assert mode["apps"][0]["args"] == ["--a"]
    assert mode["urls"] == ["https://example.com"]


def test_app_entry_round_trip():
    entry = AppEntry(name="Editor", kind="shortcut", path="C:/Editor.lnk")
    clean = normalize_app_entry(entry.to_dict())
    assert clean["name"] == "Editor"
    assert clean["kind"] == "shortcut"
    assert clean["path"] == "C:/Editor.lnk"


def test_app_key_deduplicates_shortcuts_by_name_and_packaged_apps_by_aumid():
    assert app_key({"path": "C:/Start Menu/Editor.lnk"}) == ("shortcut", "editor")
    assert app_key({"path": "C:/Taskbar/Editor.lnk"}) == ("shortcut", "editor")
    assert app_key({"aumid": "Calc!App", "name": "Calculator"}) == ("packaged_app", "calc!app")
    assert app_key({"path": "C:/Tools/Editor.exe"}) == ("executable", "c:/tools/editor.exe")
    assert app_key({"args": []}) is None
