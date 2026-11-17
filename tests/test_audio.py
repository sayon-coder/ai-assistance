"""Adaptive VAD: quiet speech, noisy rooms, clipping and calibration."""
from __future__ import annotations

from talcon.audio import AdaptiveVad, UtteranceVad, pcm_stats

SILENT = b"\x00\x00" * 4000
QUIET = (90).to_bytes(2, "little", signed=True) * 4000
NORMAL = (1200).to_bytes(2, "little", signed=True) * 4000
LOUD = (15000).to_bytes(2, "little", signed=True) * 4000
NOISE = (220).to_bytes(2, "little", signed=True) * 4000
CLIPPED = (32767).to_bytes(2, "little", signed=True) * 4000


def test_stats_report_rms_peak_and_clipping():
    stats = pcm_stats(CLIPPED)
    assert stats.rms > 32000
    assert stats.peak == 32767
    assert stats.clipping is True
    quiet = pcm_stats(QUIET)
    assert 80 < quiet.rms < 100
    assert quiet.clipping is False


def test_quiet_speech_is_detected_above_a_low_noise_floor():
    vad = UtteranceVad(AdaptiveVad(noise_floor=15))
    for _ in range(5):
        vad.update(SILENT)
    state = vad.update(QUIET)
    assert state.speech is True
    assert state.speech_seen is True
    assert state.threshold < 120


def test_noisy_room_calibration_blocks_background():
    adaptive = AdaptiveVad()
    adaptive.calibrate([220.0] * 20)
    vad = UtteranceVad(adaptive)
    state = vad.update(NOISE)
    assert state.noise_floor > 200
    assert state.speech is False


def test_normal_speech_opens_the_gate_and_releases_after_silence():
    vad = UtteranceVad(AdaptiveVad(noise_floor=20))
    vad.update(NORMAL)
    assert vad.active is True
    for _ in range(4):
        vad.update(SILENT)
    assert vad.active is False
    assert vad.speech_seen is True


def test_clipping_is_visible_without_stopping_recognition():
    vad = UtteranceVad(AdaptiveVad(noise_floor=20))
    state = vad.update(CLIPPED)
    assert state.clipping is True
    assert state.speech is True
