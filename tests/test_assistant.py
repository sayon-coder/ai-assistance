"""Assistant routing, lifecycle and approval tests with a fake Hermes client."""
from __future__ import annotations

import threading
import time

import pytest

import talcon.assistant as assistant_module
from talcon.assistant import CONTEXT_TURNS, AssistantController
from talcon.hermes_client import HermesUnavailable


class FakeSpeech:
    def __init__(self):
        self.said: list[str] = []
        self._lock = threading.Lock()

    def say(self, text):
        with self._lock:
            self.said.append(text)


class FakeClient:
    """Scripted stand-in implementing the HermesClient surface used here."""

    def __init__(self, events=None, chat_answer="chat answer",
                 run_answer=None, fail=None, hold=False):
        self.events = events if events is not None else [
            {"event": "message.delta", "delta": "42"},
            {"event": "run.completed", "output": "42"},
        ]
        self.chat_answer = chat_answer
        self.run_answer = run_answer
        self.fail = fail              # exception raised by submit_run/chat
        self.hold = hold              # stream until cancelled, never completes
        self.submitted: list[str] = []
        self.chatted: list[list] = []
        self.stopped: list[str] = []
        self.approvals: list[tuple] = []
        self.last_prompt = ""

    def capabilities(self, timeout=6.0):
        return {"features": {"run_submission": True, "run_events_sse": True}}

    def health(self, timeout=4.0):
        if self.fail:
            raise self.fail
        return {"status": "ok"}

    def submit_run(self, input_text, idempotency_key="", timeout=15.0):
        if self.fail:
            raise self.fail
        self.last_prompt = input_text
        self.submitted.append(input_text)
        return {"run_id": "run-1", "status": "started", "replayed": False}

    def stream_run_events(self, run_id, on_event, cancel=None,
                          max_seconds=0, idle_seconds=0):
        if self.hold:
            deadline = time.monotonic() + 5
            while not cancel.is_set() and time.monotonic() < deadline:
                time.sleep(0.02)
            return False
        for event in self.events:
            on_event(event)
        return True

    def run_status(self, run_id, timeout=10.0):
        return {"status": "completed", "output": self.run_answer or ""}

    def stop_run(self, run_id, timeout=10.0):
        self.stopped.append(run_id)
        return {"status": "cancelled"}

    def respond_approval(self, run_id, approved, approval_id="", timeout=10.0):
        self.approvals.append((bool(approved), approval_id))
        return {"ok": True}

    def chat(self, messages, timeout=90.0):
        if self.fail:
            raise self.fail
        self.chatted.append(messages)
        return self.chat_answer

    def redact(self, text):
        return str(text)


def make(settings=None, client=None, **kwargs):
    # Most controller tests cover the optional agent-run path explicitly.
    settings = {"ai_enabled": True, "ai_use_agent_runs": True, **dict(settings or {})}
    speech = FakeSpeech()
    states: list[dict] = []
    fake = client if client is not None else FakeClient()
    controller = AssistantController(
        settings=lambda: settings, speech=speech,
        on_state=states.append, client_factory=lambda **kw: fake, **kwargs,
    )
    return controller, settings, speech, states, fake


def wait_until(predicate, timeout=4.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.02)
    return False


# -- gating ----------------------------------------------------------------
def test_disabled_assistant_never_accepts_a_phrase():
    controller, _s, speech, _st, fake = make({"ai_enabled": False})
    assert controller.handle_phrase("what is the tallest mountain?") is False
    assert fake.submitted == [] and speech.said == []


def test_paused_commands_block_the_assistant():
    controller, _s, speech, _st, fake = make({"commands_paused": True})
    assert controller.handle_phrase("why is the sky blue?") is False
    assert fake.submitted == [] and speech.said == []


@pytest.mark.parametrize("phrase", [
    "weather",                       # single word
    "the the the",                   # filler only
    "okay so um",                    # filler only
    "x" * 2000,                      # absurd length
])
def test_ineligible_phrases_are_not_sent_to_the_ai(phrase):
    controller, _s, _speech, _st, fake = make()
    assert controller.handle_phrase(phrase) is False
    assert fake.submitted == []


