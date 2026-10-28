"""Finish-line synchronization regressions, including captured AC packets."""
import json
import pathlib
import tempfile
import unittest

from engineer.analysis import Engineer
from tests.test_analysis import frame


class LapBoundaryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.engineer = Engineer(pathlib.Path(self.temp.name))

    def tearDown(self):
        self.engineer.close()
        self.temp.cleanup()

    def arm_and_fill(self):
        self.engineer.update(frame(0, .99))
        self.engineer.update(frame(1, 0))
        for i in range(1, 100):
            self.engineer.update(frame(1, i / 100))

    def test_actual_split_position_timer_counter_capture_completes_lap(self):
        capture = json.loads((pathlib.Path(__file__).parent / "fixtures/ac-boundary-split.json").read_text())
        for ai in (False, True):
            with self.subTest(ai_controlled=ai):
                self.engineer.reset()
                start = capture[0]["timestamp"] - capture[0]["lap_time_ms"] / 1000
                self.engineer.update(frame(2, .99, timestamp=start - .1, is_ai_controlled=ai))
                self.engineer.update(frame(3, 0, timestamp=start, is_ai_controlled=ai))
                slope = capture[0]["lap_time_ms"] / capture[0]["normalized_pos"]
                for i in range(1, 94):
                    elapsed = i / 100 * slope
                    self.engineer.update(frame(3, i / 100, lap_time_ms=elapsed,
                        timestamp=start + elapsed / 1000, is_ai_controlled=ai))
                for packet in capture:
                    current = frame(packet["lap_count"], packet["normalized_pos"], is_ai_controlled=ai)
                    current.update(packet)
                    result = self.engineer.update(current)
                self.assertEqual(len(result["laps"]), 1)
                self.assertEqual(result["laps"][0]["lap_count"], 3)
                self.assertEqual(result["laps"][0]["lap_time_ms"], 91284)
                self.assertEqual(result["laps"][0]["clean"], not ai)
                self.assertTrue(result["session"]["collecting_complete_lap"])
                if ai:
                    self.assertIn("AI-controlled", result["laps"][0]["reason"])
                    self.assertIsNone(result["reference"])
                else:
                    self.assertIsNotNone(result["reference"])

    def test_counter_may_arrive_before_position_wrap(self):
        self.arm_and_fill()
        self.engineer.update(frame(2, .999, lap_time_ms=0, timestamp=2000))
        result = self.engineer.update(frame(2, .001, lap_time_ms=60, timestamp=2000.06))
        self.assertEqual(len(result["laps"]), 1)
        self.assertTrue(result["laps"][0]["clean"])
        self.assertEqual(result["laps"][0]["lap_count"], 1)

    def test_first_out_lap_without_counter_increment_arms_next_hot_lap(self):
        self.engineer.update(frame(0, .999938488, lap_time_ms=129036, timestamp=1000, is_ai_controlled=True))
        self.engineer.update(frame(0, .000625008, lap_time_ms=15, timestamp=1000.06, is_ai_controlled=True))
        result = self.engineer.update(frame(0, .009, lap_time_ms=555, timestamp=1000.60, is_ai_controlled=True))
        self.assertTrue(result["session"]["collecting_complete_lap"])
        self.assertEqual(result["laps"], [])
        for i in range(2, 100):
            self.engineer.update(frame(0, i / 100, duration=92.760, timestamp=1000.06 + i / 100 * 92.760, is_ai_controlled=True))
        self.engineer.update(frame(0, .999375343, lap_time_ms=92718, timestamp=1092.77, is_ai_controlled=True))
        self.engineer.update(frame(0, .000497323, lap_time_ms=6, last_lap_ms=92760, timestamp=1092.82, is_ai_controlled=True))
        result = self.engineer.update(frame(1, .001362140, lap_time_ms=66, last_lap_ms=92760, timestamp=1092.88, is_ai_controlled=True))
        self.assertEqual(len(result["laps"]), 1)
        self.assertEqual(result["laps"][0]["lap_count"], 0)
        self.assertFalse(result["laps"][0]["clean"])
        self.assertIn("AI-controlled", result["laps"][0]["reason"])
        self.assertNotIn("discontinuity", result["laps"][0]["reason"])
        self.assertIsNone(result["reference"])

    def test_counter_without_timer_reset_is_never_a_clean_lap(self):
        self.arm_and_fill()
        self.engineer.update(frame(2, 0, lap_time_ms=60050, timestamp=2000))
        result = self.engineer.update(frame(2, .005, lap_time_ms=60700, timestamp=2000.65))
        self.assertFalse(result["session"]["collecting_complete_lap"])
        self.assertIsNone(result["reference"])
        self.assertFalse(any(lap["clean"] for lap in result["laps"]))

    def test_delayed_counter_outside_bound_cannot_validate_teleport(self):
        self.arm_and_fill()
        self.engineer.update(frame(1, .001, lap_time_ms=10, timestamp=2000))
        result = self.engineer.update(frame(2, .005, lap_time_ms=700, timestamp=2000.7))
        self.assertFalse(result["session"]["collecting_complete_lap"])
        self.assertIsNone(result["reference"])
        self.assertFalse(any(lap["clean"] for lap in result["laps"]))
        # A rejected synchronization attempt must not poison later laps.
        for i in range(1, 100):
            self.engineer.update(frame(2, i / 100))
        self.engineer.update(frame(3, 0))
        for i in range(1, 100):
            self.engineer.update(frame(3, i / 100))
        result = self.engineer.update(frame(4, 0))
        self.assertTrue(result["laps"][0]["clean"])
        self.assertIsNotNone(result["reference"])

    def test_counter_increment_away_from_line_does_not_become_reference(self):
        self.engineer.update(frame(0, .99))
        self.engineer.update(frame(1, 0))
        self.engineer.update(frame(1, .49))
        result = self.engineer.update(frame(2, .50, lap_time_ms=0))
        self.assertFalse(result["session"]["collecting_complete_lap"])
        self.assertIsNone(result["reference"])

    def test_recorded_frames_preserve_ai_control_provenance(self):
        self.engineer.update(frame(0, .5, is_ai_controlled=True))
        row = self.engineer._db.execute("SELECT payload FROM frames LIMIT 1").fetchone()
        self.assertTrue(json.loads(row[0])["is_ai_controlled"])


if __name__ == "__main__":
    unittest.main()
