"""Eye-tracking car controller for the ESP32-S3 Rover.

Control scheme:
  - Double blink  →  toggle FORWARD on / off
  - Look LEFT     →  steer left (while moving)
  - Look RIGHT    →  steer right (while moving)
  - Look CENTER   →  go straight (while moving)
  - Long blink    →  emergency stop (always)
  - Face lost     →  emergency stop (after 0.5s)

Precision features:
  - Median filter over recent samples to reject outliers
  - Hold timer: a direction must be maintained for N consecutive
    frames before it activates, preventing accidental flicks
  - Wide dead-zone around CENTER so only deliberate eye movements
    register
"""

import argparse
import sys
import time
from collections import deque
from pathlib import Path
from statistics import median
from typing import Optional

from car_transport import CarCommandSender

from camera_control import (
    Calibration,
    BlinkDetector,
    BlinkEvent,
    ControlConfig,
    VisionSample,
)

# ──────────────────────────────────────────────────────────
#  Paths
# ──────────────────────────────────────────────────────────

ROOT = Path(__file__).resolve().parent
DEFAULT_MODEL = ROOT / "models" / "face_landmarker.task"
DEFAULT_CALIBRATION = ROOT / "calibration.json"

# ──────────────────────────────────────────────────────────
#  Horizontal gaze helper (same as camera_app)
# ──────────────────────────────────────────────────────────

def iris_gaze_horizontal(landmarks) -> float:
    """Horizontal iris position, normalized around 0."""
    positions = []
    for iris_idx, corner_a, corner_b in ((468, 33, 133), (473, 362, 263)):
        lx, rx = sorted((landmarks[corner_a].x, landmarks[corner_b].x))
        w = rx - lx
        if w <= 1e-6:
            raise ValueError("Eye corners too close")
        positions.append((landmarks[iris_idx].x - lx) / w)
    return sum(positions) / len(positions) - 0.5


# ──────────────────────────────────────────────────────────
#  Stable Zone Classifier — smoothing + hold timer
# ──────────────────────────────────────────────────────────

class StableZone:
    """Classify a continuous score into LEFT / CENTER / RIGHT
    with median smoothing and a hold timer so random glances
    don't trigger steering."""

    def __init__(
        self,
        left_threshold: float,
        right_threshold: float,
        hold_frames: int = 5,
        buffer_size: int = 7,
    ):
        self.left_threshold = left_threshold
        self.right_threshold = right_threshold
        self.hold_frames = hold_frames

        self._buffer: deque = deque(maxlen=buffer_size)
        self._candidate: str = "CENTER"
        self._candidate_count: int = 0
        self._committed: str = "CENTER"

    def reset(self) -> None:
        self._buffer.clear()
        self._candidate = "CENTER"
        self._candidate_count = 0
        self._committed = "CENTER"

    def update(self, raw_score: float) -> str:
        self._buffer.append(raw_score)
        score = median(self._buffer)

        if score <= self.left_threshold:
            zone = "LEFT"
        elif score >= self.right_threshold:
            zone = "RIGHT"
        else:
            zone = "CENTER"

        if zone == self._candidate:
            self._candidate_count += 1
        else:
            self._candidate = zone
            self._candidate_count = 1

        if self._candidate_count >= self.hold_frames:
            self._committed = self._candidate

        return self._committed


# ──────────────────────────────────────────────────────────
#  HTTP command sender
# ──────────────────────────────────────────────────────────

# ──────────────────────────────────────────────────────────
#  Eye Car Controller — toggle-forward scheme
# ──────────────────────────────────────────────────────────

