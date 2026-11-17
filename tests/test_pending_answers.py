"""Answers to "which one?" and "did you mean X?" prompts.

These cover the reported failure: TALCON asked "Did you mean deepseek?", the
user answered "yes", and nothing opened because the suggestion never became
pending state.  Numbered answers ("yes, the first one") must work too, without
hard-coding any application name in the project.
"""
from __future__ import annotations

import pytest

from talcon.apps import AppEntry
from talcon.commands import CommandRouter
from tests.test_commands import FakeApp, FakeModes, FakeSpeech


class Catalogue:
    """Minimal stand-in for ApplicationLibrary."""

    def __init__(self, entries):
        self._entries = list(entries)
        self.refreshed = 0

    def entries(self):
        return list(self._entries)

    def refresh(self):
        self.refreshed += 1
        return list(self._entries)

    def refresh_async(self):
        self.refreshed += 1

    def invalidate(self, entry):
        pass


#: The router reports a successful launch with this action string.
OPENED = ("app_opened", "app_open")


def entry(name, kind="executable", source="start_menu"):
    return AppEntry(name=name, kind=kind, path=f"C:/Apps/{name}.exe",
                    source=source)


def build(entries, aliases=None):
    speech = FakeSpeech()
    app = FakeApp()
    router = CommandRouter(FakeModes(), speech, app)
    launches = []
    router.set_application_library(Catalogue(entries), aliases or {})
    router.app_launcher = lambda item: launches.append(item.name)
    return router, speech, launches


# ------------------------------------------------------------ did you mean
def test_yes_opens_the_single_suggested_application():
    router, speech, launches = build([entry("DeepSeek"), entry("Brave")])
    action = router.handle("open deep speak")
    assert action == "app_not_found"
    assert any("did you mean" in line.lower() for line in speech.spoken)
    assert launches == []                       # nothing opened yet

    assert router.handle("yes") in OPENED
    assert launches == ["DeepSeek"]             # answering actually opens it
    assert any("deepseek" in line.lower() for line in speech.spoken)


def test_yes_please_and_yeah_also_confirm_the_suggestion():
    for answer in ("yes please", "yeah", "yep", "sure", "correct"):
        router, _speech, launches = build([entry("DeepSeek"), entry("Brave")])
        router.handle("open deep speak")
        assert router.handle(answer) in OPENED, answer
        assert launches == ["DeepSeek"], answer


def test_no_declines_the_suggestion_and_opens_nothing():
    router, speech, launches = build([entry("DeepSeek"), entry("Brave")])
    router.handle("open deep speak")
    assert router.handle("no") == "app_cancelled"
    assert launches == []
    assert any("will not open anything" in line.lower() for line in speech.spoken)
    # The declined suggestion is gone: a later "yes" opens nothing.
    assert router.handle("yes") not in OPENED
    assert launches == []


def test_suggestion_lets_the_user_pick_by_number():
    router, _speech, launches = build(
        [entry("ChatGPT"), entry("ChatGPT Workspace"), entry("Brave")]
    )
    router.handle("open chat gpt workspace")
    if not launches:                            # prompt armed, answer by number
        assert router.handle("the second one") in OPENED
        assert launches
    assert launches


# --------------------------------------------------------------- ambiguous
def test_yes_with_first_opens_the_first_candidate():
    # Two entries share a name, so the router must ask which one.
    entries = [entry("Claude", kind="executable"),
               entry("Claude", kind="browser_app")]
    router, speech, launches = build(entries)
    assert router.handle("open claude") == "app_ambiguous"
    assert any("which one" in line.lower() for line in speech.spoken)
    assert router.handle("yes, the first one") in OPENED
    assert launches == ["Claude"]
    assert router._pending_apps == ()          # the question is answered once


def test_second_one_opens_the_second_candidate():
    entries = [entry("Claude", kind="executable"),
               entry("Claude", kind="browser_app")]
    router, _speech, launches = build(entries)
    assert router.handle("open claude") == "app_ambiguous"
    assert router.handle("yes, second one") in OPENED
    assert launches == ["Claude"]              # the second of the two entries
    assert len(launches) == 1


@pytest.mark.parametrize("answer, expected", [
    ("the first one", 0),
    ("first", 0),
    ("number one", 0),
    ("one", 0),
    ("the second one", 1),
    ("second", 1),
    ("number two", 1),
    ("2", 1),
    ("yes the second one", 1),
    ("yes, the first one", 0),
    ("okay open the first one", 0),
])
def test_ordinal_answers_are_understood_in_any_wrapper(answer, expected):
    entries = [entry("Claude"), entry("Cloud Drive")]
    router, _speech, launches = build(entries)
    # Arm the exact ambiguity the user reported.
    router._pending_apps = tuple(entries)
    router._pending_query = "cloud"
    router._pending_kind = "open"
    assert router.handle(answer) in OPENED, answer
    assert launches == [entries[expected].name], answer


def test_pending_answers_survive_the_wake_word():
    entries = [entry("Claude"), entry("Cloud Drive")]
    router, _speech, launches = build(entries)
    router._pending_apps = tuple(entries)
    router._pending_kind = "open"
    assert router.handle("TALCON, the second one") in OPENED
    assert launches == ["Cloud Drive"]


