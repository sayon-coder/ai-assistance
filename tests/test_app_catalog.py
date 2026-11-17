"""Automatic application discovery, matching and universal \"open\" commands.

Discovery always uses temporary folders and mocked Windows data; no real
program is installed or launched.  The launch tests assert the selected
mechanism, not that a Windows application really started.
"""
from __future__ import annotations

import json
from types import SimpleNamespace

from talcon.app_catalog import (
    AppResolution, extract_app_command, is_safe_query, load_aliases,
    normalize_app_name, resolve_application,
)
from talcon.apps import (
    AppEntry, ApplicationLibrary, deduplicate, discover_shortcuts,
    discover_start_apps, launch_app, shortcut_hints,
)
from talcon.commands import CommandRouter, build_recognition_grammar
from talcon.runtime import APP_GRAMMAR_LIMIT, MAX_APP_PHRASES, TalconRuntime


class FakeSpeech:
    enabled = True

    def __init__(self):
        self.spoken = []

    def say(self, text):
        self.spoken.append(str(text))


class FakeApp:
    def __init__(self):
        self.refreshes = []

    def refresh_apps(self, announce=True):
        self.refreshes.append(bool(announce))

    def show_settings(self):
        pass


class FakeLibrary:
    def __init__(self, entries):
        self._entries = list(entries)
        self.invalidated = []
        self.refreshed = 0
        self.async_refreshes = 0

    def entries(self, force=False):
        return list(self._entries)

    def cached_entries(self):
        return list(self._entries)

    def refresh(self):
        self.refreshed += 1
        return list(self._entries)

    def refresh_async(self, callback=None):
        self.async_refreshes += 1
        if callback is not None:
            callback(list(self._entries), "")

    def invalidate(self, entry):
        self.invalidated.append(entry)
        self._entries = [item for item in self._entries if item.key() != entry.key()]


def touch(path, data=b"stub"):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return path


def router_with(entries, aliases=None, launcher=None):
    speech = FakeSpeech()
    app = FakeApp()
    router = CommandRouter(SimpleNamespace(current_mode="normal"), speech, app)
    library = FakeLibrary(entries)
    router.set_application_library(library, aliases or {})
    if launcher is not None:
        router.app_launcher = launcher
    return router, library, speech, app


# -- discovery --------------------------------------------------------------
def test_get_start_apps_json_keeps_packaged_and_classic_entries():
    raw = json.dumps([
        {"Name": "Calculator", "AppID": "Microsoft.WindowsCalculator_8wekyb3d8bbwe!App"},
        {"Name": "Command Prompt", "AppID": "cmd"},
        {"Name": "Notepad", "AppID": "Microsoft.WindowsNotepad_8wekyb3d8bbwe!App"},
        {"Name": "", "AppID": "ignored"},
        {"Name": "No id", "AppID": ""},
    ])
    found = discover_start_apps(raw)
    assert [entry.name for entry in found] == ["Calculator", "Command Prompt", "Notepad"]
    kinds = {entry.name: entry.kind for entry in found}
    assert kinds["Calculator"] == "packaged_app"
    assert kinds["Command Prompt"] == "start_app"
    assert found[0].source == "Installed"
    assert found[1].source == "Start Apps"


def test_unreadable_or_failed_get_start_apps_is_not_fatal():
    assert discover_start_apps("not json") == []
    assert discover_start_apps("[]") == []

    def boom():
        raise RuntimeError("PowerShell unavailable")

    assert discover_start_apps(None, runner=boom) == []


def test_lnk_and_url_shortcuts_are_discovered(tmp_path):
    touch(tmp_path / "Editor.lnk")
    touch(tmp_path / "YouTube.url")
    found = discover_shortcuts([tmp_path], "Start Menu")
    assert {entry.name: entry.kind for entry in found} == {
        "Editor": "shortcut", "YouTube": "browser_app",
    }


