from pathlib import Path

from nova import startup


def test_startup_folder_and_shortcut_are_per_user():
    folder = startup.startup_folder()
    assert "Start Menu" in str(folder)
    assert "Startup" in str(folder)
    assert startup.shortcut_path().name == "NOVA.lnk"
    assert startup.shortcut_path().parent == folder


def test_launch_command_targets_pythonw_with_startup_flag():
    target, arguments = startup.launch_command()
    assert Path(target).suffix.lower() == ".exe"
    assert "--startup" in arguments
    assert "main.py" in arguments
    assert "pythonw" in str(target).lower() or getattr(__import__("sys"), "frozen", False)


def test_enabled_reflects_shortcut_presence(monkeypatch, tmp_path):
    monkeypatch.setattr(startup, "startup_folder", lambda: tmp_path)
    monkeypatch.setattr(startup, "shortcut_path", lambda: tmp_path / "NOVA.lnk")
    assert startup.is_enabled() is False
    (tmp_path / "NOVA.lnk").write_text("stub")
    assert startup.is_enabled() is True
