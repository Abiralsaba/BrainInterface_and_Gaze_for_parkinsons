import unittest

from camera_control import Calibration, VisionSample
from merged import EEGReader, FilteredSteering, MergedDriveController


class FakeSender:
    def __init__(self):
        self.commands = []

    def send(self, throttle, steering):
        self.commands.append((throttle, steering))


class MergedDriveTests(unittest.TestCase):
    def make_controller(self, **kwargs):
        sender = FakeSender()
        controller = MergedDriveController(
            Calibration(-0.1, 0.1, 0.5),
            sender,
            focus_hold=1.0,
            eeg_timeout=1.5,
            median_window=1,
            hold_frames=1,
            blink_median_window=1,
            now=0.0,
            **kwargs,
        )
        controller.update_vision(VisionSample(0.0, True, 0.0, 0.0), 0.0)
        return controller, sender

    def blink(self, controller, start):
        controller.update_vision(VisionSample(start, True, 0.0, 0.9), start)
        controller.update_vision(VisionSample(start + 0.1, True, 0.0, 0.0), start + 0.1)

    def test_eeg_controls_forward_when_blink_mode_is_off(self):
        controller, sender = self.make_controller()
        controller.update_eeg(True, 0.0)
        controller.tick(1.0)
        self.assertEqual(controller.throttle, "FORWARD")
        self.assertEqual(sender.commands[-1], ("FORWARD", "CENTER"))

    def test_double_blink_is_ignored_when_feature_is_off(self):
        controller, _ = self.make_controller()
        self.blink(controller, 0.1)
        self.blink(controller, 0.4)
        controller.tick(0.6)
        self.assertEqual(controller.throttle, "STOP")

    def test_enabled_double_blink_toggles_forward_and_stop(self):
        controller, sender = self.make_controller(blink_control_enabled=True)
        self.blink(controller, 0.1)
        self.blink(controller, 0.4)
        controller.tick(0.6)
        self.assertEqual(controller.throttle, "FORWARD")

        self.blink(controller, 1.0)
        self.blink(controller, 1.3)
        controller.tick(1.5)
        self.assertEqual(controller.throttle, "STOP")
        self.assertEqual(sender.commands[-1], ("STOP", "CENTER"))

    def test_mode_switch_stops_and_requires_fresh_command(self):
        controller, sender = self.make_controller()
        controller.update_eeg(True, 0.0)
        controller.tick(1.0)
        controller.set_blink_control(True, 1.1)
        self.assertEqual(controller.throttle, "STOP")
        self.assertEqual(sender.commands[-1], ("STOP", "CENTER"))

    def test_eye_steers_while_eeg_moves_forward(self):
        controller, sender = self.make_controller()
        controller.update_eeg(True, 0.0)
        controller.tick(1.0)
        controller.update_vision(VisionSample(1.1, True, -0.3, 0.0), 1.1)
        controller.tick(1.1)
        self.assertEqual(controller.steering, "LEFT")
        self.assertEqual(sender.commands[-1], ("FORWARD", "LEFT"))

    def test_face_loss_stops_both_modes(self):
        controller, sender = self.make_controller(blink_control_enabled=True)
        controller.blink_driving = True
        controller.tick(0.1)
        controller.update_vision(VisionSample(0.2, False), 0.2)
        controller.update_vision(VisionSample(0.51, False), 0.51)
        controller.tick(0.51)
        self.assertEqual(controller.throttle, "STOP")
        self.assertEqual(sender.commands[-1], ("STOP", "CENTER"))


class SteeringFilterTests(unittest.TestCase):
    def test_outlier_does_not_defeat_median_filter(self):
        steering = FilteredSteering(
            Calibration(-0.1, 0.1, 0.5),
            median_window=5,
            hold_frames=2,
            ema_alpha=1.0,
        )
        for score in (0.0, 0.0, -0.5, 0.0, 0.0):
            result = steering.update(score)
        self.assertEqual(result, "CENTER")


class PlotSnapshotTests(unittest.TestCase):
    def test_plot_snapshot_does_not_share_mutable_reader_history(self):
        class FakeDetector:
            pass

        reader = EEGReader("unused", 9600, FakeDetector())
        reader.state.times.append(1.0)
        reader.state.attention.append(55.0)
        snapshot = reader.plot_snapshot()
        reader.state.attention.append(99.0)

        self.assertEqual(snapshot["attention"], [55.0])


if __name__ == "__main__":
    unittest.main()
