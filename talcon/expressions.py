"""Eye-only expression engine for the TALCON face.

The face itself never changes: a pale-blue circular sphere with two dark oval
eyes.  Every emotion is expressed by moving, widening, narrowing or closing
those eyes.  This module owns the *decision* layer — which expression is
active, for how long, and at what priority — while :mod:`talcon.face_render`
owns the pixels.

Design goals:

* deterministic state changes with only subtle randomness in idle blinking and
  gaze drift, so tests can drive a fake clock and a seeded RNG;
* a tiny priority stack instead of competing timers;
* no background services: PC activity is read from Windows' own
  ``GetLastInputInfo`` counter, which reports only *how long* since the last
  input, never what was typed or clicked.
"""
from __future__ import annotations

import ctypes
import math
import os
import random
import time
from dataclasses import dataclass

from .face_render import FaceParams

# -- expression identifiers ------------------------------------------------
NEUTRAL = "neutral"
BLINKING = "blinking"
SLEEPY = "sleepy"
SLEEPING = "sleeping"
ATTENTIVE = "attentive"
LOOK_LEFT = "look_left"
LOOK_RIGHT = "look_right"
LOOK_UP = "look_up"
THINKING = "thinking"
HAPPY = "happy"
EXCITED = "excited"
SURPRISED = "surprised"
CURIOUS = "curious"
CONFUSED = "confused"
FOCUSED = "focused"
CONCERNED = "concerned"
SPEAKING = "speaking"

EXPRESSION_NAMES = (
    NEUTRAL, BLINKING, SLEEPY, SLEEPING, ATTENTIVE, LOOK_LEFT, LOOK_RIGHT,
    LOOK_UP, THINKING, HAPPY, EXCITED, SURPRISED, CURIOUS, CONFUSED, FOCUSED,
    CONCERNED, SPEAKING,
)

# -- priorities ------------------------------------------------------------
PRIORITY_IDLE = 20
PRIORITY_ACTIVITY = 40
PRIORITY_RESULT = 60
PRIORITY_ACTIVE = 80
PRIORITY_CRITICAL = 100

PENDING_GRACE = 8.0
ACTIVITY_RESUME_SECONDS = 1.5
ATTENTIVE_AFTER_ACTIVITY = 4.0


@dataclass(frozen=True)
class ExpressionSpec:
    """Target eye parameters for one expression.

    ``openness`` is the vertical eye opening (0 closed, 1 normal, >1 wide).
    ``width`` scales the horizontal eye size.  ``lid_top``/``lid_bottom``
    narrow the eye from the top or bottom for a squint without changing the
    overall oval identity.  ``asymmetry`` makes the two eyes differ subtly.
    """

    openness: float = 1.0
    gaze_x: float = 0.0
    gaze_y: float = 0.0
    tilt: float = 0.0
    arc: float = 0.0
    glow: float = 1.0
    width: float = 1.0
    lid_top: float = 0.0
    lid_bottom: float = 0.0
    asymmetry: float = 0.0
    blink: bool = True
    drift: bool = True
    duration: float | None = None
    priority: int = PRIORITY_RESULT


