from types import SimpleNamespace

from PySide6.QtCore import QPoint, Qt
from PySide6.QtTest import QTest

from nova.face_render import FaceParams, render_face_image
from nova.face_widget import FaceWidget


def dark_pixels(image) -> int:
    total = 0
    for y in range(0, image.height(), 3):
        for x in range(0, image.width(), 3):
            colour = image.pixelColor(x, y)
            if colour.alpha() > 128 and colour.red() < 60 and colour.green() < 60 and colour.blue() < 60:
                total += 1
    return total


def test_face_is_circular_and_transparent_outside():
    image = render_face_image(132)
    assert image.pixelColor(0, 0).alpha() == 0
    assert image.pixelColor(131, 0).alpha() == 0
    assert image.pixelColor(66, 66).alpha() > 200
    # The very edge midpoint is inside the circle.
    assert image.pixelColor(66, 1).alpha() > 0


def test_face_has_two_dark_eyes_in_idle_state():
    image = render_face_image(160, FaceParams(openness=1.0))
    assert dark_pixels(image) > 40


def test_closed_eyes_use_far_less_dark_area():
    awake = render_face_image(160, FaceParams(openness=1.0))
    closed = render_face_image(160, FaceParams(openness=0.02))
    assert dark_pixels(closed) < dark_pixels(awake) * 0.6


def test_happy_expression_differs_from_error_expression():
    happy = render_face_image(160, FaceParams(openness=0.1, arc=1.0))
    error = render_face_image(160, FaceParams(openness=0.6, tilt=9.0))
    assert happy != error


def test_rim_is_blue_near_the_lower_right_edge():
    image = render_face_image(160)
    sample = image.pixelColor(140, 122)
    assert sample.alpha() > 200
    assert sample.blue() > sample.red()


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
        settings={"face_size": 66, "face_x": None, "face_y": None, "show_face": True},
        bridge=Bridge(),
        muted=False,
        set_face_position=lambda x, y, screen="": calls["positions"].append((x, y, screen)),
        set_muted=lambda value: None,
        activate_mode=lambda name: calls["modes"].append(name),
    )
    return runtime, calls


def test_face_widget_mask_is_circular_and_click_opens(qapp):
    runtime, _calls = make_runtime()
    opened = []
    face = FaceWidget(runtime, lambda: opened.append(True))
    try:
        assert face.size().width() == 66
        assert face.mask().contains(QPoint(33, 33)) is True
        assert face.mask().contains(QPoint(1, 1)) is False
        face.show()
        qapp.processEvents()
        QTest.mouseClick(face, Qt.MouseButton.LeftButton, pos=QPoint(33, 33))
        assert opened == [True]
    finally:
        face.close()


def test_expressions_change_tooltip_and_revert_to_idle(qapp):
    runtime, _calls = make_runtime()
    face = FaceWidget(runtime, lambda: None)
    try:
        face.set_state("listening")
        assert "Listening" in face.toolTip()
        face.set_state("completed")
        assert "Done" in face.toolTip()
        face.set_state("nonsense")
        assert face._state == "idle"
    finally:
        face.close()