def test_browser_app_shortcuts_keep_their_app_mode_identity(tmp_path):
    chrome_pwa = touch(
        tmp_path / "ChatGPT.lnk",
        b"C:\\Program Files\\Google\\Chrome\\chrome.exe --app-id=abcdefgh",
    )
    edge_pwa = touch(
        tmp_path / "WhatsApp.lnk",
        b"C:\\Program Files (x86)\\Microsoft\\Edge\\msedge.exe --app=https://web.whatsapp.com",
    )
    proxy_pwa = touch(
        tmp_path / "YouTube.lnk",
        b"C:\\Program Files\\BraveSoftware\\Brave-Browser\\Application\\chrome_proxy.exe",
    )
    assert shortcut_hints(chrome_pwa) == (True, "chrome")
    assert shortcut_hints(edge_pwa) == (True, "edge")
    assert shortcut_hints(proxy_pwa) == (True, "brave")
    found = {entry.name: entry for entry in discover_shortcuts([tmp_path])}
    assert found["ChatGPT"].app_mode is True
    assert found["ChatGPT"].browser == "chrome"
    assert found["WhatsApp"].browser == "edge"
    assert found["YouTube"].app_mode is True


def test_missing_directories_are_ignored(tmp_path):
    assert discover_shortcuts([tmp_path / "does-not-exist"]) == []


# -- catalogue --------------------------------------------------------------
def test_duplicate_sources_prefer_the_real_pwa_shortcut(tmp_path):
    shortcut = tmp_path / "YouTube.lnk"
    touch(shortcut, b"chrome.exe --app-id=youtube")
    entries = [
        AppEntry(name="YouTube", kind="start_app", aumid="youtube", source="Start Apps"),
        AppEntry(name="YouTube", kind="shortcut", path=str(shortcut),
                 source="Start Menu", app_mode=True, browser="chrome"),
        AppEntry(name="YouTube", kind="shortcut", path=str(tmp_path / "Taskbar" / "YouTube.lnk"),
                 source="Taskbar"),
    ]
    result = deduplicate(entries)
    assert len(result) == 1
    assert result[0].path == str(shortcut)
    assert result[0].app_mode is True, "the app-mode shortcut must not be replaced by a URL"


def test_identical_names_with_different_launch_targets_are_not_merged():
    entries = [
        AppEntry(name="WhatsApp", kind="packaged_app", aumid="A!App", source="Installed"),
        AppEntry(name="WhatsApp", kind="shortcut", path="C:/Start/WhatsApp.lnk", source="Start Menu"),
        AppEntry(name="WhatsApp", kind="packaged_app", aumid="B!App", source="Installed"),
    ]
    result = deduplicate(entries)
    assert len(result) == 3


def test_same_web_app_in_two_browsers_stays_distinct_and_is_explained():
    entries = [
        AppEntry(name="ChatGPT", kind="shortcut", path="C:/Brave/ChatGPT.lnk",
                 source="Start Menu", app_mode=True, browser="brave"),
        AppEntry(name="ChatGPT", kind="shortcut", path="C:/Chrome/ChatGPT.lnk",
                 source="Start Menu", app_mode=True, browser="chrome"),
    ]
    result = deduplicate(entries)
    assert len(result) == 2, "different browsers are different launch targets"
    router, _library, speech, _app = router_with(result, launcher=lambda item: None)
    assert router.handle("open chatgpt") == "app_ambiguous"
    message = speech.spoken[-1]
    assert "ChatGPT (Brave app)" in message
    assert "ChatGPT (Chrome app)" in message


def test_application_library_invalidates_a_missing_entry(tmp_path):
    start = tmp_path / "start"
    touch(start / "Gone.lnk")
    library = ApplicationLibrary(start_dirs=[start], pinned_dirs=[])
    entry = library.entries()[0]
    assert [item.name for item in library.cached_entries()] == ["Gone"]
    library.invalidate(entry)
    assert library.cached_entries() == []


