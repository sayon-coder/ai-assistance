"""Kokoro-only speech output: queueing, muting and runtime voice selection.

Microsoft SAPI / Windows voices are intentionally absent; these tests pin the
single-engine behaviour so a fallback cannot creep back in.
"""
from __future__ import annotations

import threading
import time
from types import SimpleNamespace

from talcon import neural_tts, speech
from talcon.speech import (
    BACKEND_AUTO, BACKEND_KOKORO, ENGINE_KOKORO, NATURAL_PREVIEW, SpeechOutput,
    VoiceInfo, humanize_for_speech, language_label, normalize_backend,
    select_voice, spoken_excerpt,
)


class FakeBackend:
    def __init__(self):
        self.said = []
        self.settings = []
        self.stopped = 0
        self.last_error = ""
        self.voices_list = ["George", "Lewis"]

    def ensure_started(self, timeout=25.0):
        return True

    def say(self, text, timeout=None):
        self.said.append(text)
        return True

    def voices(self, timeout=15.0):
        return list(self.voices_list)

    def voice_details(self, timeout=15.0):
        return [
            VoiceInfo("bm_george", "George · British male", language="en-GB", gender="Male"),
            VoiceInfo("bm_lewis", "Lewis · British male", language="en-GB", gender="Male"),
        ]

    def set_voice(self, name, wait=True):
        self.settings.append(("voice", name))
        return True

    def set_rate(self, rate):
        self.settings.append(("rate", rate))

    def set_volume(self, volume):
        self.settings.append(("volume", volume))

    def stop_speaking(self):
        self.stopped += 1

    def stop(self):
        self.stopped += 1


def wait_for(predicate, timeout=3.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.02)
    return False


# -- pure helpers ---------------------------------------------------------
def test_every_saved_backend_value_maps_to_kokoro():
    for value in ("auto", "kokoro", "neural", "windows", "sapi", "natural", "", None):
        assert normalize_backend(value) == BACKEND_KOKORO


def test_voice_info_is_always_natural_offline_kokoro():
    voice = VoiceInfo("bm_george", "George · British male")
    assert voice.engine == ENGINE_KOKORO
    assert voice.natural is True
    assert voice.offline is True
    assert voice.label.startswith("Kokoro Neural Voice")
    payload = voice.to_dict()
    assert payload["backend"] == BACKEND_KOKORO
    assert payload["source"] == "kokoro"
    assert payload["ssml"] is False


def test_language_label_formats_common_tags():
    assert language_label("en-GB") == "English (United Kingdom)"
    assert language_label("en-IN") == "English (India)"
    assert language_label("en") == "English"
    assert language_label("") == "Unknown language"


def test_select_voice_by_id_then_name_then_missing_report():
    voices = [
        VoiceInfo("bm_george", "George · British male"),
        VoiceInfo("bm_lewis", "Lewis · British male"),
    ]
    assert select_voice(voices, "bm_lewis")[0].id == "bm_lewis"
    assert select_voice(voices, "", "Lewis · British male")[0].id == "bm_lewis"
    chosen, note = select_voice(voices, "gone", "Removed voice")
    assert chosen.id == "bm_george"
    assert "not installed" in note
    chosen, note = select_voice([], "bm_george")
    assert chosen is None and "No Kokoro voices" in note


def test_spoken_excerpt_ends_on_a_sentence_boundary():
    first = "This is a complete first sentence for the test."
    text = first + " " + ("word " * 200)
    excerpt = spoken_excerpt(text, limit=100)
    assert excerpt == first
    assert len(excerpt) <= 100


def test_humanize_turns_written_shorthand_into_spoken_words():
    text = humanize_for_speech("**Done** — e.g. one & another, i.e. this, etc.")
    assert "*" not in text and "—" in text
    assert "for example" in text
    assert " and " in text
    assert "that is" in text
    assert "et cetera" in text


# -- SpeechOutput ---------------------------------------------------------
def test_speech_queue_and_speaking_callback():
    backend = FakeBackend()
    states = []
    output = SpeechOutput(enabled=True, backend=backend, on_speaking=states.append)
    try:
        output.say("Hello sir")
        assert wait_for(lambda: backend.said == ["Hello sir"])
        assert wait_for(lambda: states and states[-1] is False)
        assert states[0] is True
    finally:
        output.stop()


def test_muting_clears_queue_without_stopping_the_worker():
    backend = FakeBackend()
    output = SpeechOutput(enabled=True, backend=backend)
    try:
        output.set_enabled(False)
        output.say("should not be spoken")
        time.sleep(0.2)
        assert backend.said == []
        assert backend.stopped >= 1
        output.set_enabled(True)
        output.say("spoken again")
        assert wait_for(lambda: backend.said == ["spoken again"])
    finally:
        output.stop()


