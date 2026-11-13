import sys
import time

import pytest

from nova.speech import SapiBackend, SpeechOutput


class FakeBackend:
    def __init__(self):
        self.said = []
        self.enabled_calls = []
        self.voices_list = ["Microsoft David", "Microsoft Zira"]
        self.stopped = 0

    def ensure_started(self, timeout=25.0):
        return True

    def say(self, text):
        self.said.append(text)
        return True

    def voices(self, timeout=15.0):
        return list(self.voices_list)

    def set_voice(self, name):
        self.enabled_calls.append(("voice", name))

    def set_rate(self, rate):
        self.enabled_calls.append(("rate", rate))

    def set_volume(self, volume):
        self.enabled_calls.append(("volume", volume))

    def stop_speaking(self):
        self.stopped += 1

    def stop(self):
        self.stopped += 1


def wait_for(predicate, timeout=3.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.02)
    return False


def test_speech_queue_and_speaking_callback():
    backend = FakeBackend()
    states = []
    output = SpeechOutput(enabled=True, backend=backend, on_speaking=states.append)
    try:
        output.say("Hello sir")
        assert wait_for(lambda: backend.said == ["Hello sir"])
        assert wait_for(lambda: states and states[-1] is False)
        assert states[0] is True
    finally:
        output.stop()


def test_muting_clears_queue_without_stopping_the_worker():
    backend = FakeBackend()
    output = SpeechOutput(enabled=True, backend=backend)
    try:
        output.set_enabled(False)
        output.say("should not be spoken")
        time.sleep(0.2)
        assert backend.said == []
        assert backend.stopped >= 1  # current speech was interrupted
        output.set_enabled(True)
        output.say("spoken again")
        assert wait_for(lambda: backend.said == ["spoken again"])
    finally:
        output.stop()


def test_voice_and_rate_are_passed_to_the_backend():
    backend = FakeBackend()
    output = SpeechOutput(enabled=True, backend=backend)
    try:
        output.set_voice("Microsoft Zira")
        output.set_rate(-3)
        output.set_volume(70)
        assert ("voice", "Microsoft Zira") in backend.enabled_calls
        assert ("rate", -3) in backend.enabled_calls
        assert ("volume", 70) in backend.enabled_calls
    finally:
        output.stop()


def test_say_and_wait_waits_for_real_playback_completion():
    class SlowBackend(FakeBackend):
        def say(self, text):
            self.said.append(text)
            time.sleep(0.15)
            return True

    backend = SlowBackend()
    output = SpeechOutput(enabled=True, backend=backend)
    try:
        assert output.say_and_wait("A complete sentence", timeout=3.0) is True
        assert backend.said == ["A complete sentence"]
        assert output.is_speaking is False
    finally:
        output.stop()


def test_wait_until_idle_returns_true_when_nothing_is_speaking():
    backend = FakeBackend()
    output = SpeechOutput(enabled=True, backend=backend)
    try:
        assert output.is_speaking is False
        assert output.wait_until_idle(0.5) is True
    finally:
        output.stop()


def test_wait_until_quiet_includes_queued_but_not_started_speech():
    class SlowBackend(FakeBackend):
        def say(self, text):
            self.said.append(text)
            time.sleep(0.15)
            return True

    backend = SlowBackend()
    output = SpeechOutput(enabled=True, backend=backend)
    try:
        output.say("Queued announcement")
        assert output.is_busy is True
        assert output.wait_until_quiet(3.0) is True
        assert backend.said == ["Queued announcement"]
        assert output.is_busy is False
    finally:
        output.stop()


@pytest.mark.skipif(sys.platform != "win32", reason="Windows SAPI only")
def test_real_windows_sapi_host_lists_installed_voices():
    backend = SapiBackend()
    try:
        voices = backend.voices(timeout=25)
        assert voices, "no SAPI voices were reported by Windows"
        assert all(isinstance(name, str) for name in voices)
    finally:
        backend.stop()
