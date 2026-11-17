"""Voice-engine tests with mocked microphone frames and speech output."""
from __future__ import annotations

import json
import queue
import threading
import time

import talcon.voice as voice
from talcon.voice import VoiceController, is_wake_phrase, tokens


SILENT = b"\x00\x00" * 2000
LOUD = (2000).to_bytes(2, "little", signed=True) * 2000


class FakeSpeech:
    def __init__(self):
        self.said = []
        self.enabled = True
        self.is_speaking = False

    def say(self, text):
        self.said.append(text)

    def say_and_wait(self, text, timeout=30.0):
        self.said.append(text)
        return True

    def wait_until_idle(self, timeout=None):
        return True


class FakeRouter:
    def __init__(self, speech):
        self.speech = speech
        self.handled = []
        self.known = {"study mode", "study", "open settings", "mute"}

    def handle(self, phrase, announce_unknown=True):
        self.handled.append((phrase, announce_unknown))
        return "study" if phrase in self.known else "unknown"


class FakeRecognizer:
    def __init__(self, text=""):
        self.text = text
        self.final_calls = 0

    def AcceptWaveform(self, data):
        return bool(self.text)

    def Result(self):
        return json.dumps({"text": self.text})

    def PartialResult(self):
        return json.dumps({"partial": ""})

    def FinalResult(self):
        self.final_calls += 1
        return json.dumps({"text": self.text})

    def Reset(self):
        pass


def make_controller(settings=None):
    speech = FakeSpeech()
    router = FakeRouter(speech)
    controller = VoiceController(
        settings if settings is not None else {"always_listen": False},
        router, beep=lambda: None, event_callback=lambda kind, text: None,
    )
    return controller, router, speech


def fast(monkeypatch):
    monkeypatch.setattr(voice, "COMMAND_START_TIMEOUT", 0.15)
    monkeypatch.setattr(voice, "COMMAND_MAX_SECONDS", 0.4)
    monkeypatch.setattr(voice, "COMMAND_SILENCE", 0.05)
    monkeypatch.setattr(voice, "MIN_SPEECH_BLOCKS", 1)
    monkeypatch.setattr(voice, "BEEP_GATE_SECONDS", 0.01)
    monkeypatch.setattr(voice, "SETTLE_SECONDS", 0.01)


# -- wake word --------------------------------------------------------------
def test_wake_word_is_an_exact_token():
    assert is_wake_phrase("talcon")
    assert is_wake_phrase("TALCON")
    assert is_wake_phrase("hey talcon")
    assert is_wake_phrase("Talcon, study mode.")
    assert is_wake_phrase("tell can")
    assert is_wake_phrase("tell can introduce yourself")
    assert is_wake_phrase("talcon")
    assert is_wake_phrase("TALCON")
    assert is_wake_phrase("hey talcon")
    assert tokens("TALCON, study mode.") == ["talcon", "study", "mode"]


def test_false_wake_words_are_rejected():
    for text in ("talcony", "talconk", "november", "[unk]", "no va", "utalcon", ""):
        assert not is_wake_phrase(text), text


# -- microphone hygiene -----------------------------------------------------
def test_beep_gating_clears_stale_frames(monkeypatch):
    fast(monkeypatch)
    beeps = []
    controller, _router, _speech = make_controller()
    controller._beep = lambda: beeps.append("beep")
    audio_q: queue.Queue = queue.Queue()
    for _ in range(5):
        audio_q.put(SILENT)
    controller._beep_and_settle(audio_q)
    assert beeps == ["beep"]
    assert audio_q.empty(), "stale microphone frames must be discarded"


def test_queue_is_bounded_by_dropping_oldest_frames():
    audio_q: queue.Queue = queue.Queue(maxsize=2)
    audio_q.put(SILENT)
    audio_q.put(SILENT)
    try:
        audio_q.put_nowait(LOUD)
    except queue.Full:
        audio_q.get_nowait()
        audio_q.put_nowait(LOUD)
    assert audio_q.qsize() == 2


def test_continuous_mode_ignores_a_final_without_voiced_audio():
    controller, router, _speech = make_controller({"always_listen": True})
    controller._model = None
    controller._grammar_dirty.clear()
    audio_q: queue.Queue = queue.Queue()
    audio_q.put(SILENT)
    thread = threading.Thread(
        target=controller._listen_loop, args=(audio_q, FakeRecognizer("hide")), daemon=True
    )
    thread.start()
    time.sleep(0.5)
    controller._stop.set()
    thread.join(timeout=2.0)
    assert router.handled == [], "noise must never execute a short command"


