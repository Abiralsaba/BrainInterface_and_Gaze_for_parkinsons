import unittest
from types import SimpleNamespace

from camera_app import GuidedCalibration, iris_gaze_score
from camera_control import (
    BlinkDetector,
    BlinkEvent,
    Calibration,
    CameraController,
    ControlConfig,
    Gaze,
    Intent,
    Mode,
    StableGazeGate,
    VisionSample,
    build_calibration,
)


CALIBRATION = Calibration(
    left_threshold=-0.20,
    right_threshold=0.20,
    blink_threshold=0.50,
)


def sample(now, gaze=0.0, blink=0.0, face=True):
    return VisionSample(
        timestamp=now,
        face_present=face,
        gaze_score=gaze,
        blink_score=blink,
    )


class CalibrationTests(unittest.TestCase):
    def test_iris_gaze_uses_position_inside_both_eyes(self):
        landmarks = [SimpleNamespace(x=0.0) for _ in range(478)]
        landmarks[33].x, landmarks[133].x = 0.10, 0.30
        landmarks[362].x, landmarks[263].x = 0.60, 0.80
        landmarks[468].x = 0.24
        landmarks[473].x = 0.74

        self.assertAlmostEqual(iris_gaze_score(landmarks), 0.20)

    def test_builds_midpoint_thresholds(self):
        result = build_calibration(
            center_scores=[-0.01, 0.0, 0.01, 0.0, 0.0],
            left_scores=[-0.5, -0.48, -0.52, -0.49, -0.51],
            right_scores=[0.48, 0.5, 0.51, 0.49, 0.52],
            open_scores=[0.02, 0.03, 0.02, 0.04, 0.03],
            closed_scores=[0.9, 0.91, 0.89, 0.92, 0.9],
        )
        self.assertAlmostEqual(result.left_threshold, -0.25)
        self.assertAlmostEqual(result.right_threshold, 0.25)
        self.assertAlmostEqual(result.blink_threshold, 0.465)
        self.assertEqual(result.classify_gaze(-0.4), Gaze.LEFT)
        self.assertEqual(result.classify_gaze(0.4), Gaze.RIGHT)
        self.assertEqual(result.classify_gaze(0.0), Gaze.CENTER)

    def test_rejects_weak_calibration(self):
        with self.assertRaisesRegex(ValueError, "Left/right gaze too similar"):
            build_calibration(
                center_scores=[0.0] * 5,
                left_scores=[-0.01] * 5,
                right_scores=[0.01] * 5,
                open_scores=[0.0] * 5,
                closed_scores=[0.9] * 5,
            )

    def test_accepts_reversed_camera_gaze_direction(self):
        result = build_calibration(
            center_scores=[0.0] * 5,
            left_scores=[0.20] * 5,
            right_scores=[-0.20] * 5,
            open_scores=[0.02] * 5,
            closed_scores=[0.90] * 5,
        )

        self.assertEqual(result.gaze_direction, -1.0)
        self.assertEqual(result.classify_gaze(0.20), Gaze.LEFT)
        self.assertEqual(result.classify_gaze(-0.20), Gaze.RIGHT)

    def test_accepts_small_but_clear_gaze_separation(self):
        result = build_calibration(
            center_scores=[0.0] * 5,
            left_scores=[-0.03] * 5,
            right_scores=[0.03] * 5,
            open_scores=[0.02] * 5,
            closed_scores=[0.90] * 5,
        )

        self.assertEqual(result.classify_gaze(-0.03), Gaze.LEFT)
        self.assertEqual(result.classify_gaze(0.03), Gaze.RIGHT)

    def test_uses_gaze_extremes_when_center_sample_drifted(self):
        result = build_calibration(
            center_scores=[0.109] * 5,
            left_scores=[-0.034] * 5,
            right_scores=[0.047] * 5,
            open_scores=[0.054] * 5,
            closed_scores=[0.90] * 5,
        )

        self.assertEqual(result.classify_gaze(-0.034), Gaze.LEFT)
        self.assertEqual(result.classify_gaze(0.047), Gaze.RIGHT)
        self.assertEqual(result.classify_gaze(-0.006), Gaze.CENTER)

    def test_rejects_invalid_saved_thresholds(self):
        with self.assertRaisesRegex(ValueError, "Left threshold"):
            Calibration.from_dict(
                {
                    "left_threshold": 0.2,
                    "right_threshold": -0.2,
                    "blink_threshold": 0.5,
                }
            )
        with self.assertRaisesRegex(ValueError, "Blink threshold"):
            Calibration.from_dict(
                {
                    "left_threshold": -0.2,
                    "right_threshold": 0.2,
                    "blink_threshold": 1.5,
                }
            )

    def test_failed_guided_calibration_waits_for_restart(self):
        calibrator = GuidedCalibration(0.0)
        calibrator.stage_index = len(calibrator.STAGES)
        calibrator.error = "Left/right gaze too similar"

        completed, message = calibrator.update(sample(10.0), 10.0)

        self.assertIsNone(completed)
        self.assertIn("CALIBRATION FAILED", message)
        self.assertIn("PRESS C", message)


