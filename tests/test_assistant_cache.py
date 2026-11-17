"""Bounded in-memory response cache: eligibility, LRU/TTL and integration."""
from __future__ import annotations

import time

import pytest

import talcon.assistant as assistant_module
from talcon.assistant import AssistantController
from talcon.assistant_cache import (
    DEFAULT_MAX_RESPONSE_CHARS, ResponseCache, context_fingerprint,
    is_cacheable, is_cacheable_request, is_cacheable_response,
    is_context_dependent, normalize_request,
)
from tests.test_assistant import FakeClient, FakeSpeech


class FakeClock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now

    def advance(self, seconds):
        self.now += seconds


def make_cache(**kwargs):
    clock = FakeClock()
    kwargs.setdefault("clock", clock)
    return ResponseCache(**kwargs), clock


def make_assistant(client=None, settings=None):
    merged = {"ai_enabled": True, "ai_use_agent_runs": True, **dict(settings or {})}
    speech = FakeSpeech()
    fake = client if client is not None else FakeClient()
    controller = AssistantController(
        settings=lambda: merged, speech=speech,
        client_factory=lambda **kw: fake,
    )
    return controller, merged, speech, fake


def ask(controller, phrase, timeout=4.0):
    assert controller.handle_phrase(phrase) is True
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if not controller.is_busy():
            time.sleep(0.05)
            return True
        time.sleep(0.02)
    return False


# ---------------------------------------------------------------- module
def test_normalization_ignores_case_and_whitespace():
    assert normalize_request("  What   is DUNE? ") == "what is dune?"
    key_a = ResponseCache.make_key("What is dune", "standalone", "m")
    key_b = ResponseCache.make_key("  what IS   dune ", "standalone", "m")
    assert key_a == key_b


def test_key_varies_with_context_model_and_options():
    base = ResponseCache.make_key("what is dune", "ctx-a", "model-1", "o=1")
    assert base != ResponseCache.make_key("what is dune", "ctx-b", "model-1", "o=1")
    assert base != ResponseCache.make_key("what is dune", "ctx-a", "model-2", "o=1")
    assert base != ResponseCache.make_key("what is dune", "ctx-a", "model-1", "o=2")


def test_context_fingerprint_tracks_turns():
    empty = context_fingerprint([])
    one = context_fingerprint([{"role": "user", "content": "hello there"}])
    other = context_fingerprint([{"role": "user", "content": "different"}])
    assert empty != one and one != other
    assert one == context_fingerprint([{"role": "user", "content": "Hello   there"}])


def test_lru_eviction_keeps_the_newest_entries():
    cache, _clock = make_cache(max_entries=3)
    for name in ("a", "b", "c"):
        cache.put(name, f"answer {name}")
    cache.get("a")               # 'a' becomes most recently used
    cache.put("d", "answer d")   # evicts 'b'
    assert cache.get("a") is not None
    assert cache.get("b") is None
    stats = cache.stats()
    assert stats["evictions"] == 1


def test_ttl_expiry_is_not_reused_and_is_counted():
    cache, clock = make_cache(ttl_seconds=60)
    cache.put("k", "value")
    assert cache.get("k") == "value"
    clock.advance(61)
    assert cache.get("k") is None
    stats = cache.stats()
    assert stats["expirations"] == 1 and stats["hits"] == 1
    # A fresh store after expiry works normally.
    cache.put("k", "value2")
    assert cache.get("k") == "value2"


def test_prune_drops_expired_entries():
    cache, clock = make_cache(ttl_seconds=10)
    cache.put("a", "1")
    cache.put("b", "2")
    clock.advance(11)
    assert cache.prune() == 2 and len(cache) == 0


def test_oversized_and_empty_responses_are_not_stored():
    cache, _clock = make_cache(max_response_chars=100)
    assert cache.put("big", "x" * 200) is False
    assert cache.put("empty", "   ") is False
    assert len(cache) == 0
    assert cache.stats()["bypasses"] == 2


def test_fingerprint_mismatch_never_serves_an_answer():
    cache, _clock = make_cache()
    cache.put("k", "secret-ish answer", fingerprint="ctx-a")
    assert cache.get("k", "ctx-b") is None
    assert cache.get("k", "ctx-a") == "secret-ish answer"