EXPRESSION_SPECS: dict[str, ExpressionSpec] = {
    NEUTRAL: ExpressionSpec(1.00, 0.0, 0.0, 0.0, 0.0, 1.00,
                            blink=True, drift=True, priority=PRIORITY_IDLE),
    BLINKING: ExpressionSpec(0.04, 0.0, 0.0, 0.0, 0.0, 1.00,
                             blink=False, drift=False, duration=0.22,
                             priority=PRIORITY_ACTIVITY),
    SLEEPY: ExpressionSpec(0.62, 0.0, 0.16, 0.0, 0.0, 0.92,
                           lid_top=0.38, blink=True, drift=False,
                           priority=PRIORITY_IDLE),
    SLEEPING: ExpressionSpec(0.03, 0.0, 0.10, 0.0, 0.0, 0.85,
                             blink=False, drift=False, priority=PRIORITY_IDLE),
    ATTENTIVE: ExpressionSpec(1.16, 0.0, -0.10, 0.0, 0.0, 1.12,
                              width=1.02, blink=True, drift=False,
                              priority=PRIORITY_ACTIVITY),
    LOOK_LEFT: ExpressionSpec(1.00, -0.62, 0.0, 0.0, 0.0, 1.00,
                              blink=True, drift=False, duration=1.6,
                              priority=PRIORITY_ACTIVITY),
    LOOK_RIGHT: ExpressionSpec(1.00, 0.62, 0.0, 0.0, 0.0, 1.00,
                               blink=True, drift=False, duration=1.6,
                               priority=PRIORITY_ACTIVITY),
    LOOK_UP: ExpressionSpec(1.00, 0.0, -0.58, 0.0, 0.0, 1.00,
                            blink=True, drift=False, duration=1.6,
                            priority=PRIORITY_ACTIVITY),
    THINKING: ExpressionSpec(0.76, 0.30, -0.52, -4.0, 0.0, 0.96,
                             lid_top=0.20, width=0.95, blink=True, drift=False,
                             duration=20.0, priority=PRIORITY_ACTIVITY),
    HAPPY: ExpressionSpec(0.12, 0.0, 0.0, 0.0, 0.95, 1.15,
                          blink=False, drift=False, duration=2.4,
                          priority=PRIORITY_RESULT),
    EXCITED: ExpressionSpec(1.32, 0.0, -0.04, 0.0, 0.0, 1.22,
                            width=1.08, blink=False, drift=False,
                            duration=1.6, priority=PRIORITY_RESULT),
    SURPRISED: ExpressionSpec(1.44, 0.0, 0.0, 0.0, 0.0, 1.10,
                              width=1.08, blink=False, drift=False,
                              duration=1.4, priority=PRIORITY_RESULT),
    CURIOUS: ExpressionSpec(1.08, 0.22, -0.16, -7.0, 0.0, 1.06,
                            width=1.04, asymmetry=0.45, blink=True,
                            drift=False, duration=2.2,
                            priority=PRIORITY_RESULT),
    CONFUSED: ExpressionSpec(0.78, 0.0, 0.12, 11.0, 0.0, 0.96,
                             asymmetry=0.60, blink=True, drift=False,
                             duration=2.2, priority=PRIORITY_RESULT),
    FOCUSED: ExpressionSpec(0.60, 0.0, 0.05, 0.0, 0.0, 0.94,
                            lid_top=0.24, lid_bottom=0.16, width=0.92,
                            blink=True, drift=False, duration=3.0,
                            priority=PRIORITY_RESULT),
    CONCERNED: ExpressionSpec(0.72, 0.0, 0.26, 8.0, 0.0, 0.94,
                              lid_top=0.16, blink=True, drift=False,
                              duration=2.8, priority=PRIORITY_RESULT),
    SPEAKING: ExpressionSpec(0.94, 0.08, 0.03, 0.0, 0.0, 1.08,
                             lid_top=0.05, blink=True, drift=False, duration=None,
                             priority=PRIORITY_ACTIVE),
}

# Assistant event → (expression, duration, priority)
EVENT_EXPRESSIONS: dict[str, tuple[str, float | None, int]] = {
    "idle": (NEUTRAL, None, PRIORITY_IDLE),
    "neutral": (NEUTRAL, None, PRIORITY_IDLE),
    "blinking": (BLINKING, 0.22, PRIORITY_ACTIVITY),
    "listening": (ATTENTIVE, None, PRIORITY_ACTIVITY),
    "heard": (ATTENTIVE, 6.0, PRIORITY_ACTIVITY),
    "command": (THINKING, 20.0, PRIORITY_ACTIVITY),
    "thinking": (THINKING, 20.0, PRIORITY_ACTIVITY),
    "working": (THINKING, None, PRIORITY_ACTIVITY),
    "speaking": (SPEAKING, None, PRIORITY_ACTIVE),
    "completed": (HAPPY, 2.4, PRIORITY_RESULT),
    "success": (EXCITED, 1.6, PRIORITY_RESULT),
    "excited": (EXCITED, 1.6, PRIORITY_RESULT),
    "surprised": (SURPRISED, 1.4, PRIORITY_RESULT),
    "curious": (CURIOUS, 2.2, PRIORITY_RESULT),
    "focused": (FOCUSED, 3.0, PRIORITY_RESULT),
    "confused": (CONFUSED, 2.2, PRIORITY_RESULT),
    "concerned": (CONCERNED, 2.8, PRIORITY_RESULT),
    "error": (CONCERNED, 2.8, PRIORITY_CRITICAL),
    "failure": (CONFUSED, 2.2, PRIORITY_RESULT),
    "look_left": (LOOK_LEFT, 1.6, PRIORITY_ACTIVITY),
    "look_right": (LOOK_RIGHT, 1.6, PRIORITY_ACTIVITY),
    "look_up": (LOOK_UP, 1.6, PRIORITY_ACTIVITY),
    "sleepy": (SLEEPY, None, PRIORITY_IDLE),
    "sleeping": (SLEEPING, None, PRIORITY_IDLE),
}


