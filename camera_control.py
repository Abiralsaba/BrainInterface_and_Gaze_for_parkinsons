"""Pure camera-intent logic for NeuroWheel.

This module has no OpenCV or MediaPipe dependency, so the safety state machine
can be tested deterministically without a camera.
"""

from dataclasses import dataclass
from enum import Enum
from math import isfinite
from statistics import median
from typing import List, Optional


class Mode(str, Enum):
    COMMUNICATION = "COMMUNICATION"
    MOBILITY = "MOBILITY"


class Gaze(str, Enum):
    LEFT = "LEFT"
    CENTER = "CENTER"
    RIGHT = "RIGHT"


class Intent(str, Enum):
    STOP = "STOP"
    CENTER = "CENTER"
    LEFT = "LEFT"
    RIGHT = "RIGHT"


class BlinkEvent(str, Enum):
    NONE = "NONE"
    SINGLE = "SINGLE"
    DOUBLE = "DOUBLE"
    LONG = "LONG"


@dataclass(frozen=True)
class Calibration:
    left_threshold: float
    right_threshold: float
    blink_threshold: float
    gaze_direction: float = 1.0

    def __post_init__(self) -> None:
        values = (
            self.left_threshold,
            self.right_threshold,
            self.blink_threshold,
            self.gaze_direction,
        )
        if not all(isfinite(value) for value in values):
            raise ValueError("Calibration values must be finite")
        if self.left_threshold >= self.right_threshold:
            raise ValueError("Left threshold must be lower than right threshold")
        if not 0.0 <= self.blink_threshold <= 1.0:
            raise ValueError("Blink threshold must be between 0 and 1")
        if self.gaze_direction not in (-1.0, 1.0):
            raise ValueError("Gaze direction must be -1 or 1")

    def classify_gaze(self, score: float) -> Gaze:
        score *= self.gaze_direction
        if score <= self.left_threshold:
            return Gaze.LEFT
        if score >= self.right_threshold:
            return Gaze.RIGHT
        return Gaze.CENTER

    def to_dict(self):
        return {
            "left_threshold": self.left_threshold,
            "right_threshold": self.right_threshold,
            "blink_threshold": self.blink_threshold,
            "gaze_direction": self.gaze_direction,
        }

    @classmethod
    def from_dict(cls, values):
        return cls(
            left_threshold=float(values["left_threshold"]),
            right_threshold=float(values["right_threshold"]),
            blink_threshold=float(values["blink_threshold"]),
            gaze_direction=float(values.get("gaze_direction", 1.0)),
        )


def build_calibration(
    center_scores: List[float],
    left_scores: List[float],
    right_scores: List[float],
    open_scores: List[float],
    closed_scores: List[float],
) -> Calibration:
    """Build validated, user-specific thresholds from guided samples."""

    groups = (center_scores, left_scores, right_scores, open_scores, closed_scores)
    if any(len(group) < 5 for group in groups):
        raise ValueError("Not enough valid face samples; repeat calibration")

    center = median(center_scores)
    left = median(left_scores)
    right = median(right_scores)
    eyes_open = median(open_scores)
    eyes_closed = median(closed_scores)

    minimum_gaze_span = 0.03
    minimum_blink_gap = 0.25

    if abs(right - left) < minimum_gaze_span:
        raise ValueError(
            "Left/right gaze too similar (left={:.3f}, right={:.3f})"
            .format(left, right)
        )
    if not eyes_closed >= eyes_open + minimum_blink_gap:
        raise ValueError("Closed eyes were not separated from open eyes")

    gaze_direction = 1.0 if left < right else -1.0
    center *= gaze_direction
    left *= gaze_direction
    right *= gaze_direction

    # A user may glance at the on-screen CENTER instruction instead of the
    # camera lens. Use CENTER when it lies between the two gaze extremes;
    # otherwise infer neutral gaze from the midpoint of LEFT and RIGHT.
    neutral = center if left < center < right else (left + right) / 2.0

    return Calibration(
        left_threshold=(left + neutral) / 2.0,
        right_threshold=(right + neutral) / 2.0,
        blink_threshold=(eyes_open + eyes_closed) / 2.0,
        gaze_direction=gaze_direction,
    )


@dataclass(frozen=True)
class VisionSample:
    timestamp: float
    face_present: bool
    gaze_score: float = 0.0
    blink_score: float = 0.0


@dataclass(frozen=True)
class ControllerOutput:
    mode: Mode
    intent: Intent
    gaze: Gaze
    text: str
    selected_key: str
    speak_text: Optional[str] = None
    reason: str = ""