@pytest.mark.parametrize("phrase", [
    "what is the tallest mountain on earth",
    "why does my keyboard stop working after sleep",
    "how do I clear the dns cache on windows",
    "can you summarize the notes folder for me",
    "difference between a tuple and a list in python",
    # Anything the local parsers could not place is offered to the AI, which
    # is what makes a half-remembered command still resolve.
    "open the pod bay doors hal",
    "deep speak is better than chatgpt",
    "close youtube and open brave",
])
def test_genuine_questions_and_requests_are_eligible(phrase):
    assert AssistantController.is_eligible(phrase)


def test_single_words_and_filler_stay_local():
    for phrase in ("yes", "no", "talcon", "first", "um", "okay"):
        assert not AssistantController.is_eligible(phrase), phrase


# -- happy path --------------------------------------------------------------
def test_question_is_answered_spoken_and_recorded():
    controller, _s, speech, states, fake = make()
    assert controller.handle_phrase("what is six times seven") is True
    assert wait_until(lambda: not controller.is_busy())
    assert fake.submitted == ["what is six times seven"]
    assert "42" in speech.said
    # A filler is only spoken when the backend is actually slow; this fake
    # answers instantly, so the answer is the first (and only) utterance.
    assert speech.said[0] == "42"
    assert "Let me think about that." not in speech.said
    kinds = [event["kind"] for event in states]
    assert "accepted" in kinds and "answered" in kinds
    assert controller.conversation() == [
        {"role": "user", "content": "what is six times seven"},
        {"role": "assistant", "content": "42"},
    ]


def test_follow_up_prompt_carries_the_ram_context():
    controller, _s, _speech, _st, fake = make()
    controller.handle_phrase("who wrote dune")
    assert wait_until(lambda: not controller.is_busy())
    controller.handle_phrase("what is it about")
    assert wait_until(lambda: len(fake.submitted) == 2)
    prompt = fake.submitted[1]
    assert "Conversation so far:" in prompt
    assert "User: who wrote dune" in prompt
    assert "Assistant: 42" in prompt
    assert prompt.rstrip().endswith("User: what is it about")


def test_slow_request_speaks_a_filler_first():
    fake = FakeClient(hold=True)
    controller, _s, speech, _st, _f = make(client=fake)
    controller.handle_phrase("can you explain how compilers work")
    assert wait_until(lambda: speech.said == ["Let me think about that."],
                      timeout=4)
    controller.cancel()
    assert wait_until(lambda: not controller.is_busy())


def test_cancelled_request_never_speaks_the_filler():
    fake = FakeClient(hold=True)
    controller, _s, speech, _st, _f = make(client=fake)
    controller.handle_phrase("can you explain how sorting works")
    assert wait_until(lambda: controller.is_busy())
    controller.cancel()
    assert wait_until(lambda: not controller.is_busy())
    assert "Let me think about that." not in speech.said


def test_context_is_trimmed_to_recent_turns():
    controller, _s, _speech, _st, fake = make()
    for index in range(CONTEXT_TURNS + 4):
        controller._remember(f"question number {index}", f"answer {index}")
    turns = controller.conversation()
    assert len(turns) == CONTEXT_TURNS * 2
    assert turns[0]["content"] != "question number 0"


def test_chat_fallback_when_runs_api_is_unavailable():
    fake = FakeClient()
    fake.capabilities = lambda timeout=6.0: {"features": {}}
    controller, _s, speech, _st, _f = make(client=fake)
    controller.handle_phrase("what does chat fallback do")
    assert wait_until(lambda: not controller.is_busy())
    assert "chat answer" in speech.said
    assert fake.chatted and fake.chatted[0][0]["role"] == "system"


