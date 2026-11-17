"""Tests for the eye-only expression engine and its face integration."""
from __future__ import annotations

import math
import random
from types import SimpleNamespace

import pytest
from PySide6.QtCore import QPoint, Qt
from PySide6.QtTest import QTest

from talcon import expressions as ex
from talcon.face_render import FaceParams, render_face_image
from talcon.face_widget import FaceWidget


class Clock:
    """A fake monotonic clock so expression timing is deterministic."""

    def __init__(self, start: float = 0.0):
        self.t = float(start)

    def __call__(self) -> float:
        return self.t

    def advance(self, seconds: float) -> None:
        self.t += float(seconds)


REQUIRED_EXPRESSIONS = {
    "neutral", "blinking", "sleepy", "sleeping", "attentive", "look_left",
    "look_right", "look_up", "thinking", "happy", "excited", "surprised",
    "curious", "confused", "focused", "concerned",
}


def test_catalogue_covers_every_required_expression():
    assert REQUIRED_EXPRESSIONS.issubset(set(ex.EXPRESSION_NAMES))
    for name in ex.EXPRESSION_NAMES:
        assert name in ex.EXPRESSION_SPECS
    # Speaking is an internal state, not a seventeenth emotion.
    assert "speaking" in ex.EXPRESSION_SPECS


def test_event_names_normalise_to_expressions():
    assert ex.normalize_expression("completed") == "happy"
    assert ex.normalize_expression("error") == "concerned"
    assert ex.normalize_expression("look-left") == "look_left"
    assert ex.normalize_expression("LOOK UP") == "look_up"
    assert ex.normalize_expression("nonsense") == "neutral"
    assert ex.normalize_expression("") == "neutral"


def test_result_overrides_ongoing_activity():
    clock = Clock()
    ctrl = ex.ExpressionController(rng=random.Random(1), clock=clock)
    ctrl.set_event("listening", now=0.0)
    assert ctrl.active_name == "attentive"
    ctrl.set_event("completed", now=0.1)
    assert ctrl.active_name == "happy"
    assert ctrl.active_priority == ex.PRIORITY_RESULT


def test_error_overrides_everything():
    clock = Clock()
    ctrl = ex.ExpressionController(rng=random.Random(1), clock=clock)
    ctrl.set_expression("happy", now=0.0)
    ctrl.set_expression("error", now=0.1)
    assert ctrl.active_name == "concerned"
    assert ctrl.active_priority == ex.PRIORITY_CRITICAL


def test_lower_priority_result_waits_for_speaking():
    clock = Clock()
    ctrl = ex.ExpressionController(rng=random.Random(1), clock=clock)
    ctrl.set_expression("speaking", now=0.0)
    assert ctrl.active_name == "speaking"

    shown = ctrl.set_expression("happy", now=0.1)
    assert shown is False
    assert ctrl.active_name == "speaking"
    assert ctrl._pending is not None

    ctrl.clear_transient(now=0.2)
    assert ctrl.active_name == "happy"


def test_expression_expires_back_to_neutral():
    clock = Clock()
    ctrl = ex.ExpressionController(rng=random.Random(1), clock=clock)
    ctrl.set_expression("happy", now=0.0)
    params = ctrl.update(now=0.1, idle_seconds=0.0)
    assert ctrl.current_name == "happy"
    assert params.arc > 0.5

    ctrl.update(now=2.6, idle_seconds=0.0)
    assert ctrl.current_name == "neutral"
    assert ctrl.active_priority == ex.PRIORITY_IDLE


def test_inactivity_moves_from_neutral_to_sleepy_to_sleeping():
    clock = Clock()
    ctrl = ex.ExpressionController(
        sleepy_after=10, sleep_after=20, rng=random.Random(1), clock=clock
    )
    ctrl.update(now=0.0, idle_seconds=0.0)
    assert ctrl.current_name == "neutral"

    ctrl.update(now=1.0, idle_seconds=11.0)
    assert ctrl.current_name == "sleepy"

    ctrl.update(now=2.0, idle_seconds=21.0)
    assert ctrl.current_name == "sleeping"