@dataclass(frozen=True)
class ControlConfig:
    gaze_hold_seconds: float = 0.25
    gaze_repeat_seconds: float = 0.22
    steering_pulse_seconds: float = 0.30
    face_loss_seconds: float = 0.30
    blink_min_seconds: float = 0.06
    blink_max_seconds: float = 0.45
    double_blink_window_seconds: float = 0.65
    long_close_seconds: float = 0.80


class BlinkDetector:
    """Detect single, double and deliberate long two-eye closures."""

    def __init__(self, threshold: float, config: ControlConfig):
        self.threshold = threshold
        self.config = config
        self._closed_at: Optional[float] = None
        self._last_short_blink_at: Optional[float] = None
        self._long_emitted = False

    def reset(self) -> None:
        self._closed_at = None
        self._last_short_blink_at = None
        self._long_emitted = False

    def update(self, score: float, now: float) -> BlinkEvent:
        closed = score >= self.threshold

        if closed:
            if self._closed_at is None:
                self._closed_at = now
                self._long_emitted = False
            if (
                not self._long_emitted
                and now - self._closed_at >= self.config.long_close_seconds
            ):
                self._long_emitted = True
                self._last_short_blink_at = None
                return BlinkEvent.LONG
            return BlinkEvent.NONE

        if self._closed_at is None:
            if (
                self._last_short_blink_at is not None
                and now - self._last_short_blink_at
                > self.config.double_blink_window_seconds
            ):
                self._last_short_blink_at = None
            return BlinkEvent.NONE

        duration = now - self._closed_at
        was_long = self._long_emitted
        self._closed_at = None
        self._long_emitted = False

        if was_long:
            return BlinkEvent.NONE

        if not (
            self.config.blink_min_seconds
            <= duration
            <= self.config.blink_max_seconds
        ):
            return BlinkEvent.NONE

        if (
            self._last_short_blink_at is not None
            and now - self._last_short_blink_at
            <= self.config.double_blink_window_seconds
        ):
            self._last_short_blink_at = None
            return BlinkEvent.DOUBLE

        self._last_short_blink_at = now
        return BlinkEvent.SINGLE


class StableGazeGate:
    """Emits repeatedly while a stable left/right gaze is maintained."""

    def __init__(self, config: ControlConfig):
        self.config = config
        self._candidate = Gaze.CENTER
        self._candidate_since = 0.0
        self._last_emit_at: Optional[float] = None

    def reset(self, now: float = 0.0) -> None:
        self._candidate = Gaze.CENTER
        self._candidate_since = now
        self._last_emit_at = None

    def update(self, gaze: Gaze, now: float) -> Optional[Gaze]:
        if gaze == Gaze.CENTER:
            self._candidate = Gaze.CENTER
            self._candidate_since = now
            self._last_emit_at = None
            return None

        if gaze != self._candidate:
            self._candidate = gaze
            self._candidate_since = now
            self._last_emit_at = None
            return None

        if (
            self._last_emit_at is None
            and now - self._candidate_since >= self.config.gaze_hold_seconds
        ):
            self._last_emit_at = now
            return gaze
        if (
            self._last_emit_at is not None
            and now - self._last_emit_at >= self.config.gaze_repeat_seconds
        ):
            self._last_emit_at = now
            return gaze
        return None