class EyeCarController:
    """
    Double blink = toggle forward on/off.
    Gaze left/right = steer while moving.
    Long blink = emergency stop.
    """

    def __init__(
        self,
        calibration: Calibration,
        car_url: str = "http://192.168.4.1",
        hold_frames: int = 3,
        median_window: int = 5,
        command_sender=None,
    ):
        self.calibration = calibration
        self.car_url = car_url
        self.command_sender = command_sender or CarCommandSender(car_url)
        self.config = ControlConfig()

        self.steering_zone = StableZone(
            left_threshold=calibration.left_threshold,
            right_threshold=calibration.right_threshold,
            hold_frames=hold_frames,
            buffer_size=median_window,
        )

        self.blink_detector = BlinkDetector(
            calibration.blink_threshold, self.config
        )

        # ── State ──
        self.driving = False       # True = going forward
        self.steering = "CENTER"   # LEFT / CENTER / RIGHT
        self.face_present = False
        self.face_lost_at: Optional[float] = None
        self.face_loss_timeout = 0.30

        # For the HUD
        self.raw_h = 0.0
        self.blink_score = 0.0
        self.status_message = "WAITING FOR FACE"

    @property
    def throttle(self) -> str:
        return "FORWARD" if self.driving else "STOP"

    def full_stop(self, reason: str = "STOPPED") -> None:
        """Stop everything."""
        self.driving = False
        self.steering = "CENTER"
        self.steering_zone.reset()
        self.command_sender.send("STOP", "CENTER")
        self.status_message = reason

    def update(self, landmarks, blink_score: float, now: float) -> None:
        """Process one frame."""

        # ── Face lost ──
        if landmarks is None:
            self.face_present = False
            if self.face_lost_at is None:
                self.face_lost_at = now
            elif now - self.face_lost_at >= self.face_loss_timeout:
                if self.driving:
                    self.full_stop("FACE LOST — STOPPED")
            self.blink_detector.reset()
            return

        self.face_present = True
        self.face_lost_at = None
        self.blink_score = blink_score

        # ── Blink detection ──
        blink_event = self.blink_detector.update(blink_score, now)
        eyes_closed = blink_score >= self.calibration.blink_threshold

        # Long blink → always emergency stop
        if blink_event == BlinkEvent.LONG:
            self.full_stop("LONG BLINK — STOPPED")
            return

        # Double blink → toggle forward
        if blink_event == BlinkEvent.DOUBLE:
            if self.driving:
                self.full_stop("DOUBLE BLINK — STOPPED")
            else:
                self.driving = True
                self.steering = "CENTER"
                self.steering_zone.reset()
                self.command_sender.send("FORWARD", "CENTER")
                self.status_message = "▶ DRIVING FORWARD"
            return

        # While eyes are closed, don't update gaze (avoid noise)
        if eyes_closed:
            return

        # ── NOT DRIVING → ignore gaze, always CENTER ──
        if not self.driving:
            self.steering = "CENTER"
            self.status_message = "■ STOPPED — double blink to start"
            return

        # ── DRIVING → Gaze controls steering ──
        try:
            h_raw = iris_gaze_horizontal(landmarks)
        except ValueError:
            return

        self.raw_h = h_raw
        h_score = h_raw * self.calibration.gaze_direction

        new_steering = self.steering_zone.update(h_score)

        changed = (new_steering != self.steering)
        self.steering = new_steering

        self.status_message = f"▶ FORWARD  |  STEER: {self.steering}"
        if changed:
            self.command_sender.send("FORWARD", self.steering)


# ──────────────────────────────────────────────────────────
#  HUD drawing
# ──────────────────────────────────────────────────────────

