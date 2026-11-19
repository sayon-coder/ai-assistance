"""Fully offline wake-word ("TALCON") and command capture using Vosk.

The wake interaction is deliberately silent: say "TALCON", hear one short
confirmation beep, then speak the command.  Microphone input is gated while
the beep plays, stale audio is discarded, and recognition pauses whenever
TALCON's speech output is actually speaking so TALCON never hears itself.

No Picovoice key, cloud service or extra model is used.  The existing small
English Vosk model is loaded once per process.
"""
from __future__ import annotations

import array
import json
import logging
import math
import queue
import re
import threading
import time

from .app_catalog import normalize_app_name, resolve_application
from .asr import (
    ENGINE_SHERPA,
    create_streaming_recognizer,
    engine_model_ready,
    engine_reason,
    resolve_engine,
)
from .asr import sherpa_engine as sherpa_asr
from .asr import whisper_engine
from .audio import AudioFrontEnd, AdaptiveVad, UtteranceVad, calibrate_microphone, pcm_stats
from .commands import (
    build_recognition_grammar,
    is_command_like,
    normalize_phrase,
    strip_wake_phrase,
)
from .model_setup import model_is_ready, resolve_model
from .phrase import Hypothesis, PHRASE_TIMEOUT, PhraseAssembler

log = logging.getLogger("talcon.voice")

SAMPLE_RATE = 16000
BLOCK_SIZE = 1600  # 0.10 s at 16 kHz (finer endpointing, still cheap)
WAKE_ALIASES = ("talcon", "tell can", "tal con", "talc on", "talk on", "talcon")
WAKE_GRAMMAR = json.dumps([
    "talcon", "tell can", "tal con", "talc on", "talk on", "talcon",
    "hey tell can", "okay tell can",
    "tell can introduce yourself", "introduce yourself",
    # Clear interruption phrases, so speech can be stopped by voice even while
    # the microphone is otherwise ignoring TALCON's own audio.
    "stop talking", "stop speaking", "be quiet", "quiet", "shut up",
    "enough", "that is enough", "[unk]",
])

#: Only these whole phrases interrupt playback.  A long, specific phrase is
#: used deliberately: TALCON must never cut its own answer short by hearing a
#: single stray word in the room.
INTERRUPT_PHRASES = frozenset({
    "stop talking", "stop speaking", "be quiet", "quiet", "shut up",
    "enough", "that is enough",
})
WAKE_DEBOUNCE = 1.5          # seconds; one wake can never start two sessions
BEEP_GATE_SECONDS = 0.45     # microphone is ignored while the beep plays
SETTLE_SECONDS = 0.30        # discard residual audio after the beep
COMMAND_START_TIMEOUT = 7.0  # wait this long for speech to begin
COMMAND_SILENCE = 1.0        # end the command after this much trailing silence
COMMAND_MAX_SECONDS = 20.0   # hard upper bound for one command
SPEECH_RMS_THRESHOLD = 300   # legacy fallback; adaptive VAD is used at runtime
MIN_SPEECH_BLOCKS = 2
MIN_FINAL_BLOCKS = 1         # voiced blocks required before a final can run
N_ALTERNATIVES = 5           # Vosk's own hypotheses, confidence-ranked
DESTRUCTIVE_ALTERNATIVES = 2  # exit/quit may only come from the best two
PRIMARY_MIN_CONFIDENCE = 0.30
ALTERNATIVE_MIN_CONFIDENCE = 0.55
DESTRUCTIVE_MIN_CONFIDENCE = 0.65
GENERIC_MIN_CONFIDENCE = 0.45
GENERIC_MAX_SECONDS = 20.0
#: The generic fallback only re-decodes the tail of an utterance.
GENERIC_FALLBACK_SECONDS = 6.0
#: Spoken commands are short; longer transcripts are never re-decoded.
GENERIC_FALLBACK_MAX_WORDS = 6
COMMAND_DEBOUNCE = 2.0       # the same command cannot run twice this quickly
REPEAT_COOLDOWN = 15.0       # never nag more than once per this window
REPEAT_MESSAGE = "Sorry, I didn't catch that command. Please repeat."
DESTRUCTIVE_ACTIONS = {"exit", "shutdown", "confirm_shutdown", "confirm_close_all"}
#: Commands that change how TALCON itself behaves.  They are only taken from
#: the best hypothesis, never from a lower-ranked alternative inside a longer,
#: noisy utterance (a misheard "mute yourself" must not silence the assistant).
STRONG_PRIMARY_ACTIONS = {"mute", "unmute", "wake_off", "close_eyes"}
_TOKEN = re.compile(r"[a-z']+")

_MODEL_CACHE: dict[str, object] = {}

#: Conservative spoken forms for common compound app names the small model
#: cannot decode as one token.  A variant is used only when every word in it
#: exists in the installed model.
_COMPOUND_SPLITS = {
    "chatgpt": ("chat g p t",),
    "gpt": ("g p t",),
    "deepai": ("deep ai",),
    "whatsapp": ("whats app",),
    "onedrive": ("one drive",),
    "vscode": ("vs code",),
    "deepseek": ("deep seek",),
    "coarena": ("co arena",),
    "tallyprime": ("tally prime",),
}


def _load_model(path: str):
    from vosk import Model, SetLogLevel

    if path not in _MODEL_CACHE:
        SetLogLevel(-1)
        log.info("Loading Vosk model from %s", path)
        _MODEL_CACHE[path] = Model(path)
    return _MODEL_CACHE[path]


def tokens(text: str) -> list[str]:
    return _TOKEN.findall(str(text or "").lower())


def is_wake_phrase(text: str) -> bool:
    """Exact token match only: "TALCON" (or "hey TALCON"), never "talcony"."""
    words = tokens(text)
    if not words:
        return False
    padded = " " + " ".join(words) + " "
    return any(f" {alias} " in padded for alias in WAKE_ALIASES)


def _has_unknown(text: str) -> bool:
    return "[unk]" in str(text or "").lower()


def _sane_confidence(value) -> float | None:
    """Map a Vosk score to 0..1, or None when it is not a probability.

    Some Vosk builds report alternative/word scores as raw likelihoods
    (values like 279.9 or 881.7).  Clamping those to 1.0 would make every
    transcript look fully confident, so out-of-range scores mean "unknown"
    and only an exact grammar match may execute them.
    """
    try:
        score = float(value)
    except (TypeError, ValueError):
        return None
    if score < 0.0 or score > 1.0:
        return None
    return score


def _payload_confidence(payload: dict) -> float | None:
    """Vosk's sentence confidence, or the mean of its word confidences."""
    if "confidence" in payload:
        return _sane_confidence(payload["confidence"])
    words = payload.get("result")
    if isinstance(words, list):
        values = []
        for item in words:
            if isinstance(item, dict) and item.get("conf") is not None:
                score = _sane_confidence(item["conf"])
                if score is not None:
                    values.append(score)
        if values:
            return sum(values) / len(values)
    return None


def _result_hypotheses(payload) -> list[Hypothesis]:
    """Return the primary text, alternatives and their confidence scores."""
    if not isinstance(payload, dict):
        return []
    primary_confidence = _payload_confidence(payload)
    hypotheses: list[Hypothesis] = []
    primary = str(payload.get("text", "") or "").strip()
    if primary:
        hypotheses.append(Hypothesis(primary, primary_confidence))
    for alternative in payload.get("alternatives", []) or []:
        if not isinstance(alternative, dict):
            continue
        text = str(alternative.get("text", "") or "").strip()
        if not text:
            continue
        confidence = alternative.get("confidence", alternative.get("conf"))
        hypotheses.append(Hypothesis(text, _sane_confidence(confidence)))
    return hypotheses


def _result_texts(payload) -> list[str]:
    """Return the primary text and Vosk's own alternatives, best first."""
    return [item.text for item in _result_hypotheses(payload)]


def _rms(pcm: bytes) -> float:
    """Root-mean-square level of 16-bit mono PCM without a heavy dependency."""
    return pcm_stats(pcm).rms


def _play_beep() -> None:
    try:
        import winsound

        winsound.PlaySound("SystemAsterisk", winsound.SND_ALIAS | winsound.SND_ASYNC)
    except Exception:
        log.debug("Confirmation beep unavailable", exc_info=True)


def list_microphones() -> list[tuple[int | None, str]]:
    result: list[tuple[int | None, str]] = [(None, "System default microphone")]
    try:
        import sounddevice as sd

        seen = set()
        for index, device in enumerate(sd.query_devices()):
            if int(device.get("max_input_channels", 0)) <= 0:
                continue
            name = str(device.get("name", "Microphone")).strip()
            key = name.lower()
            if key in seen:
                continue
            seen.add(key)
            result.append((index, name))
    except Exception:
        log.exception("Could not enumerate microphones")
    return result


def measure_input(seconds: float = 1.2, device: int | None = None) -> dict:
    """Record a short sample (never stored) and report its level for setup tests."""
    try:
        import numpy as np
        import sounddevice as sd

        frames = max(1, int(seconds * SAMPLE_RATE))
        recording = sd.rec(frames, samplerate=SAMPLE_RATE, channels=1, dtype="int16", device=device)
        sd.wait()
        data = np.abs(recording.astype("int32"))
        peak = int(data.max()) if data.size else 0
        rms = int(np.sqrt(float((data.astype("float64") ** 2).mean()))) if data.size else 0
        return {"ok": True, "peak": peak, "rms": rms, "error": ""}
    except Exception as exc:
        return {"ok": False, "peak": 0, "rms": 0, "error": str(exc)}