class CameraController:
    """Exclusive communication/mobility controller with fail-safe stop logic."""

    KEYS: List[str] = list("ABCDEFGHIJKLMNOPQRSTUVWXYZ") + [
        "SPACE",
        "BACK",
        "CLEAR",
        "SPEAK",
        "HELP",
        "WATER",
        "MOBILITY",
    ]
    KEYBOARD_COLUMNS = 7

    def __init__(
        self,
        calibration: Calibration,
        config: Optional[ControlConfig] = None,
        invert_horizontal: bool = False,
    ):
        self.calibration = calibration
        self.config = config or ControlConfig()
        self.mode = Mode.COMMUNICATION
        self.text = ""
        self.selected_index = 0
        self.invert_horizontal = invert_horizontal
        self._intent = Intent.STOP
        self._intent_until = 0.0
        self._last_face_at: Optional[float] = None
        self._blink = BlinkDetector(calibration.blink_threshold, self.config)
        self._gaze = StableGazeGate(self.config)

    @property
    def selected_key(self) -> str:
        return self.KEYS[self.selected_index]

    @property
    def intent(self) -> Intent:
        return self._intent

    def enter_mobility(self, now: float) -> None:
        self.mode = Mode.MOBILITY
        self._intent = Intent.CENTER
        self._intent_until = now
        self._last_face_at = now
        self._blink.reset()
        self._gaze.reset(now)

    def stop_to_communication(self, now: float) -> None:
        self.mode = Mode.COMMUNICATION
        self._intent = Intent.STOP
        self._intent_until = now
        self._blink.reset()
        self._gaze.reset(now)

    def clear_text(self) -> None:
        self.text = ""

    def toggle_horizontal(self, now: float) -> None:
        self.invert_horizontal = not self.invert_horizontal
        self._gaze.reset(now)

    def _move_one_row_down(self) -> None:
        next_index = self.selected_index + self.KEYBOARD_COLUMNS
        if next_index >= len(self.KEYS):
            next_index = self.selected_index % self.KEYBOARD_COLUMNS
        self.selected_index = next_index

    def update(self, sample: VisionSample) -> ControllerOutput:
        now = sample.timestamp
        reason = ""
        speak_text: Optional[str] = None

        if sample.face_present:
            self._last_face_at = now
            gaze = self.calibration.classify_gaze(sample.gaze_score)
            if self.invert_horizontal:
                if gaze == Gaze.LEFT:
                    gaze = Gaze.RIGHT
                elif gaze == Gaze.RIGHT:
                    gaze = Gaze.LEFT
            blink_event = self._blink.update(sample.blink_score, now)
            eyes_closed = sample.blink_score >= self.calibration.blink_threshold
        else:
            gaze = Gaze.CENTER
            blink_event = BlinkEvent.NONE
            eyes_closed = False
            self._blink.reset()
            self._gaze.reset(now)
            if self.mode == Mode.MOBILITY:
                self._intent = Intent.CENTER

        if self.mode == Mode.MOBILITY:
            if (
                not sample.face_present
                and self._last_face_at is not None
                and now - self._last_face_at >= self.config.face_loss_seconds
            ):
                self.stop_to_communication(now)
                reason = "FACE LOST"
            elif blink_event in (BlinkEvent.DOUBLE, BlinkEvent.LONG):
                self.stop_to_communication(now)
                reason = "EYE STOP"
            elif sample.face_present:
                if eyes_closed:
                    self._gaze.reset(now)
                    self._intent = Intent.CENTER
                    gaze_event = None
                else:
                    gaze_event = self._gaze.update(gaze, now)
                if gaze_event == Gaze.LEFT:
                    self._intent = Intent.LEFT
                    self._intent_until = now + self.config.steering_pulse_seconds
                elif gaze_event == Gaze.RIGHT:
                    self._intent = Intent.RIGHT
                    self._intent_until = now + self.config.steering_pulse_seconds
                elif now >= self._intent_until:
                    self._intent = Intent.CENTER

        else:
            self._intent = Intent.STOP
            if sample.face_present:
                if eyes_closed:
                    self._gaze.reset(now)
                    gaze_event = None
                else:
                    gaze_event = self._gaze.update(gaze, now)
                if gaze_event == Gaze.LEFT:
                    self.selected_index = (self.selected_index - 1) % len(self.KEYS)
                elif gaze_event == Gaze.RIGHT:
                    self.selected_index = (self.selected_index + 1) % len(self.KEYS)

                if blink_event == BlinkEvent.SINGLE:
                    self._move_one_row_down()
                    reason = "ROW DOWN"
                elif blink_event == BlinkEvent.LONG:
                    speak_text = self._activate_selected(now)
                    reason = "SELECT"

        return ControllerOutput(
            mode=self.mode,
            intent=self._intent,
            gaze=gaze,
            text=self.text,
            selected_key=self.selected_key,
            speak_text=speak_text,
            reason=reason,
        )

    def _activate_selected(self, now: float) -> Optional[str]:
        key = self.selected_key
        if len(key) == 1:
            self.text += key
        elif key == "SPACE":
            if self.text and not self.text.endswith(" "):
                self.text += " "
        elif key == "BACK":
            self.text = self.text[:-1]
        elif key == "CLEAR":
            self.text = ""
        elif key == "SPEAK":
            return self.text.strip() or None
        elif key == "HELP":
            self.text = "I NEED HELP"
            return self.text
        elif key == "WATER":
            self.text = "I NEED WATER"
            return self.text
        elif key == "MOBILITY":
            self.enter_mobility(now)
        return None
