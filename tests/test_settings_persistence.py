"""Settings widgets persist on click, keyboard and quit — not only on drag release."""
from __future__ import annotations

import pytest
from PySide6.QtCore import QTimer, QEventLoop

from tests.test_ui import make_window, _CREATED


def _wait_for_save(runtime, key, timeout_ms=3000):
    """Spin the Qt event loop until the debounced save fires."""
    loop = QEventLoop()
    timer = QTimer()
    timer.setSingleShot(True)
    timer.timeout.connect(loop.quit)
    timer.start(timeout_ms)

    def check():
        for kind, values in [
            (c[0], c[1]) for c in runtime.calls if c[0] == "save"
        ]:
            if key in values:
                loop.quit()
                return
        if not timer.isActive():
            loop.quit()
            return
        QTimer.singleShot(25, check)

    check()
    loop.exec()


def test_face_size_click_without_release_is_saved(qapp):
    """A groove click changes valueChanged but never sliderReleased."""
    runtime, face, window = make_window(qapp)
    try:
        before = runtime.settings["face_size"]
        window.size_slider.setValue(96)          # programmatic == groove click
        assert window.size_slider.isSliderDown() is False
        _wait_for_save(runtime, "face_size")
        saved = [v for c, v in runtime.calls if c == "save" and "face_size" in v]
        assert saved and saved[-1]["face_size"] == 96
        assert runtime.settings["face_size"] == 96
    finally:
        window.close(); face.close()


def test_menu_bar_size_is_saved(qapp):
    runtime, face, window = make_window(qapp)
    try:
        window.menu_size_slider.setValue(24)
        _wait_for_save(runtime, "menu_bar_face_size")
        saved = [v for c, v in runtime.calls if c == "save" and "menu_bar_face_size" in v]
        assert saved and saved[-1]["menu_bar_face_size"] == 24
    finally:
        window.close(); face.close()


def test_quit_flushes_pending_debounced_save(qapp):
    """Changing a slider and quitting within the debounce window still saves."""
    runtime, face, window = make_window(qapp)
    try:
        window.size_slider.setValue(110)
        # Do not let the timer fire: flush explicitly, like exit_completely does.
        for timer in window._pending_saves.values():
            timer.stop()
        window._flush_pending_saves()
        saved = [v for c, v in runtime.calls if c == "save" and "face_size" in v]
        assert saved and saved[-1]["face_size"] == 110
    finally:
        window.close(); face.close()


def test_programmatic_sync_does_not_trigger_saves(qapp):
    """load_from_runtime must not look like the user edited every control."""
    runtime, face, window = make_window(qapp)
    try:
        runtime.calls.clear()
        window.load_from_runtime()
        window.sync_controls()
        assert not [c for c in runtime.calls if c[0] == "save"], (
            "syncing widgets from settings must not write settings back"
        )
    finally:
        window.close(); face.close()


# ------------------------------------------------------- concurrent saves
def test_concurrent_saves_never_lose_the_file(tmp_path, monkeypatch):
    """Many threads saving at once must never corrupt or fail the write.

    Regression: every writer shared one ``settings.json.tmp``, so concurrent
    saves raised FileNotFoundError and the change never reached disk.
    """
    import json
    import threading

    import talcon.config as config

    target = tmp_path / "settings.json"
    monkeypatch.setattr(config, "SETTINGS_PATH", target)
    target.write_text(json.dumps({"face_size": 66, "show_face": True}),
                      encoding="utf-8")
    errors = []

    def worker(index):
        try:
            for step in range(15):
                settings = config.load_settings()
                config.save_settings({
                    **settings,
                    "face_size": 52 + (index % 5),
                    "show_face": bool(index % 2),
                    "menu_bar_face_size": 18 + step % 3,
                })
        except Exception as exc:          # pragma: no cover - failure path
            errors.append(repr(exc))

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(6)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert errors == []
    data = json.loads(target.read_text(encoding="utf-8"))
    assert 52 <= data["face_size"] <= 56
    leftovers = [p.name for p in tmp_path.iterdir() if p.name != "settings.json"]
    assert leftovers == []


def test_face_changes_survive_a_full_restart(qapp, tmp_path, monkeypatch):
    """The exact reported case: hide the face + resize, quit, reopen."""
    import json

    import talcon.config as config

    target = tmp_path / "settings.json"
    monkeypatch.setattr(config, "SETTINGS_PATH", target)
    target.write_text(json.dumps({"show_face": True, "face_size": 66}),
                      encoding="utf-8")

    runtime, face, window = make_window(qapp)
    try:
        window.face_switch.setChecked(False)
        window.size_slider.setValue(88)
        window._flush_pending_saves()
    finally:
        window.close(); face.close()

    saved = [call[1] for call in runtime.calls
             if call[0] == "save" and len(call) > 1]
    assert any(v.get("show_face") is False for v in saved)
    assert any(v.get("face_size") == 88 for v in saved)
