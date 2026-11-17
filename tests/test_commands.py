from talcon.commands import CommandRouter, normalize_phrase


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
        self.commands_paused = False
        self.settings = {"speech_volume": 90, "speech_rate": 0}

    def save_settings(self, values):
        self.settings.update(values)
        self.events.append(("settings_saved", dict(values)))
        return self.settings

    def activate_mode(self, name):
        self.events.append(("mode", name))
        return True

    def set_muted(self, value, announce=False):
        self.muted = value
        self.events.append(("mute", value, announce))

    def set_commands_paused(self, value):
        self.commands_paused = bool(value)
        self.events.append(("commands_paused", self.commands_paused))

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
    assert normalize_phrase("TALCON, activate my study mode.") == "activate my study mode"
    assert normalize_phrase("TALCON, activate my study mode.") == "activate my study mode"
    assert normalize_phrase("Hey Talcon — mute!") == "mute"
    assert normalize_phrase("Hey Talcon — mute!") == "mute"
    assert normalize_phrase("tell can introduce yourself") == "introduce yourself"
    assert normalize_phrase("hey talcon to open the eyes") == "open the eyes"


def test_study_and_entertainment():
    router, _speech, app = build()
    assert router.handle("TALCON, activate my study mode.") == "study"
    assert router.handle("get ready for my entertainment mode") == "entertainment"
    assert app.events == [("mode", "study"), ("mode", "entertainment")]


def test_open_the_eyes_wakes_the_face_and_listening():
    router, speech, app = build()
    assert router.handle("hey talcon to open the eyes") == "open_eyes"
    assert ("wake", True) in app.events
    assert ("face", True) in app.events
    assert speech.spoken
    assert router.handle("close your eyes") == "close_eyes"
    assert ("face", False) in app.events


def test_mute_and_unmute_are_independent_commands():
    router, speech, app = build()
    router.handle("mute")
    assert app.muted is True
    router.handle("unmute")
    assert app.muted is False
    assert any("enabled" in line.lower() for line in speech.spoken)
    assert ("mute", True, True) in app.events
    assert ("mute", False, True) in app.events


def test_introduce_yourself_greets_and_describes_talcon():
    router, speech, app = build()
    assert router.handle("TALCON introduce yourself") == "introduce"
    assert app.events == []
    assert speech.spoken
    said = speech.spoken[-1].lower()
    assert "hello everyone" in said
    assert "talcon" in said
    assert "desktop assistant" in said
    assert "applications" in said


def test_window_and_lifecycle_commands():
    router, _speech, app = build()
    router.handle("open settings")
    router.handle("hide talcon")
    router.handle("show talcon")
    router.handle("exit talcon")
    assert ("settings",) in app.events
    assert ("face", False) in app.events
    assert ("face", True) in app.events
    assert ("hide",) not in app.events
    assert ("exit",) in app.events


def test_show_and_hide_yourself_never_open_the_control_center():
    router, _speech, app = build()
    assert router.handle("hide yourself") == "hide"
    assert router.handle("show yourself") == "show"
    assert ("face", False) in app.events
    assert ("face", True) in app.events
    assert ("settings",) not in app.events


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


def test_custom_mode_trigger_activates_the_mode():
    router, _speech, app = build()
    conflicts = router.register_modes({
        "cinema": {"title": "Cinema night", "triggers": ["cinema mode", "movie time"]},
    })
    assert conflicts == []
    assert router.handle("movie time") == "mode"
    assert ("mode", "cinema") in app.events
    # Built-in modes keep their existing aliases.
    assert router.handle("study mode") == "study"


def test_custom_mode_trigger_conflict_is_reported_and_ignored():
    router, _speech, app = build()
    conflicts = router.register_modes({
        "cinema": {"title": "Cinema", "triggers": ["study mode"]},
    })
    assert conflicts
    assert "Cinema" in conflicts[0]
    assert router.handle("study mode") == "study"
    assert ("mode", "cinema") not in app.events


def test_removing_a_custom_mode_removes_its_trigger():
    router, _speech, _app = build()
    router.register_modes({"cinema": {"triggers": ["movie time"]}})
    router.register_modes({})
    assert router.handle("movie time") == "unknown"


def test_media_commands_are_silent():
    router, speech, app = build()
    app.media_toggle = lambda: (True, "Sent the Windows play/pause media key.")
    assert router.handle("pause video") == "media_toggle"
    assert router.handle("resume video") == "media_toggle"
    assert speech.spoken == []


