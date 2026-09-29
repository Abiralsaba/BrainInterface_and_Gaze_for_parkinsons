import unittest

from calibrate_eeg import Sample, analyze


def samples(phase, attentions, quality=0):
    return [Sample(phase, index, quality, value, 20, {}) for index, value in enumerate(attentions)]


class CalibrationAnalysisTests(unittest.TestCase):
    def test_accepts_clean_separated_trial(self):
        report = analyze(samples("focus", [65, 70, 72, 68, 75]), samples("rest", [25, 30, 35, 28, 32]))
        self.assertTrue(report["valid"])
        self.assertGreater(report["attention_threshold"], 40)

    def test_rejects_missing_contact(self):
        focus = samples("focus", [70, 72, 75, 74, 73])
        rest = samples("rest", [20, 25, 30, 22, 27], quality=200)
        report = analyze(focus, rest)
        self.assertFalse(report["valid"])
        self.assertTrue(any("contact" in reason for reason in report["rejection_reasons"]))

    def test_accepts_minor_contact_noise(self):
        focus = samples("focus", [65, 70, 72, 68, 75], quality=12)
        rest = samples("rest", [25, 30, 35, 28, 32], quality=7)
        report = analyze(focus, rest, max_signal_quality=25)
        self.assertTrue(report["valid"])

    def test_rejects_overlapping_attention(self):
        report = analyze(samples("focus", [50, 51, 49, 52, 48]), samples("rest", [49, 50, 48, 51, 47]))
        self.assertFalse(report["valid"])
        self.assertTrue(any("separation" in reason for reason in report["rejection_reasons"]))


if __name__ == "__main__":
    unittest.main()