def test_activity_after_sleep_wakes_the_face_attentively():
    clock = Clock()
    ctrl = ex.ExpressionController(
        sleepy_after=10, sleep_after=20, rng=random.Random(1), clock=clock
    )
    ctrl.update(now=1.0, idle_seconds=30.0)
    assert ctrl.current_name == "sleeping"

    ctrl.update(now=2.0, idle_seconds=0.0)
    assert ctrl.current_name == "attentive"


def test_blinking_is_occasional_and_never_interrupts_an_active_expression():
    clock = Clock()
    ctrl = ex.ExpressionController(rng=random.Random(7), clock=clock)

    # Force the scheduled idle blink and sample halfway through it.
    clock.t = ctrl._next_blink
    ctrl.update(now=clock.t, idle_seconds=0.0, dt=0.016)
    clock.advance(0.09)
    params = ctrl.update(now=clock.t, idle_seconds=0.0, dt=0.016)
    assert params.openness < 0.5

    # A happy expression disables blinking entirely.
    ctrl.set_expression("happy", now=clock.t)
    clock.advance(1.0)
    params = ctrl.update(now=clock.t, idle_seconds=0.0, dt=0.016)
    assert ctrl.current_name == "happy"
    assert params.openness == pytest.approx(0.12, abs=0.01)


def test_idle_does_not_wipe_a_fresh_result():
    clock = Clock()
    ctrl = ex.ExpressionController(rng=random.Random(1), clock=clock)
    ctrl.set_expression("happy", now=0.0)
    ctrl.clear_transient(now=0.1)
    assert ctrl.active_name == "happy"

    ctrl.update(now=2.5, idle_seconds=0.0)
    assert ctrl.current_name == "neutral"


def test_idle_still_clears_thinking_and_speaking():
    clock = Clock()
    ctrl = ex.ExpressionController(rng=random.Random(1), clock=clock)
    ctrl.set_expression("thinking", now=0.0)
    ctrl.clear_transient(now=0.1)
    assert ctrl.active_name == "neutral"

    ctrl.set_expression("speaking", now=0.2)
    ctrl.set_expression("happy", now=0.3)
    ctrl.clear_transient(now=0.4)
    assert ctrl.active_name == "happy"


def test_key_expressions_are_visibly_different_from_neutral():
    neutral = ex.EXPRESSION_SPECS["neutral"]
    attentive = ex.EXPRESSION_SPECS["attentive"]
    thinking = ex.EXPRESSION_SPECS["thinking"]
    assert attentive.openness - neutral.openness >= 0.10
    assert thinking.openness <= 0.80
    assert abs(thinking.gaze_y) >= 0.40


def test_reset_clears_active_pending_and_timers():
    clock = Clock()
    ctrl = ex.ExpressionController(rng=random.Random(3), clock=clock)
    ctrl.set_expression("speaking", now=0.0)
    ctrl.set_expression("happy", now=0.1)
    assert ctrl._pending is not None

    ctrl.reset(now=1.0)
    assert ctrl.current_name == "neutral"
    assert ctrl.active_name == "neutral"
    assert ctrl.active_priority == ex.PRIORITY_IDLE
    assert ctrl._pending is None

    params = ctrl.update(now=1.1, idle_seconds=0.0)
    assert ctrl.current_name == "neutral"
    assert params.openness == pytest.approx(1.0, abs=0.05)


def test_disabled_expressions_stay_neutral():
    clock = Clock()
    ctrl = ex.ExpressionController(enabled=False, rng=random.Random(1), clock=clock)
    ctrl.set_expression("excited", now=0.0)
    params = ctrl.update(now=0.1, idle_seconds=0.0)
    assert ctrl.current_name == "neutral"
    assert params.openness == pytest.approx(1.0)


def test_activity_monitor_reports_a_non_negative_idle_time():
    monitor = ex.ActivityMonitor()
    monitor.mark_active()
    idle = monitor.idle_seconds()
    assert isinstance(idle, float)
    assert idle >= 0.0


