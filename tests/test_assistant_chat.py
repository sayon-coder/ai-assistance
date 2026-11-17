"""The default voice-answer path uses one bounded Hermes chat response."""
from tests.test_assistant import make, wait_until


def test_default_voice_question_uses_chat_not_an_unbounded_agent_run():
    controller, _settings, speech, _states, client = make({"ai_use_agent_runs": False})
    assert controller.handle_phrase("why is the sky blue") is True
    assert wait_until(lambda: bool(speech.said))
    assert speech.said[-1] == "chat answer"
    assert client.chatted
    assert client.submitted == []
    controller.shutdown()
