"""Offline before/after benchmark for TALCON's speech front end.

It synthesises a small command corpus with the local Kokoro-82M neural voice,
then creates slow, quiet, noisy and paused variants of every utterance.  Both
the old front end (fixed RMS gate, first final only, unknown app words dropped)
and the new front end (adaptive VAD, all segments/alternatives, generic
fallback) decode exactly the same PCM.  No cloud service is used.

Run from the project root:

    .venv\\Scripts\\python.exe scripts\\benchmark_speech.py

Results are printed and written to ``logs/speech_benchmark.json``.

It also decodes the same conditioned audio with every available engine
(Vosk, optional sherpa-onnx, and experimental Parakeet) and reports word error
rate and latency at simulated 0.5 m, 2 m and 3 m distances.  Because the corpus
is synthetic, it measures recognisability of degraded audio, not a real
microphone's acoustic performance.
"""
from __future__ import annotations

import argparse
import json
import sys
import math
import queue
import random
import re
import statistics
import tempfile
import time
import wave
from pathlib import Path
from types import SimpleNamespace

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from talcon.app_catalog import (
    DEFAULT_ALIASES,
    extract_app_command,
    extract_close_command,
    extract_running_query,
    resolve_application,
)
from talcon.apps import AppEntry
from talcon.commands import build_builtin_registry, build_recognition_grammar, normalize_phrase
from talcon.phrase import PhraseAssembler, coerce_hypotheses
from talcon.audio import AudioFrontEnd, UtteranceVad, pcm_rms

SAMPLE_RATE = 16000
BLOCK = 4000
OLD_RMS_GATE = 300.0
AFTER_MIN_CONFIDENCE = 0.45

CATALOGUE = [
    AppEntry(name="Notepad", kind="executable", path="C:/Windows/notepad.exe"),
    AppEntry(name="Google Chrome", kind="executable", path="C:/chrome.exe"),
    AppEntry(name="ChatGPT", kind="executable", path="C:/chatgpt.exe"),
    AppEntry(name="Calculator", kind="executable", path="C:/calc.exe"),
    AppEntry(name="YouTube", kind="executable", path="C:/youtube.exe"),
    AppEntry(name="WhatsApp", kind="executable", path="C:/whatsapp.exe"),
]

CORPUS = [
    ("open notepad", "open:Notepad"),
    ("close notepad", "close:Notepad"),
    ("open chrome", "open:Google Chrome"),
    ("open chatgpt", "open:ChatGPT"),
    ("open calculator", "open:Calculator"),
    ("what time is it", "time"),
    ("hey talcon open the eyes", "open_eyes"),
    ("please launch notepad", "open:Notepad"),
    ("open youtube", "open:YouTube"),
    ("open whatsapp", "open:WhatsApp"),
    ("is chrome running", "running:Google Chrome"),
]

VARIANTS = ("normal", "slow", "quiet", "noisy", "paused")


def model_path(explicit: str | None) -> Path:
    if explicit:
        return Path(explicit).expanduser()
    return Path(__file__).resolve().parents[1] / "models" / "vosk-model-small-en-us-0.15"


_KOKORO_ENGINE = None


def _kokoro_engine():
    """Load the one local Kokoro model and reuse it for the whole run."""
    global _KOKORO_ENGINE
    if _KOKORO_ENGINE is None:
        from talcon.neural_tts import model_paths

        model, voices = model_paths()
        if model is None or not voices.is_file():
            raise SystemExit(
                "Kokoro model not found. Install it with: "
                "python -m talcon.model_setup --kokoro"
            )
        from kokoro_onnx import Kokoro

        _KOKORO_ENGINE = Kokoro(str(model), str(voices))
    return _KOKORO_ENGINE