def test_text_only_protocol_no_audio_or_files_ever_sent():
    controller, _s, _speech, _st, fake = make()
    controller.handle_phrase("how are you today friend")
    assert wait_until(lambda: not controller.is_busy())
    prompt = fake.submitted[0]
    assert isinstance(prompt, str)
    assert prompt.isascii() or "data:" not in prompt  # no base64 blobs either


# -- failure paths ------------------------------------------------------------
def test_backend_down_gets_a_polite_spoken_notice():
    fake = FakeClient(fail=HermesUnavailable("down"))
    controller, _s, speech, states, _f = make(client=fake)
    controller.handle_phrase("why is my internet slow")
    assert wait_until(lambda: not controller.is_busy())
    assert any("cannot reach the local ai gateway" in text.lower()
               for text in speech.said)
    assert any(event["kind"] == "error" for event in states)
    # Nothing was remembered from a failed exchange.
    assert controller.conversation() == []


# -- cancellation ---------------------------------------------------------------
def test_stop_that_cancels_the_running_request():
    fake = FakeClient(hold=True)
    controller, _s, speech, states, _f = make(client=fake)
    controller.handle_phrase("can you browse every settings page slowly")
    # Wait until the request is genuinely in flight on the backend.
    assert wait_until(lambda: bool(fake.submitted))
    assert wait_until(lambda: controller.state()["busy"])
    assert controller.handle_control("stop that") is True
    assert wait_until(lambda: not controller.is_busy())
    assert fake.stopped == ["run-1"]
    assert any(event["kind"] == "cancelled" for event in states)
    assert any("stopped that request" in text.lower() for text in speech.said)


def test_stop_before_the_request_starts_drops_it_without_calling_hermes():
    fake = FakeClient(hold=True)
    controller, _s, speech, states, _f = make(client=fake)
    controller.handle_phrase("can you browse every settings page slowly")
    assert controller.handle_control("stop that") is True   # still queued
    assert wait_until(lambda: not controller.is_busy())
    assert fake.submitted == []          # nothing was ever sent upstream
    assert any("stopped that request" in text.lower() for text in speech.said)


def test_control_phrases_do_not_fire_when_idle():
    controller, _s, _speech, _st, _fake = make()
    assert controller.handle_control("stop that") is False


def test_unrelated_speech_is_not_a_cancel():
    fake = FakeClient(hold=True)
    controller, _s, _speech, _st, _f = make(client=fake)
    controller.handle_phrase("explain how compilers work in depth")
    assert wait_until(lambda: controller.is_busy())
    assert controller.handle_control("tell me a story instead") is False
    controller.cancel()
    assert wait_until(lambda: not controller.is_busy())


# -- approvals ----------------------------------------------------------------
def test_spoken_yes_allows_a_dangerous_operation(monkeypatch):
    monkeypatch.setattr(assistant_module, "APPROVAL_TIMEOUT", 5.0)
    fake = FakeClient(events=[
        {"event": "approval.requested", "approval_id": "a1",
         "description": "delete files in Temp"},
        {"event": "run.completed", "output": "deleted"},
    ])
    armed: list[str] = []
    controller, _s, speech, _st, _f = make(
        client=fake,
        on_approval_wait=lambda: armed.append("wait"),
        on_approval_done=lambda: armed.append("done"),
    )
    controller.handle_phrase("can you clean out my temp folder")
    assert wait_until(lambda: bool(controller._approval_pending()))
    assert armed and armed[0] == "wait"
    assert any("needs your approval" in text for text in speech.said)
    assert controller.answer_approval(True) is True
    assert wait_until(lambda: not controller.is_busy())
    assert fake.approvals and fake.approvals[0] == (True, "a1")
    assert "wait" in armed and "done" in armed