class BlinkDetectorTests(unittest.TestCase):
    def setUp(self):
        self.detector = BlinkDetector(0.5, ControlConfig())

    def test_single_blink_is_detected_on_eye_open(self):
        self.assertEqual(self.detector.update(0.0, 0.0), BlinkEvent.NONE)
        self.assertEqual(self.detector.update(0.9, 0.10), BlinkEvent.NONE)
        self.assertEqual(self.detector.update(0.0, 0.22), BlinkEvent.SINGLE)
        self.assertEqual(self.detector.update(0.0, 1.0), BlinkEvent.NONE)

    def test_double_blink_is_detected(self):
        self.detector.update(0.9, 0.10)
        self.detector.update(0.0, 0.20)
        self.detector.update(0.9, 0.40)
        event = self.detector.update(0.0, 0.50)
        self.assertEqual(event, BlinkEvent.DOUBLE)

    def test_long_closure_emits_once(self):
        self.detector.update(0.9, 0.0)
        self.assertEqual(self.detector.update(0.9, 0.81), BlinkEvent.LONG)
        self.assertEqual(self.detector.update(0.9, 1.1), BlinkEvent.NONE)
        self.assertEqual(self.detector.update(0.0, 1.2), BlinkEvent.NONE)


class GazeGateTests(unittest.TestCase):
    def test_gaze_repeats_while_held(self):
        gate = StableGazeGate(ControlConfig())
        gate.reset(0.0)
        self.assertIsNone(gate.update(Gaze.LEFT, 0.1))
        self.assertEqual(gate.update(Gaze.LEFT, 0.36), Gaze.LEFT)
        self.assertIsNone(gate.update(Gaze.LEFT, 0.50))
        self.assertEqual(gate.update(Gaze.LEFT, 0.59), Gaze.LEFT)
        self.assertIsNone(gate.update(Gaze.CENTER, 0.60))
        self.assertIsNone(gate.update(Gaze.RIGHT, 0.61))
        self.assertEqual(gate.update(Gaze.RIGHT, 0.87), Gaze.RIGHT)


