"""Procedural rendering of the NOVA face.

The supplied GIF is the visual reference: a perfectly circular pearl-white
sphere with a blue rim light and two black oval eyes. The face is painted at
runtime instead of replayed frame-by-frame so it can blink, look around and
hold expressions smoothly at ~30 FPS without shipping decorations (sparkles,
hearts, "Z" marks) that the brief explicitly excludes.
"""
from __future__ import annotations

from dataclasses import dataclass

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import (
    QBrush, QColor, QImage, QPainter, QPainterPath, QPen, QRadialGradient,
)

EYE_COLOR = QColor(9, 9, 12)
BASE_WHITE = QColor(255, 255, 255)


@dataclass
class FaceParams:
    openness: float = 1.0      # 0 = closed, 1 = normal, >1 = wide
    gaze_x: float = 0.0        # -1 … 1, fraction of an eye width
    gaze_y: float = 0.0
    tilt: float = 0.0          # degrees, mirrored per eye
    arc: float = 0.0           # 0 = oval eyes, 1 = happy closed arcs
    glow: float = 1.0          # blue rim strength

    def blended(self, other: "FaceParams", factor: float) -> "FaceParams":
        f = max(0.0, min(1.0, factor))
        return FaceParams(
            self.openness + (other.openness - self.openness) * f,
            self.gaze_x + (other.gaze_x - self.gaze_x) * f,
            self.gaze_y + (other.gaze_y - self.gaze_y) * f,
            self.tilt + (other.tilt - self.tilt) * f,
            self.arc + (other.arc - self.arc) * f,
            self.glow + (other.glow - self.glow) * f,
        )


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def paint_face(painter: QPainter, size: float, params: FaceParams | None = None) -> None:
    """Paint the face into a size×size square, centred and fully transparent outside."""
    params = params or FaceParams()
    size = float(size)
    if size <= 1:
        return
    painter.save()
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    painter.setPen(Qt.PenStyle.NoPen)

    inset = max(0.6, size * 0.012)
    circle = QRectF(inset, inset, size - inset * 2, size - inset * 2)
    cx, cy = circle.center().x(), circle.center().y()
    radius = circle.width() / 2

    clip = QPainterPath()
    clip.addEllipse(circle)
    painter.setClipPath(clip)

    # 1. Pearl body: white highlight up-left, cool grey toward the rim.
    body = QRadialGradient(QPointF(cx - size * 0.20, cy - size * 0.24), radius * 1.55)
    body.setColorAt(0.00, BASE_WHITE)
    body.setColorAt(0.34, QColor(253, 253, 255))
    body.setColorAt(0.60, QColor(241, 243, 250))
    body.setColorAt(0.78, QColor(223, 228, 241))
    body.setColorAt(0.90, QColor(199, 207, 228))
    body.setColorAt(1.00, QColor(160, 174, 206))
    painter.setBrush(QBrush(body))
    painter.drawEllipse(circle)

    # 2. Blue rim light, strongest along the lower-right edge.
    glow = _clamp(params.glow, 0.4, 1.6)
    rim = QRadialGradient(QPointF(cx + size * 0.055, cy + size * 0.075), radius * 1.10)
    rim.setColorAt(0.74, QColor(70, 110, 230, 0))
    rim.setColorAt(0.88, QColor(76, 116, 232, int(58 * glow)))
    rim.setColorAt(0.955, QColor(52, 96, 226, int(175 * glow)))
    rim.setColorAt(0.99, QColor(34, 72, 205, int(225 * glow)))
    rim.setColorAt(1.00, QColor(24, 56, 178, int(235 * glow)))
    painter.setBrush(QBrush(rim))
    painter.drawEllipse(circle)

    # 3. Specular sheen (top-left), kept soft and restrained.
    sheen = QRadialGradient(QPointF(cx - size * 0.24, cy - size * 0.30), radius * 0.62)
    sheen.setColorAt(0.0, QColor(255, 255, 255, 215))
    sheen.setColorAt(0.45, QColor(255, 255, 255, 95))
    sheen.setColorAt(1.0, QColor(255, 255, 255, 0))
    painter.setBrush(QBrush(sheen))
    painter.drawEllipse(circle)

    # 4. Subtle weight at the bottom so the sphere reads as three-dimensional.
    shade = QRadialGradient(QPointF(cx + size * 0.02, cy + size * 0.46), radius * 0.78)
    shade.setColorAt(0.45, QColor(28, 52, 130, 0))
    shade.setColorAt(0.82, QColor(28, 52, 130, 26))
    shade.setColorAt(1.0, QColor(22, 44, 118, 55))
    painter.setBrush(QBrush(shade))
    painter.drawEllipse(circle)

    # 5. Eyes.
    open_amount = _clamp(params.openness, 0.0, 1.45)
    eye_w = size * 0.150
    eye_h = max(size * 0.020, size * 0.228 * open_amount)
    separation = size * 0.168
    base_y = cy + size * 0.015 + params.gaze_y * size * 0.028
    offset_x = params.gaze_x * size * 0.028
    arc_alpha = _clamp(params.arc, 0.0, 1.0)

    painter.setBrush(QBrush(EYE_COLOR))
    for side in (-1, 1):
        ex = cx + side * separation + offset_x
        painter.save()
        painter.translate(ex, base_y)
        angle = params.tilt * (-side)
        if abs(angle) > 0.01:
            painter.rotate(angle)
        if arc_alpha < 0.999:
            painter.save()
            painter.setOpacity(1.0 - arc_alpha)
            capsule = QPainterPath()
            capsule.addRoundedRect(
                QRectF(-eye_w / 2, -eye_h / 2, eye_w, eye_h),
                eye_w / 2, eye_w / 2,
            )
            painter.drawPath(capsule)
            painter.restore()
        if arc_alpha > 0.001:
            painter.save()
            painter.setOpacity(arc_alpha)
            pen = QPen(EYE_COLOR)
            pen.setWidthF(max(1.4, size * 0.034))
            pen.setCapStyle(Qt.PenCapStyle.RoundCap)
            painter.setPen(pen)
            painter.setBrush(Qt.BrushStyle.NoBrush)
            path = QPainterPath()
            path.moveTo(-eye_w * 0.66, eye_h * 0.16)
            path.quadTo(0.0, -size * 0.105, eye_w * 0.66, eye_h * 0.16)
            painter.drawPath(path)
            painter.restore()
        painter.restore()

    painter.setClipping(False)
    painter.restore()


def render_face_image(size: int, params: FaceParams | None = None, ratio: float = 1.0) -> QImage:
    physical = max(1, int(round(size * max(1.0, ratio))))
    image = QImage(physical, physical, QImage.Format.Format_ARGB32_Premultiplied)
    image.fill(Qt.GlobalColor.transparent)
    painter = QPainter(image)
    paint_face(painter, physical, params)
    painter.end()
    if ratio != 1.0:
        image.setDevicePixelRatio(ratio)
    return image


def idle_params() -> FaceParams:
    return FaceParams(1.0, 0.0, 0.0, 0.0, 0.0, 1.0)