def test_continuous_mode_accepts_a_final_after_voiced_audio():
    controller, router, _speech = make_controller({
        "always_listen": True, "speech_trailing_silence": 0.1,
    })
    controller._model = None
    controller._grammar_dirty.clear()
    audio_q: queue.Queue = queue.Queue()
    for _ in range(4):
        audio_q.put(SILENT)
    audio_q.put(LOUD)
    thread = threading.Thread(
        target=controller._listen_loop, args=(audio_q, FakeRecognizer("hide")), daemon=True
    )
    thread.start()
    time.sleep(1.0)
    controller._stop.set()
    thread.join(timeout=2.0)
    assert router.handled and router.handled[0][0] == "hide"


def test_capture_reports_silence_as_case_a(monkeypatch):
    fast(monkeypatch)
    controller, _router, _speech = make_controller()
    audio_q: queue.Queue = queue.Queue()
    audio_q.put(SILENT)
    audio_q.put(SILENT)
    text, heard, aborted = controller._capture(audio_q, FakeRecognizer(""))
    assert text == ""
    assert heard is False
    assert aborted is False


def test_capture_detects_speech_and_returns_transcript(monkeypatch):
    fast(monkeypatch)
    controller, _router, _speech = make_controller()
    audio_q: queue.Queue = queue.Queue()
    audio_q.put(LOUD)
    text, heard, aborted = controller._capture(audio_q, FakeRecognizer("study mode"))
    assert text == "study mode"
    assert heard is True
    assert aborted is False


def test_capture_aborts_when_talcon_is_speaking(monkeypatch):
    fast(monkeypatch)
    controller, _router, speech = make_controller()
    speech.is_speaking = True
    audio_q: queue.Queue = queue.Queue()
    audio_q.put(LOUD)
    text, heard, aborted = controller._capture(audio_q, FakeRecognizer("study mode"))
    assert text == ""
    assert aborted is True, "TALCON must never capture its own speech"


# -- session behaviour ------------------------------------------------------
def test_valid_command_executes_exactly_once(monkeypatch):
    controller, router, _speech = make_controller()
    controller._beep_and_settle = lambda audio_q: None
    controller._capture = lambda audio_q, recognizer: ("study mode", True, False)
    controller._run_command_session(queue.Queue(), FakeRecognizer())
    assert router.handled == [("study mode", False)]


def test_unintelligible_speech_is_ignored_without_nagging():
    controller, router, speech = make_controller()
    controller._beep_and_settle = lambda audio_q: None
    controller._capture = lambda audio_q, recognizer: ("", True, False)
    controller._run_command_session(queue.Queue(), FakeRecognizer())
    assert router.handled == []
    assert speech.said == [], "TALCON must not repeat a 'did not catch that' prompt"


def test_case_a_stays_silent(monkeypatch):
    controller, router, speech = make_controller()
    controller._beep_and_settle = lambda audio_q: None
    controller._capture = lambda audio_q, recognizer: ("", False, False)
    controller._run_command_session(queue.Queue(), FakeRecognizer())
    assert router.handled == []
    assert speech.said == []


# -- continuous listening ---------------------------------------------------
def continuous_controller():
    speech = FakeSpeech()
    router = FakeRouter(speech)
    controller = VoiceController(
        {"always_listen": True}, router, beep=lambda: None,
        event_callback=lambda kind, text: None,
    )
    return controller, router, speech


def test_continuous_mode_executes_a_command_without_a_wake_word():
    controller, router, _speech = continuous_controller()
    audio_q: queue.Queue = queue.Queue()
    action = controller._dispatch_transcript("study mode", audio_q, FakeRecognizer())
    assert action == "study"
    assert router.handled == [("study mode", False)]


def test_continuous_mode_ignores_unknown_speech_silently():
    controller, router, speech = continuous_controller()
    action = controller._dispatch_transcript(
        "random background conversation", queue.Queue(), FakeRecognizer()
    )
    assert action is None
    assert speech.said == []
    assert router.handled[-1][0] == "random background conversation"


def test_continuous_mode_joins_short_fragments():
    controller, router, _speech = continuous_controller()
    audio_q: queue.Queue = queue.Queue()
    assert controller._dispatch_transcript("open", audio_q, FakeRecognizer()) is None
    assert controller._dispatch_transcript("settings", audio_q, FakeRecognizer()) == "study"
    assert ("open settings", False) in router.handled


def test_continuous_mode_debounces_the_same_command():
    controller, router, _speech = continuous_controller()
    audio_q: queue.Queue = queue.Queue()
    assert controller._dispatch_transcript("mute", audio_q, FakeRecognizer()) == "study"
    assert controller._dispatch_transcript("mute", audio_q, FakeRecognizer()) is None
    assert router.handled.count(("mute", False)) == 1


