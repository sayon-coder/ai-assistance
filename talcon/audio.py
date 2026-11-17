"""Adaptive microphone level detection and calibration for TALCON.

The old detector used a single fixed RMS threshold (300).  That is too high
for quiet speech and too low in a noisy room.  This module tracks the ambient
noise floor continuously, opens the speech gate with hysteresis, and reports
clipping.  It never filters or rewrites the PCM sent to Vosk, so quiet
consonants cannot be removed by an aggressive denoiser.
"""
from __future__ import annotations

import array
import math
import statistics
from collections import deque
from dataclasses import dataclass

SAMPLE_RATE = 16000
CLIP_PEAK = 32000
MIN_NOISE_FLOOR = 2.0
MIN_SPEECH_RMS = 25.0
NOISE_HISTORY = 60
SNR_MULTIPLIER = 2.35
FLOOR_MARGIN = 30.0
RELEASE_MULTIPLIER = 1.55
RELEASE_MARGIN = 20.0
SPEECH_CONFIRM_BLOCKS = 1
SILENCE_RELEASE_BLOCKS = 3
#: About one second of blocks at the default 0.25 s block size.  Until this
#: many levels are measured, a provisional floor from the pre-roll is used.
WARMUP_BLOCKS = 4
#: Bounded pre-roll (about 6 s) of recent levels used to estimate the floor
#: while the detector is warming up.
PREROLL_BLOCKS = 24
#: Upper bound for a floor estimated during warm-up.  Speech is much louder
#: than this, so a first word can never be learned as the ambient floor; a
#: genuinely noisy room is calibrated properly through the user's setting.
WARMUP_FLOOR_CAP = 450.0


@dataclass(frozen=True)
class PcmStats:
    rms: float
    peak: int
    clipping: bool


@dataclass(frozen=True)
class VadState:
    level: float
    peak: int
    noise_floor: float
    threshold: float
    speech: bool
    speech_seen: bool
    clipping: bool
    silent_blocks: int


def pcm_stats(pcm: bytes) -> PcmStats:
    """Return RMS, absolute peak and clipping for 16-bit mono PCM."""
    if not pcm:
        return PcmStats(0.0, 0, False)
    usable = len(pcm) - (len(pcm) % 2)
    if usable <= 0:
        return PcmStats(0.0, 0, False)
    samples = array.array("h")
    samples.frombytes(pcm[:usable])
    if not samples:
        return PcmStats(0.0, 0, False)
    peak = 0
    total = 0
    for sample in samples:
        value = abs(sample)
        if value > peak:
            peak = value
        total += sample * sample
    return PcmStats(
        math.sqrt(total / len(samples)),
        peak,
        peak >= CLIP_PEAK,
    )


def pcm_rms(pcm: bytes) -> float:
    return pcm_stats(pcm).rms