def test_spoken_no_denies_and_timeout_defaults_to_deny(monkeypatch):
    monkeypatch.setattr(assistant_module, "APPROVAL_TIMEOUT", 0.4)
    fake = FakeClient(events=[
        {"event": "approval.requested", "approval_id": "a2",
         "description": "shut the machine down"},
        {"event": "run.completed", "output": "ok"},
    ])
    controller, _s, speech, _st, _f = make(client=fake)
    controller.handle_phrase("could you shut the machine down")
    assert wait_until(lambda: bool(controller._approval_pending()))
    assert controller.answer_approval(False) is True
    assert wait_until(lambda: not controller.is_busy())
    assert fake.approvals[0] == (False, "a2")

    # And when nobody answers: deny, and tell the user it was left alone.
    fake2 = FakeClient(events=[
        {"event": "approval.requested", "approval_id": "a3",
         "description": "erase the disk"},
        {"event": "run.completed", "output": "nope"},
    ])
    controller2, _s2, speech2, _st2, _f2 = make(client=fake2)
    controller2.handle_phrase("can you erase the disk completely")
    assert wait_until(lambda: bool(controller2._approval_pending()))
    assert wait_until(lambda: not controller2.is_busy(), timeout=5)
    assert fake2.approvals[0] == (False, "a3")
    assert any("left it alone" in text for text in speech2.said)


def test_answer_approval_is_ignored_when_none_is_pending():
    controller, _s, _speech, _st, _fake = make()
    assert controller.answer_approval(True) is False


# -- lifecycle ------------------------------------------------------------
def test_clear_context_empties_the_ram_history():
    controller, _s, _speech, _st, fake = make()
    controller.handle_phrase("what is the capital of france")
    assert wait_until(lambda: controller.conversation())
    controller.clear_context()
    assert controller.conversation() == []


def test_shutdown_stops_worker_and_clears_history():
    controller, _s, _speech, _st, fake = make()
    controller.handle_phrase("what is the capital of peru")
    assert wait_until(lambda: controller.conversation())
    controller.shutdown()
    assert controller.conversation() == []
    assert controller._worker is None or not controller._worker.is_alive()


def test_state_snapshot_reflects_the_settings():
    settings = {
        "ai_enabled": True,
        "commands_paused": False,
        "ai_use_agent_runs": True,
    }
    speech = FakeSpeech()
    fake = FakeClient(hold=True)
    controller = AssistantController(settings=lambda: settings, speech=speech,
                                     client_factory=lambda **kw: fake)
    controller.handle_phrase("can you walk me through the migration plan")
    assert wait_until(lambda: controller.state()["busy"])
    assert controller.state()["enabled"] is True
    controller.cancel()
    assert wait_until(lambda: not controller.state()["busy"])


def test_worker_idles_out_instead_of_spinning():
    """Regression: a stuck wake flag must never burn CPU in a hot loop."""
    controller, _s, _speech, _st, fake = make()
    assert controller.handle_phrase("what is the tallest mountain")
    assert wait_until(lambda: not controller.is_busy())
    # With the wake event consumed, the idle poll expires and the thread ends.
    assert wait_until(lambda: controller._worker is None, timeout=4.0)


# -- timeouts and self-healing ------------------------------------------------
def test_slow_request_is_stopped_and_reported(monkeypatch):
    """A stalled backend must not leave TALCON 'thinking' forever."""
    fake = FakeClient(hold=True)
    controller, _s, speech, states, _f = make(client=fake)
    # The real setting clamps to 15s; override the accessor for a fast test.
    controller._request_timeout = lambda: 0.5
    assert controller.handle_phrase("can you plan my entire year for me")
    assert wait_until(lambda: not controller.is_busy(), timeout=6)
    assert any("took longer than" in text.lower() for text in speech.said)
    assert fake.stopped == ["run-1"]          # the run was actually stopped
    assert any(event["kind"] == "error" for event in states)


def test_a_dead_worker_never_blocks_the_next_question():
    controller, _s, speech, _st, fake = make()
    # Simulate a worker that died while a request was in flight.
    controller._busy_phrase = "a question that killed the thread"
    controller._worker = None
    assert controller.is_busy() is False      # self-heals instead of hanging
    assert controller.handle_phrase("what is the capital of spain") is True
    assert wait_until(lambda: not controller.is_busy())
    assert "42" in speech.said