def test_voice_rate_and_volume_are_passed_to_the_backend():
    backend = FakeBackend()
    output = SpeechOutput(enabled=True, backend=backend)
    try:
        output.set_voice("Lewis")
        output.set_rate(-3)
        output.set_volume(70)
        assert ("voice", "Lewis") in backend.settings
        assert ("rate", -3) in backend.settings
        assert ("volume", 70) in backend.settings
    finally:
        output.stop()


def test_say_and_wait_waits_for_real_playback_completion():
    class SlowBackend(FakeBackend):
        def say(self, text, timeout=None):
            self.said.append(text)
            time.sleep(0.15)
            return True

    backend = SlowBackend()
    output = SpeechOutput(enabled=True, backend=backend)
    try:
        assert output.say_and_wait("A complete sentence", timeout=3.0) is True
        assert backend.said == ["A complete sentence"]
        assert output.is_speaking is False
    finally:
        output.stop()


def test_wait_until_quiet_includes_queued_but_not_started_speech():
    class SlowBackend(FakeBackend):
        def say(self, text, timeout=None):
            self.said.append(text)
            time.sleep(0.15)
            return True

    backend = SlowBackend()
    output = SpeechOutput(enabled=True, backend=backend)
    try:
        output.say("Queued announcement")
        assert output.is_busy is True
        assert output.wait_until_quiet(3.0) is True
        assert backend.said == ["Queued announcement"]
        assert output.is_busy is False
    finally:
        output.stop()


def test_stop_speaking_drops_the_queue_and_interrupts_the_backend():
    backend = FakeBackend()
    output = SpeechOutput(enabled=False, backend=backend)
    try:
        output.say("A sentence that will never play.")
        output.stop_speaking()
        assert backend.stopped == 1
        assert output.is_speaking is False
    finally:
        output.stop()


def test_speech_is_serialised_and_a_shutdown_clears_pending_work():
    class TrackingBackend(FakeBackend):
        def __init__(self):
            super().__init__()
            self.active = 0
            self.max_active = 0
            self.lock = threading.Lock()

        def say(self, text, timeout=None):
            with self.lock:
                self.active += 1
                self.max_active = max(self.max_active, self.active)
            time.sleep(0.05)
            with self.lock:
                self.active -= 1
            self.said.append(text)
            return True

    backend = TrackingBackend()
    output = SpeechOutput(enabled=True, backend=backend)
    try:
        output.say("first")
        output.say("second")
        output.say("third")
        assert output.wait_until_quiet(4.0) is True
        assert backend.said == ["first", "second", "third"]
        assert backend.max_active == 1
        assert output.is_busy is False
    finally:
        output.stop()


def test_say_humanizes_the_engine_text_but_keeps_the_full_response():
    seen = []
    backend = FakeBackend()
    output = SpeechOutput(enabled=True, backend=backend, on_utterance=seen.append)
    try:
        output.say("**Ready** & waiting.")
        assert output.wait_until_quiet(5.0)
        assert backend.said == ["Ready and waiting."]
        assert seen == ["**Ready** & waiting."]
        assert output.last_spoken == "**Ready** & waiting."
    finally:
        output.stop()


# -- engine selection -----------------------------------------------------
def test_engine_is_always_kokoro_and_reports_availability(monkeypatch):
    monkeypatch.setattr(neural_tts, "available", lambda: True)
    monkeypatch.setattr(
        speech, "create_backend",
        lambda kind=BACKEND_KOKORO, on_error=None: FakeBackend(),
    )
    output = SpeechOutput(enabled=False, backend_kind=BACKEND_AUTO)
    try:
        backend, active, natural, note = output._build_backend(BACKEND_AUTO)
        assert isinstance(backend, FakeBackend)
        assert active == BACKEND_KOKORO
        assert natural is True
        assert "Kokoro" in note
    finally:
        output.stop()


def test_missing_model_is_reported_honestly(monkeypatch, tmp_path):
    monkeypatch.setattr(neural_tts, "KOKORO_DIR", tmp_path)
    report = speech.voice_diagnostic(audible=False)
    assert "Kokoro" in report
    assert "unavailable" in report.lower()
    assert "not installed" in report.lower()


def test_natural_preview_phrase_is_the_documented_sentence():
    assert NATURAL_PREVIEW == (
        "Hello, sir. I'm TALCON, your personal desktop assistant. "
        "How can I help you today?"
    )


# -- runtime voice selection and persistence -------------------------------
class _FakeSignal:
    def __init__(self):
        self.events = []

    def emit(self, *args):
        self.events.append(args)