def normalize_expression(value: str) -> str:
    """Map an event or expression name to a known expression, or neutral."""
    text = str(value or "").strip().lower().replace("-", "_").replace(" ", "_")
    if text in EXPRESSION_SPECS:
        return text
    event = EVENT_EXPRESSIONS.get(text)
    if event:
        return event[0]
    return NEUTRAL


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


class ActivityMonitor:
    """Read only *how long* since the last keyboard/mouse input.

    On Windows this uses ``GetLastInputInfo`` — a cheap, documented counter.
    It never receives the input itself, so no key, click, password or typed
    text is captured.  On other platforms, or if the call fails, it falls back
    to assistant activity timestamps.
    """

    def __init__(self) -> None:
        self._last_assistant_activity = time.monotonic()
        self._get_last_input = None
        self._last_input_info = None
        if os.name == "nt":
            try:
                class LASTINPUTINFO(ctypes.Structure):
                    _fields_ = [("cbSize", ctypes.c_uint), ("dwTime", ctypes.c_uint)]

                user32 = ctypes.windll.user32
                user32.GetLastInputInfo.argtypes = [ctypes.POINTER(LASTINPUTINFO)]
                user32.GetLastInputInfo.restype = ctypes.c_bool
                kernel32 = ctypes.windll.kernel32
                kernel32.GetTickCount64.restype = ctypes.c_ulonglong
                self._last_input_info = LASTINPUTINFO
                self._get_last_input = user32.GetLastInputInfo
                self._get_tick_count = kernel32.GetTickCount64
            except Exception:
                self._get_last_input = None

    def mark_active(self, now: float | None = None) -> None:
        self._last_assistant_activity = time.monotonic() if now is None else now

    def idle_seconds(self, now: float | None = None) -> float:
        now = time.monotonic() if now is None else now
        if self._get_last_input is not None:
            try:
                info = self._last_input_info()
                info.cbSize = ctypes.sizeof(self._last_input_info)
                if self._get_last_input(ctypes.byref(info)):
                    tick = int(self._get_tick_count())
                    # GetLastInputInfo stores a 32-bit tick; GetTickCount64 may
                    # have wrapped it, so compare in 32-bit space.
                    idle_ms = (tick - int(info.dwTime)) & 0xFFFFFFFF
                    return max(0.0, idle_ms / 1000.0)
            except Exception:
                pass
        return max(0.0, now - self._last_assistant_activity)