class AdaptiveVad:
    """Noise-tracking voice activity detector with hysteresis."""

    def __init__(self, noise_floor: float | None = None,
                 min_speech_rms: float = MIN_SPEECH_RMS,
                 snr_multiplier: float = SNR_MULTIPLIER,
                 floor_margin: float = FLOOR_MARGIN,
                 release_multiplier: float = RELEASE_MULTIPLIER,
                 release_margin: float = RELEASE_MARGIN):
        self.min_speech_rms = max(10.0, float(min_speech_rms))
        self.snr_multiplier = max(1.2, float(snr_multiplier))
        self.floor_margin = max(0.0, float(floor_margin))
        self.release_multiplier = max(1.0, float(release_multiplier))
        self.release_margin = max(0.0, float(release_margin))
        self.noise_floor = (
            max(MIN_NOISE_FLOOR, float(noise_floor))
            if noise_floor is not None else None
        )
        self._history: deque[float] = deque(maxlen=NOISE_HISTORY)
        self._preroll: deque[float] = deque(maxlen=PREROLL_BLOCKS)
        self._ready = noise_floor is not None

    @property
    def threshold(self) -> float:
        floor = self.noise_floor or MIN_NOISE_FLOOR
        return max(
            self.min_speech_rms,
            floor * self.snr_multiplier,
            floor + self.floor_margin,
        )

    @property
    def release_threshold(self) -> float:
        floor = self.noise_floor or MIN_NOISE_FLOOR
        return max(
            30.0,
            floor * self.release_multiplier,
            floor + self.release_margin,
        )

    def calibrate(self, levels) -> float:
        """Set the floor from measured ambient blocks (20th percentile)."""
        values = [max(0.0, float(value)) for value in levels or () if value is not None]
        if not values:
            return self.noise_floor or 0.0
        floor = statistics.quantiles(values, n=5)[0] if len(values) >= 5 else min(values)
        self.noise_floor = max(MIN_NOISE_FLOOR, float(floor))
        self._history.clear()
        self._history.extend(values[-NOISE_HISTORY:])
        self._preroll.clear()
        self._ready = True
        return self.noise_floor

    def update_stats(self, stats: PcmStats, active: bool = False) -> VadState:
        level = float(stats.rms)
        if not self._ready:
            # Keep a bounded pre-roll and estimate ambient floor robustly.
            # Before enough levels are available, use a conservative threshold
            # so speech on the very first block is not suppressed. Loud speech
            # is capped and cannot become an unbounded floor estimate.
            self._preroll.append(level)
            provisional = max(MIN_NOISE_FLOOR, min(self._preroll))
            self.noise_floor = min(provisional, WARMUP_FLOOR_CAP)
            if len(self._preroll) >= WARMUP_BLOCKS:
                ordered = sorted(self._preroll)
                index = max(0, int(len(ordered) * 0.20) - 1)
                ambient = ordered[index]
                self.noise_floor = min(
                    max(MIN_NOISE_FLOOR, ambient), WARMUP_FLOOR_CAP
                )
                self._history.extend(self._preroll)
                self._ready = True
            return self.snapshot(stats, warmup=True)
        # While speech is active the floor must not learn the speaker's level.
        if not active:
            # Ignore isolated bursts; only sustained ambient levels can move
            # the rolling floor. The upper bound prevents a sudden loud event
            # from ratcheting the speech threshold upward.
            self._history.append(level)
            if len(self._history) >= 5:
                ordered = sorted(self._history)
                index = max(0, int(len(ordered) * 0.20) - 1)
                ambient = ordered[index]
            else:
                ambient = min(self._history)
            # Rise slowly, fall quickly: a sudden noise burst must not lift the
            # gate for the rest of the session.
            if ambient < self.noise_floor:
                self.noise_floor = (
                    self.noise_floor * 0.55 + ambient * 0.45
                )
            else:
                bounded_ambient = min(ambient, max(self.noise_floor * 1.5, self.noise_floor + 100.0))
                self.noise_floor = (
                    self.noise_floor * 0.95 + bounded_ambient * 0.05
                )
            self.noise_floor = max(MIN_NOISE_FLOOR, self.noise_floor)
        return self.snapshot(stats)

    def snapshot(self, stats: PcmStats | None = None, warmup: bool = False) -> VadState:
        stats = stats or PcmStats(0.0, 0, False)
        # Before warm-up has enough blocks, do not gate out an immediate first
        # word based on a noise estimate that does not exist yet.
        threshold = self.min_speech_rms if warmup and not self._ready else self.threshold
        speech = (self._ready or warmup) and stats.rms >= threshold
        return VadState(
            level=stats.rms,
            peak=stats.peak,
            noise_floor=self.noise_floor or 0.0,
            threshold=threshold,
            speech=speech,
            speech_seen=speech,
            clipping=stats.clipping,
            silent_blocks=0 if speech else 1,
        )


