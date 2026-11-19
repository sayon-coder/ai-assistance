"""Natural-language bridge between TALCON's voice pipeline and Hermes.

The command pipeline always wins: :class:`talcon.commands.CommandRouter` calls
:meth:`AssistantController.handle_phrase` only after every local parser has
rejected the phrase, and only while the AI setting is enabled.  This module
owns the conversation's *request context* (a small RAM-only transcript used
to give Hermes follow-up memory) and the lifecycle of one in-flight Hermes
run; the spoken-response history shown in the UI lives in the runtime and is
fed by the speech engine itself.

Threading model: ``handle_phrase`` runs on the microphone thread and must
never block, so it only queues.  One daemon worker performs every network
call and hands results to the speech queue (which has its own thread).
"""
from __future__ import annotations

import itertools
import logging
import re
import threading
import time

from .hermes_client import (
    DEFAULT_HOST, DEFAULT_MODEL, DEFAULT_PORT,
    HermesAuthError, HermesClient, HermesConfigError, HermesError,
    HermesUnavailable,
)
from .assistant_cache import (
    DEFAULT_MAX_ENTRIES, DEFAULT_TTL_SECONDS, ResponseCache,
    context_fingerprint, is_cacheable, is_context_dependent,
    is_cacheable_request, to_speech_text,
)

log = logging.getLogger("talcon.assistant")

#: Conversation turns kept for follow-up context (RAM only, never persisted).
CONTEXT_TURNS = 6
#: Long dictations are trimmed to keep one request bounded.
MAX_REQUEST_CHARS = 900
#: Silence while Hermes asks for a dangerous-tool approval defaults to "no".
APPROVAL_TIMEOUT = 25.0
#: Hard ceiling on one agent run before TALCON gives up waiting.  A slow
#: model must never leave the user staring at "Let me think about that": the
#: request is stopped and reported instead.  Overridable with the
#: ``ai_timeout_seconds`` setting.
RUN_TIMEOUT = 75.0
#: How long the event stream may stay silent before it counts as stalled.
STREAM_IDLE_TIMEOUT = 45.0
#: Cache for the capabilities probe so every phrase does not hit the network.
CAPABILITIES_TTL = 60.0
#: How long a slow request may run before TALCON admits it is still thinking.
#: Cache hits answer far sooner, so they never trigger this filler.
ACK_DELAY = 1.2

#: Words that carry no request on their own; a phrase made only of these is
#: treated as noise rather than sent to the AI.
_FILLER_ONLY = frozenset({
    "um", "uh", "er", "erm", "hmm", "mm", "oh", "ah", "eh", "so", "well",
    "okay", "ok", "the", "a", "an", "and", "but", "or", "then", "like",
    "yeah", "yes", "no", "right", "i", "you", "me", "it", "is", "that",
    "this", "there", "here", "just", "really", "actually", "anyway",
})


