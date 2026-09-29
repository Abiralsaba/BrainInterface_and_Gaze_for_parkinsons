"""Hand-gesture car controller for the ESP32-S3 Rover.

Control scheme (using MediaPipe Hand Landmarks):
  ✊  Fist / fingers curled (come gesture)  →  FORWARD
  ✋  Open palm (hi-5, all fingers out)     →  STOP
  ☝️  1 finger up (index only)              →  turn RIGHT
  ✌️  2 fingers up (index + middle)         →  turn LEFT

Precision features:
  - Median filter over recent gesture readings
  - Hold timer: gesture must be stable for N consecutive frames
  - Visual HUD with live hand skeleton + gesture label
"""

import argparse
import sys
import time
import urllib.request
from collections import deque
from pathlib import Path
from statistics import mode as stat_mode
from typing import List, Optional, Tuple

from car_transport import CarCommandSender

# ──────────────────────────────────────────────────────────
#  Paths & model
# ──────────────────────────────────────────────────────────

ROOT = Path(__file__).resolve().parent
HAND_MODEL = ROOT / "models" / "hand_landmarker.task"
HAND_MODEL_URL = (
    "https://storage.googleapis.com/mediapipe-models/hand_landmarker/"
    "hand_landmarker/float16/latest/hand_landmarker.task"
)


def ensure_hand_model(path: Path) -> None:
    """Download the MediaPipe hand landmarker model if needed."""
    if path.is_file() and path.stat().st_size > 500_000:
        return
    import os
    import tempfile

    path.parent.mkdir(parents=True, exist_ok=True)
    print("Downloading MediaPipe hand landmarker model...")
    with tempfile.NamedTemporaryFile(dir=str(path.parent), delete=False) as tmp:
        tmp_path = Path(tmp.name)
    try:
        urllib.request.urlretrieve(HAND_MODEL_URL, str(tmp_path))
        if tmp_path.stat().st_size <= 500_000:
            raise RuntimeError("Downloaded model is too small")
        os.replace(str(tmp_path), str(path))
        print("Model saved to", path)
    finally:
        if tmp_path.exists():
            tmp_path.unlink()


# ──────────────────────────────────────────────────────────
#  Finger detection from hand landmarks
# ──────────────────────────────────────────────────────────

# MediaPipe hand landmark indices
#   0  = WRIST
#   1-4   = THUMB  (CMC, MCP, IP, TIP)
#   5-8   = INDEX  (MCP, PIP, DIP, TIP)
#   9-12  = MIDDLE (MCP, PIP, DIP, TIP)
#  13-16  = RING   (MCP, PIP, DIP, TIP)
#  17-20  = PINKY  (MCP, PIP, DIP, TIP)

FINGER_TIPS = [4, 8, 12, 16, 20]     # TIP landmarks
FINGER_PIPS = [3, 6, 10, 14, 18]     # PIP (or IP for thumb) landmarks
FINGER_MCPS = [2, 5, 9, 13, 17]      # MCP landmarks


def count_fingers_up(landmarks, handedness: str = "Right") -> Tuple[int, List[bool]]:
    """Count how many fingers are extended.

    Returns (count, [thumb, index, middle, ring, pinky]).
    """
    fingers = [False] * 5

    # Thumb: compare tip.x vs IP.x
    # For right hand (mirrored in camera): thumb tip LEFT of IP = open
    # For left hand: thumb tip RIGHT of IP = open
    if handedness == "Right":
        fingers[0] = landmarks[4].x < landmarks[3].x
    else:
        fingers[0] = landmarks[4].x > landmarks[3].x

    # Other 4 fingers: tip.y < PIP.y means finger is UP
    # (y increases downward in image coordinates)
    for i in range(1, 5):
        tip = landmarks[FINGER_TIPS[i]]
        pip = landmarks[FINGER_PIPS[i]]
        fingers[i] = tip.y < pip.y

    return sum(fingers), fingers


# ──────────────────────────────────────────────────────────
#  Gesture classification
# ──────────────────────────────────────────────────────────

# Gesture names
GESTURE_FIST = "FIST"           # 0 fingers → FORWARD
GESTURE_ONE = "ONE_FINGER"      # 1 finger (index) → RIGHT
GESTURE_TWO = "TWO_FINGERS"     # 2 fingers (index+middle) → LEFT
GESTURE_OPEN = "OPEN_PALM"      # 4-5 fingers → STOP
GESTURE_OTHER = "OTHER"         # 3 fingers or unrecognized → safe stop