# -- matching ---------------------------------------------------------------
def test_exact_application_name_wins():
    entries = [AppEntry(name="Chrome"), AppEntry(name="Chrome Remote Desktop")]
    result = resolve_application("Chrome", entries)
    assert result.status == "match"
    assert result.entry.name == "Chrome"
    assert result.reason == "exact name"


def test_configured_alias_resolves_only_when_installed():
    entries = [AppEntry(name="Visual Studio Code")]
    aliases = {"my editor": "Visual Studio Code"}
    result = resolve_application("my editor", entries, aliases)
    assert result.matched
    assert result.entry.name == "Visual Studio Code"
    assert resolve_application("my editor", []).status == "none"


def test_builtin_spoken_alias_maps_vs_code():
    aliases = load_aliases(path=None)
    entries = [AppEntry(name="Visual Studio Code")]
    result = resolve_application("vs code", entries, aliases)
    assert result.matched
    assert result.entry.name == "Visual Studio Code"


def test_fuzzy_matching_is_conservative():
    entries = [AppEntry(name="Visual Studio Code"), AppEntry(name="Spotify")]
    assert resolve_application("visual studio", entries).matched
    assert resolve_application("zzzzzz", entries).status == "none"


def test_similar_names_are_ambiguous_not_guessed():
    entries = [AppEntry(name="Photo Editor"), AppEntry(name="Photo Viewer")]
    result = resolve_application("photo", entries)
    assert result.status == "ambiguous"
    assert {entry.name for entry in result.candidates} == {"Photo Editor", "Photo Viewer"}


def test_unknown_name_returns_suggestions():
    entries = [AppEntry(name="Notepad"), AppEntry(name="Calculator")]
    result = resolve_application("notepd", entries)
    assert result.status == "match"  # a single close name is safe
    assert result.entry.name == "Notepad"
    missing = resolve_application("qqqqq", entries)
    assert missing.status == "none"


def test_aliases_file_is_merged_without_touching_settings(tmp_path):
    path = tmp_path / "app_aliases.json"
    path.write_text(json.dumps({"my music": "Spotify"}), encoding="utf-8")
    aliases = load_aliases(path)
    assert aliases["my music"] == "Spotify"
    assert aliases["vs code"] == "visual studio code"


# -- launching --------------------------------------------------------------
def test_packaged_and_classic_apps_launch_through_appsfolder():
    calls = []

    def fake_popen(args, **kwargs):
        calls.append(args)
        return object()

    launch_app({"kind": "packaged_app", "name": "Calculator", "aumid": "Calc!App"},
               popen=fake_popen)
    launch_app({"kind": "start_app", "name": "Command Prompt", "aumid": "cmd"},
               popen=fake_popen)
    assert calls == [
        ["explorer.exe", "shell:AppsFolder\\Calc!App"],
        ["explorer.exe", "shell:AppsFolder\\cmd"],
    ]


def test_pwa_launch_uses_the_shortcut_itself_not_a_plain_url(tmp_path):
    link = touch(tmp_path / "ChatGPT.lnk", b"chrome.exe --app-id=abc")
    opened = []
    result = launch_app(
        {"kind": "shortcut", "name": "ChatGPT", "path": str(link)},
        startfile=opened.append,
        popen=lambda *a, **k: (_ for _ in ()).throw(AssertionError("no Popen")),
    )
    assert result is None
    assert opened == [str(link)]


def test_url_shortcuts_launch_through_the_windows_shell(tmp_path):
    url = touch(tmp_path / "YouTube.url", b"[InternetShortcut]\nURL=https://youtube.com")
    opened = []
    launch_app({"kind": "browser_app", "name": "YouTube", "path": str(url)},
               startfile=opened.append)
    assert opened == [str(url)]