class ExpressionController:
    """Priority-aware, deterministic expression state machine."""

    def __init__(
        self,
        sleepy_after: float = 90.0,
        sleep_after: float = 300.0,
        enabled: bool = True,
        rng: random.Random | None = None,
        clock=time.monotonic,
    ) -> None:
        self.sleepy_after = max(10.0, float(sleepy_after))
        self.sleep_after = max(self.sleepy_after + 10.0, float(sleep_after))
        self.enabled = bool(enabled)
        self._rng = rng or random.Random()
        self._clock = clock
        self._active_name = NEUTRAL
        self._active_priority = PRIORITY_IDLE
        self._active_until = 0.0
        self._pending: tuple[str, float, int, float] | None = None
        self._last_idle = 0.0
        self._current_name = NEUTRAL

        now = self._clock()
        self._next_blink = now + self._rng.uniform(2.4, 5.6)
        self._blink_start: float | None = None
        self._blink_duration = 0.18
        self._blink_queue = 0
        self._gaze_target = (0.0, 0.0)
        self._gaze_x = 0.0
        self._gaze_y = 0.0
        self._next_gaze = now + self._rng.uniform(2.0, 5.0)

    # -- configuration -----------------------------------------------------
    def configure(
        self,
        sleepy_after: float | None = None,
        sleep_after: float | None = None,
        enabled: bool | None = None,
    ) -> None:
        if sleepy_after is not None:
            self.sleepy_after = max(10.0, float(sleepy_after))
        if sleep_after is not None:
            self.sleep_after = max(self.sleepy_after + 10.0, float(sleep_after))
        if enabled is not None:
            self.enabled = bool(enabled)

    @property
    def current_name(self) -> str:
        return self._current_name

    @property
    def active_name(self) -> str:
        return self._active_name

    @property
    def active_priority(self) -> int:
        return self._active_priority

    def _now(self, now: float | None) -> float:
        return self._clock() if now is None else float(now)

    # -- state changes -----------------------------------------------------
    def set_expression(
        self,
        name: str,
        duration: float | None = None,
        priority: int | None = None,
        now: float | None = None,
    ) -> bool:
        """Request an expression. Returns True when it is shown immediately."""
        key = str(name or "").strip().lower()
        spec = EXPRESSION_SPECS.get(key)
        if spec is None:
            event = EVENT_EXPRESSIONS.get(key)
            if event is None:
                return False
            key, event_duration, event_priority = event
            spec = EXPRESSION_SPECS[key]
            duration = event_duration if duration is None else duration
            priority = event_priority if priority is None else priority
        now = self._now(now)
        priority = spec.priority if priority is None else int(priority)
        duration = spec.duration if duration is None else duration

        if key == NEUTRAL:
            return self.clear_transient(now)

        if self._active_until and now >= self._active_until:
            self._fall_back(now)

        if priority >= self._active_priority:
            self._activate(key, duration, priority, now)
            return True

        # A lower-priority result still matters: remember it briefly so it can
        # appear when the higher-priority state ends.
        if priority >= PRIORITY_RESULT and duration:
            expiry = now + float(duration) + PENDING_GRACE
            if self._pending is None or priority >= self._pending[2]:
                self._pending = (key, float(duration), priority, expiry)
        return False

    def set_event(self, event: str, now: float | None = None) -> bool:
        text = str(event or "").strip().lower()
        if text == "idle":
            return self.clear_transient(now)
        mapped = EVENT_EXPRESSIONS.get(text)
        if mapped is None:
            return self.set_expression(text, now=now)
        name, duration, priority = mapped
        return self.set_expression(name, duration=duration, priority=priority, now=now)

    def clear_transient(self, now: float | None = None) -> bool:
        """End an activity state without wiping a fresh result.

        A voice session or a finished sentence must not erase the reaction the
        user is meant to see.  Results and errors are allowed to finish their
        own short display time; speaking, listening and working are cleared
        immediately.
        """
        now = self._now(now)
        if (
            self._active_priority >= PRIORITY_RESULT
            and self._active_name not in (SPEAKING, BLINKING)
            and self._active_until
            and now < self._active_until
        ):
            self._pending = None
            return True
        if self._pending and now <= self._pending[3]:
            name, duration, priority, _expiry = self._pending
            self._pending = None
            self._activate(name, duration, priority, now)
            return True
        self._pending = None
        self._active_name = NEUTRAL
        self._active_priority = PRIORITY_IDLE
        self._active_until = 0.0
        return True

    def mark_active(self, now: float | None = None) -> None:
        """Tell the controller the user (or the assistant) just did something."""
        now = self._now(now)
        self._last_idle = 0.0
        self._activate(ATTENTIVE, ATTENTIVE_AFTER_ACTIVITY, PRIORITY_ACTIVITY, now)

    def _activate(
        self, name: str, duration: float | None, priority: int, now: float
    ) -> None:
        self._active_name = name
        self._active_priority = int(priority)
        self._active_until = now + float(duration) if duration else 0.0
        if name == BLINKING:
            self._next_blink = now

    def _fall_back(self, now: float) -> None:
        if self._pending and now <= self._pending[3]:
            name, duration, priority, _expiry = self._pending
            self._pending = None
            self._activate(name, duration, priority, now)
            return
        self._pending = None
        self._active_name = NEUTRAL
        self._active_priority = PRIORITY_IDLE
        self._active_until = 0.0

    # -- frame -------------------------------------------------------------
    def update(
        self,
        now: float | None = None,
        idle_seconds: float | None = None,
        dt: float = 0.033,
    ) -> FaceParams:
        now = self._now(now)
        dt = max(0.001, min(0.2, float(dt)))

        if not self.enabled:
            self._current_name = NEUTRAL
            return self._params(EXPRESSION_SPECS[NEUTRAL], now, dt, 1.0)

        if idle_seconds is not None:
            idle = max(0.0, float(idle_seconds))
            if self._last_idle >= self.sleepy_after and idle < ACTIVITY_RESUME_SECONDS:
                self._activate(
                    ATTENTIVE, ATTENTIVE_AFTER_ACTIVITY, PRIORITY_ACTIVITY, now
                )
            self._last_idle = idle
        else:
            idle = self._last_idle

        if idle >= self.sleep_after:
            base = SLEEPING
        elif idle >= self.sleepy_after:
            base = SLEEPY
        else:
            base = NEUTRAL

        if self._active_until and now >= self._active_until:
            self._fall_back(now)

        if self._active_priority <= PRIORITY_IDLE:
            effective = base
        else:
            effective = self._active_name
        if effective not in EXPRESSION_SPECS:
            effective = NEUTRAL
        self._current_name = effective
        spec = EXPRESSION_SPECS[effective]

        blink_factor = self._blink_factor(effective, spec, now)
        return self._params(spec, now, dt, blink_factor)

    def _blink_factor(self, name: str, spec: ExpressionSpec, now: float) -> float:
        if not spec.blink or name in (SLEEPING, HAPPY, EXCITED, SURPRISED):
            return 1.0
        if self._blink_start is None and now >= self._next_blink:
            self._blink_start = now
            base = 0.34 if name == SLEEPY else self._rng.uniform(0.16, 0.24)
            self._blink_duration = base
            self._blink_queue = 1 if self._rng.random() < 0.12 else 0
        if self._blink_start is None:
            return 1.0
        progress = (now - self._blink_start) / max(0.05, self._blink_duration)
        if progress >= 1.0:
            self._blink_start = None
            if self._blink_queue:
                self._blink_queue -= 1
                self._next_blink = now + 0.14
            else:
                low, high = (4.2, 8.6) if name == SLEEPY else (2.4, 6.8)
                self._next_blink = now + self._rng.uniform(low, high)
            return 1.0
        return 1.0 - math.sin(math.pi * progress)

    def _params(
        self, spec: ExpressionSpec, now: float, dt: float, blink_factor: float
    ) -> FaceParams:
        gaze_x = spec.gaze_x
        gaze_y = spec.gaze_y
        if spec.drift:
            if now >= self._next_gaze:
                self._gaze_target = (
                    self._rng.uniform(-0.30, 0.30),
                    self._rng.uniform(-0.12, 0.16),
                )
                self._next_gaze = now + self._rng.uniform(2.4, 6.4)
            k = 1.0 - math.exp(-dt * 3.0)
            self._gaze_x += (self._gaze_target[0] - self._gaze_x) * k
            self._gaze_y += (self._gaze_target[1] - self._gaze_y) * k
            gaze_x += self._gaze_x
            gaze_y += self._gaze_y
        elif self._current_name == THINKING:
            gaze_x += math.sin(now * 0.9) * 0.12
            gaze_y += math.cos(now * 0.7) * 0.05
        elif self._current_name == SPEAKING:
            gaze_x += math.sin(now * 1.6) * 0.05
            gaze_y += math.sin(now * 1.1) * 0.03
        elif self._current_name == EXCITED:
            gaze_x += math.sin(now * 9.0) * 0.05

        openness = spec.openness * blink_factor
        if self._current_name == SLEEPING:
            openness = 0.03 + math.sin(now * 0.6) * 0.006
            gaze_y += math.sin(now * 0.25) * 0.02
        elif self._current_name == EXCITED:
            openness += math.sin(now * 8.0) * 0.02

        return FaceParams(
            openness=_clamp(openness, 0.0, 1.45),
            gaze_x=_clamp(gaze_x, -1.0, 1.0),
            gaze_y=_clamp(gaze_y, -1.0, 1.0),
            tilt=spec.tilt,
            arc=spec.arc,
            glow=spec.glow,
            width=spec.width,
            lid_top=spec.lid_top,
            lid_bottom=spec.lid_bottom,
            asymmetry=spec.asymmetry,
        )

    def reset(self, now: float | None = None) -> None:
        """Return to neutral and clear every transient/timer."""
        now = self._now(now)
        self._active_name = NEUTRAL
        self._active_priority = PRIORITY_IDLE
        self._active_until = 0.0
        self._pending = None
        self._current_name = NEUTRAL
        self._last_idle = 0.0
        self._blink_start = None
        self._blink_queue = 0
        self._next_blink = now + self._rng.uniform(2.4, 5.6)
        self._gaze_target = (0.0, 0.0)
        self._gaze_x = 0.0
        self._gaze_y = 0.0
        self._next_gaze = now + self._rng.uniform(2.0, 5.0)