def test_time_and_help():
    router, speech, _app = build()
    assert router.handle("what time is it") == "time"
    assert router.handle("what can you do") == "help"
    assert len(speech.spoken) == 2


def test_ambiguous_answer_learns_a_local_pronunciation(tmp_path, monkeypatch):
    import json
    import talcon.commands as commands
    from talcon.apps import AppEntry

    monkeypatch.setattr(commands, "CONFIG_DIR", tmp_path)
    router, _speech, _app = build()
    entry = AppEntry(name="Google Chrome", kind="executable", path="C:/chrome.exe")
    router._learn_alias("chrome browser", entry)
    assert router.app_aliases["chrome browser"] == "Google Chrome"
    saved = json.loads((tmp_path / "app_aliases.json").read_text(encoding="utf-8"))
    assert saved == {"chrome browser": "Google Chrome"}


def test_commands_off_and_on_toggle_command_listening():
    router, speech, app = build()
    assert router.commands_paused() is False
    assert router.handle("don't take commands") == "commands_off"
    assert app.commands_paused is True
    assert "stop taking commands" in speech.spoken[-1].lower()
    assert router.handle("take command") == "commands_on"
    assert app.commands_paused is False
    assert "taking commands again" in speech.spoken[-1].lower()


def test_commands_off_aliases_are_exact():
    router, _speech, app = build()
    for phrase in (
        "dont take commands", "do not take commands", "stop taking commands",
        "pause commands", "no commands",
    ):
        assert router.handle(phrase) == "commands_off", phrase
    assert app.commands_paused is True


def test_commands_on_aliases_are_exact():
    router, _speech, app = build()
    for phrase in ("take command", "take commands", "resume commands", "start commands"):
        assert router.handle(phrase) == "commands_on", phrase
    assert app.commands_paused is False


def test_commands_paused_state_survives_unrelated_commands():
    router, _speech, app = build()
    router.handle("don't take commands")
    assert router.commands_paused() is True

def test_voice_volume_and_speed_intents_persist_settings():
    router, speech, app = build()
    assert router.handle("set the volume to forty five percent") == "volume_set"
    assert app.settings["speech_volume"] == 45
    assert ("settings_saved", {"speech_volume": 45}) in app.events
    assert "45 percent" in speech.spoken[-1]

    assert router.handle("turn it up") == "volume_up"
    assert app.settings["speech_volume"] == 60
    assert speech.spoken[-1] == "Louder, sir."

    assert router.handle("speak slower") == "rate_down"
    assert app.settings["speech_rate"] == -1
    assert speech.spoken[-1] == "A little slower, sir."

    assert router.handle("speak normally") == "rate_reset"
    assert app.settings["speech_rate"] == 0


def test_volume_and_rate_never_leave_their_safe_range():
    router, _speech, app = build()
    assert router.handle("volume 100") == "volume_set"
    assert router.handle("turn it up") == "volume_up"
    assert app.settings["speech_volume"] == 100
    assert router.handle("volume down") == "volume_down"
    for _ in range(10):
        router.handle("quieter")
    assert app.settings["speech_volume"] == 0
    for _ in range(15):
        router.handle("faster")
    assert app.settings["speech_rate"] == 10


def test_wellbeing_and_greeting_are_short_and_human():
    router, speech, _app = build()
    assert router.handle("how are you doing") == "wellbeing"
    assert "smoothly" in speech.spoken[-1]
    assert router.handle("good evening") == "greeting"
    assert "Good " in speech.spoken[-1]
    assert "How can I help?" in speech.spoken[-1]


def test_repeat_says_the_last_utterance_only_when_there_is_one():
    router, speech, _app = build()
    assert router.handle("say that again") == "nothing_to_repeat"
    assert "not said anything" in speech.spoken[-1]

    speech.last_spoken = "Study mode activated, sir."
    assert router.handle("repeat that") == "repeated"
    assert speech.spoken[-1] == "Study mode activated, sir."


def test_silence_interrupts_speech_without_muting():
    router, speech, app = build()
    calls = []
    speech.stop_speaking = lambda: calls.append("stopped")
    assert router.handle("be quiet") == "silenced"
    assert calls == ["stopped"]
    assert app.muted is False
    assert speech.spoken == []