class ControllerTests(unittest.TestCase):
    def test_mobility_gaze_produces_short_pulse(self):
        controller = CameraController(CALIBRATION)
        controller.enter_mobility(0.0)
        controller.update(sample(0.1, gaze=-0.5))
        output = controller.update(sample(0.71, gaze=-0.5))
        self.assertEqual(output.intent, Intent.LEFT)
        self.assertEqual(output.mode, Mode.MOBILITY)

        output = controller.update(sample(0.90, gaze=-0.5))
        self.assertEqual(output.intent, Intent.LEFT)
        output = controller.update(sample(1.02, gaze=0.0))
        self.assertEqual(output.intent, Intent.CENTER)

    def test_single_blink_never_changes_mobility(self):
        controller = CameraController(CALIBRATION)
        controller.enter_mobility(0.0)
        controller.update(sample(0.1, blink=0.9))
        output = controller.update(sample(0.2, blink=0.0))
        self.assertEqual(output.mode, Mode.MOBILITY)
        self.assertEqual(output.intent, Intent.CENTER)

    def test_double_blink_stops_and_exits_mobility(self):
        controller = CameraController(CALIBRATION)
        controller.enter_mobility(0.0)
        controller.update(sample(0.1, blink=0.9))
        controller.update(sample(0.2, blink=0.0))
        controller.update(sample(0.4, blink=0.9))
        output = controller.update(sample(0.5, blink=0.0))
        self.assertEqual(output.mode, Mode.COMMUNICATION)
        self.assertEqual(output.intent, Intent.STOP)
        self.assertEqual(output.reason, "EYE STOP")

    def test_long_closure_stops_and_exits_mobility(self):
        controller = CameraController(CALIBRATION)
        controller.enter_mobility(0.0)
        controller.update(sample(0.1, blink=0.9))
        output = controller.update(sample(0.91, blink=0.9))
        self.assertEqual(output.mode, Mode.COMMUNICATION)
        self.assertEqual(output.intent, Intent.STOP)

    def test_face_loss_stops_after_timeout(self):
        controller = CameraController(CALIBRATION)
        controller.enter_mobility(0.0)
        controller.update(sample(0.1))
        output = controller.update(sample(0.25, face=False))
        self.assertEqual(output.mode, Mode.MOBILITY)
        output = controller.update(sample(0.41, face=False))
        self.assertEqual(output.mode, Mode.COMMUNICATION)
        self.assertEqual(output.intent, Intent.STOP)
        self.assertEqual(output.reason, "FACE LOST")

    def test_face_reacquisition_requires_a_fresh_gaze_hold(self):
        controller = CameraController(CALIBRATION)
        controller.enter_mobility(0.0)
        controller.update(sample(0.10, gaze=-0.5))

        output = controller.update(sample(0.25, face=False))
        self.assertEqual(output.mode, Mode.MOBILITY)
        self.assertEqual(output.intent, Intent.CENTER)

        controller.update(sample(0.26, gaze=-0.5))
        output = controller.update(sample(0.40, gaze=-0.5))
        self.assertEqual(output.intent, Intent.CENTER)
        output = controller.update(sample(0.52, gaze=-0.5))
        self.assertEqual(output.intent, Intent.LEFT)

    def test_single_blink_moves_one_keyboard_row_down(self):
        controller = CameraController(CALIBRATION)
        controller.update(sample(0.10, blink=0.9))
        output = controller.update(sample(0.22, blink=0.0))

        self.assertEqual(output.selected_key, "H")
        self.assertEqual(output.reason, "ROW DOWN")

    def test_keyboard_navigation_repeats_while_gaze_is_held(self):
        controller = CameraController(CALIBRATION)
        controller.update(sample(0.10, gaze=0.5))
        first = controller.update(sample(0.36, gaze=0.5))
        second = controller.update(sample(0.59, gaze=0.5))

        self.assertEqual(first.selected_key, "B")
        self.assertEqual(second.selected_key, "C")

    def test_closed_eyes_do_not_cause_horizontal_navigation(self):
        controller = CameraController(CALIBRATION)
        controller.update(sample(0.10, gaze=0.5, blink=0.9))
        controller.update(sample(0.40, gaze=0.5, blink=0.9))
        output = controller.update(sample(0.91, gaze=0.5, blink=0.9))

        self.assertEqual(output.selected_key, "A")
        self.assertEqual(output.text, "A")

    def test_horizontal_direction_can_be_reversed(self):
        controller = CameraController(CALIBRATION, invert_horizontal=True)
        controller.enter_mobility(0.0)
        controller.update(sample(0.10, gaze=-0.5))
        output = controller.update(sample(0.36, gaze=-0.5))

        self.assertEqual(output.gaze, Gaze.RIGHT)
        self.assertEqual(output.intent, Intent.RIGHT)

    def test_communication_gaze_navigation_and_long_blink_select(self):
        controller = CameraController(CALIBRATION)
        controller.update(sample(0.0))
        controller.update(sample(0.1, gaze=0.5))
        output = controller.update(sample(0.71, gaze=0.5))
        self.assertEqual(output.selected_key, "B")

        controller.update(sample(0.8, blink=0.9))
        output = controller.update(sample(1.61, blink=0.9))
        self.assertEqual(output.text, "B")
        self.assertEqual(output.mode, Mode.COMMUNICATION)


if __name__ == "__main__":
    unittest.main()
