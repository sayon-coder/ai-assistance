import json
import zipfile
from pathlib import Path

import pytest

import nova.config as config
from nova.model_setup import _safe_extract, model_is_ready


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


def test_legacy_paid_keys_removed(monkeypatch, tmp_path):
    use_temp_paths(monkeypatch, tmp_path)
    (tmp_path / "settings.json").write_text(json.dumps({
        "porcupine_access_key": "secret",
        "porcupine_keyword_path": "nova.ppn",
        "face_size": 999,
        "speech_rate": 99,
    }), encoding="utf-8")
    settings = config.load_settings()
    assert "porcupine_access_key" not in settings
    assert "porcupine_keyword_path" not in settings
    assert settings["face_size"] == 128
    assert settings["speech_rate"] == 10


def test_corrupt_settings_recovered(monkeypatch, tmp_path):
    use_temp_paths(monkeypatch, tmp_path)
    (tmp_path / "settings.json").write_text("{not json", encoding="utf-8")
    settings = config.load_settings()
    assert settings["assistant_name"] == "NOVA"
    assert list(tmp_path.glob("settings.json.invalid-*"))


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
    assert modes["study"]["apps"] == [{"path": "C:/x.exe", "args": ["--a"]}]
    assert modes["extra"]["apps"] == []


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