class UtteranceVad:
    """Per-utterance state layered on top of :class:`AdaptiveVad`."""

    def __init__(self, vad: AdaptiveVad | None = None):
        self.vad = vad or AdaptiveVad()
        self.active = False
        self.speech_seen = False
        self.silent_blocks = 0
        self.confirm_blocks = 0
        self.last_level = 0.0
        self.last_peak = 0
        self.last_noise = self.vad.noise_floor or 0.0
        self.last_threshold = self.vad.threshold
        self.clipping = False

    def update(self, pcm: bytes) -> VadState:
        stats = pcm_stats(pcm)
        self.last_level = stats.rms
        self.last_peak = stats.peak
        self.clipping = self.clipping or stats.clipping
        base = self.vad.update_stats(stats, active=self.active)
        self.last_noise = base.noise_floor
        self.last_threshold = base.threshold
        if not self.active:
            if base.speech:
                self.confirm_blocks += 1
                if self.confirm_blocks >= SPEECH_CONFIRM_BLOCKS:
                    self.active = True
                    self.speech_seen = True
                    self.silent_blocks = 0
            else:
                self.confirm_blocks = 0
                self.silent_blocks += 1
        else:
            if stats.rms >= self.vad.release_threshold:
                self.silent_blocks = 0
            else:
                self.silent_blocks += 1
                if self.silent_blocks >= SILENCE_RELEASE_BLOCKS:
                    self.active = False
                    self.confirm_blocks = 0
        return VadState(
            level=stats.rms,
            peak=stats.peak,
            noise_floor=self.last_noise,
            threshold=self.last_threshold,
            speech=self.active,
            speech_seen=self.speech_seen,
            clipping=stats.clipping,
            silent_blocks=self.silent_blocks,
        )

    def reset(self) -> None:
        self.active = False
        self.speech_seen = False
        self.silent_blocks = 0
        self.confirm_blocks = 0
        self.clipping = False


def calibrate_microphone(seconds_noise: float = 2.0,
                         seconds_speech: float = 3.0,
                         device: int | None = None) -> dict:
    """Record ambient noise then normal speech and report usable levels.

    The recording is never written to disk.  The caller should ask the user to
    stay quiet for the first part and then speak normally.
    """
    try:
        import numpy as np
        import sounddevice as sd
    except Exception as exc:
        return {"ok": False, "error": str(exc)}
    noise_seconds = max(0.5, float(seconds_noise))
    speech_seconds = max(0.5, float(seconds_speech))
    try:
        noise_frames = int(noise_seconds * SAMPLE_RATE)
        speech_frames = int(speech_seconds * SAMPLE_RATE)
        noise = sd.rec(
            noise_frames, samplerate=SAMPLE_RATE, channels=1,
            dtype="int16", device=device,
        )
        sd.wait()
        speech = sd.rec(
            speech_frames, samplerate=SAMPLE_RATE, channels=1,
            dtype="int16", device=device,
        )
        sd.wait()
        noise_values = noise.reshape(-1).astype("float64")
        speech_values = speech.reshape(-1).astype("float64")
        noise_blocks = [
            float(np.sqrt(np.mean(block * block)))
            for block in np.array_split(noise_values, max(1, int(noise_seconds * 4)))
            if block.size
        ]
        speech_blocks = [
            float(np.sqrt(np.mean(block * block)))
            for block in np.array_split(speech_values, max(1, int(speech_seconds * 4)))
            if block.size
        ]
        nonsilent = [value for value in speech_blocks if value > 0]
        noise_floor = (
            float(np.percentile(noise_blocks, 20))
            if noise_blocks else 0.0
        )
        speech_level = (
            float(np.percentile(nonsilent, 80))
            if nonsilent else 0.0
        )
        peak = int(max(
            abs(int(noise_values.max(initial=0))),
            abs(int(noise_values.min(initial=0))),
            abs(int(speech_values.max(initial=0))),
            abs(int(speech_values.min(initial=0))),
        ))
        clipping_ratio = float(
            np.mean(np.abs(speech_values) >= CLIP_PEAK)
        ) if speech_values.size else 0.0
        vad = AdaptiveVad(noise_floor=noise_floor)
        return {
            "ok": True,
            "noise_floor": round(noise_floor, 1),
            "speech_rms": round(speech_level, 1),
            "threshold": round(vad.threshold, 1),
            "peak": peak,
            "clipping": bool(peak >= CLIP_PEAK or clipping_ratio > 0.001),
            "clipping_ratio": round(clipping_ratio, 4),
            "snr": round(speech_level / max(1.0, noise_floor), 2),
            "error": "",
        }
    except Exception as exc:
        return {"ok": False, "error": str(exc)}