def test_one_wake_produces_one_session_during_debounce(monkeypatch):
    controller, _router, _speech = make_controller()
    calls = []
    controller._run_command_session = lambda audio_q, recognizer=None: calls.append(time.monotonic())
    audio_q: queue.Queue = queue.Queue()
    recognizer = FakeRecognizer("talcon")

    thread = threading.Thread(target=controller._wake_loop, args=(audio_q, recognizer))
    thread.start()
    try:
        audio_q.put(LOUD)
        time.sleep(0.12)
        audio_q.put(LOUD)
        time.sleep(0.12)
    finally:
        controller._stop.set()
        thread.join(timeout=2.0)
    assert len(calls) == 1, "a second wake inside the debounce window must be ignored"


def test_manual_listen_requests_one_session():
    controller, _router, _speech = make_controller()
    gate = threading.Event()
    controller._thread = threading.Thread(target=gate.wait)
    controller._thread.start()
    try:
        controller.request_manual_listen()
        assert controller._manual.is_set()
    finally:
        gate.set()
        controller._thread.join(timeout=1.0)


# -- adaptive VAD, hypotheses and confidence --------------------------------
def test_adaptive_vad_starts_open_on_a_loud_first_word():
    from talcon.audio import UtteranceVad

    vad = UtteranceVad()
    for _ in range(4):
        vad.update(SILENT)
    state = vad.update(LOUD)
    assert state.speech is True
    assert state.threshold < 1000, "a measured quiet floor must keep quiet speech audible"


def test_capture_keeps_all_vosk_alternatives(monkeypatch):
    fast(monkeypatch)
    controller, _router, _speech = make_controller()
    payload = {
        "text": "open note pad",
        "alternatives": [
            {"text": "open note pad", "confidence": 0.71},
            {"text": "open notepad", "confidence": 0.63},
        ],
    }

    class AlternativeRecognizer(FakeRecognizer):
        def AcceptWaveform(self, data):
            return True

        def Result(self):
            return json.dumps(payload)

        def FinalResult(self):
            return json.dumps(payload)

    audio_q: queue.Queue = queue.Queue()
    audio_q.put(LOUD)
    controller._capture(audio_q, AlternativeRecognizer())
    assembly = controller._last_capture_assembly
    assert assembly is not None
    assert "open note pad" in assembly.candidates
    assert "open notepad" in assembly.candidates
    assert assembly.scores["open note pad"] == 0.71


def test_weak_hypothesis_never_executes():
    from talcon.phrase import Assembly

    controller, router, _speech = make_controller()
    assembly = Assembly(
        heard="study mode", matched="study mode",
        confidence=0.2, scores={"study mode": 0.2},
    )
    action = controller._execute_assembly(assembly, queue.Queue(), time.monotonic())
    assert action is None
    assert router.handled == []


def test_destructive_command_is_not_taken_from_a_lower_alternative():
    from talcon.phrase import Assembly

    controller, router, _speech = make_controller()

    class Registry:
        @staticmethod
        def match(text):
            from types import SimpleNamespace
            return SimpleNamespace(
                action="shutdown" if "shut" in text else "study"
            )

    router.registry = Registry()
    assembly = Assembly(
        heard="shut down", candidates=["shut down", "study mode"],
        confidence=0.4, scores={"shut down": 0.4, "study mode": 0.95},
    )
    action = controller._execute_assembly(assembly, queue.Queue(), time.monotonic())
    assert action is None, "a destructive command needs a confident primary hypothesis"
    assert router.handled == []


def test_generic_fallback_accepts_a_confident_natural_variation(monkeypatch):
    from talcon.phrase import Hypothesis

    controller, router, _speech = make_controller()
    controller._generic_hypotheses = lambda pcm: [Hypothesis("please study mode", 0.8)]
    action = controller._try_generic(LOUD, queue.Queue(), time.monotonic())
    assert action == "study"
    assert router.handled == [("study mode", False)]


def test_generic_fallback_rejects_low_confidence(monkeypatch):
    from talcon.phrase import Hypothesis

    controller, router, _speech = make_controller()
    controller._generic_hypotheses = lambda pcm: [Hypothesis("study mode", 0.2)]
    action = controller._try_generic(LOUD, queue.Queue(), time.monotonic())
    assert action is None
    assert router.handled == []


def test_apply_settings_updates_timing_without_reconnecting():
    controller, _router, _speech = make_controller()
    controller.apply_settings({
        "speech_start_timeout": 9.0,
        "speech_trailing_silence": 2.2,
        "speech_max_utterance": 25.0,
        "speech_min_confidence": 0.42,
    })
    assert controller._start_timeout == 9.0
    assert controller._trailing_silence == 2.2
    assert controller._max_utterance == 25.0
    assert controller._min_confidence == 0.42