class _RuntimeSpeech:
    def __init__(self, details, info):
        self.details = details
        self.info = info
        self.selected = []
        self.rates = []
        self.volumes = []

    def ensure_ready(self, timeout=25.0):
        return True

    def voice_details(self):
        return list(self.details)

    def engine_info(self):
        return dict(self.info)

    def set_voice(self, voice_id):
        self.selected.append(voice_id)

    def verify_voice(self, timeout=3.0):
        return True, (self.selected[-1] if self.selected else ""), ""

    def synthesis_summary(self):
        return ""

    def set_rate(self, rate):
        self.rates.append(rate)

    def set_volume(self, volume):
        self.volumes.append(volume)


def _make_runtime(fake_speech, settings):
    from talcon.runtime import TalconRuntime

    runtime = TalconRuntime.__new__(TalconRuntime)
    runtime.speech = fake_speech
    runtime.settings = settings
    runtime.muted = False
    runtime.status = {}
    runtime.bridge = SimpleNamespace(
        status_changed=_FakeSignal(),
        voices_available=_FakeSignal(),
        speech_voices_changed=_FakeSignal(),
    )
    runtime.voice_details = []
    runtime.voices = []
    runtime._active_voice = None
    runtime._speech_note = ""
    runtime._voice_load_lock = threading.Lock()
    runtime.status_snapshot = lambda: dict(runtime.status)
    return runtime


def _kokoro_info():
    return {
        "requested": BACKEND_AUTO,
        "requested_label": "Kokoro Neural Voice (offline, on-device)",
        "active": BACKEND_KOKORO,
        "active_label": "Kokoro Neural Voice",
        "natural_available": True,
        "note": "",
    }


def test_runtime_restores_the_saved_kokoro_voice(monkeypatch):
    from talcon import runtime as runtime_module

    details = [
        VoiceInfo("bm_george", "George · British male", language="en-GB", gender="Male"),
        VoiceInfo("bm_lewis", "Lewis · British male", language="en-GB", gender="Male"),
    ]
    fake = _RuntimeSpeech(details, _kokoro_info())
    settings = {
        "speech_voice_id": "bm_lewis",
        "speech_voice": "Lewis · British male",
        "speech_rate": 1,
        "speech_volume": 80,
    }
    saved = []
    monkeypatch.setattr(
        runtime_module, "persist_settings",
        lambda value: saved.append(dict(value)) or dict(value),
    )
    runtime = _make_runtime(fake, settings)
    runtime._load_voices()

    assert fake.selected == ["bm_lewis"]
    assert fake.rates == [1]
    assert fake.volumes == [80]
    assert runtime.status["speech"] == "Kokoro Neural Voice · Lewis · British male"
    assert runtime.status["speech_engine_active"] == "Kokoro Neural Voice"
    assert saved


def test_runtime_defaults_to_george_when_nothing_is_saved(monkeypatch):
    from talcon import runtime as runtime_module

    details = [
        VoiceInfo("bm_george", "George · British male", language="en-GB", gender="Male"),
        VoiceInfo("bm_lewis", "Lewis · British male", language="en-GB", gender="Male"),
    ]
    fake = _RuntimeSpeech(details, _kokoro_info())
    settings = {"speech_rate": 0, "speech_volume": 90}
    monkeypatch.setattr(runtime_module, "persist_settings", lambda value: dict(value))
    runtime = _make_runtime(fake, settings)
    runtime._load_voices()

    assert fake.selected == ["bm_george"]
    assert runtime.settings["speech_voice_id"] == "bm_george"
    assert runtime.settings["speech_voice_ids"] == {"kokoro": "bm_george"}


def test_runtime_missing_voice_falls_back_without_rewriting_preferences(monkeypatch):
    from talcon import runtime as runtime_module

    details = [
        VoiceInfo("bm_george", "George · British male", language="en-GB", gender="Male")
    ]
    fake = _RuntimeSpeech(details, _kokoro_info())
    settings = {
        "speech_voice_ids": {"kokoro": "removed-voice"},
        "speech_voice_id": "removed-voice",
        "speech_voice": "Removed Voice",
        "speech_rate": 0,
        "speech_volume": 90,
    }
    saved = []
    monkeypatch.setattr(
        runtime_module, "persist_settings",
        lambda value: saved.append(dict(value)) or dict(value),
    )
    runtime = _make_runtime(fake, settings)
    runtime._load_voices()

    assert fake.selected == ["bm_george"]
    assert settings["speech_voice_id"] == "removed-voice"
    assert saved == []
    assert "not installed" in runtime.status["speech_engine_note"]
