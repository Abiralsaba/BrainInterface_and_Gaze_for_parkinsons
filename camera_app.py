"""NeuroWheel camera control and communication prototype."""

import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time
from typing import Dict, List, Optional, Tuple
import urllib.request

from camera_control import (
    Calibration,
    CameraController,
    Gaze,
    Intent,
    Mode,
    VisionSample,
    build_calibration,
)


ROOT = Path(__file__).resolve().parent
DEFAULT_MODEL = ROOT / "models" / "face_landmarker.task"
DEFAULT_CALIBRATION = ROOT / "calibration.json"
MODEL_URL = (
    "https://storage.googleapis.com/mediapipe-models/face_landmarker/"
    "face_landmarker/float16/latest/face_landmarker.task"
)


def ensure_model(path: Path) -> None:
    if path.is_file() and path.stat().st_size > 1_000_000:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    print("Downloading the official MediaPipe face model...")
    with tempfile.NamedTemporaryFile(dir=str(path.parent), delete=False) as temp:
        temp_path = Path(temp.name)
    try:
        urllib.request.urlretrieve(MODEL_URL, str(temp_path))
        if temp_path.stat().st_size <= 1_000_000:
            raise RuntimeError("Downloaded model is unexpectedly small")
        os.replace(str(temp_path), str(path))
    finally:
        if temp_path.exists():
            temp_path.unlink()


def load_calibration(path: Path) -> Optional[Calibration]:
    try:
        with path.open("r", encoding="utf-8") as handle:
            return Calibration.from_dict(json.load(handle))
    except (FileNotFoundError, KeyError, TypeError, ValueError, json.JSONDecodeError):
        return None


def save_calibration(path: Path, calibration: Calibration) -> None:
    path.write_text(
        json.dumps(calibration.to_dict(), indent=2) + "\n",
        encoding="utf-8",
    )


def iris_gaze_score(landmarks) -> float:
    """Return horizontal iris position normalized to the two eye widths."""

    positions = []
    # MediaPipe: right iris/corners, then left iris/corners.
    for iris_index, corner_a, corner_b in ((468, 33, 133), (473, 362, 263)):
        left_x, right_x = sorted(
            (landmarks[corner_a].x, landmarks[corner_b].x)
        )
        eye_width = right_x - left_x
        if eye_width <= 1e-6:
            raise ValueError("Eye landmarks are too close")
        positions.append((landmarks[iris_index].x - left_x) / eye_width)
    return sum(positions) / len(positions) - 0.5


class FaceTracker:
    def __init__(self, model_path: Path):
        import mediapipe as mp

        self._mp = mp
        options = mp.tasks.vision.FaceLandmarkerOptions(
            base_options=mp.tasks.BaseOptions(model_asset_path=str(model_path)),
            running_mode=mp.tasks.vision.RunningMode.VIDEO,
            num_faces=1,
            min_face_detection_confidence=0.60,
            min_face_presence_confidence=0.60,
            min_tracking_confidence=0.60,
            output_face_blendshapes=True,
        )
        self._landmarker = mp.tasks.vision.FaceLandmarker.create_from_options(options)
        self._last_timestamp_ms = -1

    def close(self) -> None:
        self._landmarker.close()

    def detect(self, bgr_frame, timestamp: float) -> VisionSample:
        import cv2

        timestamp_ms = max(int(timestamp * 1000), self._last_timestamp_ms + 1)
        self._last_timestamp_ms = timestamp_ms
        rgb = cv2.cvtColor(bgr_frame, cv2.COLOR_BGR2RGB)
        image = self._mp.Image(image_format=self._mp.ImageFormat.SRGB, data=rgb)
        result = self._landmarker.detect_for_video(image, timestamp_ms)

        if not result.face_landmarks or not result.face_blendshapes:
            return VisionSample(timestamp=timestamp, face_present=False)

        scores: Dict[str, float] = {
            item.category_name: float(item.score)
            for item in result.face_blendshapes[0]
        }
        required = ("eyeBlinkLeft", "eyeBlinkRight")
        if any(name not in scores for name in required):
            return VisionSample(timestamp=timestamp, face_present=False)

        landmarks = result.face_landmarks[0]
        if len(landmarks) < 474:
            return VisionSample(timestamp=timestamp, face_present=False)
        try:
            gaze_score = iris_gaze_score(landmarks)
        except ValueError:
            return VisionSample(timestamp=timestamp, face_present=False)

        # min() requires both eyes to be closed, so a wink cannot become STOP.
        blink_score = min(scores["eyeBlinkLeft"], scores["eyeBlinkRight"])
        return VisionSample(
            timestamp=timestamp,
            face_present=True,
            gaze_score=gaze_score,
            blink_score=blink_score,
        )