def test_unsupported_app_word_is_replaced_by_a_decodable_spoken_form():
    class Model:
        @staticmethod
        def vosk_model_find_word(word):
            return 0 if word in {
                "open", "notepad", "chat", "g", "p", "t", "mute",
            } else -1

    controller, router, _speech = make_controller()
    controller._model = Model()
    router.app_aliases = {"chat gpt": "chatgpt"}
    kept = controller._drop_unsupported_app_phrases({
        "open notepad", "open chatgpt", "mute",
    })
    assert "open notepad" in kept
    assert "mute" in kept
    assert "open chat g p t" in kept
    assert router.app_aliases["chat g p t"] == "chatgpt"
    assert "open chatgpt" not in kept


def test_capture_trims_an_oversized_pcm_buffer_without_a_slice_error(monkeypatch):
    fast(monkeypatch)
    controller, _router, _speech = make_controller()
    controller._max_utterance = 0.5
    audio_q: queue.Queue = queue.Queue()
    for _ in range(20):
        audio_q.put(LOUD)
    text, heard, aborted = controller._capture(audio_q, FakeRecognizer("study mode"))
    assert text == "study mode"
    assert heard is True
    assert aborted is False


def test_vosk_likelihood_scores_are_treated_as_unknown_confidence():
    payload = {
        "text": "open notepad",
        "alternatives": [{"text": "open notepad", "confidence": 279.918518}],
    }
    hypotheses = voice._result_hypotheses(payload)
    assert all(item.confidence is None for item in hypotheses), (
        "likelihood scores are not probabilities and must never pass the gate"
    )


def test_long_transcripts_skip_the_generic_fallback():
    from talcon.phrase import Assembly

    controller, _router, _speech = make_controller()
    calls = []
    controller._generic_hypotheses = lambda pcm: calls.append(pcm) or []
    controller._execute_phrase = lambda phrase, primary=True: "unknown"
    chatter = " ".join(["word"] * 10)
    assembly = Assembly(heard=chatter, candidates=[], confidence=None, scores={})
    controller._dispatch_transcript(
        chatter, queue.Queue(), None, audio=LOUD, preassembled=assembly,
    )
    assert calls == [], "ambient chatter must not be re-decoded"


def test_continuous_mode_never_joins_two_utterances(monkeypatch):
    controller, router, _speech = make_controller({"always_listen": True})
    controller._model = None
    controller._grammar_dirty.clear()
    controller.apply_settings({"speech_trailing_silence": 0.1})

    class SequentialRecognizer:
        def __init__(self, texts):
            self.texts = list(texts)
            self.i = 0
            self.current = ""

        def AcceptWaveform(self, data):
            if self.i < len(self.texts):
                self.current = self.texts[self.i]
                self.i += 1
                return True
            return False

        def Result(self):
            return json.dumps({"text": self.current})

        def FinalResult(self):
            return json.dumps({"text": ""})

        def Reset(self):
            pass

    audio_q: queue.Queue = queue.Queue()
    for _ in range(4):
        audio_q.put(SILENT)
    recognizer = SequentialRecognizer(["get talcon", "terminal"])
    thread = threading.Thread(
        target=controller._listen_loop, args=(audio_q, recognizer), daemon=True
    )
    thread.start()
    time.sleep(1.2)
    controller._stop.set()
    thread.join(timeout=2.0)
    joined = [phrase for phrase, _announce in router.handled if "talcon terminal" in phrase]
    assert joined == [], "the second utterance must never join the first"


def test_unintelligible_final_still_triggers_the_generic_fallback(monkeypatch):
    controller, _router, _speech = make_controller({"always_listen": True})
    controller._model = None
    controller._grammar_dirty.clear()
    controller.apply_settings({"speech_trailing_silence": 0.1})

    class UnknownRecognizer(FakeRecognizer):
        def AcceptWaveform(self, data):
            return True

        def Result(self):
            return json.dumps({"text": "open [unk]"})

        def FinalResult(self):
            return json.dumps({"text": ""})

    attempts = []
    controller._lenient_app_action = lambda phrase: attempts.append(phrase) or None
    controller._generic_hypotheses = lambda pcm: []
    audio_q: queue.Queue = queue.Queue()
    for _ in range(4):
        audio_q.put(SILENT)
    audio_q.put(LOUD)
    thread = threading.Thread(
        target=controller._listen_loop, args=(audio_q, UnknownRecognizer("open [unk]")),
        daemon=True,
    )
    thread.start()
    time.sleep(1.0)
    controller._stop.set()
    thread.join(timeout=2.0)
    assert "open unk" in attempts, "a filtered final must still reach the fallback"


