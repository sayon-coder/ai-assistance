"""Adaptive VAD warm-up, pre-roll, noise bursts and clipping regressions."""
from __future__ import annotations

from talcon.audio import AdaptiveVad, UtteranceVad, pcm_stats


def block(level: int, count: int = 2000) -> bytes:
    return int(level).to_bytes(2, "little", signed=True) * count


SILENT = block(0)
QUIET_SPEECH = block(90)
NORMAL_SPEECH = block(1200)
NOISE = block(220)
LOUD = block(15000)
CLIPPED = block(32767)


def test_quiet_speech_is_detected_after_ambient_calibration():
    vad = UtteranceVad(AdaptiveVad(noise_floor=15))
    for _ in range(5):
        vad.update(SILENT)
    state = vad.update(QUIET_SPEECH)
    assert state.speech_seen
    assert state.level == 90


def test_noisy_background_does_not_open_the_gate():
    adaptive = AdaptiveVad()
    adaptive.calibrate([220.0] * 20)
    vad = UtteranceVad(adaptive)
    for _ in range(8):
        state = vad.update(NOISE)
    assert not state.speech
    assert not state.speech_seen
    assert state.noise_floor >= 200


def test_speech_on_first_warmup_block_is_detected():
    vad = UtteranceVad(AdaptiveVad())
    state = vad.update(NORMAL_SPEECH)
    assert state.speech_seen, "warm-up must not suppress the first word"


def test_loud_speech_during_warmup_does_not_become_noise_floor():
    adaptive = AdaptiveVad()
    vad = UtteranceVad(adaptive)
    state = vad.update(LOUD)
    assert state.speech_seen
    assert state.noise_floor <= 450
    assert state.threshold < 1000


def test_sudden_noise_burst_does_not_ratchet_up_the_floor():
    adaptive = AdaptiveVad(noise_floor=40)
    vad = UtteranceVad(adaptive)
    for _ in range(8):
        state = vad.update(block(4000))
    assert state.noise_floor < 250
    # Normal speech remains above threshold after the burst.
    state = vad.update(NORMAL_SPEECH)
    assert state.speech_seen


def test_clipping_detection_survives_warmup():
    stats = pcm_stats(CLIPPED)
    assert stats.clipping
    vad = UtteranceVad(AdaptiveVad())
    state = vad.update(CLIPPED)
    assert state.clipping
    assert state.speech_seen


def test_warmup_level_history_is_bounded():
    adaptive = AdaptiveVad()
    vad = UtteranceVad(adaptive)
    for level in range(10, 1000, 10):
        vad.update(block(level))
    assert len(adaptive._preroll) <= 24