class GuidedCalibration:
    SETTLE_SECONDS = 0.65
    SAMPLE_SECONDS = 1.35
    STAGES: Tuple[Tuple[str, str], ...] = (
        ("CENTER", "LOOK AT THE CAMERA LENS - EYES OPEN"),
        ("LEFT", "EYES ONLY: LOOK STRONGLY TO YOUR LEFT"),
        ("RIGHT", "EYES ONLY: LOOK STRONGLY TO YOUR RIGHT"),
        ("CLOSED", "CLOSE BOTH EYES"),
    )

    def __init__(self, now: float):
        self.stage_index = 0
        self.stage_started = now
        self.samples: Dict[str, List[float]] = {
            "CENTER": [],
            "LEFT": [],
            "RIGHT": [],
            "OPEN": [],
            "CLOSED": [],
        }
        self.error = ""

    @property
    def instruction(self) -> str:
        if self.stage_index >= len(self.STAGES):
            return "CALIBRATION COMPLETE"
        return self.STAGES[self.stage_index][1]

    def update(
        self, sample: VisionSample, now: float
    ) -> Tuple[Optional[Calibration], str]:
        if self.error:
            return None, "CALIBRATION FAILED: {} - PRESS C".format(self.error)

        if not sample.face_present:
            self.stage_started = now
            return None, "FACE NOT FOUND - CENTER YOUR FACE"

        stage_name, instruction = self.STAGES[self.stage_index]
        elapsed = now - self.stage_started
        total = self.SETTLE_SECONDS + self.SAMPLE_SECONDS
        remaining = max(0.0, total - elapsed)
        message = "{}  {:.1f}s".format(instruction, remaining)

        if elapsed >= self.SETTLE_SECONDS:
            if stage_name == "CLOSED":
                self.samples["CLOSED"].append(sample.blink_score)
            else:
                self.samples[stage_name].append(sample.gaze_score)
                if stage_name == "CENTER":
                    self.samples["OPEN"].append(sample.blink_score)

        if elapsed < total:
            return None, message

        self.stage_index += 1
        self.stage_started = now
        if self.stage_index < len(self.STAGES):
            return None, self.instruction

        try:
            calibration = build_calibration(
                center_scores=self.samples["CENTER"],
                left_scores=self.samples["LEFT"],
                right_scores=self.samples["RIGHT"],
                open_scores=self.samples["OPEN"],
                closed_scores=self.samples["CLOSED"],
            )
        except ValueError as exc:
            self.error = str(exc)
            return None, "CALIBRATION FAILED: {} - PRESS C".format(exc)
        return calibration, "CALIBRATION SAVED"