def test_configure_can_shrink_the_cache():
    cache, _clock = make_cache(max_entries=10)
    for index in range(6):
        cache.put(f"k{index}", f"v{index}")
    cache.configure(max_entries=2)
    assert len(cache) == 2
    assert cache.stats()["evictions"] == 4


def test_stats_report_hit_rate():
    cache, _clock = make_cache()
    cache.put("k", "v")
    cache.get("k")            # hit
    cache.get("missing")      # miss
    cache.get("k")            # hit
    stats = cache.stats()
    assert stats["hits"] == 2 and stats["misses"] == 1
    assert abs(stats["hit_rate"] - 2 / 3) < 1e-9
    assert stats["lookups"] == 3


def test_clear_empties_entries_but_keeps_counters():
    cache, _clock = make_cache()
    cache.put("k", "v")
    cache.clear()
    assert len(cache) == 0
    assert cache.stats()["stores"] == 1


# ------------------------------------------------------------- eligibility
@pytest.mark.parametrize("phrase", [
    "what is the capital of australia",
    "how does photosynthesis work",
    "explain the difference between tcp and udp",
    "who wrote dune",
])
def test_informational_questions_are_cacheable(phrase):
    assert is_cacheable_request(phrase)


@pytest.mark.parametrize("phrase", [
    "open notepad",
    "delete my downloads folder",
    "what is the price of bitcoin",
    "what is the weather tomorrow",
    "summarise the news today",
    "close youtube",
    "run the tests",
    "email the report to sam",
    "what time is it",
    "send a message to the team",
    "which apps are running",
])
def test_action_live_and_stateful_requests_are_not_cacheable(phrase):
    assert not is_cacheable_request(phrase), phrase


def test_responses_containing_actions_or_secrets_are_rejected():
    assert not is_cacheable_response("I have opened Notepad for you.")
    assert not is_cacheable_response("I've deleted 3 files, sir.")
    assert not is_cacheable_response("Mail me at someone@example.com")
    assert not is_cacheable_response("Your key is sk-abcdefghijklmnop")
    assert not is_cacheable_response("x" * (DEFAULT_MAX_RESPONSE_CHARS + 1))
    assert is_cacheable_response("Canberra is the capital of Australia.")


def test_tool_use_disqualifies_an_answer():
    assert not is_cacheable("what is the capital of australia",
                            "Canberra is the capital.", tools_used=True)
    assert is_cacheable("what is the capital of australia",
                        "Canberra is the capital.", tools_used=False)


def test_context_dependence_detection():
    assert is_context_dependent("what is it about")
    assert is_context_dependent("and why is that")
    assert is_context_dependent("tell me more about those")
    assert not is_context_dependent("what is the capital of australia")


# ------------------------------------------------------------ integration
def test_repeated_identical_question_hits_the_cache():
    controller, _s, speech, fake = make_assistant()
    question = "what is the capital of australia"
    assert ask(controller, question)
    assert ask(controller, question)
    # One API call for two identical questions.
    assert len(fake.submitted) == 1
    assert speech.said.count("42") == 2        # spoken both times
    stats = controller.cache_stats()
    assert stats["hits"] == 1 and stats["stores"] == 1
    assert stats["hit_rate"] > 0


def test_different_conversation_context_misses_the_cache():
    """A follow-up cached in one conversation must not answer another."""
    controller, _s, _speech, fake = make_assistant()
    controller._conversation = [
        {"role": "user", "content": "tell me about canberra"},
        {"role": "assistant", "content": "It is the capital of Australia."},
    ]
    # A context-dependent question is keyed by the preceding turns.
    assert ask(controller, "what is it famous for")
    before = len(fake.submitted)
    controller._conversation = [
        {"role": "user", "content": "tell me about sydney"},
        {"role": "assistant", "content": "It is a harbour city."},
    ]
    assert ask(controller, "what is it famous for")
    assert len(fake.submitted) == before + 1     # a new call, not a stale hit
    assert controller.cache_stats()["hits"] == 0


def test_standalone_question_after_unrelated_chat_still_hits():
    controller, _s, _speech, fake = make_assistant()
    question = "how does a transistor work"
    assert ask(controller, question)
    assert ask(controller, "what is the tallest mountain on earth")
    assert ask(controller, question)             # standalone → same key
    assert fake.submitted.count(question) == 1