def classify_gesture(landmarks, handedness: str = "Right") -> str:
    """Classify hand landmarks into a gesture."""
    count, fingers = count_fingers_up(landmarks, handedness)

    # thumb, index, middle, ring, pinky = fingers

    if count == 0:
        return GESTURE_FIST

    if count == 1 and fingers[1]:
        # Only index finger up
        return GESTURE_ONE

    if count == 2 and fingers[1] and fingers[2]:
        # Index + middle up (peace/V sign)
        return GESTURE_TWO

    if count >= 4:
        # Open palm / hi-5
        return GESTURE_OPEN

    return GESTURE_OTHER


# ──────────────────────────────────────────────────────────
#  Stable Gesture Classifier (with smoothing + hold)
# ──────────────────────────────────────────────────────────

class StableGesture:
    """Require a gesture to be stable for `hold_frames` before committing."""

    def __init__(self, hold_frames: int = 6, buffer_size: int = 9):
        self.hold_frames = hold_frames
        self._buffer: deque = deque(maxlen=buffer_size)
        self._candidate: str = GESTURE_OTHER
        self._candidate_count: int = 0
        self._committed: str = GESTURE_OTHER

    def reset(self) -> None:
        self._buffer.clear()
        self._candidate = GESTURE_OTHER
        self._candidate_count = 0
        self._committed = GESTURE_OTHER

    def update(self, gesture: str) -> str:
        self._buffer.append(gesture)

        # Use mode (most frequent) of the buffer as the smoothed gesture
        try:
            smoothed = stat_mode(self._buffer)
        except Exception:
            smoothed = gesture

        if smoothed == self._candidate:
            self._candidate_count += 1
        else:
            self._candidate = smoothed
            self._candidate_count = 1

        if self._candidate_count >= self.hold_frames:
            self._committed = self._candidate

        return self._committed


# ──────────────────────────────────────────────────────────
#  Hand Car Controller
# ──────────────────────────────────────────────────────────

class HandCarController:
    """Translate hand gestures into car commands.

    ✊ FIST        → FORWARD + CENTER
    ✋ OPEN PALM   → STOP + CENTER
    ☝️ ONE FINGER  → FORWARD + RIGHT
    ✌️ TWO FINGERS → FORWARD + LEFT
    """

    def __init__(
        self,
        car_url: str = "http://192.168.4.1",
        hold_frames: int = 3,
        buffer_size: int = 5,
        command_sender=None,
    ):
        self.car_url = car_url
        self.command_sender = command_sender or CarCommandSender(car_url)
        self.gesture_filter = StableGesture(hold_frames, buffer_size)

        self.throttle = "STOP"
        self.steering = "CENTER"
        self.raw_gesture = GESTURE_OTHER
        self.stable_gesture = GESTURE_OTHER
        self.hand_present = False
        self.finger_count = 0
        self.fingers = [False] * 5
        self.status_message = "NO HAND DETECTED"
        self.hand_lost_at: Optional[float] = None
        self.hand_loss_timeout = 0.30

    def full_stop(self, reason: str = "STOPPED") -> None:
        self.throttle = "STOP"
        self.steering = "CENTER"
        self.gesture_filter.reset()
        self.command_sender.send("STOP", "CENTER")
        self.status_message = reason

    def update(
        self,
        landmarks,
        handedness: str,
        now: float,
    ) -> None:
        """Process one frame."""

        if landmarks is None:
            self.hand_present = False
            if self.hand_lost_at is None:
                self.hand_lost_at = now
                self.gesture_filter.reset()
            elif now - self.hand_lost_at >= self.hand_loss_timeout:
                if self.throttle != "STOP" or self.steering != "CENTER":
                    self.full_stop("HAND LOST — STOPPED")
                else:
                    self.status_message = "HAND LOST — STOPPED"
            return

        self.hand_present = True
        self.hand_lost_at = None

        # Count fingers and classify
        self.finger_count, self.fingers = count_fingers_up(landmarks, handedness)
        self.raw_gesture = classify_gesture(landmarks, handedness)
        self.stable_gesture = self.gesture_filter.update(self.raw_gesture)

        # The deliberate stop gesture bypasses the movement smoothing delay.
        if self.raw_gesture == GESTURE_OPEN:
            self.full_stop("✋ OPEN → STOP")
            return

        # Map gesture → car command
        prev_throttle = self.throttle
        prev_steering = self.steering

        if self.stable_gesture == GESTURE_FIST:
            self.throttle = "FORWARD"
            self.steering = "CENTER"
            self.status_message = "✊ FIST → FORWARD"

        elif self.stable_gesture == GESTURE_OPEN:
            self.throttle = "STOP"
            self.steering = "CENTER"
            self.status_message = "✋ OPEN → STOP"

        elif self.stable_gesture == GESTURE_ONE:
            self.throttle = "FORWARD"
            self.steering = "RIGHT"
            self.status_message = "☝ ONE → FORWARD + RIGHT"

        elif self.stable_gesture == GESTURE_TWO:
            self.throttle = "FORWARD"
            self.steering = "LEFT"
            self.status_message = "✌ TWO → FORWARD + LEFT"

        else:
            # Never preserve a movement command after a stable unknown gesture.
            if self.throttle != "STOP" or self.steering != "CENTER":
                self.full_stop(f"? OTHER ({self.finger_count}) — STOPPED")
            else:
                self.status_message = f"? OTHER ({self.finger_count}) — STOPPED"
            return

        # Send on change
        if self.throttle != prev_throttle or self.steering != prev_steering:
            self.command_sender.send(self.throttle, self.steering)