class VoiceController:
    def __init__(self, settings: dict, command_router, status_callback=None,
                 face_callback=None, event_callback=None, beep=None,
                 grammar_provider=None, phrase_timeout=None):
        self.settings = dict(settings or {})
        self.command_router = command_router
        self.status_callback = status_callback or (lambda text: None)
        self.face_callback = face_callback or (lambda state: None)
        self.event_callback = event_callback or (lambda kind, text: None)
        self._beep = beep or _play_beep
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._manual = threading.Event()
        self._session_lock = threading.Lock()
        self._status = ""
        self._model = None
        # The engine is resolved once per listener start.  Changing it in
        # settings restarts the listener (runtime.save_settings does that).
        self._engine = resolve_engine(self.settings)
        self._engine_note = engine_reason(self.settings, self._engine)
        self._sherpa_path = sherpa_asr.resolve_model_path(
            self.settings.get("sherpa_model_path")
        )
        if self._engine != "vosk":
            log.info("Speech recognition engine: %s (%s)", self._engine, self._engine_note)
        self._frontend = AudioFrontEnd.from_settings(self.settings)
        self._frontend_error_logged = False
        self._whisper = None
        self._whisper_key = ""
        self._last_manual_thread: threading.Thread | None = None
        self._next_wake_at = 0.0
        self._continuous = False
        self._interrupt_listening = False
        self.always_listen = bool(self.settings.get("always_listen", True))
        self._last_executed: tuple[str, float] = ("", 0.0)
        self._last_repeat_at = 0.0
        self._last_capture_hypotheses: list[Hypothesis] = []
        self._last_capture_pcm = b""
        self._last_capture_assembly = None
        self._last_capture_state = None
        self._last_capture_endpoint = None
        self._last_diagnostics: dict = {}
        self._unsupported_phrases: set[str] = set()

        # -- recognition timing, confidence and adaptive VAD -----------------
        def number(key, default, low, high):
            try:
                return max(low, min(high, float(self.settings.get(key, default))))
            except (TypeError, ValueError):
                return float(default)

        self._start_timeout = number(
            "speech_start_timeout", COMMAND_START_TIMEOUT, 2.0, 15.0
        )
        self._trailing_silence = number(
            "speech_trailing_silence", COMMAND_SILENCE, 0.6, 4.0
        )
        self._max_utterance = number(
            "speech_max_utterance", COMMAND_MAX_SECONDS, 6.0, 45.0
        )
        self._min_confidence = number(
            "speech_min_confidence", PRIMARY_MIN_CONFIDENCE, 0.0, 1.0
        )
        self._alt_min_confidence = number(
            "speech_alt_min_confidence", ALTERNATIVE_MIN_CONFIDENCE, 0.0, 1.0
        )
        self._endpointer_mode = str(
            self.settings.get("speech_endpointer_mode") or "long"
        ).strip().lower()
        floor_setting = number("vad_noise_floor", 0.0, 0.0, 5000.0)
        self._adaptive_vad = AdaptiveVad(
            noise_floor=floor_setting if floor_setting > 0 else None
        )
        self._vad = UtteranceVad(self._adaptive_vad)
        self._vosk_module = None
        self._endpointer_status = "unconfigured"
        self._endpointer_reported = False

        # -- dynamic grammar -------------------------------------------------
        self._grammar_provider = grammar_provider
        self._grammar_lock = threading.Lock()
        self._grammar_json = ""
        self._grammar_phrases: set[str] = set()
        self._grammar_dirty = threading.Event()
        timeout = phrase_timeout
        if timeout is None:
            try:
                timeout = float(self.settings.get("phrase_timeout", PHRASE_TIMEOUT))
            except (TypeError, ValueError):
                timeout = PHRASE_TIMEOUT
        timeout = max(2.0, min(8.0, float(timeout)))
        self._assembler = PhraseAssembler(
            vocabulary=self._vocabulary, timeout=timeout
        )
        self.refresh_grammar()

    # -- vocabulary and grammar -------------------------------------------
    def _vocabulary(self) -> set[str] | None:
        with self._grammar_lock:
            if self._grammar_phrases:
                return set(self._grammar_phrases)
        registry = getattr(self.command_router, "registry", None)
        if registry is None:
            # Lightweight routers (for example in tests) have no registry; let
            # the assembler fall back to trying every exact candidate.
            return None
        try:
            return set(registry.phrases())
        except Exception:
            log.debug("Could not read registry phrases", exc_info=True)
            return None

    def _grammar_source(self) -> set[str]:
        if self._grammar_provider is not None:
            try:
                return {str(item) for item in self._grammar_provider() if str(item).strip()}
            except Exception:
                log.exception("Grammar provider failed")
        registry = getattr(self.command_router, "registry", None)
        if registry is not None:
            try:
                return set(registry.phrases())
            except Exception:
                log.debug("Could not read registry phrases", exc_info=True)
        return set()

    def refresh_grammar(self) -> None:
        """Rebuild the command grammar and schedule a thread-safe application.

        The Vosk model is never reloaded and the microphone is not restarted;
        the listener thread applies the new grammar between audio blocks.
        """
        phrases = self._grammar_source()
        phrases = self._drop_unsupported_app_phrases(phrases)
        grammar = build_recognition_grammar(phrases)
        with self._grammar_lock:
            self._grammar_phrases = {str(p) for p in phrases}
            self._grammar_json = grammar
        self._grammar_dirty.set()

    #: Only application phrases are filtered.  Built-in commands keep the
    #: existing spoken-form handling and must never be dropped at runtime.
    _APP_PHRASE_VERBS = (
        "open", "launch", "start", "run", "close", "exit", "quit",
    )

    @classmethod
    def _is_app_phrase(cls, words: list[str]) -> bool:
        if not words:
            return False
        if words[0] in cls._APP_PHRASE_VERBS or words[0] == "is":
            return True
        return words[:2] == ["force", "close"] or words[:3] == [
            "confirm", "force", "close"
        ]

    def _speech_aliases(self) -> dict[str, str]:
        commands = getattr(self.command_router, "app_aliases", None)
        return commands if isinstance(commands, dict) else {}

    def _app_phrase_parts(self, words: list[str]):
        """Split an app phrase into (prefix, app-name, suffix)."""
        if words and words[0] in self._APP_PHRASE_VERBS:
            return ([words[0]], words[1:], [])
        if words[:2] == ["force", "close"]:
            return (["force", "close"], words[2:], [])
        if words[:3] == ["confirm", "force", "close"]:
            return (["confirm", "force", "close"], words[3:], [])
        if words and words[0] == "is" and words[-1:] in (["running"], ["open"]):
            return (["is"], words[1:-1], [words[-1]])
        return None

    def _app_phrase_variants(self, words: list[str]) -> list[str]:
        """Practical spoken forms for app names missing from the small model."""
        parts = self._app_phrase_parts(words)
        if parts is None:
            return []
        prefix, name_words, suffix = parts
        if not name_words:
            return []
        name = " ".join(name_words)
        aliases = self._speech_aliases()
        reverse: dict[str, list[str]] = {}
        for spoken, target in aliases.items():
            key = normalize_app_name(target)
            reverse.setdefault(key, []).append(normalize_app_name(spoken))
        name_key = normalize_app_name(name)
        variants: list[str] = []
        for spoken in reverse.get(name_key, ()):
            if spoken:
                variants.append(" ".join([*prefix, *spoken.split(), *suffix]))
        for index, word in enumerate(name_words):
            for replacement in _COMPOUND_SPLITS.get(word, ()):
                words_variant = [
                    *name_words[:index], *replacement.split(),
                    *name_words[index + 1:],
                ]
                variants.append(" ".join([*prefix, *words_variant, *suffix]))
        # Generic compound splitting: unknown one-word app names are often
        # two ordinary words run together ("localsend" -> "local send").
        # Every candidate is validated against the model vocabulary by the
        # caller, so wrong guesses are never accepted into the grammar.
        for index, word in enumerate(name_words):
            if word in _COMPOUND_SPLITS or len(word) < 6:
                continue
            for cut in range(3, len(word) - 2):
                left, right = word[:cut], word[cut:]
                if not (left.isalpha() and right.isalpha()):
                    continue
                variants.append(" ".join([
                    *prefix, *name_words[:index], left, right,
                    *name_words[index + 1:], *suffix,
                ]))
        if len(name_words) == 1:
            # User-corrected aliases are the preferred source; compound
            # splitting is a fallback only for known token shapes.
            for spoken in reverse.get(name_key, ()):
                if spoken and spoken not in variants:
                    variants.append(" ".join([*prefix, *spoken.split(), *suffix]))
        clean = []
        for variant in variants:
            variant = normalize_phrase(variant)
            if variant and variant not in clean:
                clean.append(variant)
        return clean[:8]

    def _drop_unsupported_app_phrases(self, phrases) -> set[str]:
        """Keep app commands available through known or phonetic spoken forms.

        Vosk silently ignores an unknown word inside a grammar phrase, which
        can turn "open chatgpt" into a different phrase or leave a bare
        "open".  Instead of silently deleting the command, we:
        * substitute a curated/alias spoken form whose words are all known;
        * register that spoken form as an app alias for the resolver;
        * retain an explicit diagnostics list when nothing can be decoded.
        """
        model = self._model
        find = getattr(model, "vosk_model_find_word", None) if model is not None else None
        if not callable(find):
            return {str(p) for p in phrases}
        kept: set[str] = set()
        dropped: list[str] = []
        substituted = 0
        for phrase in phrases:
            words = normalize_phrase(phrase).split()
            if not self._is_app_phrase(words):
                kept.add(str(phrase))
                continue
            try:
                unknown = any(find(word) < 0 for word in words)
            except Exception:
                log.debug("Model vocabulary lookup failed", exc_info=True)
                unknown = False
            if not unknown:
                kept.add(str(phrase))
                continue
            variant = None
            for candidate in self._app_phrase_variants(words):
                parts = self._app_phrase_parts(words)
                candidate_words = candidate.split()
                try:
                    if all(find(word) >= 0 for word in candidate_words):
                        variant = candidate
                        if parts is not None and parts[1]:
                            spoken_name = " ".join(parts[1])
                            aliases = self._speech_aliases()
                            # Map the usable spoken form back to the canonical
                            # app name, so the resolver can launch it.
                            candidate_parts = self._app_phrase_parts(candidate_words)
                            if candidate_parts and candidate_parts[1]:
                                aliases.setdefault(
                                    " ".join(candidate_parts[1]),
                                    normalize_app_name(spoken_name),
                                )
                        break
                except Exception:
                    continue
            if variant:
                kept.add(variant)
                substituted += 1
            else:
                dropped.append(normalize_phrase(phrase))
        self._unsupported_phrases = set(dropped)
        if substituted:
            log.info("Grammar: substituted %d app phrases with spoken forms", substituted)
        if dropped:
            log.warning(
                "Grammar: %d app phrases have no decodable spoken form; "
                "generic fallback will still listen for them: %s",
                len(dropped), ", ".join(sorted(dropped)[:8]),
            )
        return kept

    def _current_grammar(self) -> str:
        with self._grammar_lock:
            return self._grammar_json

    def _apply_grammar(self, recognizer):
        """Swap in a recogniser built from the latest command vocabulary.

        This runs in the listener thread and never restarts the microphone or
        reloads the Vosk model.  A fresh recogniser is used instead of
        SetGrammar because SetGrammar drops words that are missing from the
        model's word list (for example "unmute").
        """
        if not self._grammar_dirty.is_set():
            return recognizer
        self._grammar_dirty.clear()
        if self._engine == ENGINE_SHERPA:
            # The streaming transducer has no grammar; command matching still
            # happens against the registry after every final transcript.
            return recognizer
        if not self._current_grammar() or self._model is None:
            return recognizer
        try:
            fresh = self._make_recognizer()
            log.info("Recognition grammar refreshed (%d phrases)", len(self._grammar_phrases))
            return fresh
        except Exception:
            log.exception("Could not refresh the recognition grammar; keeping the previous one")
            return recognizer

    def _make_recognizer(self, wake: bool = False):
        if self._engine == ENGINE_SHERPA:
            # Transducers have no phrase-list grammar; endpointing is handled
            # by sherpa-onnx and TALCON's own silence windows in parallel.
            recognizer = create_streaming_recognizer(
                self.settings,
                engine=ENGINE_SHERPA,
                trailing_silence=self._trailing_silence,
                max_utterance=self._max_utterance,
            )
            self._endpointer_status = "sherpa-onnx"
            return recognizer
        from vosk import KaldiRecognizer

        grammar = WAKE_GRAMMAR if wake else self._current_grammar()
        try:
            recognizer = (
                KaldiRecognizer(self._model, SAMPLE_RATE, grammar) if grammar
                else KaldiRecognizer(self._model, SAMPLE_RATE)
            )
        except Exception:
            log.exception("Grammar recogniser could not be created; using generic recognition")
            recognizer = KaldiRecognizer(self._model, SAMPLE_RATE)
        if not wake:
            try:
                recognizer.SetMaxAlternatives(N_ALTERNATIVES)
            except Exception:
                log.debug("Recognition alternatives are not supported", exc_info=True)
            try:
                recognizer.SetWords(True)
            except Exception:
                log.debug("Word confidence is not supported", exc_info=True)
        self._endpointer_status = self._configure_endpointer(recognizer, wake=wake)
        return recognizer

    def _configure_endpointer(self, recognizer, wake: bool = False) -> str:
        """Configure Vosk's built-in endpointer and report what really happened.

        Returns "skipped", "configured", "unsupported" or "failed".  TALCON's
        own silence-window endpointing (speech_start_timeout, trailing
        silence, maximum utterance) is always active as well, so recognition
        never depends on this call succeeding.

        One reliable strategy is used: only the three-argument
        ``SetEndpointerDelays`` call.  This Vosk build has no
        ``EndpointerMode`` enum and no ``SetEndpointerMode`` at all, and
        applying mode and delays sequentially risks one overwriting the
        other, so the delays alone define the endpoint rules.
        """
        if wake:
            return "skipped"
        delays = getattr(recognizer, "SetEndpointerDelays", None)
        if not callable(delays):
            self._endpointer_status = "unsupported"
            if not self._endpointer_reported:
                self._endpointer_reported = True
                log.info(
                    "This Vosk build (%s) does not expose SetEndpointerDelays; "
                    "using TALCON's own silence endpointing "
                    "(start timeout %.1fs, trailing silence %.1fs, maximum %.0fs)",
                    getattr(self._vosk_module, "__version__", "unknown")
                    if self._vosk_module else "unknown",
                    self._start_timeout, self._trailing_silence, self._max_utterance,
                )
            return "unsupported"
        try:
            # All three delays in seconds, derived from the speech settings.
            t_start = max(0.3, min(2.0, self._trailing_silence * 0.5))
            t_delta = float(self._trailing_silence)
            t_end = float(self._max_utterance)
            delays(t_start, t_delta, t_end)
        except Exception as exc:
            self._endpointer_status = "failed"
            log.warning(
                "Vosk endpointer configuration failed (%s); using TALCON's own "
                "silence endpointing instead", exc,
            )
            return "failed"
        self._endpointer_status = "configured"
        log.info(
            "Vosk endpointer delays applied: start=%.2fs delta=%.2fs end=%.2fs",
            t_start, t_delta, t_end,
        )
        return "configured"

    # -- helpers -----------------------------------------------------------
    def _set_status(self, text: str) -> None:
        if text == self._status:
            return
        self._status = text
        try:
            self.status_callback(text)
        except Exception:
            log.exception("Voice status callback failed")

    def _emit(self, kind: str, text: str) -> None:
        try:
            self.event_callback(str(kind), str(text))
        except Exception:
            log.exception("Voice event callback failed")

    def apply_settings(self, settings: dict) -> None:
        """Apply recognition timing/confidence changes without reconnecting."""
        self.settings = dict(settings or {})
        self._start_timeout = float(self.settings.get("speech_start_timeout", COMMAND_START_TIMEOUT))
        self._trailing_silence = float(self.settings.get("speech_trailing_silence", COMMAND_SILENCE))
        self._max_utterance = float(self.settings.get("speech_max_utterance", COMMAND_MAX_SECONDS))
        self._min_confidence = float(self.settings.get("speech_min_confidence", PRIMARY_MIN_CONFIDENCE))
        self._alt_min_confidence = float(
            self.settings.get("speech_alt_min_confidence", ALTERNATIVE_MIN_CONFIDENCE)
        )
        self._endpointer_mode = str(
            self.settings.get("speech_endpointer_mode", "long")
        ).lower()
        floor = float(self.settings.get("vad_noise_floor", 0.0) or 0.0)
        if floor > 0:
            self.set_noise_floor(floor)
        frontend = AudioFrontEnd.from_settings(self.settings)
        if (
            self._frontend is None
            or frontend.enabled != self._frontend.enabled
            or frontend.target_rms != self._frontend.target_rms
            or frontend.agc != self._frontend.agc
            or frontend.noise_suppression != self._frontend.noise_suppression
            or frontend.aec != self._frontend.aec
        ):
            self._frontend = frontend
        # An engine or model change is handled by restarting the listener in
        # runtime.save_settings; settings that affect the next final only
        # (Whisper and audio conditioning) apply immediately here.

    def set_noise_floor(self, value: float) -> None:
        """Apply a freshly calibrated ambient floor to the live detector."""
        try:
            self._adaptive_vad.calibrate([float(value)] * 12)
        except Exception:
            log.debug("Could not apply the calibrated noise floor", exc_info=True)

    def _emit_diag(self, *, state=None, text="", alternatives=(),
                   confidence=None, endpoint=None, reason="") -> None:
        payload = {
            "noise": round(float(getattr(state, "noise_floor", 0.0) or 0.0), 1),
            "rms": round(float(getattr(state, "level", 0.0) or 0.0), 1),
            "peak": int(getattr(state, "peak", 0) or 0),
            "clipping": bool(getattr(state, "clipping", False)),
            "threshold": round(float(getattr(state, "threshold", 0.0) or 0.0), 1),
            "text": str(text or ""),
            "alternatives": [str(item) for item in alternatives if str(item)],
            "confidence": None if confidence is None else round(float(confidence), 3),
            "endpoint": None if endpoint is None else round(float(endpoint), 2),
            "reason": str(reason or ""),
        }
        self._last_diagnostics = payload
        try:
            self._emit("recognition_diag", json.dumps(payload))
        except Exception:
            log.debug("Could not emit recognition diagnostics", exc_info=True)

    def _device(self):
        value = self.settings.get("microphone_device")
        if value in (None, ""):
            return None
        try:
            return int(value)
        except (TypeError, ValueError):
            return None

    def _model_path(self):
        return resolve_model(self.settings.get("vosk_model_path"))

    @property
    def _speech(self):
        return getattr(self.command_router, "speech", None)

    def _speech_is_speaking(self) -> bool:
        speech = self._speech
        if speech is None:
            return False
        busy = getattr(speech, "is_busy", None)
        if busy is not None:
            return bool(busy)
        return bool(getattr(speech, "is_speaking", False))

    def _wait_for_speech_end(self, timeout: float) -> bool:
        waiter = getattr(self._speech, "wait_until_quiet", None)
        if not callable(waiter):
            waiter = getattr(self._speech, "wait_until_idle", None)
        if not callable(waiter):
            return True
        try:
            return bool(waiter(timeout))
        except Exception:
            log.exception("Could not wait for speech output")
            return False

    @staticmethod
    def _drain(audio_q: queue.Queue) -> int:
        dropped = 0
        while True:
            try:
                audio_q.get_nowait()
                dropped += 1
            except queue.Empty:
                return dropped

    def _gate_microphone(self, audio_q: queue.Queue, seconds: float) -> None:
        deadline = time.monotonic() + max(0.0, seconds)
        while time.monotonic() < deadline and not self._stop.is_set():
            try:
                audio_q.get(timeout=min(0.05, max(0.005, deadline - time.monotonic())))
            except queue.Empty:
                pass
        self._drain(audio_q)

    @staticmethod
    def _reset(recognizer) -> None:
        try:
            recognizer.Reset()
        except Exception:
            log.debug("Recogniser reset was not available", exc_info=True)

    def _enhance(self, pcm: bytes) -> bytes:
        """Condition one microphone block for the selected recogniser.

        The VAD still receives the raw block, so gain or denoising can never
        hide speech from the endpoint logic.  A front-end failure degrades to
        the original bytes instead of stopping the listener.
        """
        frontend = self._frontend
        if frontend is None or not frontend.enabled or not pcm:
            return pcm
        try:
            frontend.set_reference_active(self._speech_is_speaking())
            return frontend.process(pcm)
        except Exception:
            if not self._frontend_error_logged:
                self._frontend_error_logged = True
                log.exception("Audio front-end failed; passing raw audio through")
            return pcm

    def _long_question_transcriber(self):
        """Lazily build the optional faster-whisper pass."""
        if not self.settings.get("long_question_whisper", False):
            return None
        model = str(self.settings.get("whisper_model") or "small.en")
        if self._whisper is None or self._whisper_key != model:
            self._whisper = whisper_engine.LongQuestionTranscriber(model)
            self._whisper_key = model
        return self._whisper if self._whisper.available() else None

    def _refine_long_question(self, text: str, audio: bytes | None) -> str:
        """Re-decode a long question with faster-whisper when it is enabled.

        Commands are never eligible: the prefix test only accepts question
        wording such as "explain…" or "what is…".  The refined text is used
        only by the natural-language fallback, not by command matching.
        """
        if not whisper_engine.is_long_question(text):
            return text
        pcm = audio or self._last_capture_pcm
        if not pcm:
            return text
        transcriber = self._long_question_transcriber()
        if transcriber is None:
            return text
        refined = transcriber.transcribe(pcm)
        if refined and refined.strip() and refined.strip() != text.strip():
            log.info("Long question re-decoded with faster-whisper: %s", refined)
            return refined.strip()
        return text

    # -- lifecycle ---------------------------------------------------------
    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="TALCON-Voice", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread and self._thread.is_alive() and self._thread is not threading.current_thread():
            self._thread.join(timeout=2.5)
        if (self._last_manual_thread and self._last_manual_thread.is_alive()
                and self._last_manual_thread is not threading.current_thread()):
            self._last_manual_thread.join(timeout=1.5)
        self._thread = None

    def request_manual_listen(self) -> None:
        """Capture one command now, even without saying the wake word."""
        if self._thread and self._thread.is_alive():
            if self.always_listen:
                self._set_status("Listening continuously")
                return
            self._manual.set()
            self._set_status("Listening for your command…")
            return
        if self._last_manual_thread and self._last_manual_thread.is_alive():
            return
        self._last_manual_thread = threading.Thread(
            target=self._manual_session, name="TALCON-Voice-Manual", daemon=True
        )
        self._last_manual_thread.start()

    # -- main loop ---------------------------------------------------------
    def _run(self) -> None:
        from .win_threads import lower_current_thread

        lower_current_thread()
        # Re-resolve at start time: the settings may have changed since the
        # controller was constructed (and the model may have just installed).
        self._engine = resolve_engine(self.settings)
        self._engine_note = engine_reason(self.settings, self._engine)
        self._sherpa_path = sherpa_asr.resolve_model_path(
            self.settings.get("sherpa_model_path")
        )
        if not engine_model_ready(self.settings):
            self._set_status("Speech model missing · download it in Voice settings")
            log.warning("Speech model for %s is not ready (%s)",
                        self._engine, self._engine_note)
            return
        try:
            import sounddevice as sd
        except Exception as exc:
            self._set_status(f"Speech recognition unavailable · {exc}")
            log.exception("sounddevice import failed")
            return

        if self._engine == ENGINE_SHERPA:
            try:
                # Construct once now so a broken model fails before the
                # microphone stream opens.
                probe = self._make_recognizer()
                self._reset(probe)
                del probe
            except Exception as exc:
                # A corrupt or incompatible sherpa model must not leave the
                # user without voice input: fall back to Vosk for this run.
                log.exception("Could not load sherpa-onnx; falling back to Vosk")
                self._engine_note = f"sherpa-onnx failed ({exc}); using Vosk"
                self._engine = "vosk"
                self._set_status("Using Vosk · sherpa-onnx could not start")
            if self._engine == ENGINE_SHERPA:
                self._model = None
                self.refresh_grammar()

        if self._engine != ENGINE_SHERPA:
            path = self._model_path()
            if not model_is_ready(path):
                self._set_status("Speech model missing · download it in Voice settings")
                log.warning("Vosk fallback model not found at %s", path)
                return
        if self._engine != ENGINE_SHERPA:
            path = self._model_path()
            try:
                import vosk as vosk_module

                self._vosk_module = vosk_module
                self._model = _load_model(str(path))
            except Exception as exc:
                self._set_status(f"Speech model could not be loaded · {exc}")
                log.exception("Could not load the Vosk model")
                return
            # Rebuild once the model is available so app phrases containing
            # words the small model cannot pronounce are filtered.
            self.refresh_grammar()

        device = self._device()
        while not self._stop.is_set():
            audio_q: queue.Queue = queue.Queue(maxsize=16)

            def callback(indata, frames, time_info, status, _q=audio_q):
                if status:
                    log.debug("Microphone status: %s", status)
                try:
                    _q.put_nowait(bytes(indata))
                except queue.Full:
                    # Drop the oldest block so the queue stays bounded.
                    try:
                        _q.get_nowait()
                        _q.put_nowait(bytes(indata))
                    except queue.Empty:
                        pass

            try:
                recognizer = self._make_recognizer(wake=not self.always_listen)
                with sd.RawInputStream(
                    samplerate=SAMPLE_RATE,
                    blocksize=BLOCK_SIZE,
                    dtype="int16",
                    channels=1,
                    callback=callback,
                    device=device,
                ):
                    self._drain(audio_q)
                    self._reset(recognizer)
                    if self.always_listen:
                        self._set_status("Listening continuously")
                        log.info("Continuous listener started on device %s",
                                 device if device is not None else "default")
                    else:
                        self._set_status("Listening for TALCON")
                        log.info("Wake-word listener started on device %s",
                                 device if device is not None else "default")
                    self._emit("mic_error", "")
                    if self.always_listen:
                        self._listen_loop(audio_q, recognizer)
                    else:
                        self._wake_loop(audio_q, recognizer)
            except Exception as exc:
                if self._stop.is_set():
                    break
                log.warning("Microphone stream failed: %s", exc)
                self._set_status("Microphone unavailable · retrying")
                self._emit("mic_error", str(exc))
                self.face_callback("idle")
                self._stop.wait(2.5)
                if device is not None:
                    # Configured device may have been unplugged; fall back once.
                    device = None

        self._continuous = False
        self._set_status("Listening stopped")
        self.face_callback("idle")

    def _listen_loop(self, audio_q: queue.Queue, recognizer) -> None:
        """Always-on command recognition with adaptive VAD and confidence.

        Finals are buffered until the adaptive VAD has seen trailing silence.
        Vosk often finalises a short segment during a pause, so dispatching the
        first final immediately would truncate "open... notepad".  Waiting for
        the same silence window as a normal command keeps slow speech intact.

        Nothing a final says is silently dropped: when every hypothesis is
        undecodable the raw text is kept for the generic fallback, and when
        Vosk never finalises at all the running partial is used once the user
        has clearly stopped speaking.
        """
        self._continuous = True
        self._assembler.reset()
        self._vad.reset()
        utterance_pcm = bytearray()
        current_assembly = None
        current_hypotheses: list[Hypothesis] = []
        current_primary = ""
        current_raw = ""
        last_final_at = 0.0
        last_state = None
        last_voice_at = None
        partial_done = False
        last_mic_log = time.monotonic()
        last_sound_log = 0.0

        def reset_utterance() -> None:
            nonlocal current_assembly, current_hypotheses, current_primary
            nonlocal current_raw, last_final_at, last_state
            nonlocal partial_done, last_voice_at
            current_assembly = None
            current_hypotheses = []
            current_primary = ""
            current_raw = ""
            last_final_at = 0.0
            last_state = None
            last_voice_at = None
            partial_done = False
            # The utterance is complete: reset only now, after the final or
            # last-resort partial has been consumed. Fragments never cross
            # into the next utterance.
            self._assembler.reset()
            self._reset(recognizer)
            self._vad.reset()
            utterance_pcm.clear()

        def after_action(action) -> None:
            if action not in (None, "none", "unknown"):
                # A command ran: drop any audio from its spoken reply.
                if not self._wait_for_speech_end(20.0):
                    log.warning("Speech output stayed busy; resuming listening")
                self._drain(audio_q)
                self._reset(recognizer)
                self.face_callback("idle")
                self._set_status("Listening continuously")

        def silence_elapsed(now: float) -> bool:
            """True once the configured trailing-silence window has passed
            since the last voiced audio block.  Finals and partials alike
            must wait for it, so slow speech is never cut short."""
            return last_voice_at is None or now - last_voice_at >= self._trailing_silence

        def flush(force: bool = False) -> str | None:
            now = time.monotonic()
            # Latency: when the assembler has already matched a complete
            # registered command, run it immediately instead of waiting for
            # trailing silence.  Prefix-pending assemblies still wait so a
            # slow second word can finish the command.
            matched = current_assembly is not None and bool(current_assembly.matched)
            if not force and not matched and not silence_elapsed(now):
                return None
            if (not force and not matched and current_assembly is not None
                    and now - last_final_at < self._trailing_silence):
                return None
            if current_assembly is not None and current_primary:
                action = self._dispatch_transcript(
                    current_primary, audio_q, recognizer,
                    hypotheses=current_hypotheses,
                    audio=bytes(utterance_pcm),
                    state=last_state,
                    preassembled=current_assembly,
                )
                reset_utterance()
                after_action(action)
                return action
            if current_raw:
                # Every grammar hypothesis was undecodable.  Keep the raw
                # text as the last clue instead of dropping the utterance.
                phrase = normalize_phrase(current_raw)
                audio = bytes(utterance_pcm)
                reset_utterance()
                action = None
                if phrase and len(phrase.split()) <= GENERIC_FALLBACK_MAX_WORDS:
                    log.info("Grammar gave nothing usable; raw text: %s", phrase)
                    action = self._lenient_app_action(phrase)
                    if action in (None, "none", "unknown"):
                        action = self._try_generic(audio, audio_q, now)
                after_action(action)
                return action
            return None

        def flush_partial() -> str | None:
            """Last chance only after the full configured silence window."""
            nonlocal partial_done
            if partial_done or current_assembly is not None or current_raw:
                return None
            now = time.monotonic()
            if not silence_elapsed(now) or not self._vad.speech_seen:
                return None
            partial_done = True
            try:
                payload = json.loads(recognizer.PartialResult())
            except Exception:
                return None
            phrase = normalize_phrase(str(payload.get("partial") or ""))
            audio = bytes(utterance_pcm)
            reset_utterance()
            if not phrase or len(phrase.split()) > GENERIC_FALLBACK_MAX_WORDS:
                return None
            log.info("No final was produced; trying the running partial: %s", phrase)
            # A partial that exactly names a registered, non-destructive
            # command is safe to run directly ("what time is it" may never
            # reach a Vosk final, especially with a small model).
            action = None
            if not self._is_destructive(phrase):
                action = self._execute_phrase(phrase, primary=True)
            if action in (None, "none", "unknown"):
                action = self._lenient_app_action(phrase)
            if action in (None, "none", "unknown"):
                action = self._try_generic(audio, audio_q, time.monotonic())
            after_action(action)
            return action

        try:
            while not self._stop.is_set():
                # Grammar updates are applied here, in the listener thread, so
                # the microphone and model are never touched from the UI thread.
                updated = self._apply_grammar(recognizer)
                if updated is not recognizer:
                    recognizer = updated
                    self._vad.reset()
                    current_assembly = None
                    utterance_pcm.clear()

                # Never listen while TALCON is speaking, otherwise it hears itself.
                if self._speech_is_speaking():
                    self._drain(audio_q)
                    self._reset(recognizer)
                    self._vad.reset()
                    current_assembly = None
                    utterance_pcm.clear()
                    self._stop.wait(0.05)
                    continue

                try:
                    pcm = audio_q.get(timeout=0.25)
                except queue.Empty:
                    if self._assembler.expire():
                        self._set_status("Listening continuously")
                        current_assembly = None
                    action = flush()
                    if action is None and current_assembly is None and not current_raw:
                        action = flush_partial()
                    if action not in (None, "none", "unknown"):
                        self._drain(audio_q)
                        self._reset(recognizer)
                        self.face_callback("idle")
                        self._set_status("Listening continuously")
                    continue

                if self._assembler.expire():
                    self._set_status("Listening continuously")
                    current_assembly = None

                if self._speech_is_speaking():
                    self._drain(audio_q)
                    self._reset(recognizer)
                    self._vad.reset()
                    current_assembly = None
                    utterance_pcm.clear()
                    continue

                previous_voice_at = last_voice_at
                state = self._vad.update(pcm)
                last_state = state
                now = time.monotonic()
                if state.level >= self._adaptive_vad.release_threshold:
                    last_voice_at = now
                elif state.speech_seen and last_voice_at is None:
                    # If warm-up detects speech from the pre-roll, start the
                    # silence timer at the first block that woke the detector.
                    last_voice_at = now
                if now - last_mic_log >= 120.0:
                    last_mic_log = now
                    log.info(
                        "Microphone live: level=%.0f noise=%.0f gate=%.0f "
                        "speech=%s voiced=%s queue=%d",
                        state.level, state.noise_floor, state.threshold,
                        state.speech, state.speech_seen, audio_q.qsize(),
                    )
                if state.level >= 20.0 and now - last_sound_log >= 2.0:
                    last_sound_log = now
                    log.info(
                        "Microphone audio: level=%.0f peak=%d gate=%.0f speech=%s",
                        state.level, state.peak, state.threshold, state.speech,
                    )
                if state.speech:
                    partial_done = False
                # Every block, including warm-up and pre-roll, is already
                # retained here and submitted to the recogniser below. Do not
                # prepend the VAD pre-roll again: doing so duplicates and
                # reorders samples.  The VAD above saw the raw block; the
                # engine sees the conditioned block.
                enhanced = self._enhance(pcm)
                utterance_pcm.extend(enhanced)
                pcm_limit = int(GENERIC_MAX_SECONDS * SAMPLE_RATE * 2)
                if len(utterance_pcm) > pcm_limit:
                    del utterance_pcm[:-pcm_limit]

                try:
                    if recognizer.AcceptWaveform(enhanced):
                        payload = json.loads(recognizer.Result())
                        hypotheses = [
                            item for item in _result_hypotheses(payload)
                            if not _has_unknown(item.text)
                        ]
                        raw_text = str(payload.get("text") or "").strip()
                        # A final that starts after a full silence window is
                        # a new utterance.  Flush the previous assembly now,
                        # otherwise two commands separated by a long pause
                        # can be joined (or the first one lost) just because
                        # the new final arrived before the next empty-queue
                        # tick.
                        if (current_assembly is not None
                                and previous_voice_at is not None
                                and last_final_at
                                and now - previous_voice_at >= self._trailing_silence
                                and now - last_final_at >= self._trailing_silence):
                            flush(force=True)
                        if hypotheses and state.speech_seen:
                            current_hypotheses = hypotheses
                            current_primary = hypotheses[0].text
                            current_assembly = self._assembler.add(hypotheses)
                            last_final_at = time.monotonic()
                            log.info(
                                "Heard: %s (conf=%s, noise=%.0f, rms=%.0f, threshold=%.0f)",
                                " | ".join(item.text for item in hypotheses),
                                "?" if hypotheses[0].confidence is None
                                else f"{hypotheses[0].confidence:.2f}",
                                state.noise_floor, state.level, state.threshold,
                            )
                        elif raw_text and state.speech_seen and current_assembly is None:
                            current_raw = raw_text
                            last_final_at = time.monotonic()
                            log.info("Unintelligible final: %s", raw_text)
                except Exception:
                    log.exception("Continuous recognizer failed")
                    return

                # Once the full silence window has elapsed, Vosk never
                # finalised and no raw final arrived: try the running partial
                # exactly once.  The helper independently enforces the window
                # so neither VAD release nor an empty queue can bypass it.
                if (not partial_done and current_assembly is None and not current_raw):
                    action = flush_partial()
                    if action not in (None, "none", "unknown"):
                        continue

                action = flush()
                if action not in (None, "none", "unknown"):
                    continue
        finally:
            self._continuous = False

    def _dispatch_transcript(self, text: str, audio_q: queue.Queue, recognizer,
                             alternatives=None, hypotheses=None, audio=None,
                             state=None, preassembled=None) -> str | None:
        """Match a final transcript through confidence-aware phrase assembly."""
        now = time.monotonic()
        if hypotheses is None:
            hypotheses = [Hypothesis(text)]
            hypotheses.extend(Hypothesis(item) for item in (alternatives or []))
        hypotheses = [item for item in hypotheses if not _has_unknown(item.text)]
        if not hypotheses:
            return None
        assembly = preassembled
        if assembly is None:
            assembly = self._assembler.add(hypotheses, now=now)
        if assembly.heard:
            self._emit("last_heard", assembly.heard)
        if assembly.pending:
            self._set_status(f"Heard: {assembly.heard} …")
            self._emit_diag(
                state=state, text=assembly.heard,
                alternatives=[item.text for item in hypotheses[1:]],
                confidence=assembly.confidence,
                reason="Incomplete phrase; waiting for the rest",
            )
            return None

        action = self._execute_assembly(assembly, audio_q, now, state)
        if action not in (None, "none", "unknown"):
            return action

        # The small grammar may not contain a natural variation or an app name
        # with an unknown token.  Re-decode only this utterance with the same
        # Vosk model without a phrase list, then accept a command only when its
        # confidence and the resolver are both strong.  A long transcript is
        # ambient chatter, not a command; re-decoding it wastes CPU and lets
        # the queue drop live microphone audio.
        heard_words = len((assembly.heard or "").split())
        if audio and heard_words <= GENERIC_FALLBACK_MAX_WORDS:
            action = self._try_generic(audio, audio_q, now, state)
            if action not in (None, "none", "unknown"):
                return action

        if assembly.heard:
            self._emit_diag(
                state=state, text=assembly.heard,
                alternatives=[item.text for item in hypotheses[1:]],
                confidence=assembly.confidence,
                reason="No confident registered command matched",
            )
            # Long questions may justify a slower, more accurate second pass.
            # The refined text still goes through natural-language handling
            # only; it is never matched as a command.
            refined = self._refine_long_question(assembly.heard, audio)
            if refined != assembly.heard:
                self._emit("last_heard", refined)
            self._handle_unintelligible(refined, audio_q)
        return None

    def _execute_assembly(self, assembly, audio_q: queue.Queue, now: float,
                          state=None) -> str | None:
        attempts = [assembly.matched] if assembly.matched else list(assembly.candidates)
        primary_text = assembly.heard.split("  ")[0] if assembly.heard else ""
        primary_norm = normalize_phrase(primary_text)
        strong = {primary_norm} if primary_norm else set()
        for index, candidate in enumerate(attempts):
            if not candidate:
                continue
            confidence = assembly.scores.get(candidate)
            if confidence is None and assembly.matched == candidate:
                confidence = assembly.confidence
            if confidence is not None:
                minimum = (
                    self._min_confidence if index == 0
                    else self._alt_min_confidence
                )
                if confidence < minimum:
                    log.info(
                        "Ignored a low-confidence hypothesis (%.2f < %.2f): %s",
                        confidence, minimum, candidate,
                    )
                    continue
            if self._is_destructive(candidate):
                # A destructive command is never taken from a lower-ranked
                # alternative or a weak hypothesis.
                allowed = candidate in strong
                if confidence is not None and confidence < DESTRUCTIVE_MIN_CONFIDENCE:
                    allowed = False
                if not allowed:
                    log.info("Ignored a weak destructive hypothesis: %s", candidate)
                    # Never pivot from an uncertain destructive primary to a
                    # different command in the alternatives.
                    return None
            if self._needs_strong_primary(candidate) and candidate not in strong:
                log.info(
                    "Ignored a lower-ranked self-affecting hypothesis: %s",
                    candidate,
                )
                return None
            if (candidate == self._last_executed[0]
                    and now - self._last_executed[1] < COMMAND_DEBOUNCE):
                self._assembler.reset()
                return None
            action = self._execute_phrase(candidate, primary=index == 0,
                                          confidence=confidence)
            if action not in (None, "none", "unknown"):
                self._assembler.reset()
                self._last_executed = (candidate, now)
                self._emit_diag(
                    state=state, text=assembly.heard,
                    confidence=confidence, reason="Matched registered command",
                )
                return action
        return None

    def _make_generic_recognizer(self):
        from vosk import KaldiRecognizer

        recognizer = KaldiRecognizer(self._model, SAMPLE_RATE)
        try:
            recognizer.SetMaxAlternatives(N_ALTERNATIVES)
            recognizer.SetWords(True)
        except Exception:
            log.debug("Generic recognition options are unavailable", exc_info=True)
        return recognizer

    def _generic_hypotheses(self, pcm: bytes) -> list[Hypothesis]:
        """Decode one utterance without the phrase list, for natural wording.

        Only the trailing few seconds are decoded: a spoken command fits in
        that window, and a short decode keeps the listener responsive.
        """
        limit = int(GENERIC_FALLBACK_SECONDS * SAMPLE_RATE * 2)
        if len(pcm) > limit:
            pcm = pcm[-limit:]
        if not pcm or len(pcm) < SAMPLE_RATE // 4 or self._model is None:
            return []
        try:
            recognizer = self._make_generic_recognizer()
            step = BLOCK_SIZE * 2
            for offset in range(0, len(pcm), step):
                recognizer.AcceptWaveform(pcm[offset:offset + step])
            payload = json.loads(recognizer.FinalResult())
        except Exception:
            log.debug("Generic fallback recognition failed", exc_info=True)
            return []
        return [
            item for item in _result_hypotheses(payload)
            if not _has_unknown(item.text)
        ]

    def _try_generic(self, pcm: bytes, audio_q: queue.Queue, now: float,
                     state=None) -> str | None:
        if self._commands_paused():
            return None
        hypotheses = self._generic_hypotheses(pcm)
        if not hypotheses:
            return None
        top = hypotheses[0]
        confidence = top.confidence
        log.info(
            "Generic fallback: %s (conf=%s)",
            " | ".join(item.text for item in hypotheses),
            "?" if confidence is None else f"{confidence:.2f}",
        )
        if confidence is not None and confidence < GENERIC_MIN_CONFIDENCE:
            self._emit_diag(
                state=state, text=top.text,
                alternatives=[item.text for item in hypotheses[1:]],
                confidence=confidence,
                reason="Generic hypothesis was not confident enough",
            )
            return None
        for index, hypothesis in enumerate(hypotheses):
            candidate = hypothesis.normalized()
            if not candidate:
                continue
            item_confidence = hypothesis.confidence
            if self._is_destructive(candidate):
                # A destructive command is never accepted without a real
                # confident score on the primary hypothesis.
                if index > 0 or item_confidence is None or (
                    item_confidence < DESTRUCTIVE_MIN_CONFIDENCE
                ):
                    return None
            if self._needs_strong_primary(candidate) and index > 0:
                return None
            if (candidate == self._last_executed[0]
                    and now - self._last_executed[1] < COMMAND_DEBOUNCE):
                return None
            if item_confidence is None:
                # No usable score: an exact registered command (never a
                # destructive one) or an exact app match is safe.
                if index > 0:
                    continue
                action = "unknown"
                if not self._is_destructive(candidate):
                    action = self._execute_phrase(candidate, primary=True)
                if action in (None, "none", "unknown"):
                    action = self._lenient_app_action(candidate)
            else:
                if index > 0 and item_confidence < self._alt_min_confidence:
                    continue
                action = self._execute_phrase(
                    candidate, primary=index == 0, confidence=item_confidence
                )
                if action in (None, "none", "unknown"):
                    action = self._lenient_app_action(candidate)
            if action not in (None, "none", "unknown"):
                self._last_executed = (candidate, now)
                self._emit_diag(
                    state=state, text=top.text,
                    alternatives=[item.text for item in hypotheses[1:]],
                    confidence=confidence,
                    reason="Matched through generic fallback",
                )
                return action
        self._emit_diag(
            state=state, text=top.text,
            alternatives=[item.text for item in hypotheses[1:]],
            confidence=confidence,
            reason="Generic transcript did not match a safe command",
        )
        return None

    def _lenient_app_action(self, phrase: str) -> str | None:
        """Exact-only application match for natural speech the grammar missed.

        Used only after strict matching failed.  It never closes anything,
        never touches destructive commands and never fuzzy-guesses: only an
        exact name, a configured alias or a normalised catalogue name is
        accepted.
        """
        if self._commands_paused():
            return None
        words = normalize_phrase(phrase).split()
        if not words or len(words) > GENERIC_FALLBACK_MAX_WORDS + 2:
            return None
        library = getattr(self.command_router, "apps", None)
        if library is None:
            return None
        try:
            entries = library.cached_entries()
        except Exception:
            log.debug("Could not read the application cache", exc_info=True)
            return None
        if not entries:
            return None
        aliases = getattr(self.command_router, "app_aliases", {}) or {}
        exact_reasons = {"exact name", "configured alias", "normalised name"}
        open_verbs = {"open", "launch", "start", "run"}

        def exact(query: str):
            return resolve_application(query, entries, aliases, threshold=0.95)

        if words[0] == "running" and len(words) >= 2:
            result = exact(" ".join(words[1:]))
            if result.matched and result.reason in exact_reasons:
                handler = getattr(self.command_router, "_do_is_running", None)
                if callable(handler):
                    return handler(result.entry.name)
        if words[-1] in ("running", "open") and len(words) >= 2:
            middle = [
                word for word in words[:-1]
                if word not in {"is", "it", "currently", "still"}
            ]
            result = exact(" ".join(middle))
            if result.matched and result.reason in exact_reasons:
                handler = getattr(self.command_router, "_do_is_running", None)
                if callable(handler):
                    return handler(result.entry.name)
            return None
        verb_index = None
        for index, word in enumerate(words):
            if word in open_verbs:
                verb_index = index
        if verb_index is None:
            return None
        fillers = {
            "the", "a", "an", "my", "our", "up", "app", "application",
            "please", "program", "window",
        }
        tail = [w for w in words[verb_index + 1:] if w not in fillers]
        if not 1 <= len(tail) <= 3:
            return None
        result = exact(" ".join(tail))
        if not result.matched or result.reason not in exact_reasons:
            return None
        log.info("Natural speech matched the application %s", result.entry.name)
        return self._execute_phrase(f"open {result.entry.name}", primary=True)

    def _is_destructive(self, phrase: str) -> bool:
        registry = getattr(self.command_router, "registry", None)
        matcher = getattr(registry, "match", None)
        if not callable(matcher):
            return False
        try:
            command = matcher(phrase)
        except Exception:
            return False
        return command is not None and getattr(command, "action", "") in DESTRUCTIVE_ACTIONS

    def _needs_strong_primary(self, phrase: str) -> bool:
        """True for self-affecting commands that must be the best hypothesis."""
        registry = getattr(self.command_router, "registry", None)
        matcher = getattr(registry, "match", None)
        if not callable(matcher):
            return False
        try:
            command = matcher(phrase)
        except Exception:
            return False
        return (
            command is not None
            and getattr(command, "action", "") in STRONG_PRIMARY_ACTIONS
        )

    def _commands_paused(self) -> bool:
        getter = getattr(self.command_router, "commands_paused", None)
        if not callable(getter):
            return False
        try:
            return bool(getter())
        except Exception:
            return False

    def _is_resume_phrase(self, phrase: str) -> bool:
        registry = getattr(self.command_router, "registry", None)
        matcher = getattr(registry, "match", None)
        if not callable(matcher):
            return False
        try:
            command = matcher(phrase)
        except Exception:
            command = None
        if command is not None and getattr(command, "action", "") == "commands_on":
            return True
        # Lenient fallback: while paused, a slightly misheard resume phrase
        # still counts when it names the commands and a resume verb, and never
        # when it contains a pause word ("don't take commands").
        words = set(re.findall(r"[a-z']+", str(phrase or "").lower()))
        if not ({"command", "commands"} & words):
            return False
        if {"dont", "don't", "do", "not", "stop", "pause", "no", "ignore"} & words:
            return False
        return bool({"take", "taking", "start", "resume", "begin", "listen"} & words)

    def _execute_phrase(self, phrase: str, primary: bool = True,
                        confidence: float | None = None) -> str:
        started = time.monotonic()
        paused = self._commands_paused()
        resume = self._is_resume_phrase(phrase) if paused else False
        if paused and not resume:
            log.debug("Commands are paused; ignoring: %s", phrase)
            return "none"
        try:
            try:
                action = self.command_router.handle(phrase, announce_unknown=False)
            except TypeError:
                action = self.command_router.handle(phrase)
        except Exception as exc:
            # The phrase matched but the action itself failed.  This is an
            # execution failure, not a recognition failure.
            log.exception("Command handling failed")
            self._emit("last_match", phrase)
            self._emit("last_command", phrase)
            self._emit("last_result", f"Action failed: {exc}")
            self._emit_command_done(phrase, "failed", started, confidence)
            return "failed"
        if action in (None, "none", "unknown"):
            if paused and resume:
                # A lenient resume phrase that the registry does not know:
                # still honour it, because resuming is harmless.
                handler = getattr(self.command_router, "_do_commands_on", None)
                if callable(handler):
                    action = handler()
            if action in (None, "none", "unknown"):
                log.info("Ignored unrecognized speech: %s", phrase)
                return "unknown"
        self._emit("last_match", phrase)
        self._emit("last_command", phrase)
        if not str(action).startswith(("custom", "automation")):
            self._emit("last_result", f"Executed: {action}")
        self._emit_command_done(phrase, str(action), started, confidence)
        self.face_callback("listening")
        return str(action)

    def _emit_command_done(self, phrase: str, action: str, started: float,
                           confidence: float | None) -> None:
        """Publish one completed command for the Commands page."""
        try:
            payload = {
                "phrase": str(phrase or ""),
                "action": str(action or ""),
                "ms": int(max(0.0, (time.monotonic() - started) * 1000)),
                "confidence": None if confidence is None else round(float(confidence), 3),
            }
            self._emit("command_done", json.dumps(payload, ensure_ascii=False))
        except Exception:
            log.debug("Could not publish the command log entry", exc_info=True)

    def _wake_loop(self, audio_q: queue.Queue, recognizer) -> None:
        self._continuous = True
        try:
            while not self._stop.is_set():
                speaking_now = self._speech_is_speaking()
                if speaking_now != self._interrupt_listening:
                    # Entering or leaving playback: start from clean audio so a
                    # half-heard sentence can never leak into command matching.
                    self._interrupt_listening = speaking_now
                    self._reset(recognizer)
                    self._drain(audio_q)
                    if not speaking_now:
                        continue
                # While TALCON itself is speaking, listen only for an explicit
                # interruption such as "be quiet".  Every other word is
                # discarded, so TALCON's own voice can never be recognised as
                # a wake word or command.
                if speaking_now:
                    try:
                        pcm = audio_q.get(timeout=0.1)
                    except queue.Empty:
                        continue
                    try:
                        finished = recognizer.AcceptWaveform(self._enhance(pcm))
                        if finished:
                            heard = json.loads(recognizer.Result()).get("text", "")
                        else:
                            heard = json.loads(recognizer.PartialResult()).get("partial", "")
                    except Exception:
                        log.exception("Interrupt recognizer failed")
                        self._reset(recognizer)
                        continue
                    if finished:
                        remainder = strip_wake_phrase(heard)
                        if remainder in INTERRUPT_PHRASES:
                            log.info("Speech interrupted by voice: %s", remainder)
                            stopper = getattr(self._speech, "stop_speaking", None)
                            if callable(stopper):
                                try:
                                    stopper()
                                except Exception:
                                    log.exception("Could not interrupt speech")
                        self._reset(recognizer)
                        self._drain(audio_q)
                    continue

                if self._manual.is_set():
                    self._manual.clear()
                    self._run_command_session(audio_q)
                    self._reset(recognizer)
                    self._drain(audio_q)
                    continue

                try:
                    pcm = audio_q.get(timeout=0.25)
                except queue.Empty:
                    continue

                if self._speech_is_speaking():
                    self._drain(audio_q)
                    self._reset(recognizer)
                    continue

                try:
                    if recognizer.AcceptWaveform(self._enhance(pcm)):
                        text = json.loads(recognizer.Result()).get("text", "")
                    else:
                        text = json.loads(recognizer.PartialResult()).get("partial", "")
                except Exception:
                    log.exception("Wake recognizer failed")
                    return

                now = time.monotonic()
                if now < self._next_wake_at:
                    continue
                if is_wake_phrase(text):
                    self._next_wake_at = now + WAKE_DEBOUNCE
                    log.info("Wake word detected: %s", text)
                    remainder = strip_wake_phrase(text)
                    if remainder:
                        self._run_phrase_now(remainder, audio_q)
                    else:
                        self._run_command_session(audio_q)
                    self._reset(recognizer)
                    self._drain(audio_q)
        finally:
            self._continuous = False

    # -- one command session ----------------------------------------------
    def _run_phrase_now(self, phrase: str, audio_q: queue.Queue) -> None:
        """Execute a command spoken in the same breath as the wake word."""
        if not self._session_lock.acquire(blocking=False):
            return
        try:
            self.face_callback("command")
            self._emit("last_heard", phrase)
            self._set_status(f"Heard: {phrase}")
            action = self._execute_phrase(phrase)
            if action in (None, "none", "unknown"):
                self._handle_unintelligible(phrase, audio_q)
            self._wait_for_speech_end(20.0)
            self._drain(audio_q)
        finally:
            self.face_callback("idle")
            self._set_status("Listening for TALCON" if self._continuous else "Listening stopped")
            self._session_lock.release()

    def _run_command_session(self, audio_q: queue.Queue, recognizer=None) -> None:
        if not self._session_lock.acquire(blocking=False):
            return
        try:
            self.face_callback("listening")
            self._set_status("Listening for your command…")
            # Never start capturing while an announcement is still playing.
            self._wait_for_speech_end(10.0)
            if self._stop.is_set():
                return
            self._beep_and_settle(audio_q)
            if self._stop.is_set():
                return

            if recognizer is None:
                recognizer = self._make_recognizer()

            phrase, heard_speech, aborted = self._capture(audio_q, recognizer)
            if self._stop.is_set() or aborted:
                return
            if not heard_speech:
                # CASE A: no actual speech. Stay completely silent.
                self._emit("last_result", "No speech detected")
                return
            if not phrase and self._last_capture_assembly is None:
                # CASE B: speech but nothing intelligible. Never execute.
                self._emit("last_failure", "Speech heard but no words recognised")
                return

            # CASE C: a transcript exists; confidence-aware matching decides.
            if phrase:
                self._emit("last_heard", phrase)
                self._set_status(f"Heard: {phrase}")
            now = time.monotonic()
            action = None
            assembly = self._last_capture_assembly
            if assembly is not None:
                action = self._execute_assembly(
                    assembly, audio_q, now, state=self._last_capture_state
                )
            if action in (None, "none", "unknown") and self._last_capture_pcm:
                action = self._try_generic(
                    self._last_capture_pcm, audio_q, now,
                    state=self._last_capture_state,
                )
            if (action in (None, "none", "unknown")
                    and assembly is None and phrase):
                action = self._execute_phrase(phrase)
            if action in (None, "none", "unknown") and phrase:
                # If no safe command matched, use the same repeat policy as
                # continuous mode.  A missing transcript stays silent.
                self._handle_unintelligible(
                    self._refine_long_question(phrase, self._last_capture_pcm),
                    audio_q,
                )
            # Wait for any spoken confirmation before reopening the microphone.
            self._wait_for_speech_end(20.0)
            self._drain(audio_q)
        finally:
            self.face_callback("idle")
            self._set_status("Listening for TALCON" if self._continuous else "Listening stopped")
            self._session_lock.release()

    def _beep_and_settle(self, audio_q: queue.Queue) -> None:
        self._drain(audio_q)
        try:
            self._beep()
        except Exception:
            log.debug("Could not play the confirmation beep", exc_info=True)
        self._gate_microphone(audio_q, BEEP_GATE_SECONDS)
        self._gate_microphone(audio_q, SETTLE_SECONDS)
        self._drain(audio_q)

    def _handle_unintelligible(self, text: str, audio_q: queue.Queue) -> None:
        """Ask for one repeat only when the utterance sounded like a command.

        Unrelated background conversation is ignored silently, and a cooldown
        prevents an apology loop.  Nothing is executed here.
        """
        if self._commands_paused():
            # Commands are paused: never nag about a phrase we would ignore.
            self._drain(audio_q)
            return
        phrase = normalize_phrase(text)
        if not phrase:
            self._drain(audio_q)
            return
        natural = getattr(self.command_router, "natural_handler", None)
        if callable(natural) and not self._commands_paused():
            # Local matching failed completely.  A clearly natural-language
            # question or request goes to the AI assistant (when the user has
            # enabled it) instead of the repeat apology.  Everything local
            # keeps priority: this runs only after every local path failed.
            try:
                accepted = bool(natural(phrase))
            except Exception:
                log.exception("Assistant fallback failed")
                accepted = False
            if accepted:
                self._emit("last_match", phrase)
                self._drain(audio_q)
                return
        if not is_command_like(phrase, self._vocabulary() or ()):
            # Ordinary chatter is not a recognition failure, so leave the
            # diagnostics untouched and stay silent.
            self._drain(audio_q)
            return
        self._emit("last_failure", "No matching registered command")
        self._set_status("Speech detected · no matching command")
        now = time.monotonic()
        if now - self._last_repeat_at < REPEAT_COOLDOWN:
            self._drain(audio_q)
            return
        self._last_repeat_at = now
        speech = self._speech
        if speech is not None and getattr(speech, "enabled", False):
            try:
                say_and_wait = getattr(speech, "say_and_wait", None)
                if callable(say_and_wait):
                    say_and_wait(REPEAT_MESSAGE, 15.0)
                else:
                    speech.say(REPEAT_MESSAGE)
                    self._wait_for_speech_end(15.0)
            except Exception:
                log.exception("Could not speak the repeat request")
        self._wait_for_speech_end(15.0)
        self._drain(audio_q)

    def _capture(self, audio_q: queue.Queue, recognizer) -> tuple[str, bool, bool]:
        """Return (text, heard_speech, aborted).

        Every final segment and all of Vosk's alternatives are accumulated
        until the adaptive VAD has seen real trailing silence.  No fixed RMS
        threshold is used, and the audio is retained only in memory so the
        generic fallback can re-decode one weak utterance.
        """
        started = time.monotonic()
        last_voice = started
        heard_speech = False
        self._vad.reset()
        self._assembler.reset()
        self._last_capture_assembly = None
        self._last_capture_pcm = b""
        self._last_capture_hypotheses = []
        self._last_capture_state = None
        self._last_capture_endpoint = None
        pcm_buffer = bytearray()
        assembly = None
        last_added = ""
        last_state = None

        while time.monotonic() - started < self._max_utterance:
            if self._stop.is_set():
                return "", heard_speech, True
            if self._speech_is_speaking():
                self._drain(audio_q)
                return "", heard_speech, True
            try:
                data = audio_q.get(timeout=0.1)
            except queue.Empty:
                if not heard_speech and time.monotonic() - started > self._start_timeout:
                    return "", False, False
                if heard_speech and time.monotonic() - last_voice > self._trailing_silence:
                    break
                continue

            state = self._vad.update(data)
            last_state = state
            enhanced = self._enhance(data)
            pcm_buffer.extend(enhanced)
            pcm_limit = int(self._max_utterance * SAMPLE_RATE * 2)
            if len(pcm_buffer) > pcm_limit:
                del pcm_buffer[:-pcm_limit]
            if state.speech or state.speech_seen:
                heard_speech = True
                if state.silent_blocks == 0:
                    last_voice = time.monotonic()

            try:
                if recognizer.AcceptWaveform(enhanced):
                    payload = json.loads(recognizer.Result())
                    hypotheses = [
                        item for item in _result_hypotheses(payload)
                        if not _has_unknown(item.text)
                    ]
                    if hypotheses and hypotheses[0].normalized() != last_added:
                        last_added = hypotheses[0].normalized()
                        assembly = self._assembler.add(hypotheses)
                    if assembly is not None and assembly.heard:
                        heard_speech = True
            except Exception:
                log.exception("Command recognition failed")
                return "", heard_speech, False

            if heard_speech and time.monotonic() - last_voice > self._trailing_silence:
                break

        try:
            payload = json.loads(recognizer.FinalResult())
            hypotheses = [
                item for item in _result_hypotheses(payload)
                if not _has_unknown(item.text)
            ]
            if hypotheses and hypotheses[0].normalized() != last_added:
                self._last_capture_hypotheses = hypotheses
                assembly = self._assembler.add(hypotheses)
            elif assembly is not None:
                self._last_capture_hypotheses = [
                    Hypothesis(item) for item in assembly.candidates
                ]
        except Exception:
            log.debug("Could not read the final recognition result", exc_info=True)

        endpoint = time.monotonic() - last_voice
        phrase = ""
        if assembly is not None:
            phrase = assembly.matched or assembly.heard
        if phrase:
            heard_speech = True
            log.info(
                "Voice command recognized: %s (confidence=%s, endpoint=%.2fs, noise=%.0f)",
                phrase,
                "?" if assembly is None or assembly.confidence is None
                else f"{assembly.confidence:.2f}",
                endpoint,
                getattr(last_state, "noise_floor", 0.0),
            )
        self._last_capture_assembly = assembly
        self._last_capture_pcm = bytes(pcm_buffer)
        self._last_capture_state = last_state
        self._last_capture_endpoint = endpoint
        if last_state is not None:
            self._emit_diag(
                state=last_state, text=phrase,
                alternatives=[item.text for item in self._last_capture_hypotheses[1:]],
                confidence=assembly.confidence if assembly is not None else None,
                endpoint=endpoint,
                reason="Captured command utterance",
            )
        return phrase, heard_speech, False

    # -- manual listening when the wake loop is not running ----------------
    def _manual_session(self) -> None:
        self._engine = resolve_engine(self.settings)
        if not engine_model_ready(self.settings):
            self._set_status("Speech model missing · download it in Voice settings")
            return
        try:
            import sounddevice as sd
        except Exception as exc:
            self._set_status(f"Microphone unavailable · {exc}")
            return
        if self._engine != ENGINE_SHERPA:
            path = self._model_path()
            try:
                if self._model is None:
                    self._model = _load_model(str(path))
            except Exception as exc:
                self._set_status(f"Speech model could not be loaded · {exc}")
                return

        audio_q: queue.Queue = queue.Queue(maxsize=16)

        def callback(indata, frames, time_info, status):
            try:
                audio_q.put_nowait(bytes(indata))
            except queue.Full:
                try:
                    audio_q.get_nowait()
                    audio_q.put_nowait(bytes(indata))
                except queue.Empty:
                    pass

        try:
            with sd.RawInputStream(
                samplerate=SAMPLE_RATE, blocksize=BLOCK_SIZE, dtype="int16",
                channels=1, callback=callback, device=self._device(),
            ):
                self._drain(audio_q)
                self._run_command_session(audio_q)
        except Exception as exc:
            log.warning("Manual listen failed: %s", exc)
            self._set_status(f"Microphone unavailable · {exc}")
            self._emit("mic_error", str(exc))
            self.face_callback("idle")