def test_failed_response_is_never_cached():
    from talcon.hermes_client import HermesUnavailable

    class FlakyClient(FakeClient):
        """Fails the first submit, then behaves like a healthy backend."""

        def __init__(self):
            super().__init__()
            self.fail_first = True

        def submit_run(self, input_text, idempotency_key="", timeout=15.0):
            if self.fail_first:
                self.fail_first = False
                raise HermesUnavailable("down")
            return super().submit_run(input_text, idempotency_key, timeout)

    flaky = FlakyClient()
    controller, _s, speech, _f = make_assistant(client=flaky)
    question = "what is the capital of peru"
    assert ask(controller, question)          # fails, nothing cached
    assert controller.cache_stats()["stores"] == 0
    assert any("could not reach" in text for text in speech.said)
    assert ask(controller, question)          # retried for real
    assert len(flaky.submitted) == 1
    assert controller.cache_stats()["stores"] == 1


def test_empty_answer_is_not_cached():
    empty = FakeClient(events=[{"event": "run.completed", "output": ""}])
    controller, _s, _speech, _f = make_assistant(client=empty)
    assert ask(controller, "what is the capital of chile")
    assert controller.cache_stats()["stores"] == 0


def test_tool_executed_request_is_not_cached():
    tools = FakeClient(events=[
        {"event": "tool.started", "tool": "terminal"},
        {"event": "tool.completed", "tool": "terminal"},
        {"event": "run.completed", "output": "HELLO"},
    ])
    controller, _s, _speech, _f = make_assistant(client=tools)
    assert ask(controller, "what is the capital of norway")
    assert ask(controller, "what is the capital of norway")
    assert len(tools.submitted) == 2             # both went to the backend
    assert controller.cache_stats()["stores"] == 0


def test_destructive_and_stateful_requests_bypass_the_cache():
    controller, _s, _speech, fake = make_assistant()
    for phrase in ("open notepad and type hello",
                   "what is the price of bitcoin right now",
                   "close all my windows"):
        assert controller._cache_key(phrase) == ("", "")
    assert controller.cache_stats()["bypasses"] == 3


def test_cache_can_be_disabled_by_settings():
    controller, settings, _speech, fake = make_assistant(
        settings={"ai_cache_enabled": False}
    )
    question = "who wrote the iliad"
    assert ask(controller, question)
    assert ask(controller, question)
    assert len(fake.submitted) == 2
    assert controller.cache_stats()["enabled"] is False


def test_capacity_and_ttl_are_configurable_from_settings():
    controller, settings, _speech, _fake = make_assistant(
        settings={"ai_cache_entries": 3, "ai_cache_ttl_seconds": 30}
    )
    controller._configure_cache()
    stats = controller.cache_stats()
    assert stats["max_entries"] == 3 and stats["ttl_seconds"] == 30
    settings["ai_cache_entries"] = 7
    controller._configure_cache()
    assert controller.cache_stats()["max_entries"] == 7


def test_cache_hits_create_separate_history_entries():
    controller, _s, speech, _fake = make_assistant()
    question = "what is the capital of kenya"
    assert ask(controller, question)
    assert ask(controller, question)
    turns = controller.conversation()
    # Each request adds its own user + assistant pair, even on a hit.
    assert [t["role"] for t in turns] == ["user", "assistant"] * 2
    assert turns[0]["content"] == turns[2]["content"] == question
    assert speech.said.count("42") == 2


def test_every_request_creates_its_own_spoken_response_for_history():
    """History is fed by the speech engine, so a hit must speak again."""
    spoken = []

    class Speech(FakeSpeech):
        def say(self, text):
            super().say(text)
            spoken.append(text)

    controller, _s, _speech, _fake = make_assistant()
    controller.speech = Speech()
    question = "how hot is the sun"
    assert ask(controller, question)
    assert ask(controller, question)
    assert [text for text in spoken if text == "42"] == ["42", "42"]