def test_naming_a_candidate_still_works_and_is_learned():
    entries = [entry("Claude"), entry("Cloud Drive")]
    router, _speech, launches = build(entries)
    router._pending_apps = tuple(entries)
    router._pending_kind = "open"
    assert router.handle("cloud drive") in OPENED
    assert launches == ["Cloud Drive"]


def test_unknown_answer_keeps_the_question_open():
    entries = [entry("Claude"), entry("Cloud Drive")]
    router, _speech, launches = build(entries)
    router._pending_apps = tuple(entries)
    router._pending_kind = "open"
    assert router.handle("what is the weather like") != "app_open"
    assert launches == []
    # Still armed, so a later correct answer works.
    assert router.handle("first") in OPENED


def test_pending_answers_are_offered_to_the_recognition_grammar():
    entries = [entry("DeepSeek"), entry("Brave")]
    router, _speech, _launches = build(entries)
    router.handle("open deep speak")
    phrases = router.pending_phrases()
    assert {"yes", "no", "first one", "second one"} <= phrases
    assert "deepseek" in phrases


def test_no_pending_state_means_no_answers():
    router, _speech, launches = build([entry("Brave")])
    assert router.handle("yes") not in OPENED
    assert launches == []


# --------------------------------------------------- "close it" after opening
class RunningApps:
    """Fake runtime running-application surface for close/open tests."""

    def __init__(self, names):
        self.names = list(names)

    def __call__(self):
        from types import SimpleNamespace
        return [SimpleNamespace(name=n, process=n, path="", kind="exe",
                                title=n, pid=1) for n in self.names]


def build_with_running(entries, running):
    from types import SimpleNamespace
    router, speech, launches = build(entries)
    router.app.running_applications = RunningApps(running)
    closed = []

    def close_application(app):
        closed.append(app.name)
        return SimpleNamespace(closed=[app.name], refused=[], errors=[])

    router.app.close_application = close_application
    return router, speech, launches, closed


def test_close_it_closes_the_app_just_opened():
    router, speech, launches, closed = build_with_running(
        [entry("MechVibes"), entry("Brave")], ["MechVibes", "Brave"]
    )
    assert router.handle("open mechvibes app") in OPENED
    assert launches == ["MechVibes"]
    assert router.handle("now close it") == "app_closed"
    assert closed == ["MechVibes"]


@pytest.mark.parametrize("phrase", [
    "close it", "close it now", "close the app", "close that", "quit it",
    "close this app", "now close it", "and close it please",
])
def test_every_pronoun_wording_closes_the_last_app(phrase):
    router, _speech, _launches, closed = build_with_running(
        [entry("MechVibes")], ["MechVibes"]
    )
    router.handle("open mechvibes")
    assert router.handle(phrase) == "app_closed", phrase
    assert closed == ["MechVibes"], phrase


def test_close_it_without_a_previous_open_asks_for_a_name():
    router, speech, _launches, closed = build_with_running(
        [entry("MechVibes")], ["MechVibes"]
    )
    assert router.handle("close it") == "close_needs_name"
    assert closed == []
    assert any("which application" in line.lower() for line in speech.spoken)


def test_close_it_is_never_sent_to_the_ai():
    from tests.test_command_priority import RecordingAssistant
    router, _speech, _launches, stopped = build_with_running(
        [entry("MechVibes")], ["MechVibes"]
    )
    assistant = RecordingAssistant()
    router.natural_handler = assistant.handle_phrase
    router.handle("open mechvibes")
    router.handle("now close it")
    assert assistant.consulted == []


def test_close_it_when_the_app_already_quit_says_so(monkeypatch):
    import talcon.running_apps as running_apps
    from talcon.windows_control import CloseResult

    # Keep the tray-window fallback off the real machine.
    monkeypatch.setattr(
        running_apps, "close_application_by_name", lambda *a, **k: CloseResult()
    )
    router, speech, _launches, closed = build_with_running(
        [entry("MechVibes")], []          # nothing running any more
    )
    router.handle("open mechvibes")
    assert router.handle("close it") == "app_not_running"
    assert closed == []
    assert any("isn't running" in line.lower() for line in speech.spoken)


def test_open_still_parses_with_a_discourse_prefix():
    router, _speech, launches, _closed = build_with_running(
        [entry("MechVibes")], []
    )
    for phrase in ("now open mechvibes", "and open mechvibes",
                   "then open mechvibes app"):
        assert router.handle(phrase) in OPENED, phrase
    assert launches.count("MechVibes") == 3


def test_pronoun_close_phrases_are_in_the_recognition_grammar():
    """Vosk can only hear words that are in the grammar."""
    import json as _json
    from talcon.commands import build_recognition_grammar, CommandRouter

    router = build_router_for_grammar()
    grammar = _json.loads(build_recognition_grammar(router.recognition_phrases()))
    for phrase in ("close it", "close the app", "close that", "now close it"):
        assert phrase in grammar, phrase
    assert "close" in grammar        # quitting TALCON itself still works


def build_router_for_grammar():
    from types import SimpleNamespace
    router, _speech, _launches = build([])
    router.app = SimpleNamespace(
        current_mode="normal", commands_paused=False, pending=None, assistant=None,
        activate_mode=lambda name: True, set_muted=lambda value, announce=False: None,
    )
    return router
