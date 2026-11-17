import json
import zipfile
from pathlib import Path

import pytest

import talcon.config as config
from talcon.model_setup import _safe_extract, model_is_ready


def use_temp_paths(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "CONFIG_DIR", tmp_path)
    monkeypatch.setattr(config, "SETTINGS_PATH", tmp_path / "settings.json")
    monkeypatch.setattr(config, "MODES_PATH", tmp_path / "modes.json")


def test_defaults_created_and_merged(monkeypatch, tmp_path):
    use_temp_paths(monkeypatch, tmp_path)
    settings = config.load_settings()
    assert settings["face_size"] == 66
    assert settings["wake_enabled"] is True
    assert settings["opencode_base_url"].startswith("http://127.0.0.1")
    assert (tmp_path / "settings.json").exists()
    assert settings["schema_version"] == 2


def test_menu_bar_face_defaults_are_safe(monkeypatch, tmp_path):
    use_temp_paths(monkeypatch, tmp_path)
    settings = config.load_settings()
    assert settings["menu_bar_face_enabled"] is True
    assert settings["menu_bar_face_size"] == 18
    assert settings["menu_bar_face_x_offset"] == 0
    assert settings["menu_bar_face_y_offset"] == 0
    assert settings["menu_bar_face_height"] == 0
    assert settings["menu_bar_face_animate"] is True
    assert settings["menu_bar_face_x_ratio"] == 0.0


def test_menu_bar_face_values_are_clamped(monkeypatch, tmp_path):
    use_temp_paths(monkeypatch, tmp_path)
    (tmp_path / "settings.json").write_text(json.dumps({
        "menu_bar_face_size": 999,
        "menu_bar_face_x_offset": -9999,
        "menu_bar_face_y_offset": 9999,
        "menu_bar_face_height": 9999,
        "menu_bar_face_x_ratio": 4.5,
    }), encoding="utf-8")
    settings = config.load_settings()
    assert settings["menu_bar_face_size"] == 32
    assert settings["menu_bar_face_x_offset"] == -600
    assert settings["menu_bar_face_y_offset"] == 16
    assert settings["menu_bar_face_height"] == 64
    assert settings["menu_bar_face_x_ratio"] == 1.0


def test_legacy_paid_keys_removed(monkeypatch, tmp_path):
    use_temp_paths(monkeypatch, tmp_path)
    (tmp_path / "settings.json").write_text(json.dumps({
        "porcupine_access_key": "secret",
        "porcupine_keyword_path": "talcon.ppn",
        "face_size": 999,
        "speech_rate": 99,
    }), encoding="utf-8")
    settings = config.load_settings()
    assert "porcupine_access_key" not in settings
    assert "porcupine_keyword_path" not in settings
    assert settings["face_size"] == 128
    assert settings["speech_rate"] == 10


def test_speech_backend_and_stable_voice_id_are_normalised(monkeypatch, tmp_path):
    use_temp_paths(monkeypatch, tmp_path)
    (tmp_path / "settings.json").write_text(json.dumps({
        "speech_backend": "natural",
        "speech_voice_id": 123,
        "speech_voice": "  George  ",
    }), encoding="utf-8")
    settings = config.load_settings()
    assert settings["speech_backend"] == "kokoro"
    assert settings["speech_voice_id"] == "123"
    assert settings["speech_voice"] == "George"

    (tmp_path / "settings.json").write_text(json.dumps({
        "speech_backend": "not-a-real-engine",
    }), encoding="utf-8")
    settings = config.load_settings()
    assert settings["speech_backend"] == "kokoro"
    assert settings["speech_voice_id"] == ""


def test_default_speech_settings_are_safe(monkeypatch, tmp_path):
    use_temp_paths(monkeypatch, tmp_path)
    settings = config.load_settings()
    assert settings["speech_backend"] == "kokoro"
    assert settings["speech_voice_id"] == ""
    assert settings["speech_voice_ids"] == {}
    assert settings["speech_rate"] == 0
    assert settings["speech_volume"] == 90


def test_only_the_kokoro_voice_id_is_kept(monkeypatch, tmp_path):
    use_temp_paths(monkeypatch, tmp_path)
    (tmp_path / "settings.json").write_text(json.dumps({
        "speech_voice_ids": {
            "kokoro": "  bm_george  ",
            "windows": "HKEY_LOCAL_MACHINE\\token",
            "sapi": "legacy-zira",
            "empty": "   ",
        },
    }), encoding="utf-8")
    settings = config.load_settings()
    assert settings["speech_voice_ids"] == {"kokoro": "bm_george"}

    (tmp_path / "settings.json").write_text(json.dumps({
        "speech_voice_ids": ["not", "a", "mapping"],
    }), encoding="utf-8")
    assert config.load_settings()["speech_voice_ids"] == {}