class AssistantController:
    """Owns eligibility, one worker thread and the session's AI context."""

    def __init__(self, settings, speech, on_state=None, client_factory=None,
                 on_approval_wait=None, on_approval_done=None):
        #: callable returning the live settings dict
        self._settings = settings if callable(settings) else (lambda: dict(settings or {}))
        self.speech = speech
        #: callable(dict) publishing progress to the UI bridge
        self._on_state = on_state or (lambda event: None)
        #: hooks that arm/disarm the shared yes/no confirmation window while
        #: an approval is pending, so the existing spoken yes/no handling
        #: covers AI permission requests too.
        self._on_approval_wait = on_approval_wait or (lambda: None)
        self._on_approval_done = on_approval_done or (lambda: None)
        self._client_factory = client_factory or HermesClient

        self._lock = threading.RLock()
        self._queue: list[str] = []
        self._wake = threading.Event()
        self._worker: threading.Thread | None = None
        self._stopped = threading.Event()

        self._conversation: list[dict] = []          # RAM-only request context
        self._busy_phrase = ""                        # request currently running
        self._cancel = threading.Event()
        self._active_run_id = ""
        self._approval_answer = threading.Event()
        self._approval_value: bool | None = None
        self._approval_context = ""
        self._client: HermesClient | None = None
        self._client_signature: tuple = ()
        self._caps: tuple[dict, float] | None = None
        self._counter = itertools.count(1)
        # Bounded, RAM-only cache of complete informational answers.  It never
        # touches disk and is cleared on complete shutdown only.
        self._cache = ResponseCache()
        self._cache_signature: tuple = ()
        self._ack_timer: threading.Timer | None = None
        self._timed_out = False
        #: True when this request's answer was already spoken as it streamed.
        self._streamed_spoken = False

    # -- live configuration --------------------------------------------------
    def _setting(self, key: str, default=None):
        try:
            return self._settings().get(key, default)
        except Exception:
            return default

    def enabled(self) -> bool:
        return bool(self._setting("ai_enabled", False))

    def commands_paused(self) -> bool:
        return bool(self._setting("commands_paused", False))

    def is_busy(self) -> bool:
        with self._lock:
            if self._busy_phrase and not self._worker_alive():
                # The worker died with a request marked in flight.  Never leave
                # the UI (or the next question) waiting on a dead thread.
                log.warning("Assistant worker vanished; recovering")
                self._busy_phrase = ""
                self._queue.clear()
                self._approval_context = ""
                self._worker = None
            return bool(self._busy_phrase) or bool(self._queue)

    def _worker_alive(self) -> bool:
        worker = self._worker
        return bool(worker is not None and worker.is_alive())

    def state(self) -> dict:
        with self._lock:
            return {
                "enabled": self.enabled(),
                "busy": bool(self._busy_phrase),
                "phrase": self._busy_phrase,
                "approval": bool(self._approval_context),
            }

    # -- entry points (called from the voice thread) ---------------------------
    def handle_phrase(self, phrase: str) -> bool:
        """Take an unmatched natural-language phrase.  True if handled.

        Called by the command router only after every local command, alias,
        app and custom automation has failed to match, so local behaviour can
        never be shadowed by the assistant.
        """
        text = phrase if isinstance(phrase, str) else ""
        text = text.strip()
        if not text or not self.enabled() or self.commands_paused():
            return False
        if not self.is_eligible(text):
            return False
        with self._lock:
            self._queue.append(text)
        self._emit("accepted", text)
        # A short filler only appears if the backend is genuinely slow; cache
        # hits answer immediately and must not be preceded by "thinking".
        self._schedule_ack(text)
        self._ensure_worker()
        return True

    def handle_control(self, phrase: str) -> bool:
        """Consume 'stop that' / 'never mind' while a request is in flight."""
        text = " ".join(str(phrase or "").lower().replace("?", "").split())
        if not self.is_busy() and not self._approval_pending():
            return False
        if text in {
            "stop that", "never mind", "nevermind", "cancel that", "stop it",
            "forget it", "cancel the request", "stop the request",
            "abort that", "just stop", "stop helping",
        }:
            self.cancel()
            self._safe_say("Stopped that request, sir.")
            self._emit("cancelled", "")
            return True
        return False

    def cancel(self) -> None:
        """Ask the worker and the server to abandon the active run."""
        with self._lock:
            self._queue.clear()
            self._cancel.set()
            run_id = self._active_run_id
            # Claim the active run so only one stop request is ever sent.
            self._active_run_id = ""
            client = self._client
            self._answer_approval(False)
        if run_id and client is not None:
            try:
                client.stop_run(run_id)
            except Exception:
                log.debug("Could not stop the Hermes run in time", exc_info=True)

    # -- approval workflow -----------------------------------------------------
    def _approval_pending(self) -> bool:
        with self._lock:
            return bool(self._approval_context)

    def answer_approval(self, approved: bool) -> bool:
        """Called with the spoken yes/no for a pending dangerous operation."""
        with self._lock:
            if not self._approval_context:
                return False
            self._approval_value = bool(approved)
            self._approval_context = ""
            self._approval_answer.set()
            return True

    # -- eligibility --------------------------------------------------------
    @staticmethod
    def is_eligible(text: str) -> bool:
        """True for anything a person plausibly meant as a request.

        The assistant runs only after every local command, alias, application
        and custom automation has failed, so the job here is simply to keep
        single words and pure filler out — everything else the user said is
        offered to the AI, which is what makes misremembered commands work.
        """
        words = text.lower().replace("?", " ").replace(",", " ").split()
        if len(words) < 2 or len(text) > MAX_REQUEST_CHARS:
            return False
        return any(word.strip(".,!?;:") not in _FILLER_ONLY for word in words)

    # -- the worker -----------------------------------------------------------
    def _ensure_worker(self) -> None:
        with self._lock:
            if self._stopped.is_set():
                return
            if self._worker is not None and self._worker.is_alive():
                self._wake.set()
                return
            self._worker = threading.Thread(target=self._worker_loop,
                                            name="talcon-assistant", daemon=True)
            self._worker.start()

    def _worker_loop(self) -> None:
        from .win_threads import lower_current_thread

        lower_current_thread()
        while not self._stopped.is_set():
            with self._lock:
                phrase = self._queue.pop(0) if self._queue else None
                if phrase is not None:
                    self._busy_phrase = phrase
                    self._cancel.clear()
                    self._approval_value = None
                    self._approval_answer.clear()
                    # Consume the wake-up: a level-triggered flag left set
                    # would turn the idle wait below into a hot spin.
                    self._wake.clear()
            if phrase is None:
                self._wake.wait(0.5)
                self._wake.clear()   # always clear, set or timed out
                with self._lock:
                    # Re-check under the lock so a phrase queued between the
                    # pop and this moment cannot be stranded.
                    if not self._queue:
                        self._worker = None
                        return
                continue
            try:
                self._run_request(phrase)
            except BaseException:
                # BaseException too: a thread must never die holding the busy
                # flag, or every later question would be silently ignored.
                log.exception("Assistant request failed")
                self._fail("Sorry, the AI assistant hit an unexpected error.")
            finally:
                with self._lock:
                    self._busy_phrase = ""
                    self._approval_context = ""

    def _get_client(self) -> HermesClient | None:
        signature = (
            str(self._setting("hermes_host", DEFAULT_HOST)),
            int(self._setting("hermes_port", DEFAULT_PORT) or DEFAULT_PORT),
            str(self._setting("hermes_model", DEFAULT_MODEL)),
        )
        with self._lock:
            if self._client is not None and self._client_signature == signature:
                return self._client
        try:
            client = self._client_factory(
                host=signature[0], port=signature[1], model=signature[2]
            )
        except HermesConfigError as exc:
            log.warning("Hermes is not configured: %s", exc)
            self._fail(
                "The local AI is not configured yet, sir. Open Voice Settings "
                "and check the Hermes address and API key."
            )
            return None
        except Exception:
            log.exception("Could not build the Hermes client")
            self._fail("The AI assistant is not configured correctly.")
            return None
        with self._lock:
            self._client = client
            self._client_signature = signature
        self._caps = None
        return client

    def _features(self, client: HermesClient) -> dict:
        now = time.monotonic()
        if self._caps and now - self._caps[1] < CAPABILITIES_TTL:
            return self._caps[0]
        try:
            caps = client.capabilities()
        except Exception:
            caps = {"features": {}}
        self._caps = (caps, now)
        return caps

    def _run_request(self, phrase: str) -> None:
        self._configure_cache()
        self._streamed_spoken = False
        # Look the answer up before any client work: a hit needs no backend at
        # all, so it costs zero network requests and answers immediately.
        key, fingerprint = self._cache_key(phrase)
        if key:
            cached = self._cache.get(key, fingerprint)
            if cached is not None:
                log.info("Assistant cache hit for %r", phrase[:120])
                self._emit("cache_hit", phrase)
                self._deliver(phrase, cached)
                return

        client = self._get_client()
        if client is None:
            return
        # Fail fast when the local gateway is not usable instead of leaving the
        # user listening to "Let me think about that" for a whole timeout.  A
        # installed-but-stopped Hermes is started here, automatically.
        failure = self._ensure_backend(client)
        if failure:
            self._fail(failure)
            return
        features = self._features(client).get("features", {})
        timeout = self._request_timeout()
        self._timed_out = False
        watchdog = threading.Timer(timeout, self._expire_request)
        watchdog.daemon = True
        watchdog.start()
        tools_used = False
        try:
            # A spoken question expects a spoken answer.  Hermes's optional
            # run/SSE interface is for longer tool workflows and, in practice,
            # can remain open after the user has already heard the thinking
            # acknowledgement.  Keep it opt-in; direct chat has a bounded
            # request and a single, reliable final response.
            use_agent_runs = bool(self._setting("ai_use_agent_runs", False))
            if (use_agent_runs and features.get("run_submission")
                    and features.get("run_events_sse")):
                answer, tools_used = self._via_runs(client, phrase, timeout)
            else:
                answer, tools_used = self._via_chat(client, phrase, timeout)
        except HermesUnavailable:
            self._fail(
                "I could not reach the local AI gateway on "
                f"{getattr(client, 'host', '127.0.0.1')}:"
                f"{getattr(client, 'port', DEFAULT_PORT)}, sir. Open Voice "
                "Settings to check it."
            )
            return
        except HermesAuthError:
            self._fail("The local AI gateway rejected the API key, sir. "
                       "Please check the key in your Hermes .env file.")
            return
        except HermesError as exc:
            log.warning("Hermes request failed: %s", client.redact(exc))
            self._fail("The AI assistant could not complete that request.")
            return
        finally:
            watchdog.cancel()
        if self._timed_out:
            self._fail(f"That took longer than {timeout:.0f} seconds, sir, "
                       "so I stopped it. Please try a shorter question.")
            return
        if self._cancel.is_set():
            self._emit("cancelled", phrase)
            return
        answer = to_speech_text(str(answer or ""))
        if not answer:
            self._fail("The assistant did not return an answer.")
            return
        if key and is_cacheable(phrase, answer, tools_used):
            if self._cache.put(key, answer, fingerprint):
                log.info("Assistant answer cached (%d entries)", len(self._cache))
        spoken = bool(getattr(self, "_streamed_spoken", False))
        self._deliver(phrase, answer, spoken=spoken)

    def _deliver(self, phrase: str, answer: str, spoken: bool = False) -> None:
        """Publish one final answer: context, UI events and speech.

        Cache hits and fresh answers share this path exactly, so a hit still
        becomes its own History entry, still updates the preview, and is still
        spoken by the local Kokoro speech engine.

        ``spoken`` is True when a streamed answer was already queued sentence
        by sentence; the transcript is then published without repeating it.
        """
        self._cancel_ack()
        self._remember(phrase, answer)
        self._emit("answered", answer)
        if spoken:
            announce = getattr(self.speech, "announce", None)
            if callable(announce):
                try:
                    announce(answer)
                except Exception:
                    log.exception("Assistant could not publish the answer")
                return
            return
        # The speech engine's utterance hook publishes this text to the
        # preview and the session history; nothing else needs to be wired.
        self._safe_say(answer)

    # -- backends ---------------------------------------------------------
    def _request_timeout(self) -> float:
        try:
            value = float(self._setting("ai_timeout_seconds", RUN_TIMEOUT))
        except (TypeError, ValueError):
            value = RUN_TIMEOUT
        return max(15.0, min(300.0, value))

    def _expire_request(self) -> None:
        """Watchdog: stop a request that outlived its budget."""
        with self._lock:
            if not self._busy_phrase:
                return
            self._timed_out = True
            phrase = self._busy_phrase
        log.warning("Assistant request timed out (%r); stopping it", phrase[:120])
        self.cancel()

    def _ensure_backend(self, client: HermesClient) -> str:
        """Return ``""`` when the gateway is usable, else a spoken reason.

        A Hermes install whose gateway is simply not running is started here.
        That is the common case on a fresh sign-in: the files and key are all
        present, only nothing is listening on the loopback API port.
        """
        try:
            client.health(timeout=2.5)
            return ""
        except HermesAuthError:
            return ("The local AI gateway is running but rejected the API key, "
                    "sir. Please check the key in your Hermes .env file.")
        except HermesUnavailable:
            pass
        except HermesError:
            # The server answered, just not with a documented /health payload.
            return ""

        from .hermes_service import ensure_running, installed

        host = str(getattr(client, "host", "127.0.0.1"))
        port = int(getattr(client, "port", DEFAULT_PORT) or DEFAULT_PORT)
        can_autostart = bool(
            getattr(client, "allow_autostart", isinstance(client, HermesClient))
        )
        if (not bool(self._setting("ai_autostart_backend", True))
                or not can_autostart):
            detail = (f"Nothing is listening on {host}:{port}."
                      if not installed() else
                      "The Hermes gateway is installed but not running.")
            log.warning("Hermes unavailable: %s", detail)
            return (f"I cannot reach the local AI gateway on {host}:{port}, "
                    "sir. Please start Hermes, or switch on automatic start in "
                    "Voice Settings.")

        self._emit("progress", "Starting the local AI gateway…")
        # The start can take up to half a minute; say why instead of leaving the
        # generic "Let me think about that" filler to explain the wait.
        self._cancel_ack()
        self._safe_say("Starting the local AI gateway, sir. One moment.")
        started, detail = ensure_running(
            host, port, autostart=True,
            on_progress=lambda text: self._emit("progress", text),
        )
        if not started:
            log.warning("Hermes gateway did not start: %s", detail)
            if not installed():
                return ("The local AI gateway is not running, sir, and Hermes "
                        "is not installed on this PC. Open Voice Settings for "
                        "the AI setup.")
            return ("I tried to start the local Hermes gateway but it did not "
                    "answer, sir. Open Voice Settings and press Test "
                    "connection for the exact reason.")

        # The port is open; give the authorised health check a few tries while
        # the gateway finishes loading its agent.
        deadline = time.monotonic() + 10.0
        while time.monotonic() < deadline:
            try:
                client.health(timeout=3.0)
                self._caps = None
                return ""
            except HermesAuthError:
                return ("The local AI gateway started but rejected the API "
                        "key, sir. Please check your Hermes .env file.")
            except HermesError:
                time.sleep(0.5)
        return ("The gateway opened its port but did not answer the health "
                "check, sir. Please look at the Hermes gateway log.")

    def _via_runs(self, client: HermesClient, phrase: str,
                  timeout: float = RUN_TIMEOUT) -> tuple[str, bool]:
        request_id = f"talcon-{next(self._counter)}-{int(time.time())}"
        submission = client.submit_run(self._prompt(phrase),
                                       idempotency_key=request_id)
        run_id = str(submission.get("run_id"))
        with self._lock:
            self._active_run_id = run_id
        self._emit("working", phrase)

        state = {"text": "", "failed": "", "tools": False}

        def on_event(event: dict) -> None:
            name = str(event.get("event") or "")
            if name == "message.delta":
                state["text"] += str(event.get("delta") or "")
            elif name == "tool.started":
                # Any tool use means the answer is a report about an operation,
                # never a reusable informational answer.
                state["tools"] = True
                tool = str(event.get("tool") or "a tool")
                self._emit("progress", f"Using {tool}")
            elif name == "tool.completed":
                self._emit("progress", "")
            elif name in ("run.failed", "run.cancelled"):
                state["failed"] = name
            elif name == "run.completed":
                output = event.get("output")
                if isinstance(output, str) and output.strip():
                    state["text"] = output.strip()
            elif "approval" in name and name.endswith("requested"):
                state["tools"] = True
                description = str(
                    event.get("description") or event.get("reason")
                    or event.get("command") or event.get("tool")
                    or "a tool operation that needs permission"
                )
                approval_id = str(event.get("approval_id")
                                  or event.get("request_id") or "")
                approved = self.request_approval(description, approval_id)
                try:
                    client.respond_approval(run_id, approved, approval_id)
                except HermesError as exc:
                    log.warning("Approval response failed: %s",
                                client.redact(exc))

        client.stream_run_events(run_id, on_event, cancel=self._cancel,
                                 max_seconds=timeout + 2.0,
                                 idle_seconds=min(STREAM_IDLE_TIMEOUT, timeout))
        with self._lock:
            # ``cancel()`` clears the id when it already sent the stop; only a
            # race that beat the assignment needs a stop from here.
            need_stop = self._active_run_id == run_id and self._cancel.is_set()
            if self._active_run_id == run_id:
                self._active_run_id = ""
        if need_stop:
            try:
                client.stop_run(run_id)
            except Exception:
                pass
        if self._cancel.is_set():
            return "", state["tools"]
        if state["failed"]:
            raise HermesError(state["failed"])
        text = state["text"].strip()
        if not text:
            status = client.run_status(run_id)
            text = str(status.get("output") or "").strip()
            if status.get("status") == "failed":
                raise HermesError("run failed")
        return text, bool(state["tools"])

    def _via_chat(self, client: HermesClient, phrase: str,
                  timeout: float = RUN_TIMEOUT) -> tuple[str, bool]:
        messages = [{"role": "system", "content": self._system_prompt()}]
        for turn in self._context_turns():
            messages.append(turn)
        messages.append({"role": "user", "content": phrase})
        self._emit("working", phrase)
        if (bool(self._setting("ai_streaming", True))
                and hasattr(client, "chat_stream")):
            text = self._via_chat_stream(client, messages, timeout)
            if text:
                return text, False
            # The server ignored ``stream`` and returned no text; fall back to
            # the ordinary request so the user still gets an answer.
            self._emit("working", phrase)
        return client.chat(messages, timeout=timeout), False

    def _via_chat_stream(self, client: HermesClient, messages: list[dict],
                         timeout: float) -> str:
        """Speak the answer sentence by sentence while it is still arriving."""
        buffer = ""
        spoken_any = False

        def speak_sentence(sentence: str) -> None:
            nonlocal spoken_any
            sentence = sentence.strip()
            if not sentence:
                return
            say = getattr(self.speech, "say", None)
            if not callable(say):
                return
            try:
                say(sentence, announce=False)
            except TypeError:
                say(sentence)
            spoken_any = True

        def drain(final: bool = False) -> None:
            nonlocal buffer
            while True:
                match = re.search(r"[.!?](?=\s|$)", buffer)
                if match is None:
                    break
                speak_sentence(buffer[:match.end()])
                buffer = buffer[match.end():].lstrip()
            # A long unpunctuated answer still needs to start speaking early.
            while len(buffer) >= 180:
                cut = buffer.rfind(" ", 0, 180)
                if cut < 60:
                    cut = 180
                speak_sentence(buffer[:cut])
                buffer = buffer[cut:].lstrip()
            if final and buffer.strip():
                speak_sentence(buffer)
                buffer = ""

        def on_delta(piece) -> None:
            nonlocal buffer
            buffer += str(piece or "")
            drain()

        text = client.chat_stream(
            messages, timeout=timeout, on_delta=on_delta, cancel=self._cancel
        )
        drain(final=True)
        self._streamed_spoken = bool(spoken_any)
        return str(text or "").strip()

    # -- RAM conversation context ----------------------------------------
    def _system_prompt(self) -> str:
        return (
            "You are the answer engine behind TALCON, a local desktop voice "
            "assistant, and your reply is read aloud. Answer in at most three "
            "short sentences (about 45 words) of plain spoken prose: no "
            "markdown, no bullet lists, no code fences, no emoji. Lead with the "
            "direct answer. If the topic genuinely needs more, give the short "
            "answer and say you can explain further. When the user asks you to "
            "DO something on the computer, use your tools, then confirm in one "
            "sentence."
        )

    def _prompt(self, phrase: str) -> str:
        with self._lock:
            prior = list(self._conversation)[-(CONTEXT_TURNS * 2):]
        if not prior:
            return phrase
        lines = [self._system_prompt(), "", "Conversation so far:"]
        for turn in prior:
            who = "User" if turn.get("role") == "user" else "Assistant"
            lines.append(f"{who}: {turn.get('content', '')}")
        lines.append("")
        lines.append(f"User: {phrase}")
        return "\n".join(lines)

    def _context_turns(self) -> list[dict]:
        with self._lock:
            return list(self._conversation)

    def _remember(self, phrase: str, answer: str) -> None:
        with self._lock:
            self._conversation.append({"role": "user", "content": phrase})
            self._conversation.append(
                {"role": "assistant", "content": answer[:1200]}
            )
            overflow = len(self._conversation) - CONTEXT_TURNS * 2
            if overflow > 0:
                del self._conversation[:overflow]

    def clear_context(self) -> None:
        with self._lock:
            self._conversation.clear()

    # -- response cache -----------------------------------------------------
    def _cache_enabled(self) -> bool:
        return bool(self._setting("ai_cache_enabled", True))

    def _configure_cache(self) -> None:
        """Apply capacity/TTL settings, rebuilding only when they change."""
        try:
            entries = int(self._setting("ai_cache_entries", DEFAULT_MAX_ENTRIES))
            ttl = float(self._setting("ai_cache_ttl_seconds", DEFAULT_TTL_SECONDS))
        except (TypeError, ValueError):
            entries, ttl = DEFAULT_MAX_ENTRIES, DEFAULT_TTL_SECONDS
        signature = (entries, ttl)
        if signature == self._cache_signature:
            return
        self._cache.configure(max_entries=entries, ttl_seconds=ttl)
        self._cache_signature = signature
        log.info("Assistant cache configured: %d entries, %.0fs TTL",
                 self._cache.max_entries, self._cache.ttl_seconds)

    def _cache_key(self, phrase: str, model: str = "") -> tuple[str, str]:
        """Return ``(key, fingerprint)`` or ``("", "")`` when ineligible.

        Self-contained questions use a stable fingerprint, so asking the same
        factual question twice is a hit.  Follow-ups are tied to the exact
        conversation they belong to, so the same words in a different
        conversation can never reuse the earlier answer.
        """
        if not self._cache_enabled():
            return "", ""
        if not is_cacheable_request(phrase):
            self._cache.note_bypass()
            return "", ""
        if is_context_dependent(phrase):
            with self._lock:
                prior = [dict(turn) for turn in self._conversation]
            fingerprint = context_fingerprint(prior)
        else:
            fingerprint = "standalone"
        model = model or self._setting("hermes_model", "") or ""
        options = f"ctx={CONTEXT_TURNS}"
        key = ResponseCache.make_key(phrase, fingerprint, model, options)
        return key, fingerprint

    def cache_stats(self) -> dict:
        stats = self._cache.stats()
        stats["enabled"] = self._cache_enabled()
        return stats

    def clear_cache(self) -> None:
        self._cache.clear()

    # -- deferred acknowledgment -------------------------------------------
    def _schedule_ack(self, phrase: str) -> None:
        """Say a filler line only when the request is actually slow."""
        timer = threading.Timer(ACK_DELAY, self._ack_if_pending, args=(phrase,))
        timer.daemon = True
        self._ack_timer = timer
        timer.start()

    def _ack_if_pending(self, phrase: str) -> None:
        with self._lock:
            if not self._busy_phrase or self._busy_phrase != phrase:
                return
        self._emit("ack", phrase)
        self._safe_say("Let me think about that.")

    def _cancel_ack(self) -> None:
        timer = self._ack_timer
        self._ack_timer = None
        if timer is not None:
            try:
                timer.cancel()
            except Exception:
                pass

    def conversation(self) -> list[dict]:
        with self._lock:
            return [dict(turn) for turn in self._conversation]

    # -- approvals --------------------------------------------------------
    def request_approval(self, description: str, approval_id: str = "") -> bool:
        """Block the worker until the user answers; silence means deny."""
        with self._lock:
            self._approval_context = description
            self._approval_value = None
            self._approval_answer.clear()
        self._emit("approval", description)
        self._on_approval_wait()
        try:
            self._safe_say(
                f"That needs your approval: {description} "
                "Should I go ahead? Say yes or no."
            )
            answered = self._approval_answer.wait(APPROVAL_TIMEOUT)
        finally:
            self._on_approval_done()
        if not answered:
            with self._lock:
                self._approval_context = ""
                self._approval_value = None
            self._safe_say("I did not hear an answer, so I left it alone.")
            return False
        with self._lock:
            approved = bool(self._approval_value)
            self._approval_context = ""
        del approval_id
        return approved

    def _answer_approval(self, approved: bool) -> None:
        if self._approval_context:
            self._approval_value = bool(approved)
            self._approval_context = ""
            self._approval_answer.set()

    # -- plumbing -----------------------------------------------------------
    def _fail(self, message: str) -> None:
        self._cancel_ack()
        self._emit("error", message)
        self._safe_say(message)

    def _emit(self, kind: str, text: str) -> None:
        try:
            self._on_state({"kind": kind, "text": str(text or "")})
        except Exception:
            log.debug("Assistant state callback failed", exc_info=True)

    def _safe_say(self, text: str) -> None:
        try:
            self.speech.say(text)
        except Exception:
            log.exception("Assistant could not speak")

    def shutdown(self) -> None:
        self._stopped.set()
        self.cancel()
        self._cancel_ack()
        self.clear_context()
        # Complete shutdown only: hiding to the tray keeps the cache warm.
        self.clear_cache()
        worker = self._worker
        if worker is not None and worker.is_alive() and worker is not threading.current_thread():
            worker.join(timeout=2.0)
        self._worker = None
