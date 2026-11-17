"""The deterministic intent layer: natural wording, no language model."""
from talcon.commands import normalize_phrase
from talcon.intent import IntentMatch, intent_labels, match_intent, parse_level


def test_running_questions_tolerate_filler_words():
    for phrase in (
        "how many programs do i have open right now",
        "how many apps are running",
        "number of applications currently running",
        "what apps are open",
        "what's running",
        "which programs are up",
        "are there any apps open",
        "show me the running applications",
    ):
        assert match_intent(phrase).action == "list_running", phrase


def test_is_running_keeps_the_spoken_application_name():
    result = match_intent("is google chrome running right now")
    assert result == IntentMatch("is_running", "google chrome", "is <app> running")
    result = match_intent("do i have spotify open")
    assert result.action == "is_running"
    assert result.slot == "spotify"


def test_volume_intents_parse_digits_and_words():
    assert match_intent("volume 30") == IntentMatch(
        "set_volume", "30", "volume <level>"
    )
    assert match_intent("set the volume to forty five percent").slot == "45"
    assert match_intent("turn the volume up").action == "nudge_volume_up"
    assert match_intent("quieter").action == "nudge_volume_down"
    assert match_intent("raise your voice").action == "nudge_volume_up"


def test_parse_level_accepts_safe_extremes_and_rejects_junk():
    assert parse_level("0") == 0
    assert parse_level("100") == 100
    assert parse_level("forty five") == 45
    assert parse_level("one hundred") == 100
    assert parse_level("max") == 100
    assert parse_level("lowest") == 0
    assert parse_level("loud") is None
    assert parse_level("") is None


def test_rate_and_small_talk_intents():
    assert match_intent("speak slower").action == "nudge_rate_down"
    assert match_intent("faster").action == "nudge_rate_up"
    assert match_intent("speak normally").action == "reset_rate"
    assert match_intent("how are you doing").action == "wellbeing"
    assert match_intent("good evening").action == "greeting"
    assert match_intent("what is your name").action == "introduce"


def test_repeat_silence_and_follow_up_intents():
    assert match_intent("say that again").action == "repeat"
    assert match_intent("what did you say").action == "repeat"
    assert match_intent("be quiet").action == "silence"
    assert match_intent("open it again").action == "reopen"


def test_ordinary_speech_is_not_turned_into_a_command():
    for phrase in (
        "open chrome",
        "study mode",
        "tell me a joke",
        "what is the capital of australia",
        "close spotify",
        "how many people live in australia",
    ):
        assert match_intent(phrase) is None, phrase


def test_matching_is_stable_after_normalisation():
    """The router normalises first, so matching normalised text must work."""
    raw = "TALCON, could you please tell me how many programs do I have open right now?"
    normalized = normalize_phrase(raw)
    assert match_intent(normalized).action == "list_running"


def test_every_intent_has_a_readable_label():
    labels = intent_labels()
    assert labels and all(isinstance(label, str) and label for label in labels)