def test_missing_shortcut_is_reported_and_catalogue_is_invalidated(tmp_path):
    entry = AppEntry(name="Gone", kind="shortcut", path=str(tmp_path / "Gone.lnk"))
    router, library, speech, app = router_with(
        [entry], launcher=lambda item: (_ for _ in ()).throw(FileNotFoundError())
    )
    action = router.handle("open gone")
    assert action == "app_missing"
    assert library.invalidated and library.invalidated[0].name == "Gone"
    assert app.refreshes == [False]
    assert "no longer installed" in speech.spoken[-1]


# -- voice command integration ---------------------------------------------
def test_open_command_resolves_from_the_catalogue_and_launches_trusted_entry():
    entries = [AppEntry(name="Chrome", kind="shortcut", path="C:/Chrome.lnk")]
    launched = []
    router, _library, speech, _app = router_with(entries, launcher=launched.append)
    assert router.handle("open chrome") == "app_opened"
    assert launched == entries
    assert "Opening Chrome" in speech.spoken[-1]


def test_launch_start_and_run_are_supported():
    entries = [AppEntry(name="Notepad", kind="shortcut", path="C:/Notepad.lnk")]
    for phrase in ("open notepad", "launch notepad", "start notepad", "run notepad"):
        launched = []
        router, _library, _speech, _app = router_with(entries, launcher=launched.append)
        assert router.handle(phrase) == "app_opened"
        assert launched == entries


def test_existing_commands_keep_priority_over_application_launching():
    launched = []
    router, _library, speech, _app = router_with(
        [AppEntry(name="Settings", kind="shortcut", path="C:/Settings.lnk")],
        launcher=launched.append,
    )
    assert router.handle("open settings") == "settings"
    assert launched == []


def test_custom_commands_are_not_shadowed_by_the_app_catalogue():
    launched = []
    router, _library, _speech, _app = router_with(
        [AppEntry(name="Notes", kind="shortcut", path="C:/Notes.lnk")],
        launcher=launched.append,
    )
    router.custom_handler = lambda text: "custom" if text == "open notes" else None
    assert router.handle("open notes") == "custom"
    assert launched == []


def test_ambiguous_prompt_and_follow_up_by_name():
    entries = [AppEntry(name="Photo Editor"), AppEntry(name="Photo Viewer")]
    launched = []
    router, _library, speech, _app = router_with(entries, launcher=launched.append)
    assert router.handle("open photo") == "app_ambiguous"
    assert "Photo Editor and Photo Viewer" in speech.spoken[-1]
    assert router.handle("photo viewer") == "app_opened"
    assert launched[0].name == "Photo Viewer"


def test_ambiguous_prompt_can_be_answered_with_an_ordinal():
    entries = [AppEntry(name="Chat App"), AppEntry(name="Chat Web")]
    launched = []
    router, _library, _speech, _app = router_with(entries, launcher=launched.append)
    assert router.handle("open chat") == "app_ambiguous"
    assert router.handle("second") == "app_opened"
    assert launched[0].name == "Chat Web"


def test_not_found_does_not_launch_anything():
    entries = [AppEntry(name="Chrome", kind="shortcut", path="C:/Chrome.lnk")]
    launched = []
    router, _library, speech, _app = router_with(entries, launcher=launched.append)
    assert router.handle("open calculator") == "app_not_found"
    assert launched == []
    assert "could not find" in speech.spoken[-1]


def test_refresh_app_list_command_refreshes_asynchronously():
    router, _library, speech, app = router_with([AppEntry(name="Chrome")])
    assert router.handle("refresh app list") == "apps_refreshing"
    assert app.refreshes == [True]
    assert "Refreshing" in speech.spoken[-1]


def test_shell_injection_in_voice_input_cannot_reach_the_launcher():
    entries = [AppEntry(name="Chrome", kind="shortcut", path="C:/Chrome.lnk")]
    launched = []
    router, _library, _speech, _app = router_with(entries, launcher=launched.append)
    for phrase in (
        "open chrome & calc",
        "open C:\\Windows\\System32\\calc.exe",
        "run calc.exe; shutdown /s",
        "launch $(calc)",
    ):
        assert router.handle(phrase) in {"app_not_found", "unknown"}
    assert launched == []
    assert is_safe_query("C:/Windows/calc.exe") is False
    assert is_safe_query("chrome && calc") is False


