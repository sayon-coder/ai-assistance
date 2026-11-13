from pathlib import Path

from nova.modes import ModeManager, normalize_url


class FakeSpeech:
    def __init__(self):
        self.spoken = []

    def say(self, text):
        self.spoken.append(text)


def manager(mode, tmp_path=None):
    speech = FakeSpeech()
    opened_urls = []
    launched = []
    modes = {"study": mode}
    manager = ModeManager(
        modes, speech,
        notify=lambda title, body: None,
        url_opener=lambda url: opened_urls.append(url),
        app_launcher=lambda path, args: launched.append((str(path), list(args))),
    )
    return manager, speech, opened_urls, launched


def test_normalize_url_accepts_bare_hosts_and_rejects_others():
    assert normalize_url("example.com") == "https://example.com"
    assert normalize_url(" http://127.0.0.1:4096/doc ") == "http://127.0.0.1:4096/doc"
    assert normalize_url("javascript:alert(1)") is None
    assert normalize_url("file:///C:/secret.txt") is None
    assert normalize_url("") is None
    assert normalize_url("two words.com") is None


def test_activation_opens_urls_and_speaks_greeting():
    m, speech, urls, launched = manager({
        "greeting": "Welcome back, sir.",
        "urls": ["google.com", "not a url"],
        "apps": [],
    })
    assert m.activate("study") is True
    assert urls == ["https://google.com"]
    assert speech.spoken == ["Welcome back, sir."]
    assert m.current_mode == "study"


def test_activation_launches_application(tmp_path):
    exe = tmp_path / "app.exe"
    exe.write_text("stub")
    m, _speech, _urls, launched = manager({
        "greeting": "Ready.",
        "urls": [],
        "apps": [{"path": str(exe), "args": ["--flag"]}],
    })
    assert m.activate("study") is True
    assert launched == [(str(exe), ["--flag"])]


def test_missing_application_means_no_false_success():
    m, speech, _urls, launched = manager({
        "greeting": "Ready.",
        "urls": [],
        "apps": [{"path": "C:/definitely/missing.exe", "args": []}],
    })
    assert m.activate("study") is False
    assert launched == []
    assert "could not open anything" in speech.spoken[-1].lower()


def test_unknown_mode_is_handled():
    m, speech, _urls, _launched = manager({"greeting": "x", "urls": [], "apps": []})
    assert m.activate("cinema") is False
    assert "could not find" in speech.spoken[-1].lower()
