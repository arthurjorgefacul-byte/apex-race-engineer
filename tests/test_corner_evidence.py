import copy
import unittest

from engineer.analysis import Engineer, _HistoryAccess
from tests.test_analysis import frame


class CornerEvidenceTests(unittest.TestCase):
    def test_sample_preserves_actual_gear_and_rpm_and_missing_is_unavailable(self):
        sample = Engineer._sample(frame(1, .2, gear="3", rpm=7500))
        self.assertEqual(sample["gear"], "3")
        self.assertEqual(sample["rpm"], 7500)
        missing = Engineer._sample(frame(1, .2, gear=None, rpm=float("nan")))
        self.assertIsNone(missing["gear"])
        self.assertIsNone(missing["rpm"])

    def test_gear_uses_nearest_actual_sample_and_never_interpolates(self):
        trace = [{"x": .20, "gear": "3"}, {"x": .21, "gear": "2"}]
        self.assertEqual(_HistoryAccess._categorical_at(trace, .204, "gear"), "3")
        self.assertEqual(_HistoryAccess._categorical_at(trace, .208, "gear"), "2")
        self.assertIsNone(_HistoryAccess._categorical_at(trace, .3, "gear"))
        self.assertIsNone(_HistoryAccess._categorical_at([{"x": .2}], .2, "gear"))

    def test_brake_release_and_deceleration_are_observed_and_optional(self):
        trace = [{"x": .1 + i * .01, "t": i, "speed": 100 - i * 10,
                  "brake": .5 if i < 2 else 0} for i in range(4)]
        evidence = _HistoryAccess._braking_evidence(trace, .1, .14)
        self.assertAlmostEqual(evidence["release_pos"], .12)
        self.assertAlmostEqual(evidence["mean_deceleration_mps2"], 2.778, places=3)
        self.assertIsNone(_HistoryAccess._braking_evidence(trace, None, .14)["mean_deceleration_mps2"])

    def test_corner_comparison_old_trace_has_no_invented_gear(self):
        trace = []
        for i in range(101):
            x = i / 100
            trace.append({"x": x, "t": x * 60, "speed": 80 if .2 <= x <= .3 else 120,
                          "brake": .5 if .15 <= x <= .19 else 0,
                          "gas": .4 if .2 <= x <= .3 else 1, "steer": .2 if .2 <= x <= .3 else 0})
        old = {"trace": trace, "meta": {"track_length_m": 2000}, "summary": {"lap_number": 1}}
        current = copy.deepcopy(old)
        for point in current["trace"]:
            point.update(gear="3", rpm=7000)
        result = _HistoryAccess()._compare_corners(current, old)[0]
        self.assertEqual(result["entry_gear"], "3")
        self.assertIsNone(result["reference_entry_gear"])
        self.assertEqual(result["entry_rpm"], 7000)
        self.assertIsNone(result["reference_entry_rpm"])
        self.assertIn("does not measure tyre grip", result["braking_evidence_note"])


if __name__ == "__main__":
    unittest.main()
