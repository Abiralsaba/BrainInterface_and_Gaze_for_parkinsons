import unittest

from thinkgear_eeg import (
    BAND_NAMES,
    EEGUpdate,
    FocusDetector,
    FocusDriveController,
    ThinkGearPacketParser,
    build_packet,
    decode_payload,
)


class PacketParserTests(unittest.TestCase):
    def test_fragmented_packet(self):
        wire = build_packet(bytes((0x02, 0, 0x04, 61, 0x05, 37)))
        parser = ThinkGearPacketParser()
        self.assertEqual(parser.feed(wire[:4]), [])
        packets = parser.feed(wire[4:])
        self.assertEqual(len(packets), 1)
        update = decode_payload(packets[0].payload)
        self.assertEqual(update.signal_quality, 0)
        self.assertEqual(update.attention, 61)
        self.assertEqual(update.meditation, 37)

    def test_resynchronizes_after_noise_and_bad_checksum(self):
        good = build_packet(bytes((0x04, 42)))
        bad = good[:-1] + bytes((good[-1] ^ 0x01,))
        parser = ThinkGearPacketParser()
        packets = parser.feed(b"noise" + bad + good)
        self.assertEqual(len(packets), 1)
        self.assertEqual(decode_payload(packets[0].payload).attention, 42)
        self.assertEqual(parser.stats.checksum_errors, 1)
        self.assertGreaterEqual(parser.stats.noise_bytes, 5)

    def test_raw_signed_sample(self):
        update = decode_payload(bytes((0x80, 0x02, 0xFF, 0x9C)))
        self.assertEqual(update.raw_samples, [-100])

    def test_all_eeg_bands_are_big_endian_24_bit(self):
        values = [1, 256, 65536, 0x123456, 9, 10, 11, 0xFFFFFF]
        encoded = b"".join(value.to_bytes(3, "big") for value in values)
        update = decode_payload(bytes((0x83, 24)) + encoded)
        self.assertEqual(update.bands, dict(zip(BAND_NAMES, values)))

    def test_malformed_value_length_is_reported(self):
        update = decode_payload(bytes((0x83, 24, 1, 2)))
        self.assertTrue(update.malformed)


class FocusDetectorTests(unittest.TestCase):
    def test_requires_majority_of_three_good_samples(self):
        detector = FocusDetector(threshold=53.0, max_signal_quality=25)
        self.assertFalse(detector.update(EEGUpdate(signal_quality=0, attention=60)))
        self.assertFalse(detector.update(EEGUpdate(signal_quality=0, attention=40)))
        self.assertTrue(detector.update(EEGUpdate(signal_quality=0, attention=70)))

    def test_bad_contact_clears_detection(self):
        detector = FocusDetector(threshold=53.0, max_signal_quality=25, window=1)
        self.assertTrue(detector.update(EEGUpdate(signal_quality=0, attention=60)))
        self.assertIsNone(detector.update(EEGUpdate(signal_quality=200, attention=80)))


class FakeCarSender:
    def __init__(self):
        self.commands = []
        self.closed = False

    def send(self, throttle, steering):
        self.commands.append((throttle, steering))

    def close(self):
        self.closed = True


class FocusDriveControllerTests(unittest.TestCase):
    def test_requires_three_continuous_seconds_then_moves(self):
        sender = FakeCarSender()
        controller = FocusDriveController(sender, hold_seconds=3.0, now=0.0)
        controller.update(True, 0.0)
        controller.update(True, 2.9)
        self.assertEqual(controller.throttle, "STOP")
        controller.update(True, 3.0)
        self.assertEqual(controller.throttle, "FORWARD")
        self.assertEqual(sender.commands[-1], ("FORWARD", "CENTER"))

    def test_focus_loss_stops_immediately(self):
        sender = FakeCarSender()
        controller = FocusDriveController(sender, hold_seconds=1.0, now=0.0)
        controller.update(True, 0.0)
        controller.update(True, 1.0)
        controller.update(False, 1.01)
        self.assertEqual(controller.throttle, "STOP")
        self.assertEqual(sender.commands[-1], ("STOP", "CENTER"))

    def test_stale_eeg_stops_and_close_sends_stop(self):
        sender = FakeCarSender()
        controller = FocusDriveController(
            sender,
            hold_seconds=1.0,
            packet_timeout=1.5,
            now=0.0,
        )
        controller.update(True, 0.0)
        controller.update(True, 1.0)
        controller.tick(2.6)
        self.assertEqual(controller.throttle, "STOP")
        controller.close(2.7)
        self.assertTrue(sender.closed)
        self.assertEqual(sender.commands[-1], ("STOP", "CENTER"))


if __name__ == "__main__":
    unittest.main()