def test_never_finalised_speech_uses_the_running_partial():
    controller, _router, _speech = make_controller({"always_listen": True})
    controller._model = None
    controller._grammar_dirty.clear()
    controller.apply_settings({"speech_trailing_silence": 0.1})

    class PartialRecognizer(FakeRecognizer):
        def AcceptWaveform(self, data):
            return False  # Vosk never finalises

        def PartialResult(self):
            return json.dumps({"partial": "open youtube"})

        def FinalResult(self):
            return json.dumps({"text": ""})

    attempts = []
    controller._lenient_app_action = lambda phrase: attempts.append(phrase) or None
    audio_q: queue.Queue = queue.Queue()
    for _ in range(4):
        audio_q.put(SILENT)
    audio_q.put(LOUD)
    thread = threading.Thread(
        target=controller._listen_loop, args=(audio_q, PartialRecognizer()), daemon=True,
    )
    thread.start()
    time.sleep(1.0)
    controller._stop.set()
    thread.join(timeout=2.0)
    assert "open youtube" in attempts, "the running partial must be tried once"


def test_lenient_app_match_only_accepts_exact_names():
    from talcon.apps import AppEntry

    controller, _router, _speech = make_controller()
    said = []
    controller._execute_phrase = lambda phrase, primary=True: said.append(phrase) or "opened"

    class Library:
        @staticmethod
        def cached_entries():
            return [AppEntry(name="Notepad", kind="executable", path="C:/n.exe")]

    router = controller.command_router
    router.apps = Library()
    assert controller._lenient_app_action("can you open notepad") == "opened"
    assert said == ["open Notepad"]
    said.clear()
    # Fuzzy-only matches are never accepted from natural speech.
    assert controller._lenient_app_action("open not") is None
    assert said == []
    router.apps = None
    assert controller._lenient_app_action("open notepad") is None


def test_self_affecting_commands_require_the_primary_hypothesis():
    from types import SimpleNamespace

    from talcon.commands import CommandRouter

    controller, _router, speech = make_controller()
    controller.command_router = CommandRouter(
        SimpleNamespace(current_mode="normal"), speech, SimpleNamespace()
    )
    assert controller._needs_strong_primary("mute")
    assert controller._needs_strong_primary("mute yourself")
    assert controller._needs_strong_primary("stop speaking")
    assert controller._needs_strong_primary("unmute")
    assert not controller._needs_strong_primary("open settings")
    assert not controller._needs_strong_primary("what time is it")


def test_paused_commands_are_ignored_except_the_resume_phrase():
    from types import SimpleNamespace

    from talcon.commands import CommandRouter

    class _PausedApp:
        def __init__(self):
            self.commands_paused = True

        def set_commands_paused(self, value):
            self.commands_paused = bool(value)

    controller, _router, speech = make_controller()
    app = _PausedApp()
    controller.command_router = CommandRouter(
        SimpleNamespace(current_mode="normal"), speech, app
    )
    controller._execute_phrase("what time is it")
    assert speech.said == []
    controller._execute_phrase("open settings")
    assert speech.said == []
    assert controller._execute_phrase("take command") == "commands_on"
    assert app.commands_paused is False
    assert speech.said


def test_lenient_resume_phrase_works_while_paused():
    from types import SimpleNamespace

    from talcon.commands import CommandRouter

    class _PausedApp:
        def __init__(self):
            self.commands_paused = True

        def set_commands_paused(self, value):
            self.commands_paused = bool(value)

    controller, _router, speech = make_controller()
    app = _PausedApp()
    controller.command_router = CommandRouter(
        SimpleNamespace(current_mode="normal"), speech, app
    )
    # Not a registered alias, but unambiguously a resume request.
    assert controller._execute_phrase("you can take command now") == "commands_on"
    assert app.commands_paused is False

    # A pause phrase is never treated as a resume.
    app.commands_paused = True
    assert controller._execute_phrase("do not take commands") == "none"
    assert app.commands_paused is True
    assert speech.said

def test_clear_interruption_phrases_are_in_the_wake_grammar():
    import json as _json
    from talcon.voice import INTERRUPT_PHRASES, WAKE_GRAMMAR
    grammar = set(_json.loads(WAKE_GRAMMAR))
    assert {"be quiet", "stop talking", "stop speaking"} <= grammar
    assert {"stop", "no", "cancel"} & INTERRUPT_PHRASES == set()
