import json
import contextlib
import math
import pathlib
import sqlite3
import tempfile
import unittest

from engineer.analysis import Engineer


def frame(count, position, duration=60.0, fuel=None, **overrides):
    # Internally consistent 1.8km synthetic course, with two corner groups.
    turn = ((0.2 <= position <= 0.29) or (0.6 <= position <= 0.71))
    sample = {
        "source": "live", "status": "live", "timestamp": 1000 + count * duration + position * duration,
        "car_id": "test_gt3", "track_id": "test_track", "layout": "gp",
        "driver": "Driver", "tyre_compound": "Medium", "packet_id": count * 1000 + round(position * 1000),
        "lap_count": count, "lap_time_ms": duration * position * 1000,
        "last_lap_ms": duration * 1000, "normalized_pos": position,
        "track_length_m": 1800.0, "speed_kmh": 75 if turn else 125,
        "gas": 0.35 if turn else 1.0, "brake": 0.6 if 0.15 <= position < 0.2 else 0,
        "steer": 0.2 if turn else 0, "acc_g": [0.8 if turn else 0, 0, 0],
        "fuel_l": 30 - count * 1.5 - position * 1.5 if fuel is None else fuel,
        "in_pit": False, "wheels_out": 0, "lap_valid": None,
        "road_c": 28.0, "ambient_c": 23.0, "surface_grip": 0.98,
        "tyre_core_c": [80.0, 80.0, 80.0, 80.0],
        "tyre_pressure_psi": [26.0, 26.1, 26.0, 26.1],
        "session_type": "practice", "session_total_laps": 0,
    }
    sample.update(overrides)
    return sample


class AnalysisTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.engineer = Engineer(pathlib.Path(self.temp.name))

    def tearDown(self):
        self.engineer.close()
        self.temp.cleanup()

    def arm(self, **overrides):
        for i in range(70, 100):
            self.engineer.update(frame(0, i / 100, **overrides))
        return self.engineer.update(frame(1, 0, **overrides))

    def lap(self, count, duration=60, issue=None, **overrides):
        for i in range(1, 100):
            values = dict(overrides)
            if issue and i == 50:
                values.update(issue)
            self.engineer.update(frame(count, i / 100, duration, **values))
        return self.engineer.update(frame(count + 1, 0, duration, **overrides))

    def test_first_partial_is_not_a_reference(self):
        snapshot = self.arm()
        self.assertIsNone(snapshot["reference"])
        self.assertEqual(snapshot["laps"], [])
        self.assertTrue(snapshot["session"]["collecting_complete_lap"])

    def test_clean_full_lap_records_reference_and_consumption(self):
        self.arm()
        result = self.lap(1)
        self.assertTrue(result["laps"][0]["clean"])
        self.assertEqual(result["reference"]["lap_time_s"], 60)
        self.assertEqual(result["fuel"]["consumption_l_per_lap"], 1.5)
        self.assertEqual(result["reference"]["scope"], "session")
        self.assertEqual(len(result["corners"]), 2)
        self.assertFalse(result["coaching"]["comparison_available"])
        with contextlib.closing(sqlite3.connect(self.engineer.database_path)) as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM laps WHERE clean=1").fetchone()[0], 1)
            self.assertGreater(db.execute("SELECT COUNT(*) FROM frames").fetchone()[0], 100)

    def test_off_track_lap_is_excluded_and_cannot_set_reference(self):
        self.arm()
        result = self.lap(1, issue={"wheels_out": 4})
        self.assertFalse(result["laps"][0]["clean"])
        self.assertIn("Off track", result["laps"][0]["reason"])
        self.assertIsNone(result["reference"])
        self.assertEqual(result["fuel"]["samples"], 0)

    def test_game_invalidity_and_pit_laps_are_excluded(self):
        self.arm()
        self.assertFalse(self.lap(1, issue={"lap_valid": False})["laps"][0]["clean"])
        result = self.lap(2, issue={"in_pit": True})
        self.assertFalse(result["laps"][0]["clean"])
        self.assertIn("Pit", result["laps"][0]["reason"])

    def test_faster_reference_and_measured_corner_deltas(self):
        self.arm()
        self.lap(1, 60)
        # Crossing just occurred at t=0; use the faster duration consistently.
        result = self.lap(2, 59)
        self.assertEqual(result["reference"]["lap_time_s"], 59)
        self.assertEqual(result["laps"][0]["delta_s"], -1)
        self.assertTrue(all(c["time_loss_s"] < 0 for c in result["corners"]))
        self.assertEqual(result["coaching"]["reference_lap_time_s"], 60)
        self.assertTrue(result["coaching"]["comparison_available"])
        self.assertEqual(result["coaching"]["comparison_lap_number"], 3)

    def test_missing_driver_input_rejects_lap_instead_of_inventing_zero(self):
        for channel in ("speed_kmh", "gas", "brake", "steer", "normalized_pos", "lap_time_ms"):
            with self.subTest(channel=channel):
                self.engineer.reset()
                self.arm()
                result = self.lap(1, issue={channel: None})
                self.assertFalse(result["laps"][0]["clean"])
                self.assertIn("unavailable", result["laps"][0]["reason"])
                self.assertIsNone(result["reference"])

    def test_reference_is_not_car_or_source_agnostic(self):
        self.arm()
        self.lap(1)
        changed = self.engineer.update(frame(2, 0.01, car_id="formula_mod"))
        self.assertIsNone(changed["reference"])
        self.assertEqual(changed["laps"], [])
        demo = self.engineer.update(frame(0, 0.3, source="demo"))
        self.assertEqual(demo["session"]["source"], "demo")
        self.assertIsNone(demo["reference"])

    def test_conditions_change_establishes_new_baseline(self):
        self.arm()
        self.lap(1)
        # Start metadata is captured at crossing. Change the whole following lap.
        self.lap(2, road_c=45)
        result = self.lap(3, road_c=45)
        self.assertIsNone(result["laps"][0]["delta_s"])
        self.assertIn("Conditions changed", result["coaching"]["summary"])

    def test_refuelling_does_not_become_negative_consumption(self):
        self.arm()
        result = self.lap(1, issue={"fuel_l": 80})
        self.assertEqual(result["fuel"]["samples"], 0)
        self.assertFalse(result["laps"][0]["clean"])
        self.assertIn("Refuelling", result["laps"][0]["reason"])

    def test_pause_does_not_record_fake_frames_or_end_laps(self):
        self.arm()
        self.engineer.update(frame(1, 0.2))
        with contextlib.closing(sqlite3.connect(self.engineer.database_path)) as db:
            before = db.execute("SELECT COUNT(*) FROM frames").fetchone()[0]
        result = self.engineer.update(frame(2, 0, status="paused"))
        self.assertFalse(result["session"]["recording"])
        self.assertEqual(result["laps"], [])
        with contextlib.closing(sqlite3.connect(self.engineer.database_path)) as db:
            self.assertEqual(before, db.execute("SELECT COUNT(*) FROM frames").fetchone()[0])

    def test_replay_requires_new_complete_lap(self):
        self.arm()
        self.engineer.update(frame(1, 0.3, status="replay"))
        result = self.engineer.update(frame(1, 0.4))
        self.assertFalse(result["session"]["collecting_complete_lap"])

    def test_teleport_cannot_set_reference(self):
        self.arm()
        for i in range(1, 100):
            position = i / 100
            if i == 50:
                position = 0.85
            self.engineer.update(frame(1, position))
        result = self.engineer.update(frame(2, 0))
        self.assertFalse(result["laps"][0]["clean"])

    def test_no_nan_or_default_tyre_target(self):
        self.engineer.update(frame(0, 0.3, speed_kmh=math.nan, fuel_l=math.inf))
        result = self.engineer.snapshot()
        json.dumps(result, allow_nan=False)
        self.assertEqual(result["setup"][0]["kind"], "needs_profile")
        self.engineer.configure({"target_pressure_psi": 27.5, "fuel_reserve_l": 3, "focus": "stability"})
        self.assertEqual(self.engineer.snapshot()["setup"][0]["kind"], "measured")

    def test_fuel_to_finish_only_in_race_and_respects_reserve(self):
        self.arm(session_type="race", session_total_laps=10)
        result = self.lap(1, session_type="race", session_total_laps=10)
        self.assertEqual(result["fuel"]["fuel_to_finish_l"], 14)
        self.assertEqual(result["fuel"]["reserve_l"], 2)
        self.assertFalse(result["fuel"]["finish_estimated"])

    def test_reset_preserves_database_and_discards_reference(self):
        self.arm()
        self.lap(1)
        self.engineer.reset()
        self.assertIsNone(self.engineer.snapshot()["reference"])
        with contextlib.closing(sqlite3.connect(self.engineer.database_path)) as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM laps").fetchone()[0], 1)


if __name__ == "__main__":
    unittest.main()
