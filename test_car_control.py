import threading
import unittest
from types import SimpleNamespace
from unittest import mock

from car_transport import CarCommandSender
from camera_control import Calibration
from eye_car_control import EyeCarController
from hand_car_control import HandCarController


class FakeSender:
    def __init__(self):
        self.commands = []

    def send(self, throttle, steering):
        self.commands.append((throttle, steering))
        return len(self.commands)


def hand_landmarks(extended):
    points = [SimpleNamespace(x=0.5, y=0.5) for _ in range(21)]
    # Right-hand thumb is extended when tip.x < IP.x.
    points[3].x = 0.5
    points[4].x = 0.2 if 0 in extended else 0.8
    for finger, (tip_index, pip_index) in enumerate(
        ((8, 6), (12, 10), (16, 14), (20, 18)), start=1
    ):
        points[pip_index].y = 0.5
        points[tip_index].y = 0.2 if finger in extended else 0.8
    return points


class HandSafetyTests(unittest.TestCase):
    def setUp(self):
        self.sender = FakeSender()
        self.controller = HandCarController(
            hold_frames=1,
            buffer_size=1,
            command_sender=self.sender,
        )

    def test_open_palm_stops_without_filter_delay(self):
        self.controller.update(hand_landmarks(set()), "Right", 0.0)
        self.assertEqual(self.controller.throttle, "FORWARD")

        self.controller.update(hand_landmarks({0, 1, 2, 3, 4}), "Right", 0.1)

        self.assertEqual(self.controller.throttle, "STOP")
        self.assertEqual(self.sender.commands[-1], ("STOP", "CENTER"))

    def test_unknown_gesture_does_not_preserve_forward(self):
        self.controller.update(hand_landmarks(set()), "Right", 0.0)
        self.controller.update(hand_landmarks({0, 1, 2}), "Right", 0.1)

        self.assertEqual(self.controller.throttle, "STOP")
        self.assertEqual(self.sender.commands[-1], ("STOP", "CENTER"))

    def test_hand_loss_stops_after_short_timeout(self):
        self.controller.update(hand_landmarks(set()), "Right", 0.0)
        self.controller.update(None, "Right", 0.1)
        self.assertEqual(self.controller.throttle, "FORWARD")

        self.controller.update(None, "Right", 0.41)

        self.assertEqual(self.controller.throttle, "STOP")
        self.assertEqual(self.sender.commands[-1], ("STOP", "CENTER"))


class EyeSafetyTests(unittest.TestCase):
    def test_face_loss_stops_after_short_timeout(self):
        sender = FakeSender()
        controller = EyeCarController(
            Calibration(-0.1, 0.1, 0.5),
            command_sender=sender,
        )
        controller.driving = True

        controller.update(None, 0.0, 1.0)
        self.assertTrue(controller.driving)
        controller.update(None, 0.0, 1.31)

        self.assertFalse(controller.driving)
        self.assertEqual(sender.commands[-1], ("STOP", "CENTER"))


class TransportTests(unittest.TestCase):
    def test_slow_network_discards_superseded_commands(self):
        paths = []
        first_seen = threading.Event()
        release_first = threading.Event()

        class FakeResponse:
            def __enter__(self):
                return self

            def __exit__(self, *args):
                pass

            def read(self, size):
                return b"OK"

        def slow_urlopen(url, timeout):
            paths.append(url)
            if len(paths) == 1:
                first_seen.set()
                release_first.wait(0.5)
            return FakeResponse()

        with mock.patch("car_transport.urllib.request.urlopen", slow_urlopen):
            sender = CarCommandSender("http://192.168.4.1", timeout=0.5)
            sender.send("FORWARD", "CENTER")
            self.assertTrue(first_seen.wait(0.5))
            sender.send("FORWARD", "LEFT")
            sender.send("STOP", "CENTER")
            release_first.set()
            sender.close()

        self.assertIn("throttle=FORWARD&steering=CENTER", paths[0])
        self.assertFalse(any("steering=LEFT" in path for path in paths))
        self.assertIn("throttle=STOP&steering=CENTER", paths[-1])


if __name__ == "__main__":
    unittest.main()