def draw_hud(frame, ctrl: EyeCarController) -> None:
    import cv2

    frame[:] = cv2.flip(frame, 1)
    h, w = frame.shape[:2]

    # ── Top bar ──
    cv2.rectangle(frame, (0, 0), (w, 130), (15, 15, 15), -1)

    # Face
    if ctrl.face_present:
        _text(frame, "FACE OK", (20, 30), color=(80, 220, 80))
    else:
        _text(frame, "FACE LOST", (20, 30), color=(40, 40, 240))

    # Raw score
    _text(
        frame,
        f"GAZE H: {ctrl.raw_h:+.3f}   BLINK: {ctrl.blink_score:.2f}",
        (20, 60),
        scale=0.5,
    )

    # Status line
    _text(frame, ctrl.status_message, (20, 100), scale=0.7, color=(50, 220, 255))

    # Driving indicator top-right
    if ctrl.driving:
        _text(frame, "DRIVING", (w - 180, 30), scale=0.8, color=(80, 255, 80))
    else:
        _text(frame, "STOPPED", (w - 180, 30), scale=0.8, color=(100, 100, 100))

    # ── Bottom bar ──
    cv2.rectangle(frame, (0, h - 100), (w, h), (15, 15, 15), -1)

    # Throttle
    if ctrl.driving:
        _text(frame, "THROTTLE: FORWARD", (20, h - 65), scale=0.8, color=(80, 220, 80))
    else:
        _text(frame, "THROTTLE: STOP", (20, h - 65), scale=0.8, color=(100, 100, 100))

    # Steering
    s_colors = {"LEFT": (0, 180, 255), "CENTER": (100, 100, 100), "RIGHT": (0, 180, 255)}
    _text(
        frame,
        f"STEERING: {ctrl.steering}",
        (20, h - 30),
        scale=0.8,
        color=s_colors.get(ctrl.steering, (100, 100, 100)),
    )

    # Gaze crosshair (horizontal only)
    cx = int(w / 2 + ctrl.raw_h * w * 2)
    cy = h // 2
    cx = max(20, min(w - 20, cx))
    cv2.circle(frame, (cx, cy), 18, (0, 255, 255), 2)
    cv2.line(frame, (cx - 25, cy), (cx + 25, cy), (0, 255, 255), 1)

    # Help
    _text(
        frame,
        "DOUBLE BLINK = toggle forward | LONG BLINK = stop | Q = quit | C = recalibrate",
        (15, h - 5),
        scale=0.38,
    )


def _text(frame, text, pos, scale=0.6, color=(255, 255, 255)):
    import cv2
    cv2.putText(
        frame, text, pos,
        cv2.FONT_HERSHEY_SIMPLEX, scale, color, 2, cv2.LINE_AA,
    )


# ──────────────────────────────────────────────────────────
#  Re-use calibration + face tracker from camera_app
# ──────────────────────────────────────────────────────────

from camera_app import (
    ensure_model,
    load_calibration,
    save_calibration,
    FaceTracker,
    GuidedCalibration,
    draw_interface,
)


# ──────────────────────────────────────────────────────────
#  Monkey-patch FaceTracker to cache raw landmarks
#  (we need them for iris_gaze_horizontal)
# ──────────────────────────────────────────────────────────

_cached_landmarks = None


def _patched_detect(self, bgr_frame, timestamp):
    global _cached_landmarks
    import cv2

    timestamp_ms = max(int(timestamp * 1000), self._last_timestamp_ms + 1)
    self._last_timestamp_ms = timestamp_ms
    rgb = cv2.cvtColor(bgr_frame, cv2.COLOR_BGR2RGB)
    image = self._mp.Image(image_format=self._mp.ImageFormat.SRGB, data=rgb)
    result = self._landmarker.detect_for_video(image, timestamp_ms)

    if not result.face_landmarks or not result.face_blendshapes:
        _cached_landmarks = None
        return VisionSample(timestamp=timestamp, face_present=False)

    scores = {
        item.category_name: float(item.score)
        for item in result.face_blendshapes[0]
    }
    if any(n not in scores for n in ("eyeBlinkLeft", "eyeBlinkRight")):
        _cached_landmarks = None
        return VisionSample(timestamp=timestamp, face_present=False)

    landmarks = result.face_landmarks[0]
    if len(landmarks) < 474:
        _cached_landmarks = None
        return VisionSample(timestamp=timestamp, face_present=False)

    _cached_landmarks = landmarks

    try:
        gaze_score = iris_gaze_horizontal(landmarks)
    except ValueError:
        _cached_landmarks = None
        return VisionSample(timestamp=timestamp, face_present=False)

    blink_score = min(scores["eyeBlinkLeft"], scores["eyeBlinkRight"])
    return VisionSample(
        timestamp=timestamp,
        face_present=True,
        gaze_score=gaze_score,
        blink_score=blink_score,
    )


FaceTracker.detect = _patched_detect


# ──────────────────────────────────────────────────────────
#  Main loop
# ──────────────────────────────────────────────────────────