def speak(text: str) -> None:
    text = text.strip()
    if not text:
        return
    command = shutil.which("say")
    if command:
        subprocess.Popen(
            [command, text],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    else:
        print("TTS:", text)


def draw_text(frame, text: str, position, scale=0.65, color=(255, 255, 255)):
    import cv2

    cv2.putText(
        frame,
        text,
        position,
        cv2.FONT_HERSHEY_SIMPLEX,
        scale,
        color,
        2,
        cv2.LINE_AA,
    )


def draw_keyboard(frame, controller: CameraController) -> None:
    import cv2

    height, width = frame.shape[:2]
    columns = controller.KEYBOARD_COLUMNS
    key_width = max(82, width // columns)
    key_height = 42
    rows = (len(controller.KEYS) + columns - 1) // columns
    start_y = height - rows * key_height - 24

    for index, key in enumerate(controller.KEYS):
        row, column = divmod(index, columns)
        x1 = column * key_width
        y1 = start_y + row * key_height
        x2 = min(width - 1, x1 + key_width - 3)
        y2 = y1 + key_height - 3
        selected = index == controller.selected_index
        fill = (30, 130, 230) if selected else (35, 35, 35)
        cv2.rectangle(frame, (x1, y1), (x2, y2), fill, -1)
        draw_text(
            frame,
            key,
            (x1 + 7, y1 + 28),
            scale=0.48 if len(key) > 5 else 0.58,
        )


def draw_interface(
    frame,
    sample: VisionSample,
    controller: Optional[CameraController],
    message: str,
) -> None:
    import cv2

    frame[:] = cv2.flip(frame, 1)
    height, width = frame.shape[:2]
    cv2.rectangle(frame, (0, 0), (width, 120), (20, 20, 20), -1)

    face_status = "FACE OK" if sample.face_present else "FACE LOST"
    face_color = (80, 220, 80) if sample.face_present else (40, 40, 240)
    draw_text(frame, face_status, (20, 30), color=face_color)
    draw_text(
        frame,
        "gaze {:+.3f}  eyes {:.3f}".format(sample.gaze_score, sample.blink_score),
        (20, 58),
        scale=0.55,
    )
    draw_text(frame, message, (20, 92), scale=0.55, color=(50, 220, 255))

    if controller is None:
        draw_text(frame, "CALIBRATION REQUIRED", (width - 310, 30), color=(0, 180, 255))
        return

    draw_text(frame, controller.mode.value, (width - 230, 30), color=(0, 220, 255))
    horizontal = "REVERSED" if controller.invert_horizontal else "NORMAL"
    draw_text(
        frame,
        "HORIZONTAL " + horizontal,
        (width - 230, 58),
        scale=0.48,
        color=(0, 220, 255),
    )
    if controller.mode == Mode.MOBILITY:
        colors = {
            Intent.LEFT: (0, 180, 255),
            Intent.RIGHT: (0, 180, 255),
            Intent.CENTER: (80, 220, 80),
            Intent.STOP: (40, 40, 240),
        }
        draw_text(
            frame,
            "CAMERA INTENT: {}".format(controller.intent.value),
            (20, height - 35),
            scale=0.9,
            color=colors[controller.intent],
        )
        draw_text(
            frame,
            "Keep looking to steer | double/long blink = STOP",
            (20, height - 70),
            scale=0.55,
        )
    else:
        cv2.rectangle(frame, (0, 120), (width, 170), (5, 5, 5), -1)
        draw_text(frame, "TEXT: " + (controller.text or "_"), (15, 153), scale=0.7)
        draw_keyboard(frame, controller)

    draw_text(
        frame,
        "Q quit | C calibrate | M mode | I reverse gaze",
        (15, height - 8),
        scale=0.42,
    )


def dependency_check(model_path: Path) -> int:
    ensure_model(model_path)
    try:
        import cv2
        import mediapipe

        tracker = FaceTracker(model_path)
        tracker.close()
        print("OpenCV", cv2.__version__)
        print("MediaPipe", mediapipe.__version__)
        print("Model", model_path)
        print("Camera subsystem dependency check passed")
        return 0
    except Exception as exc:
        print("Dependency check failed: {}".format(exc), file=sys.stderr)
        return 1


def run(args) -> int:
    import cv2

    ensure_model(args.model)
    calibration = load_calibration(args.calibration)
    controller = (
        CameraController(calibration, invert_horizontal=True)
        if calibration else None
    )
    calibrator = None if calibration else GuidedCalibration(time.monotonic())
    message = "PRESS C TO RECALIBRATE" if calibration else calibrator.instruction

    tracker = FaceTracker(args.model)
    camera = cv2.VideoCapture(args.camera)
    camera.set(cv2.CAP_PROP_FRAME_WIDTH, 1280)
    camera.set(cv2.CAP_PROP_FRAME_HEIGHT, 720)
    camera.set(cv2.CAP_PROP_FPS, 30)

    if not camera.isOpened():
        tracker.close()
        print(
            "Cannot open camera {}. Allow camera permission for your terminal/Python."
            .format(args.camera),
            file=sys.stderr,
        )
        return 2

    window = "NeuroWheel Camera Control"
    cv2.namedWindow(window, cv2.WINDOW_NORMAL)

    try:
        while True:
            ok, frame = camera.read()
            if not ok or frame is None:
                now = time.monotonic()
                message = "CAMERA FRAME LOST"
                missing = VisionSample(timestamp=now, face_present=False)
                if calibrator is not None:
                    calibrator.update(missing, now)
                elif controller is not None:
                    output = controller.update(missing)
                    if output.reason:
                        message = output.reason
                time.sleep(0.02)
                continue

            now = time.monotonic()
            sample = tracker.detect(frame, now)

            if calibrator is not None:
                completed, message = calibrator.update(sample, now)
                if completed is not None:
                    calibration = completed
                    save_calibration(args.calibration, completed)
                    controller = CameraController(completed, invert_horizontal=True)
                    calibrator = None
            elif controller is not None:
                output = controller.update(sample)
                if output.reason:
                    message = output.reason
                elif not sample.face_present:
                    message = "FACE NOT FOUND"
                else:
                    message = "GAZE {}".format(output.gaze.value)
                if output.speak_text:
                    speak(output.speak_text)

            draw_interface(frame, sample, controller, message)
            cv2.imshow(window, frame)
            key = cv2.waitKey(1) & 0xFF
            if key in (ord("q"), 27):
                break
            if key == ord("c"):
                if controller is not None:
                    controller.stop_to_communication(now)
                controller = None
                calibrator = GuidedCalibration(now)
                message = calibrator.instruction
            elif key == ord("m") and controller is not None:
                if controller.mode == Mode.MOBILITY:
                    controller.stop_to_communication(now)
                else:
                    controller.enter_mobility(now)
            elif key == ord("r") and controller is not None:
                controller.clear_text()
            elif key == ord("i") and controller is not None:
                controller.toggle_horizontal(now)
                state = "REVERSED" if controller.invert_horizontal else "NORMAL"
                message = "HORIZONTAL {}".format(state)
    finally:
        camera.release()
        tracker.close()
        cv2.destroyAllWindows()
    return 0


def parse_args():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--camera", type=int, default=0, help="OpenCV camera index")
    parser.add_argument("--model", type=Path, default=DEFAULT_MODEL)
    parser.add_argument("--calibration", type=Path, default=DEFAULT_CALIBRATION)
    parser.add_argument(
        "--check",
        action="store_true",
        help="load dependencies/model without opening the camera",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.check:
        return dependency_check(args.model)
    return run(args)


if __name__ == "__main__":
    raise SystemExit(main())