# -- grammar ----------------------------------------------------------------
def _runtime_with_apps(entries, aliases=None):
    runtime = TalconRuntime.__new__(TalconRuntime)
    runtime.apps = FakeLibrary(entries)
    runtime.commands = SimpleNamespace(
        recognition_phrases=lambda: {"study mode"},
        pending_phrases=lambda: set(),
        app_aliases=aliases or {},
    )
    runtime.custom = SimpleNamespace(snapshot=lambda: {"commands": [], "automations": []})
    return runtime


def test_application_phrases_are_added_to_the_vosk_grammar_safely():
    runtime = _runtime_with_apps(
        [AppEntry(name="Chrome"), AppEntry(name="Visual Studio Code")],
        aliases={"vs code": "Visual Studio Code"},
    )
    phrases = runtime.recognition_phrases()
    for expected in (
        "study mode", "open chrome", "launch chrome", "start chrome",
        "run chrome", "open vs code",
    ):
        assert expected in phrases
    grammar = json.loads(build_recognition_grammar(phrases))
    assert "open chrome" in grammar
    assert "[unk]" in grammar


def test_application_grammar_is_bounded():
    entries = [AppEntry(name=f"Application {index}") for index in range(200)]
    runtime = _runtime_with_apps(entries)
    phrases = runtime.recognition_phrases()
    app_phrases = {phrase for phrase in phrases if phrase.split()[0] in
                   {"open", "launch", "start", "run"}}
    assert len(app_phrases) <= MAX_APP_PHRASES
    assert APP_GRAMMAR_LIMIT < 200


def test_runtime_refresh_updates_the_grammar_and_status():
    runtime = _runtime_with_apps([AppEntry(name="Chrome")])
    runtime._refresh_grammar = lambda: setattr(runtime, "grammar_refreshed", True)
    runtime._set_status = lambda key, value: setattr(runtime, "status_value", value)
    runtime.speech = FakeSpeech()
    runtime.refresh_apps(announce=False)
    assert getattr(runtime, "grammar_refreshed", False) is True
    assert "1 applications discovered" == runtime.status_value


def test_application_grammar_includes_ambiguity_follow_ups():
    from talcon.runtime import FOLLOW_UP_PHRASES

    runtime = _runtime_with_apps([AppEntry(name="Chrome")])
    phrases = runtime.recognition_phrases()
    assert FOLLOW_UP_PHRASES <= phrases
    assert "second" in phrases


def test_app_command_parser_handles_natural_forms():
    assert extract_app_command("Open Chrome.") == "chrome"
    assert extract_app_command("launch the Spotify app") == "spotify"
    assert extract_app_command("start Microsoft Store") == "microsoft store"
    assert extract_app_command("run Notepad please") == "notepad"
    assert extract_app_command("study mode") is None
    assert extract_app_command("open") is None


def test_app_resolution_is_never_done_on_a_second_command_system():
    """The feature must extend the existing registry, not replace it."""
    router, _library, _speech, _app = router_with([AppEntry(name="Chrome")])
    actions = {router.registry.match("open settings").action,
               router.registry.match("refresh app list").action}
    assert actions == {"settings", "refresh_apps"}
    assert isinstance(resolve_application("chrome", [AppEntry(name="Chrome")]), AppResolution)


def test_open_up_and_whats_app_are_natural_app_phrases():
    from talcon.app_catalog import extract_app_command
    from talcon.apps import AppEntry
    from talcon.app_catalog import resolve_application, load_aliases

    assert extract_app_command("open up notepad") == "notepad"
    entries = [AppEntry(name="WhatsApp", kind="executable", path="C:/wa.exe")]
    result = resolve_application("whats", entries, load_aliases())
    assert result.matched and result.entry.name == "WhatsApp"