def test_blended_params_include_the_new_eye_controls():
    base = FaceParams()
    target = FaceParams(width=1.3, lid_top=0.4, lid_bottom=0.2, asymmetry=0.5)
    mixed = base.blended(target, 0.5)
    assert mixed.width == pytest.approx(1.15)
    assert mixed.lid_top == pytest.approx(0.2)
    assert mixed.lid_bottom == pytest.approx(0.1)
    assert mixed.asymmetry == pytest.approx(0.25)


def test_new_eye_controls_change_the_rendered_pixels():
    default = render_face_image(160, FaceParams())
    wide = render_face_image(160, FaceParams(width=1.3))
    lidded = render_face_image(160, FaceParams(lid_top=0.5, lid_bottom=0.2))
    assert wide != default
    assert lidded != default


def make_runtime():
    calls = {"positions": [], "modes": []}

    class Bridge:
        class _Signal:
            def emit(self, *args):
                pass

        face_visibility_requested = _Signal()
        hide_to_tray = _Signal()
        exit_requested = _Signal()

    runtime = SimpleNamespace(
        settings={
            "face_size": 66,
            "face_x": None,
            "face_y": None,
            "show_face": True,
            "face_expressions": True,
            "face_sleepy_after": 90,
            "face_sleep_after": 300,
        },
        bridge=Bridge(),
        muted=False,
        set_face_position=lambda x, y, screen="": calls["positions"].append((x, y, screen)),
        set_muted=lambda value: None,
        activate_mode=lambda name: calls["modes"].append(name),
    )
    return runtime, calls


@pytest.mark.parametrize(
    ("event", "label"),
    [
        ("listening", "Listening"),
        ("working", "Working"),
        ("completed", "Done"),
        ("excited", "Excited"),
        ("surprised", "Surprised"),
        ("curious", "Curious"),
        ("confused", "Needs attention"),
        ("focused", "Focused"),
        ("concerned", "Needs attention"),
        ("sleepy", "Sleepy"),
        ("sleeping", "Sleeping"),
        ("look_left", "Looking left"),
        ("look_right", "Looking right"),
        ("look_up", "Looking up"),
    ],
)
def test_face_widget_tooltips_describe_new_expressions(qapp, event, label):
    runtime, _calls = make_runtime()
    face = FaceWidget(runtime, lambda: None)
    try:
        face.set_state("idle")
        face.set_state(event)
        assert label in face.toolTip()
    finally:
        face.close()


def test_face_widget_pauses_when_hidden_and_resumes(qapp):
    runtime, _calls = make_runtime()
    face = FaceWidget(runtime, lambda: None)
    try:
        face.show()
        qapp.processEvents()
        assert face._timer.isActive()

        face.hide()
        qapp.processEvents()
        assert not face._timer.isActive()

        face.show()
        qapp.processEvents()
        assert face._timer.isActive()
    finally:
        face.close()


def test_face_widget_pauses_when_minimized_and_resumes(qapp):
    runtime, _calls = make_runtime()
    face = FaceWidget(runtime, lambda: None)
    try:
        face.show()
        qapp.processEvents()
        assert face._timer.isActive()

        face.showMinimized()
        qapp.processEvents()
        assert face.isMinimized()
        assert not face._timer.isActive()

        face.showNormal()
        qapp.processEvents()
        assert face._timer.isActive()
    finally:
        face.close()


def test_face_widget_click_marks_activity_and_opens(qapp):
    runtime, _calls = make_runtime()
    opened = []
    face = FaceWidget(runtime, lambda: opened.append(True))
    try:
        face.show()
        qapp.processEvents()
        QTest.mouseClick(face, Qt.MouseButton.LeftButton, pos=QPoint(33, 33))
        assert opened == [True]
    finally:
        face.close()


def test_face_widget_unknown_state_is_idle(qapp):
    runtime, _calls = make_runtime()
    face = FaceWidget(runtime, lambda: None)
    try:
        face.set_state("nonsense")
        assert face._state == "idle"
        assert "Idle" in face.toolTip()
    finally:
        face.close()
