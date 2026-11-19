"""AGC, noise suppression and echo suppression in the shared front end."""
from __future__ import annotations

import math

import numpy as np

from talcon.audio import AudioFrontEnd, pcm_stats


def pcm(samples) -> bytes:
    return np.clip(samples, -32768, 32767).astype("<i2").tobytes()


def speech_like(amplitude: float = 90.0, seconds: float = 0.1,
                frequency: float = 220.0) -> np.ndarray:
    t = np.arange(int(16000 * seconds))
    envelope = 0.5 + 0.5 * np.sin(2 * math.pi * 4.0 * t / 16000)
    return (amplitude * np.sin(2 * math.pi * frequency * t / 16000) * envelope).astype(
        np.float32
    )


def test_disabled_frontend_is_bit_exact_passthrough():
    frame = pcm(speech_like())
    frontend = AudioFrontEnd(agc=False, noise_suppression=False, aec=False)
    assert frontend.process(frame) == frame
    assert frontend.enabled is False


def test_frontend_keeps_block_length_and_sample_format():
    frontend = AudioFrontEnd()
    for size in (1600, 333, 1598):
        frame = pcm(np.ones(size, dtype=np.float32) * 120)
        output = frontend.process(frame)
        assert len(output) == len(frame)
        # It must still be valid 16-bit PCM.
        assert len(output) % 2 == 0
        np.frombuffer(output, dtype="<i2")


def test_agc_raises_quiet_speech_and_does_not_clip_loud_audio():
    quiet = speech_like(90.0)
    frontend = AudioFrontEnd(agc=True, noise_suppression=False)
    output = pcm(quiet)
    for _ in range(12):
        output = frontend.process(pcm(quiet))
    assert pcm_stats(output).rms > pcm_stats(pcm(quiet)).rms * 1.5

    loud = (20000 * np.sin(2 * math.pi * 300 * np.arange(1600) / 16000)).astype("float32")
    loud_out = AudioFrontEnd(agc=True, noise_suppression=False).process(pcm(loud))
    assert pcm_stats(loud_out).peak <= 32767
    assert pcm_stats(loud_out).clipping is False


def test_agc_does_not_amplify_a_steady_room_noise_floor():
    rng = np.random.default_rng(4)
    noise = rng.normal(0, 300, 1600).astype("float32")
    frontend = AudioFrontEnd(agc=True, noise_suppression=False)
    output = pcm(noise)
    for _ in range(20):
        output = frontend.process(pcm(noise))
    assert pcm_stats(output).rms <= pcm_stats(pcm(noise)).rms * 1.05


def test_noise_suppression_reduces_stationary_broadband_noise():
    rng = np.random.default_rng(7)
    noise = rng.normal(0, 300, 1600).astype("float32")
    frontend = AudioFrontEnd(agc=False, noise_suppression=True)
    output = pcm(noise)
    for _ in range(20):
        output = frontend.process(pcm(noise))
    assert pcm_stats(output).rms < pcm_stats(pcm(noise)).rms * 0.85


def test_echo_suppressor_removes_a_scaled_playback_reference():
    t = np.arange(1600)
    reference = (1000 * np.sin(2 * math.pi * 200 * t / 16000)).astype("float32")
    microphone = reference * 0.5
    frontend = AudioFrontEnd(agc=False, noise_suppression=False, aec=True)
    output = pcm(microphone)
    for _ in range(12):
        frontend.feed_reference(pcm(reference))
        output = frontend.process(pcm(microphone))
    assert pcm_stats(output).rms < pcm_stats(pcm(microphone)).rms * 0.5


def test_settings_switch_each_stage_on():
    frontend = AudioFrontEnd.from_settings({
        "audio_agc": False,
        "audio_noise_suppression": True,
        "audio_aec": True,
        "audio_agc_target_rms": 1200,
    })
    assert frontend.agc is False
    assert frontend.noise_suppression is True
    assert frontend.aec is True
    assert frontend.target_rms == 1200.0