def run(args) -> int:
    import cv2

    ensure_model(args.model)

    calibration = load_calibration(args.calibration)

    tracker = FaceTracker(args.model)
    camera = cv2.VideoCapture(args.camera)
    camera.set(cv2.CAP_PROP_FRAME_WIDTH, 1280)
    camera.set(cv2.CAP_PROP_FRAME_HEIGHT, 720)
    camera.set(cv2.CAP_PROP_FPS, 30)

    if not camera.isOpened():
        tracker.close()
        print(f"Cannot open camera {args.camera}", file=sys.stderr)
        return 2

    window = "Eye-Track Car Controller"
    cv2.namedWindow(window, cv2.WINDOW_NORMAL)

    # State
    car_ctrl: Optional[EyeCarController] = None
    calibrator: Optional[GuidedCalibration] = None
    message = ""

    if calibration is not None:
        car_ctrl = EyeCarController(
            calibration,
            car_url=args.car_url,
            hold_frames=args.hold,
            median_window=args.median,
        )
        message = "READY — double blink to start driving"
    else:
        calibrator = GuidedCalibration(time.monotonic())
        message = calibrator.instruction

    last_send = 0.0
    send_interval = 0.12  # keep-alive interval

    try:
        while True:
            ok, frame = camera.read()
            if not ok or frame is None:
                if car_ctrl is not None:
                    car_ctrl.full_stop("CAMERA FRAME LOST — STOPPED")
                time.sleep(0.02)
                continue

            now = time.monotonic()
            sample = tracker.detect(frame, now)

            # ── Calibration mode ──
            if calibrator is not None:
                completed, message = calibrator.update(sample, now)
                if completed is not None:
                    calibration = completed
                    save_calibration(args.calibration, completed)
                    car_ctrl = EyeCarController(
                        completed,
                        car_url=args.car_url,
                        hold_frames=args.hold,
                        median_window=args.median,
                    )
                    calibrator = None
                    message = "CALIBRATED — double blink to start driving"

                draw_interface(frame, sample, None, message)
                cv2.imshow(window, frame)
                key = cv2.waitKey(1) & 0xFF
                if key in (ord("q"), 27):
                    break
                continue

            # ── Car control mode ──
            if car_ctrl is not None:
                landmarks = _cached_landmarks if sample.face_present else None
                blink = sample.blink_score if sample.face_present else 0.0

                car_ctrl.update(landmarks, blink, now)

                # Periodic keep-alive send
                if now - last_send >= send_interval:
                    if car_ctrl.driving:
                        car_ctrl.command_sender.send(
                            "FORWARD", car_ctrl.steering
                        )
                    else:
                        car_ctrl.command_sender.send("STOP", "CENTER")
                    last_send = now

                draw_hud(frame, car_ctrl)
                cv2.imshow(window, frame)

            key = cv2.waitKey(1) & 0xFF
            if key in (ord("q"), 27):
                break
            elif key == ord("c"):
                if car_ctrl:
                    car_ctrl.command_sender.close()
                    car_ctrl = None
                calibrator = GuidedCalibration(now)
                message = calibrator.instruction
            elif key == ord("s"):
                if car_ctrl:
                    car_ctrl.full_stop("MANUAL STOP")
            elif key == ord("r"):
                if car_ctrl and not car_ctrl.driving:
                    car_ctrl.driving = True
                    car_ctrl.steering_zone.reset()
                    car_ctrl.command_sender.send("FORWARD", "CENTER")
                    car_ctrl.status_message = "▶ RESUMED FORWARD"

    finally:
        if car_ctrl:
            car_ctrl.command_sender.close()
        camera.release()
        tracker.close()
        cv2.destroyAllWindows()

    return 0


# ──────────────────────────────────────────────────────────
#  CLI
# ──────────────────────────────────────────────────────────

def parse_args():
    p = argparse.ArgumentParser(
        description="Control ESP32-S3 Rover with eye tracking"
    )
    p.add_argument("--camera", type=int, default=0)
    p.add_argument("--model", type=Path, default=DEFAULT_MODEL)
    p.add_argument("--calibration", type=Path, default=DEFAULT_CALIBRATION)
    p.add_argument(
        "--car-url", type=str, default="http://192.168.4.1",
        help="ESP32 rover URL (default: http://192.168.4.1)",
    )
    p.add_argument(
        "--hold", type=int, default=3,
        help="Frames a direction must hold before activating (default 3)",
    )
    p.add_argument(
        "--median", type=int, default=5,
        help="Median filter window size (default 5)",
    )
    return p.parse_args()


def main() -> int:
    return run(parse_args())


if __name__ == "__main__":
    raise SystemExit(main())