def tts_wav(text: str, target: Path) -> None:
    """Synthesise 16 kHz mono PCM with Kokoro (no Microsoft voice is used)."""
    from talcon.neural_tts import DEFAULT_LANGUAGE, DEFAULT_VOICE

    samples, rate = _kokoro_engine().create(
        text, voice=DEFAULT_VOICE, speed=1.0, lang=DEFAULT_LANGUAGE
    )
    audio = np.asarray(samples, dtype=np.float32).reshape(-1)
    if rate != SAMPLE_RATE:
        # Kokoro outputs 24 kHz; Vosk expects 16 kHz.
        duration = len(audio) / float(rate or 1)
        target_length = max(1, int(duration * SAMPLE_RATE))
        audio = np.interp(
            np.linspace(0, len(audio) - 1, target_length),
            np.arange(len(audio)),
            audio,
        ).astype(np.float32)
    pcm = np.clip(audio, -1.0, 1.0)
    pcm = (pcm * 32767.0).astype("<i2")
    with wave.open(str(target), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(SAMPLE_RATE)
        handle.writeframes(pcm.tobytes())


def read_pcm(path: Path) -> bytes:
    with wave.open(str(path), "rb") as handle:
        if (handle.getframerate(), handle.getnchannels(), handle.getsampwidth()) != (
            16000, 1, 2
        ):
            raise RuntimeError(f"Unexpected WAV format in {path}")
        return handle.readframes(handle.getnframes())


def pcm_to_array(pcm: bytes) -> np.ndarray:
    return np.frombuffer(pcm, dtype="<i2").astype(np.float32)


def array_to_pcm(samples: np.ndarray) -> bytes:
    clipped = np.clip(samples, -32768, 32767).astype("<i2")
    return clipped.tobytes()


def add_silence(pcm: bytes, seconds: float) -> bytes:
    return b"\x00\x00" * int(SAMPLE_RATE * seconds) + pcm


def make_variants(pcm: bytes, rng: random.Random) -> dict[str, bytes]:
    samples = pcm_to_array(pcm)
    if not len(samples):
        return {}
    quiet = samples * 0.12
    slow = np.interp(
        np.linspace(0, len(samples) - 1, int(len(samples) * 1.35)),
        np.arange(len(samples)), samples,
    )
    rms = float(np.sqrt(np.mean(samples.astype(np.float64) ** 2))) or 1.0
    # About 8 dB SNR: hard enough to expose the old fixed gate and weak
    # hypotheses, but still a realistic room for an offline recogniser.
    noise = np.array(
        [rng.gauss(0.0, rms / 2.5) for _ in range(len(samples))],
        dtype=np.float32,
    )
    noisy = samples + noise
    cut = int(len(samples) * 0.55)
    paused = np.concatenate([
        samples[:cut],
        np.zeros(int(SAMPLE_RATE * 1.0), dtype=np.float32),
        samples[cut:],
    ])
    return {
        "normal": pcm,
        "slow": array_to_pcm(slow),
        "quiet": array_to_pcm(quiet),
        "noisy": array_to_pcm(noisy),
        "paused": array_to_pcm(paused),
    }


def noise_only(seconds: float = 3.0, rms: float = 700.0) -> bytes:
    rng = random.Random(11)
    samples = np.array(
        [rng.gauss(0.0, rms) for _ in range(int(SAMPLE_RATE * seconds))],
        dtype=np.float32,
    )
    return array_to_pcm(samples)


def old_gate(pcm: bytes) -> bool:
    for offset in range(0, len(pcm), BLOCK * 2):
        if pcm_rms(pcm[offset:offset + BLOCK * 2]) >= OLD_RMS_GATE:
            return True
    return False


def after_gate(pcm: bytes) -> bool:
    vad = UtteranceVad()
    for offset in range(0, len(pcm), BLOCK * 2):
        state = vad.update(pcm[offset:offset + BLOCK * 2])
    return state.speech_seen


def build_phrases(aliases: dict[str, str]) -> set[str]:
    registry = build_builtin_registry()
    phrases = {normalize_phrase(item) for item in registry.phrases()}
    for entry in CATALOGUE:
        name = entry.name.lower()
        phrases.update({
            f"open {name}", f"launch {name}", f"start {name}", f"run {name}",
            f"close {name}", f"exit {name}", f"quit {name}",
            f"is {name} running",
        })
    return {item for item in phrases if item}


def old_phrases(phrases: set[str], model) -> set[str]:
    """The old filter: an app phrase with any model-unknown word is dropped."""
    find = model.vosk_model_find_word
    result = set()
    for phrase in phrases:
        words = phrase.split()
        if words and words[0] not in {
            "open", "launch", "start", "run", "close", "exit", "quit", "is",
        }:
            result.add(phrase)
            continue
        if all(find(word) >= 0 for word in words):
            result.add(phrase)
    return result


class StubRouter:
    def __init__(self, aliases):
        self.app_aliases = aliases
        self.speech = None
        self.registry = None


def new_phrases(phrases: set[str], model, aliases: dict[str, str]) -> set[str]:
    """Use the real production substitution logic, without a microphone."""
    from talcon.voice import VoiceController

    controller = VoiceController(
        {"always_listen": False}, StubRouter(aliases),
        beep=lambda: None, event_callback=lambda *_args: None,
    )
    controller._model = model
    return controller._drop_unsupported_app_phrases(phrases)


def decode_with_grammar(model, grammar: str, pcm: bytes, first_final_only: bool):
    from vosk import KaldiRecognizer

    recognizer = KaldiRecognizer(model, SAMPLE_RATE, grammar)
    recognizer.SetMaxAlternatives(4)
    recognizer.SetWords(True)
    segments = []
    for offset in range(0, len(pcm), BLOCK * 2):
        if not recognizer.AcceptWaveform(pcm[offset:offset + BLOCK * 2]):
            continue
        payload = json.loads(recognizer.Result())
        if first_final_only:
            if payload_text(payload):
                segments.append(payload)
                break
            continue
        segments.append(payload)
    final = json.loads(recognizer.FinalResult())
    if payload_text(final):
        if not first_final_only or not segments:
            segments.append(final)
        elif not payload_text(segments[-1]):
            segments[-1] = final
    return segments


def decode_generic(model, pcm: bytes):
    from vosk import KaldiRecognizer

    recognizer = KaldiRecognizer(model, SAMPLE_RATE)
    recognizer.SetMaxAlternatives(4)
    recognizer.SetWords(True)
    for offset in range(0, len(pcm), BLOCK * 2):
        recognizer.AcceptWaveform(pcm[offset:offset + BLOCK * 2])
    return json.loads(recognizer.FinalResult())


def payload_text(payload) -> str:
    """Read both the plain and the SetMaxAlternatives result shapes."""
    if payload.get("text"):
        return str(payload["text"])
    for alternative in payload.get("alternatives") or ():
        if isinstance(alternative, dict) and alternative.get("text"):
            return str(alternative["text"])
    return ""


def payload_hypotheses(payload) -> list:
    items = [payload]
    items.extend(payload.get("alternatives") or [])
    return coerce_hypotheses(items)


def interpret(text: str, aliases: dict[str, str]) -> str | None:
    phrase = normalize_phrase(text)
    if not phrase:
        return None
    if "time" in phrase and ("what" in phrase or "tell" in phrase):
        return "time"
    if "open the eyes" in phrase or "open your eyes" in phrase:
        return "open_eyes"
    if "study mode" in phrase:
        return "study"
    running = extract_running_query(phrase)
    if running:
        result = resolve_application(running, CATALOGUE, aliases)
        return f"running:{result.entry.name}" if result.matched else None
    close = extract_close_command(phrase)
    if close:
        result = resolve_application(close, CATALOGUE, aliases)
        return f"close:{result.entry.name}" if result.matched else None
    opened = extract_app_command(phrase)
    if opened:
        result = resolve_application(opened, CATALOGUE, aliases)
        return f"open:{result.entry.name}" if result.matched else None
    return None


def old_transcript(segments) -> str:
    """The old session kept only the first final segment."""
    for payload in segments:
        text = payload_text(payload)
        if text:
            return text
    return ""


def after_transcript(segments, model, pcm: bytes, vocabulary: set[str], aliases):
    """Join every segment plus alternatives, then try one generic re-decode."""
    assembler = PhraseAssembler(vocabulary=vocabulary)
    assembly = None
    for payload in segments:
        hypotheses = payload_hypotheses(payload)
        if not hypotheses:
            continue
        # Mirror continuous listening: keep buffering finals until trailing
        # silence, and decide once on the complete assembly.
        assembly = assembler.add(hypotheses)
    if assembly is not None:
        if assembly.matched:
            return assembly.matched, "phrase-list"
        for candidate in assembly.candidates:
            action = interpret(candidate, aliases)
            if action:
                confidence = assembly.scores.get(candidate)
                if confidence is None or confidence >= AFTER_MIN_CONFIDENCE:
                    return candidate, "alternative"
    generic = decode_generic(model, pcm)
    hypotheses = payload_hypotheses(generic)
    for hypothesis in hypotheses[:1]:
        action = interpret(hypothesis.text, aliases)
        if action and (
            hypothesis.confidence is not None
            and hypothesis.confidence >= AFTER_MIN_CONFIDENCE
        ):
            return hypothesis.text, "generic"
    return "", "none"


# -- engine matrix -----------------------------------------------------------
def normalize_words(text: str) -> list[str]:
    return re.findall(r"[a-z0-9']+", str(text or "").lower())


def word_error_rate(reference: str, hypothesis: str) -> float:
    """Levenshtein word error rate, the standard ASR WER definition."""
    ref = normalize_words(reference)
    hyp = normalize_words(hypothesis)
    if not ref:
        return 0.0 if not hyp else 1.0
    previous = list(range(len(hyp) + 1))
    for row, ref_word in enumerate(ref, start=1):
        current = [row]
        for column, hyp_word in enumerate(hyp, start=1):
            cost = 0 if ref_word == hyp_word else 1
            current.append(min(
                previous[column] + 1,
                current[column - 1] + 1,
                previous[column - 1] + cost,
            ))
        previous = current
    return previous[-1] / len(ref)


def at_distance(pcm: bytes, distance: float, noise_rms: float = 120.0) -> bytes:
    """Approximate a microphone at ``distance`` metres from the speaker.

    Level falls with distance, air absorption rolls off the highs, and the
    room noise floor stays roughly constant.  At 0.5 m the reference is nearly
    unchanged; at 3 m speech is about 15 dB quieter and noticeably duller.
    """
    samples = pcm_to_array(pcm)
    if not len(samples):
        return pcm
    gain = 0.5 / max(0.5, float(distance))
    attenuated = samples * gain
    spectrum = np.fft.rfft(attenuated)
    frequencies = np.fft.rfftfreq(len(attenuated), 1.0 / SAMPLE_RATE)
    cutoff = max(1800.0, 12000.0 / (1.0 + float(distance)))
    spectrum *= 1.0 / (1.0 + (frequencies / cutoff) ** 2)
    attenuated = np.fft.irfft(spectrum, n=len(attenuated))
    rng = np.random.default_rng(101 + int(float(distance) * 10))
    noise = rng.normal(0.0, float(noise_rms), len(attenuated))
    return array_to_pcm(attenuated + noise)


class VoskBenchEngine:
    name = "vosk"

    def __init__(self, model, grammar: str):
        self.model = model
        self.grammar = grammar

    def transcribe(self, pcm: bytes) -> str:
        segments = decode_with_grammar(self.model, self.grammar, pcm, False)
        return " ".join(
            text for text in (payload_text(item) for item in segments) if text
        ).strip()


class SherpaBenchEngine:
    name = "sherpa"

    def __init__(self, settings: dict):
        from talcon.asr import create_streaming_recognizer

        self.recognizer = create_streaming_recognizer(settings, engine="sherpa")

    def transcribe(self, pcm: bytes) -> str:
        recognizer = self.recognizer
        recognizer.Reset()
        texts = []
        for offset in range(0, len(pcm), BLOCK * 2):
            if recognizer.AcceptWaveform(pcm[offset:offset + BLOCK * 2]):
                payload = json.loads(recognizer.Result())
                if payload.get("text"):
                    texts.append(str(payload["text"]))
        final = json.loads(recognizer.FinalResult())
        if final.get("text"):
            texts.append(str(final["text"]))
        return " ".join(texts).strip()


class ParakeetBenchEngine:
    name = "parakeet"

    def __init__(self, model_path: str):
        from talcon.asr import parakeet_engine

        self.parakeet = parakeet_engine
        self.recognizer = parakeet_engine.create_recognizer(model_path)

    def transcribe(self, pcm: bytes) -> str:
        return self.parakeet.transcribe(self.recognizer, pcm)


def build_bench_engines(args, model, grammar: str):
    from talcon.asr import sherpa_engine

    requested = str(args.engine or "both").lower()
    engines = []
    settings = {
        "asr_engine": "sherpa",
        "sherpa_model_path": args.sherpa_model
        or "models/sherpa-onnx-streaming-zipformer-en-2023-06-26",
    }
    path = sherpa_engine.resolve_model_path(settings["sherpa_model_path"])
    sherpa_ready = (
        sherpa_engine.package_available() and sherpa_engine.model_ready(path)
    )
    if requested == "auto":
        from talcon.asr import resolve_engine

        resolved = resolve_engine(settings)
        if resolved == "sherpa" and sherpa_ready:
            engines.append(SherpaBenchEngine(settings))
        else:
            engines.append(VoskBenchEngine(model, grammar))
        return engines
    if requested in {"both", "vosk"}:
        engines.append(VoskBenchEngine(model, grammar))
    if requested in {"both", "sherpa"}:
        if sherpa_ready:
            engines.append(SherpaBenchEngine(settings))
        elif requested != "both":
            print(f"  sherpa-onnx unavailable ({sherpa_engine.describe_model(path)}); skipped")
    if requested == "parakeet":
        from talcon.asr import parakeet_engine

        if not args.parakeet_model:
            raise SystemExit("--engine parakeet requires --parakeet-model <folder>")
        if not parakeet_engine.model_ready(args.parakeet_model):
            raise SystemExit(f"Parakeet model not found: {args.parakeet_model}")
        engines.append(ParakeetBenchEngine(args.parakeet_model))
    return engines


def run_engine_matrix(args, samples, aliases: dict[str, str], model,
                      grammar: str) -> dict:
    """WER and latency per engine and distance on identical conditioned PCM."""
    distances = [float(item) for item in str(args.distances).split(",") if item.strip()]
    report: dict = {}
    for engine in build_bench_engines(args, model, grammar):
        per_distance: dict = {}
        for distance in distances:
            word_errors = 0.0
            words = 0
            latencies = []
            correct = 0
            for sample in samples:
                pcm = at_distance(sample["pcm"], distance)
                if args.frontend:
                    pcm = AudioFrontEnd.from_settings({
                        "audio_agc": True,
                        "audio_noise_suppression": True,
                        "audio_aec": False,
                    }).process(pcm)
                started = time.perf_counter()
                text = engine.transcribe(pcm)
                latencies.append((time.perf_counter() - started) * 1000.0)
                word_errors += word_error_rate(sample["text"], text) * max(
                    1, len(normalize_words(sample["text"]))
                )
                words += max(1, len(normalize_words(sample["text"])))
                if interpret(text, aliases) == sample["expected"]:
                    correct += 1
            ordered = sorted(latencies)
            duration = sum(len(sample["pcm"]) / 2 / SAMPLE_RATE for sample in samples)
            total_ms = sum(latencies)
            per_distance[f"{distance:g}m"] = {
                "samples": len(samples),
                "wer": round(word_errors / max(1, words), 3),
                "action_accuracy": round(correct / max(1, len(samples)), 3),
                "mean_latency_ms": round(statistics.mean(latencies), 1),
                "p95_latency_ms": round(
                    ordered[min(len(ordered) - 1, int(len(ordered) * 0.95))], 1
                ),
                "real_time_factor": round(
                    (total_ms / 1000.0) / max(0.001, duration), 3
                ),
            }
        report[engine.name] = per_distance
    return report


def run(args) -> dict:
    path = model_path(args.model)
    if not path.is_dir():
        raise SystemExit(f"Vosk model not found: {path}")
    from vosk import Model, SetLogLevel

    SetLogLevel(-1)
    model = Model(str(path))
    aliases_before = dict(DEFAULT_ALIASES)
    aliases_after = dict(DEFAULT_ALIASES)
    phrases = build_phrases(aliases_after)
    before_phrases = old_phrases(phrases, model)
    after_phrases = new_phrases(phrases, model, aliases_after)
    grammar_before = build_recognition_grammar(sorted(before_phrases))
    grammar_after = build_recognition_grammar(sorted(after_phrases))
    vocabulary = {normalize_phrase(item) for item in after_phrases}

    rng = random.Random(7)
    rows = []
    samples = []
    with tempfile.TemporaryDirectory(prefix="talcon-bench-") as temp:
        temp = Path(temp)
        corpus = CORPUS[: max(1, int(args.limit))] if args.limit else CORPUS
        for index, (text, expected) in enumerate(corpus):
            wav = temp / f"sample-{index}.wav"
            tts_wav(text, wav)
            base = add_silence(read_pcm(wav), 1.0)
            for variant, pcm in make_variants(base, rng).items():
                samples.append({
                    "text": text, "expected": expected,
                    "variant": variant, "pcm": pcm,
                })
                started = time.perf_counter()
                old_open = old_gate(pcm)
                old_text = old_transcript(
                    decode_with_grammar(
                        model, grammar_before, pcm, first_final_only=True
                    )
                ) if old_open else ""
                old_action = interpret(old_text, aliases_before) if old_open else None
                old_ms = (time.perf_counter() - started) * 1000

                started = time.perf_counter()
                new_open = after_gate(pcm)
                if new_open:
                    segments = decode_with_grammar(
                        model, grammar_after, pcm, first_final_only=False
                    )
                    new_text, route = after_transcript(
                        segments, model, pcm, vocabulary, aliases_after
                    )
                else:
                    new_text, route = "", "gate-closed"
                new_action = interpret(new_text, aliases_after) if new_open else None
                new_ms = (time.perf_counter() - started) * 1000
                rows.append({
                    "text": text, "variant": variant, "expected": expected,
                    "old_gate": old_open, "new_gate": new_open,
                    "old_text": old_text, "new_text": new_text,
                    "old_action": old_action, "new_action": new_action,
                    "new_route": route,
                    "old_ms": round(old_ms, 1), "new_ms": round(new_ms, 1),
                })
            print(f"  {index + 1}/{len(corpus)} {text}")

    # Noise-only false-activation check (3 seconds, both gates).
    noise = add_silence(noise_only(), 1.0)
    old_noise = old_gate(noise)
    new_noise = after_gate(noise)
    noise_old_text = old_transcript(
        decode_with_grammar(model, grammar_before, noise, first_final_only=True)
    ) if old_noise else ""
    noise_new_text = ""
    if new_noise:
        noise_new_text, _route = after_transcript(
            decode_with_grammar(model, grammar_after, noise, False),
            model, noise, vocabulary, aliases_after,
        )
    noise_row = {
        "old_gate": old_noise, "new_gate": new_noise,
        "old_text": noise_old_text, "new_text": noise_new_text,
        "old_false_action": interpret(noise_old_text, aliases_before),
        "new_false_action": interpret(noise_new_text, aliases_after),
    }

    def score(field: str, variant: str | None = None) -> dict:
        selected = [
            row for row in rows
            if variant is None or row["variant"] == variant
        ]
        correct = sum(row[field] == row["expected"] for row in selected)
        opened = sum(bool(row[field.replace("action", "gate")]) for row in selected)
        return {
            "correct": correct, "total": len(selected),
            "accuracy": round(correct / len(selected), 3) if selected else 0.0,
            "gate_open": opened,
        }

    print("Measuring engines at the configured distances…")
    engine_matrix = run_engine_matrix(
        args, samples, aliases_after, model, grammar_after
    )

    report = {
        "model": path.name,
        "samples": len(rows),
        "corpus": [text for text, _expected in corpus],
        "variants": list(VARIANTS),
        "distances_m": [
            float(item) for item in str(args.distances).split(",") if item.strip()
        ],
        "frontend": bool(args.frontend),
        "engines": engine_matrix,
        "before": {
            "overall": score("old_action"),
            "by_variant": {v: score("old_action", v) for v in VARIANTS},
            "mean_latency_ms": round(statistics.mean(r["old_ms"] for r in rows), 1),
        },
        "after": {
            "overall": score("new_action"),
            "by_variant": {v: score("new_action", v) for v in VARIANTS},
            "mean_latency_ms": round(statistics.mean(r["new_ms"] for r in rows), 1),
            "generic_routes": sum(r["new_route"] == "generic" for r in rows),
        },
        "noise_only": noise_row,
        "rows": rows,
        "notes": (
            "Synthetic Kokoro corpus, identical conditioned PCM for every engine. "
            "engines[engine][distance] reports word error rate against the spoken "
            "sentence and latency measured while feeding the whole utterance as "
            "0.25 s streaming blocks. Distances are simulated by level loss, air "
            "absorption and a fixed room-noise floor. Results are not a substitute "
            "for real-microphone testing."
        ),
    }
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, indent=2), encoding="utf-8")
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", default=None, help="Vosk model folder")
    parser.add_argument(
        "--output", default="logs/speech_benchmark.json",
        help="JSON result path (default: logs/speech_benchmark.json)",
    )
    parser.add_argument(
        "--engine", default="both",
        choices=("both", "auto", "vosk", "sherpa", "parakeet"),
        help="Recognition engine(s) to measure (default: both = Vosk + sherpa)",
    )
    parser.add_argument(
        "--distances", default="0.5,2,3",
        help="Comma-separated speaker distances in metres (default: 0.5,2,3)",
    )
    parser.add_argument("--sherpa-model", default=None,
                        help="sherpa-onnx transducer folder")
    parser.add_argument("--parakeet-model", default=None,
                        help="Experimental Parakeet ONNX model folder")
    parser.add_argument("--no-frontend", dest="frontend", action="store_false",
                        help="Disable AGC/noise suppression for this comparison")
    parser.add_argument("--limit", type=int, default=0,
                        help="Use only the first N corpus sentences")
    parser.set_defaults(frontend=True)
    args = parser.parse_args()
    report = run(args)
    print("\nBefore:", json.dumps(report["before"]["overall"]))
    print("After: ", json.dumps(report["after"]["overall"]))
    print("Noise-only:", json.dumps(report["noise_only"]))
    for name, distances in report.get("engines", {}).items():
        print(f"\n{name}:")
        for distance, values in distances.items():
            print(
                f"  {distance:>4}  WER={values['wer']:.3f}  "
                f"action={values['action_accuracy']:.3f}  "
                f"latency={values['mean_latency_ms']:.1f} ms "
                f"(p95 {values['p95_latency_ms']:.1f}, RTF {values['real_time_factor']})"
            )
    print("\nWritten to", args.output)


if __name__ == "__main__":
    main()