# ──────────────────────────────────────────────────────────
#  Hand Tracker (MediaPipe)
# ──────────────────────────────────────────────────────────

class HandTracker:
    """MediaPipe hand landmark detector."""

    def __init__(self, model_path: Path):
        import mediapipe as mp

        self._mp = mp
        options = mp.tasks.vision.HandLandmarkerOptions(
            base_options=mp.tasks.BaseOptions(
                model_asset_path=str(model_path)
            ),
            running_mode=mp.tasks.vision.RunningMode.VIDEO,
            num_hands=1,
            min_hand_detection_confidence=0.55,
            min_hand_presence_confidence=0.55,
            min_tracking_confidence=0.55,
        )
        self._landmarker = mp.tasks.vision.HandLandmarker.create_from_options(
            options
        )
        self._last_timestamp_ms = -1

    def close(self) -> None:
        self._landmarker.close()

    def detect(self, bgr_frame, timestamp: float):
        """Returns (landmarks, handedness_label) or (None, None)."""
        import cv2

        timestamp_ms = max(
            int(timestamp * 1000),
            self._last_timestamp_ms + 1,
        )
        self._last_timestamp_ms = timestamp_ms

        rgb = cv2.cvtColor(bgr_frame, cv2.COLOR_BGR2RGB)
        image = self._mp.Image(
            image_format=self._mp.ImageFormat.SRGB, data=rgb
        )
        result = self._landmarker.detect_for_video(image, timestamp_ms)

        if not result.hand_landmarks or not result.handedness:
            return None, None

        landmarks = result.hand_landmarks[0]
        handedness = result.handedness[0][0].category_name  # "Left" or "Right"

        return landmarks, handedness


# ──────────────────────────────────────────────────────────
#  HUD drawing
# ──────────────────────────────────────────────────────────

def draw_hand_hud(frame, ctrl: HandCarController, landmarks=None) -> None:
    """Draw overlay showing gesture and car state."""
    import cv2

    frame[:] = cv2.flip(frame, 1)
    h, w = frame.shape[:2]

    # Draw hand skeleton if available
    if landmarks is not None:
        _draw_hand_skeleton(frame, landmarks, w, h)

    # ── Top bar ──
    cv2.rectangle(frame, (0, 0), (w, 140), (15, 15, 15), -1)

    # Hand status
    if ctrl.hand_present:
        _text(frame, "HAND OK", (20, 30), color=(80, 220, 80))
    else:
        _text(frame, "NO HAND", (20, 30), color=(40, 40, 240))

    # Finger count
    finger_names = ["THM", "IDX", "MID", "RNG", "PNK"]
    finger_str = "  ".join(
        f"{finger_names[i]}:{'UP' if ctrl.fingers[i] else '--'}"
        for i in range(5)
    )
    _text(frame, finger_str, (20, 60), scale=0.45)

    # Gesture
    _text(
        frame,
        f"RAW: {ctrl.raw_gesture}  →  STABLE: {ctrl.stable_gesture}",
        (20, 88),
        scale=0.5,
        color=(200, 200, 200),
    )

    # Status
    _text(frame, ctrl.status_message, (20, 120), scale=0.65, color=(50, 220, 255))

    # ── Bottom bar ──
    cv2.rectangle(frame, (0, h - 100), (w, h), (15, 15, 15), -1)

    # Throttle
    if ctrl.throttle == "FORWARD":
        _text(frame, "THROTTLE: FORWARD", (20, h - 65), scale=0.8, color=(80, 220, 80))
    else:
        _text(frame, "THROTTLE: STOP", (20, h - 65), scale=0.8, color=(100, 100, 100))

    # Steering
    s_colors = {
        "LEFT": (0, 180, 255),
        "CENTER": (100, 100, 100),
        "RIGHT": (0, 180, 255),
    }
    _text(
        frame,
        f"STEERING: {ctrl.steering}",
        (20, h - 30),
        scale=0.8,
        color=s_colors.get(ctrl.steering, (100, 100, 100)),
    )

    # Legend
    _text(
        frame,
        "FIST=fwd  OPEN=stop  1finger=right  2fingers=left  |  Q=quit",
        (15, h - 5),
        scale=0.38,
    )


