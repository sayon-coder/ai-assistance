from nova.commands import CommandRouter, normalize_phrase


class FakeSpeech:
    def __init__(self):
        self.spoken = []
        self.enabled = True

    def say(self, text):
        self.spoken.append(text)


class FakeModes:
    current_mode = "normal"

    def __init__(self):
        self.activated = []

    def activate(self, name):
        self.activated.append(name)
        return True


class FakeApp:
    def __init__(self):
        self.events = []
        self.muted = False
        self.wake = True

    def activate_mode(self, name):
        self.events.append(("mode", name))

    def set_muted(self, value):
        self.muted = value
        self.events.append(("mute", value))

    def set_wake_enabled(self, value):
        self.wake = value
        self.events.append(("wake", value))

    def show_settings(self):
        self.events.append(("settings",))

    def hide_to_tray(self):
        self.events.append(("hide",))

    def set_face_visible(self, value):
        self.events.append(("face", value))

    def exit_completely(self):
        self.events.append(("exit",))


def build():
    speech = FakeSpeech()
    modes = FakeModes()
    app = FakeApp()
    return CommandRouter(modes, speech, app), speech, app


def test_normalize_strips_wake_word_and_punctuation():
    assert normalize_phrase("NOVA, activate my study mode.") == "activate my study mode"
    assert normalize_phrase("Hey Nova — mute!") == "mute"


def test_study_and_entertainment():
    router, _speech, app = build()
    assert router.handle("NOVA, activate my study mode.") == "study"
    assert router.handle("get ready for my entertainment mode") == "entertainment"
    assert app.events == [("mode", "study"), ("mode", "entertainment")]


def test_mute_and_unmute_are_independent_commands():
    router, speech, app = build()
    router.handle("mute")
    assert app.muted is True
    router.handle("unmute")
    assert app.muted is False
    assert any("enabled" in line.lower() for line in speech.spoken)


def test_window_and_lifecycle_commands():
    router, _speech, app = build()
    router.handle("open settings")
    router.handle("hide nova")
    router.handle("show nova")
    router.handle("exit nova")
    assert ("settings",) in app.events
    assert ("hide",) in app.events
    assert ("face", True) in app.events
    assert ("exit",) in app.events


def test_stop_listening_does_not_exit():
    router, _speech, app = build()
    router.handle("stop listening")
    assert app.wake is False
    assert ("exit",) not in app.events


def test_unknown_command_is_reported_not_crashing():
    router, speech, app = build()
    assert router.handle("make me a sandwich") == "unknown"
    assert app.events == []
    assert speech.spoken and "didn't catch" in speech.spoken[-1]


def test_time_and_help():
    router, speech, _app = build()
    assert router.handle("what time is it") == "time"
    assert router.handle("what can you do") == "help"
    assert len(speech.spoken) == 2
