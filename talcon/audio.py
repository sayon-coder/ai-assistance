"""Adaptive microphone level detection, calibration and audio conditioning.

The old detector used a single fixed RMS threshold (300).  That is too high
for quiet speech and too low in a noisy room.  This module tracks the ambient
noise floor continuously, opens the speech gate with hysteresis, and reports
clipping.

``AudioFrontEnd`` is the optional conditioning stage that sits between the
microphone and every speech engine (Vosk, sherpa-onnx and the long-question
Whisper pass).  It applies automatic gain control, streaming spectral noise
suppression and an optional reference-based echo suppressor.  The voice
activity detector deliberately keeps measuring the raw microphone level, so
conditioning can never hide a real utterance from the gate.
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

# -- front-end defaults ------------------------------------------------------
AGC_TARGET_RMS = 1800.0
AGC_MIN_GAIN = 0.75
AGC_MAX_GAIN = 8.0
AGC_PEAK_LIMIT = 30000.0
NS_FFT = 512
NS_HOP = 256
NS_FLOOR = 0.14
NS_STRENGTH = 0.82
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


class EchoSuppressor:
    """Small reference-based echo suppressor used when a playback tap exists.

    TALCON normally mutes the microphone while it speaks, which is stronger and
    cheaper than any adaptive filter.  When a caller *does* provide the exact
    playback reference (``feed_reference``), this class estimates the linear
    echo gain and subtracts it.  Without a reference it applies a conservative
    residual attenuation while playback is active, so barge-in audio is still
    usable instead of being discarded completely.
    """

    def __init__(self, max_gain: float = 0.95):
        self.max_gain = max(0.0, min(1.0, float(max_gain)))
        self.reference_active = False
        self._gain = 0.0
        self._reference = b""

    def feed_reference(self, pcm: bytes) -> None:
        self._reference = bytes(pcm or b"")

    def reset(self) -> None:
        self._gain = 0.0
        self._reference = b""

    def process(self, samples):
        """Return ``samples`` with an estimated playback echo removed."""
        reference = self._reference
        if reference and len(reference) // 2 == len(samples):
            ref = array.array("h")
            ref.frombytes(reference[: len(reference) - (len(reference) % 2)])
            ref_values = [float(value) for value in ref]
            energy = sum(value * value for value in ref_values)
            if energy > 1.0:
                cross = sum(
                    float(mic) * ref_value
                    for mic, ref_value in zip(samples, ref_values)
                )
                estimate = max(0.0, min(self.max_gain, cross / energy))
                # A slower rise avoids cancelling the user's first syllable;
                # the decay is quicker so playback stops being removed at once.
                if estimate > self._gain:
                    self._gain = self._gain * 0.85 + estimate * 0.15
                else:
                    self._gain = self._gain * 0.60 + estimate * 0.40
                for index, ref_value in enumerate(ref_values):
                    samples[index] -= self._gain * ref_value
                return samples
        if self.reference_active:
            # No exact reference: halve residual echo while TALCON is talking.
            return samples * 0.5
        return samples


class AudioFrontEnd:
    """AGC + spectral noise suppression + optional echo suppression.

    The stage is streaming and length-preserving: one block in, one block out,
    so the recogniser and the phrase assembler keep their existing cadence.
    It is pure NumPy (already a TALCON dependency).  Every method degrades to a
    pass-through if NumPy is unavailable, which keeps the voice pipeline alive
    even in a broken environment.
    """

    def __init__(self, agc: bool = True, noise_suppression: bool = True,
                 aec: bool = False, target_rms: float = AGC_TARGET_RMS,
                 max_gain: float = AGC_MAX_GAIN, sample_rate: int = SAMPLE_RATE):
        self.agc = bool(agc)
        self.noise_suppression = bool(noise_suppression)
        self.aec = bool(aec)
        self.target_rms = max(300.0, min(6000.0, float(target_rms)))
        self.max_gain = max(1.0, min(16.0, float(max_gain)))
        self.sample_rate = int(sample_rate)
        self.echo = EchoSuppressor()

        self._np = None
        self._np_checked = False
        self._ns_input = None
        self._ns_output = None
        self._ns_pending = None
        self._ns_noise = None
        self._ns_gain = None
        self._ns_noise_level = 0.0
        self._window = None
        self._gain = 1.0
        self._floor = 30.0
        self._levels: deque[float] = deque(maxlen=8)

    # -- construction ------------------------------------------------------
    @classmethod
    def from_settings(cls, settings: dict | None) -> "AudioFrontEnd":
        values = dict(settings or {})
        return cls(
            agc=bool(values.get("audio_agc", True)),
            noise_suppression=bool(values.get("audio_noise_suppression", True)),
            aec=bool(values.get("audio_aec", False)),
            target_rms=float(values.get("audio_agc_target_rms", AGC_TARGET_RMS) or AGC_TARGET_RMS),
        )

    @property
    def enabled(self) -> bool:
        return bool(self.agc or self.noise_suppression or self.aec)

    def _numpy(self):
        if self._np_checked:
            return self._np
        self._np_checked = True
        try:
            import numpy as np

            self._np = np
        except Exception:
            self._np = None
        return self._np

    def reset(self) -> None:
        self._ns_input = None
        self._ns_output = None
        self._ns_pending = None
        self._ns_noise = None
        self._ns_gain = None
        self._ns_noise_level = 0.0
        self._window = None
        self._gain = 1.0
        self._floor = 30.0
        self._levels: deque[float] = deque(maxlen=8)
        self.echo.reset()

    def set_reference_active(self, active: bool) -> None:
        self.echo.reference_active = bool(active)

    def feed_reference(self, pcm: bytes) -> None:
        if self.aec:
            self.echo.feed_reference(pcm)

    # -- public processing -------------------------------------------------
    def process(self, pcm: bytes) -> bytes:
        if not pcm or not self.enabled:
            return pcm
        np = self._numpy()
        if np is None:
            return pcm
        usable = len(pcm) - (len(pcm) % 2)
        if usable <= 0:
            return pcm
        # Digital silence must stay silent.  Overlap-add ringing after a loud
        # block would otherwise look like a new voiced frame to engines that
        # use a simple non-zero test.
        if max(pcm[:usable]) == 0:
            # Drop overlap state from the previous utterance so its tail can
            # never leak into the first block after a real pause.
            self._ns_input = None
            self._ns_output = None
            self._ns_pending = None
            self._ns_gain = None
            self._ns_noise = None
            self._ns_noise_level = 0.0
            return pcm
        tail = pcm[usable:]
        samples = np.frombuffer(pcm[:usable], dtype="<i2").astype(np.float32)
        if self.aec:
            samples = self.echo.process(samples)
        if self.noise_suppression:
            samples = self._denoise(samples)
        if self.agc:
            samples = self._automatic_gain(samples)
        output = np.clip(samples, -32768, 32767).astype("<i2").tobytes()
        return output + tail

    # -- AGC ---------------------------------------------------------------
    def _automatic_gain(self, samples):
        np = self._np
        if not len(samples):
            return samples
        level = float(np.sqrt(np.mean(samples.astype(np.float64) ** 2)))
        self._levels.append(level)
        # Learn the room floor slowly.  A constant fan or hum must not be
        # amplified just because it is quiet.  A steady level over several
        # blocks is treated as noise even when it is fairly loud; speech
        # modulates too much to pass that test.
        stable = False
        if len(self._levels) >= 6:
            mean = sum(self._levels) / len(self._levels)
            variance = sum((value - mean) ** 2 for value in self._levels) / len(self._levels)
            stable = mean > 0 and math.sqrt(variance) / mean < 0.18
        if stable and level > self._floor * 1.2:
            self._floor = self._floor * 0.30 + level * 0.70
        elif level < self._floor * 1.5:
            self._floor = self._floor * 0.90 + level * 0.10
        elif level > self._floor * 3.0:
            self._floor = self._floor * 0.999 + level * 0.001
        self._floor = max(8.0, min(2000.0, self._floor))

        if level > max(35.0, self._floor * 1.6):
            desired = self.target_rms / max(1.0, level)
            desired = max(AGC_MIN_GAIN, min(self.max_gain, desired))
            if desired > self._gain:
                self._gain += (desired - self._gain) * 0.35
            else:
                self._gain += (desired - self._gain) * 0.08
        else:
            self._gain += (AGC_MIN_GAIN - self._gain) * 0.20
        self._gain = max(AGC_MIN_GAIN, min(self.max_gain, self._gain))

        peak = float(np.max(np.abs(samples))) * self._gain
        if peak > AGC_PEAK_LIMIT:
            self._gain = min(self._gain, AGC_PEAK_LIMIT / max(1.0, peak))
        return samples * self._gain

    # -- spectral noise suppression ---------------------------------------
    def _ensure_stft(self):
        np = self._np
        if self._ns_input is None:
            self._ns_input = np.zeros(0, dtype=np.float32)
            self._ns_output = np.zeros(NS_FFT - NS_HOP, dtype=np.float32)
            self._ns_pending = np.zeros(0, dtype=np.float32)
            self._ns_noise = None
            self._ns_gain = np.ones(NS_FFT // 2 + 1, dtype=np.float32)
            self._window = (
                0.5 - 0.5 * np.cos(2.0 * np.pi * np.arange(NS_FFT) / NS_FFT)
            ).astype(np.float32)

    def _denoise(self, samples):
        np = self._np
        self._ensure_stft()
        self._ns_input = np.concatenate([self._ns_input, samples.astype(np.float32)])
        while len(self._ns_input) >= NS_FFT:
            frame = self._ns_input[:NS_FFT]
            self._ns_input = self._ns_input[NS_HOP:]
            spectrum = np.fft.rfft(frame * self._window)
            power = (spectrum.real ** 2 + spectrum.imag ** 2).astype(np.float32)
            frame_level = float(np.sqrt(np.mean(frame ** 2)))
            # Spectral flatness separates broadband room noise (flat) from a
            # voiced tone (peaky).  Without it, one sustained vowel would
            # slowly be learned as noise and then suppressed.
            positive = power[1:] + 1e-9
            flatness = float(
                np.exp(np.mean(np.log(positive))) / (np.mean(positive) + 1e-9)
            )
            noise_like = flatness > 0.28
            if self._ns_noise is None:
                scale = 0.90 if noise_like else 0.25
                self._ns_noise = power * scale
                self._ns_noise_level = frame_level * scale
            else:
                learn = noise_like or frame_level < max(
                    25.0, self._ns_noise_level * 1.55
                )
                if learn:
                    self._ns_noise = (
                        self._ns_noise * 0.90 + power * 0.10
                    ).astype(np.float32)
                    self._ns_noise_level = (
                        self._ns_noise_level * 0.90 + frame_level * 0.10
                    )
                else:
                    self._ns_noise = (
                        self._ns_noise * 0.998 + power * 0.002
                    ).astype(np.float32)
            snr = power / (self._ns_noise + 1e-6)
            wiener = snr / (1.0 + snr)
            gain = (1.0 - NS_STRENGTH) + NS_STRENGTH * wiener
            gain = np.maximum(gain, NS_FLOOR).astype(np.float32)
            self._ns_gain = self._ns_gain * 0.55 + gain * 0.45
            cleaned = np.fft.irfft(spectrum * self._ns_gain, n=NS_FFT)
            # A periodic Hann window squared, overlapped at 50%, sums to 0.75.
            cleaned = cleaned.astype(np.float32) * self._window / 0.75
            # Add the previous overlap, emit one hop, keep the new overlap.
            cleaned[: len(self._ns_output)] += self._ns_output
            self._ns_pending = np.concatenate([
                self._ns_pending, cleaned[:NS_HOP],
            ])
            self._ns_output = cleaned[NS_HOP:]

        needed = len(samples)
        if len(self._ns_pending) < needed:
            self._ns_pending = np.concatenate([
                self._ns_pending,
                np.zeros(needed - len(self._ns_pending), dtype=np.float32),
            ])
        output = self._ns_pending[:needed].copy()
        self._ns_pending = self._ns_pending[needed:]
        return output


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