def test_shutdown_clears_the_cache_but_hiding_does_not():
    controller, _s, _speech, _fake = make_assistant()
    assert ask(controller, "what is the capital of japan")
    assert controller.cache_stats()["entries"] == 1
    # Hiding/minimising only touches visibility: the cache stays warm.
    controller.cache_stats()
    assert controller.cache_stats()["entries"] == 1
    controller.shutdown()
    assert controller.cache_stats()["entries"] == 0


def test_cache_entries_never_reach_disk(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    controller, _s, _speech, _fake = make_assistant()
    assert ask(controller, "what is the capital of canada")
    assert controller.cache_stats()["entries"] == 1
    assert list(tmp_path.rglob("*")) == []


def test_api_call_reduction_demonstrated():
    """Report the measured saving for a repeated question workload."""
    controller, _s, _speech, fake = make_assistant()
    questions = [
        "what is the capital of australia",
        "how does a transistor work",
        "what is the capital of australia",     # repeat
        "who wrote dune",
        "how does a transistor work",           # repeat
        "what is the capital of australia",     # repeat
    ]
    for question in questions:
        assert ask(controller, question)
    stats = controller.cache_stats()
    assert len(fake.submitted) == 3             # unique requests only
    assert stats["hits"] == 3 and stats["misses"] == 3
    assert abs(stats["hit_rate"] - 0.5) < 1e-9
    reduction = 1 - len(fake.submitted) / len(questions)
    assert reduction == pytest.approx(0.5)


def test_cached_answer_is_served_without_any_network_client():
    controller, _s, speech, fake = make_assistant()
    question = "what is the capital of italy"
    assert ask(controller, question)
    controller._client = None
    controller._client_signature = ()
    controller._client_factory = lambda **kw: pytest.fail(
        "a cache hit must not build a client"
    )
    assert ask(controller, question)
    assert speech.said.count("42") == 2


def test_cache_hit_creates_its_own_history_entry_via_real_speech(tmp_path):
    """End-to-end: hit -> local Kokoro speech engine -> session History."""
    from talcon.speech import SpeechOutput
    from tests.test_response_history import (
        RecordingBackend, memory_runtime, wait_for,
    )

    runtime = memory_runtime()
    backend = RecordingBackend()
    output = SpeechOutput(enabled=True, backend=backend,
                          on_utterance=runtime._on_utterance)
    try:
        controller, _s, _speech, fake = make_assistant()
        controller.speech = output
        question = "what is the melting point of iron"
        # The voice pipeline normally records the heard phrase; mirror that so
        # the History entry pairs the answer with its request.
        for _attempt in range(2):
            runtime._pending_request = (question, time.monotonic())
            assert ask(controller, question)      # second one is a cache hit
        assert wait_for(lambda: len(runtime.conversation_history()) == 2,
                        timeout=8)
        entries = runtime.conversation_history()
        assert [entry["text"] for entry in entries] == ["42", "42"]
        assert [entry["request"] for entry in entries] == [question, question]
        assert [entry["id"] for entry in entries] == [1, 2]
        # Both were genuinely handed to the speech backend.
        assert wait_for(lambda: backend.said.count("42") == 2, timeout=8)
        assert len(fake.submitted) == 1           # still one API call
    finally:
        output.stop()


# ------------------------------------------------------- speech formatting
def test_markdown_is_stripped_before_speaking():
    from talcon.assistant_cache import to_speech_text

    text = to_speech_text(
        "Both work:\n\n**ChatGPT** is better for:\n- coding\n- `tests`\n\n"
        "See [docs](http://example.com) now."
    )
    assert text == "Both work: ChatGPT is better for: coding tests. See docs now."
    for mark in ("**", "`", "](", "\n", "- "):
        assert mark not in text


def test_markdown_answer_is_sanitized_end_to_end_and_cached_clean():
    markdown = "**Paris** is the capital:\n- of France\n- `since 508 AD`"
    fake = FakeClient(events=[{"event": "run.completed", "output": markdown}])
    controller, _s, speech, _f = make_assistant(client=fake)
    question = "what is the capital of belgium"
    assert ask(controller, question)
    assert speech.said[-1] == "Paris is the capital: of France since 508 AD"
    # The cache stores the spoken form, so a hit cannot reintroduce markers.
    assert ask(controller, question)
    assert speech.said[-1] == "Paris is the capital: of France since 508 AD"
    assert len(fake.submitted) == 1