def test_timeout_setting_bounds_the_wait():
    controller, settings, _speech, _st, _fake = make(
        settings={"ai_timeout_seconds": 30}
    )
    assert controller._request_timeout() == 30.0
    settings["ai_timeout_seconds"] = 5      # clamped to the safe minimum
    assert controller._request_timeout() == 15.0
    settings["ai_timeout_seconds"] = 9999   # and to the safe maximum
    assert controller._request_timeout() == 300.0


def test_request_after_a_timeout_still_works():
    """A timed-out request must not poison the next one."""
    class OnceSlow(FakeClient):
        def __init__(self):
            super().__init__()
            self.slow_once = True

        def stream_run_events(self, run_id, on_event, cancel=None,
                              max_seconds=0, idle_seconds=0):
            if self.slow_once:
                self.slow_once = False
                deadline = time.monotonic() + 5
                while not cancel.is_set() and time.monotonic() < deadline:
                    time.sleep(0.02)
                return False
            return super().stream_run_events(run_id, on_event, cancel,
                                             max_seconds, idle_seconds)

    fake = OnceSlow()
    controller, _s, speech, states, _f = make(client=fake)
    controller._request_timeout = lambda: 0.5
    assert controller.handle_phrase("can you plan my entire year for me")
    assert wait_until(lambda: not controller.is_busy(), timeout=6)
    assert any("took longer than" in text.lower() for text in speech.said)

    speech.said.clear()
    assert controller.handle_phrase("what is the capital of sweden") is True
    assert wait_until(lambda: not controller.is_busy())
    assert speech.said == ["42"]              # the next answer still arrives
    assert controller._timed_out is False


# -- streaming and automatic gateway start ------------------------------------
class FakeStreamingClient(FakeClient):
    def chat_stream(self, messages, timeout=90.0, on_delta=None, cancel=None):
        self.chatted.append(messages)
        pieces = ("Paris is the capital. ", "It sits on the Seine.")
        for piece in pieces:
            if on_delta:
                on_delta(piece)
        return "Paris is the capital. It sits on the Seine."


class AnnounceSpeech(FakeSpeech):
    def __init__(self):
        super().__init__()
        self.announced: list[str] = []

    def say(self, text, announce=True):
        super().say(text)

    def announce(self, text):
        self.announced.append(text)


def test_streamed_answer_is_spoken_in_sentences_and_logged_once():
    speech = AnnounceSpeech()
    states: list[dict] = []
    controller = AssistantController(
        settings=lambda: {"ai_enabled": True, "ai_streaming": True},
        speech=speech,
        on_state=states.append,
        client_factory=lambda **kw: FakeStreamingClient(),
    )
    assert controller.handle_phrase("what is the capital of france")
    assert wait_until(lambda: not controller.is_busy())
    assert speech.said == ["Paris is the capital.", "It sits on the Seine."]
    assert speech.announced == ["Paris is the capital. It sits on the Seine."]
    assert any(event["kind"] == "answered" for event in states)


class FakeDownClient(FakeClient):
    allow_autostart = True
    host = "127.0.0.1"
    port = 8642

    def __init__(self):
        super().__init__()
        self.health_calls = 0

    def health(self, timeout=4.0):
        self.health_calls += 1
        if self.health_calls <= 2:
            raise HermesUnavailable("down")
        return {"status": "ok"}


def test_stopped_gateway_is_started_automatically(monkeypatch):
    import talcon.hermes_service as service

    calls: list = []
    monkeypatch.setattr(service, "installed", lambda: True)
    monkeypatch.setattr(
        service, "ensure_running",
        lambda *a, **k: (calls.append((a, k)), (True, "started"))[1],
    )
    controller, _s, speech, states, _f = make(
        settings={"ai_use_agent_runs": False, "ai_autostart_backend": True},
        client=FakeDownClient(),
    )
    assert controller.handle_phrase("what is the weather today")
    assert wait_until(lambda: not controller.is_busy())
    assert calls, "the gateway start must be attempted"
    assert any(event["kind"] == "progress" for event in states)
    assert any("chat answer" in text for text in speech.said)