def test_default_face_expression_settings(monkeypatch, tmp_path):
    use_temp_paths(monkeypatch, tmp_path)
    settings = config.load_settings()
    assert settings["face_expressions"] is True
    assert settings["face_sleepy_after"] == 90
    assert settings["face_sleep_after"] == 300


def test_face_expression_settings_are_clamped(monkeypatch, tmp_path):
    use_temp_paths(monkeypatch, tmp_path)
    (tmp_path / "settings.json").write_text(json.dumps({
        "face_expressions": 0,
        "face_sleepy_after": 5,
        "face_sleep_after": 99999,
    }), encoding="utf-8")
    settings = config.load_settings()
    assert settings["face_expressions"] is False
    assert settings["face_sleepy_after"] == 20
    assert settings["face_sleep_after"] == 7200


def test_sleep_timer_always_follows_sleepy_timer(monkeypatch, tmp_path):
    use_temp_paths(monkeypatch, tmp_path)
    (tmp_path / "settings.json").write_text(json.dumps({
        "face_sleepy_after": 300,
        "face_sleep_after": 120,
    }), encoding="utf-8")
    settings = config.load_settings()
    assert settings["face_sleepy_after"] == 300
    assert settings["face_sleep_after"] == 360


def test_corrupt_settings_recovered(monkeypatch, tmp_path):
    use_temp_paths(monkeypatch, tmp_path)
    (tmp_path / "settings.json").write_text("{not json", encoding="utf-8")
    settings = config.load_settings()
    assert settings["assistant_name"] == "TALCON"
    assert list(tmp_path.glob("settings.json.invalid-*"))


def test_always_listen_and_phrase_timeout_are_normalised(monkeypatch, tmp_path):
    use_temp_paths(monkeypatch, tmp_path)
    (tmp_path / "settings.json").write_text(json.dumps({
        "always_listen": "yes",
        "phrase_timeout": 99,
    }), encoding="utf-8")
    settings = config.load_settings()
    assert settings["always_listen"] is True
    assert settings["phrase_timeout"] == 8.0

    (tmp_path / "settings.json").write_text(json.dumps({
        "phrase_timeout": "not a number",
    }), encoding="utf-8")
    settings = config.load_settings()
    assert settings["phrase_timeout"] == 4.0


def test_modes_are_normalised(monkeypatch, tmp_path):
    use_temp_paths(monkeypatch, tmp_path)
    (tmp_path / "modes.json").write_text(json.dumps({
        "study": {"greeting": "", "urls": ["https://a.example", 5],
                  "apps": [{"path": "C:/x.exe", "args": ["--a"]}, {"args": []}]},
        "extra": "not a mode",
    }), encoding="utf-8")
    modes = config.load_modes()
    assert modes["study"]["greeting"] == "Study mode is ready."
    assert modes["study"]["urls"] == ["https://a.example"]
    assert modes["study"]["apps"] == [{
        "kind": "executable", "name": "x", "path": "C:/x.exe",
        "args": ["--a"], "aumid": "",
    }]
    assert "extra" not in modes


def test_custom_modes_keep_title_and_spoken_triggers(monkeypatch, tmp_path):
    use_temp_paths(monkeypatch, tmp_path)
    (tmp_path / "modes.json").write_text(json.dumps({
        "study": {"greeting": "Study.", "urls": [], "apps": []},
        "cinema_night": {
            "title": "Cinema night",
            "greeting": "Lights down, sir.",
            "urls": ["https://example.com"],
            "apps": [],
            "triggers": ["cinema mode", "Cinema Mode", ""],
        },
    }), encoding="utf-8")
    modes = config.load_modes()
    assert modes["cinema_night"]["title"] == "Cinema night"
    assert modes["cinema_night"]["triggers"] == ["cinema mode"]
    assert config.mode_key("Cinema night") == "cinema_night"
    assert config.BUILTIN_MODES == ("study", "entertainment", "stock")


def test_model_ready_detection(tmp_path):
    assert not model_is_ready(tmp_path)
    (tmp_path / "am").mkdir()
    (tmp_path / "am" / "final.mdl").write_text("x")
    (tmp_path / "conf").mkdir()
    assert model_is_ready(tmp_path)


def test_zip_extraction_rejects_traversal(tmp_path):
    archive_path = tmp_path / "evil.zip"
    with zipfile.ZipFile(archive_path, "w") as archive:
        archive.writestr("../escape.txt", "bad")
    with zipfile.ZipFile(archive_path) as archive:
        with pytest.raises(ValueError):
            _safe_extract(archive, tmp_path / "out")
    assert not (tmp_path.parent / "escape.txt").exists()