def _draw_hand_skeleton(frame, landmarks, w, h) -> None:
    """Draw hand landmark points and connections."""
    import cv2

    # Connections (MediaPipe hand)
    connections = [
        (0, 1), (1, 2), (2, 3), (3, 4),      # thumb
        (0, 5), (5, 6), (6, 7), (7, 8),      # index
        (0, 9), (9, 10), (10, 11), (11, 12),  # middle
        (0, 13), (13, 14), (14, 15), (15, 16),  # ring
        (0, 17), (17, 18), (18, 19), (19, 20),  # pinky
        (5, 9), (9, 13), (13, 17),             # palm
    ]

    points = []
    for lm in landmarks:
        # Mirror x because we flipped the frame
        px = int((1.0 - lm.x) * w)
        py = int(lm.y * h)
        points.append((px, py))

    # Draw connections
    for a, b in connections:
        if a < len(points) and b < len(points):
            cv2.line(frame, points[a], points[b], (0, 200, 100), 2)

    # Draw landmarks
    for i, pt in enumerate(points):
        color = (0, 255, 255) if i in FINGER_TIPS else (0, 150, 80)
        radius = 6 if i in FINGER_TIPS else 3
        cv2.circle(frame, pt, radius, color, -1)


def _text(frame, text, pos, scale=0.6, color=(255, 255, 255)):
    import cv2
    cv2.putText(
        frame, text, pos,
        cv2.FONT_HERSHEY_SIMPLEX, scale, color, 2, cv2.LINE_AA,
    )


# ──────────────────────────────────────────────────────────
#  Main loop
# ──────────────────────────────────────────────────────────

def run(args) -> int:
    import cv2

    ensure_hand_model(args.model)

    tracker = HandTracker(args.model)
    camera = cv2.VideoCapture(args.camera)
    camera.set(cv2.CAP_PROP_FRAME_WIDTH, 1280)
    camera.set(cv2.CAP_PROP_FRAME_HEIGHT, 720)
    camera.set(cv2.CAP_PROP_FPS, 30)

    if not camera.isOpened():
        tracker.close()
        print(f"Cannot open camera {args.camera}", file=sys.stderr)
        return 2

    window = "Hand Gesture Car Controller"
    cv2.namedWindow(window, cv2.WINDOW_NORMAL)

    ctrl = HandCarController(
        car_url=args.car_url,
        hold_frames=args.hold,
        buffer_size=args.buffer,
    )

    last_send = 0.0
    send_interval = 0.12

    try:
        while True:
            ok, frame = camera.read()
            if not ok or frame is None:
                ctrl.full_stop("CAMERA FRAME LOST — STOPPED")
                time.sleep(0.02)
                continue

            now = time.monotonic()
            landmarks, handedness = tracker.detect(frame, now)

            ctrl.update(
                landmarks,
                handedness or "Right",
                now,
            )

            # Periodic keep-alive
            if now - last_send >= send_interval:
                ctrl.command_sender.send(ctrl.throttle, ctrl.steering)
                last_send = now

            draw_hand_hud(frame, ctrl, landmarks)
            cv2.imshow(window, frame)

            key = cv2.waitKey(1) & 0xFF
            if key in (ord("q"), 27):
                break
            elif key == ord("s"):
                ctrl.full_stop("MANUAL STOP")

    finally:
        ctrl.command_sender.close()
        camera.release()
        tracker.close()
        cv2.destroyAllWindows()

    return 0


# ──────────────────────────────────────────────────────────
#  CLI
# ──────────────────────────────────────────────────────────

def parse_args():
    p = argparse.ArgumentParser(
        description="Control ESP32-S3 Rover with hand gestures"
    )
    p.add_argument("--camera", type=int, default=0)
    p.add_argument(
        "--model", type=Path, default=HAND_MODEL,
        help="Path to hand_landmarker.task model",
    )
    p.add_argument(
        "--car-url", type=str, default="http://192.168.4.1",
        help="ESP32 rover URL (default: http://192.168.4.1)",
    )
    p.add_argument(
        "--hold", type=int, default=3,
        help="Frames gesture must hold before activating (default 3)",
    )
    p.add_argument(
        "--buffer", type=int, default=5,
        help="Smoothing buffer size (default 5)",
    )
    return p.parse_args()


def main() -> int:
    return run(parse_args())


if __name__ == "__main__":
    raise SystemExit(main())
