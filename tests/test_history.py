import contextlib
import copy
import json
import pathlib
import sqlite3
import tempfile
import unittest
import uuid

from engineer.analysis import Engineer
from tests.test_analysis import frame


class HistoryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = pathlib.Path(self.temp.name)
        self.engineer = Engineer(self.path)

    def tearDown(self):
        self.engineer.close()
        self.temp.cleanup()

    def arm(self, car="test_gt3", source="live"):
        for i in range(70, 100):
            self.engineer.update(frame(0, i / 100, car_id=car, source=source))
        self.engineer.update(frame(1, 0, car_id=car, source=source))
        return self.engineer.snapshot()["session"]["id"]

    def lap(self, count, duration=60, issue=None, **overrides):
        for i in range(1, 100):
            values = dict(overrides)
            if issue and i == 50:
                values.update(issue)
            self.engineer.update(frame(count, i / 100, duration, **values))
        self.engineer.update(frame(count + 1, 0, duration, **overrides))
        return self.engineer.snapshot()["laps"][0]["id"]

    def three_laps(self):
        session = self.arm()
        return session, [self.lap(1, 60), self.lap(2, 58), self.lap(3, 62)]

    def test_history_survives_process_restart_without_becoming_live_reference(self):
        session, ids = self.three_laps()
        self.engineer.close()
        self.engineer = Engineer(self.path)
        sessions = self.engineer.list_sessions()
        self.assertEqual(len(sessions), 1)
        self.assertEqual(sessions[0]["id"], session)
        self.assertEqual(sessions[0]["lap_count"], 3)
        self.assertEqual(sessions[0]["best_lap_ms"], 58000)
        self.assertEqual([lap["id"] for lap in self.engineer.session_detail(session)["laps"]], ids)
        self.assertIsNone(self.engineer.snapshot()["reference"])
        self.assertFalse(sessions[0]["active"])

    def test_complete_session_history_is_not_the_last_thirty_live_laps(self):
        session = self.arm()
        original = self.lap(1)
        row = self.engineer._db.execute("SELECT * FROM laps WHERE id=?", (original,)).fetchone()
        for count in range(2, 39):
            ident = str(uuid.uuid4())
            summary = json.loads(row["summary"])
            summary.update(id=ident, lap_count=count, lap_number=count + 1)
            self.engineer._db.execute(
                "INSERT INTO laps (id,session_id,lap_count,duration_ms,clean,reason,summary,trace,metadata) "
                "VALUES (?,?,?,?,?,?,?,?,?)", (ident, session, count, row["duration_ms"], 1,
                    row["reason"], json.dumps(summary), row["trace"], row["metadata"]),
            )
        self.engineer._db.commit()
        detail = self.engineer.session_detail(session)
        self.assertEqual(len(detail["laps"]), 38)
        self.assertEqual(detail["session"]["lap_count"], 38)
        self.assertEqual(detail["laps"][0]["id"], original)
        self.assertEqual(detail["laps"][-1]["lap_number"], 39)

    def test_delete_best_rebuilds_reference_corners_and_fuel_and_restore_reverses(self):
        session, ids = self.three_laps()
        before = self.engineer.snapshot()
        self.assertEqual(before["reference"]["id"], ids[1])
        self.assertEqual(before["fuel"]["samples"], 3)
        self.assertTrue(before["coaching"]["comparison_available"])
        removed = self.engineer.delete_lap(ids[1])
        after = self.engineer.snapshot()
        self.assertTrue(removed["deleted"])
        self.assertIsNotNone(removed["deleted_at"])
        self.assertEqual(after["reference"]["id"], ids[0])
        self.assertEqual(after["reference"]["lap_time_ms"], 60000)
        self.assertEqual(after["fuel"]["samples"], 2)
        self.assertEqual(after["session"]["clean_laps"], 2)
        self.assertEqual({lap["id"] for lap in after["laps"]}, {ids[0], ids[2]})
        self.assertTrue(all(m["kind"] == "info" for m in after["messages"]))
        restored = self.engineer.restore_lap(ids[1])
        final = self.engineer.snapshot()
        self.assertFalse(restored["deleted"])
        self.assertIsNone(restored["deleted_at"])
        self.assertEqual(final["reference"]["id"], ids[1])
        self.assertEqual(final["fuel"]["samples"], 3)
        self.assertEqual(len(self.engineer.session_detail(session)["laps"]), 3)

    def test_only_one_remaining_lap_is_baseline_without_false_comparison(self):
        session = self.arm()
        first, second = self.lap(1), self.lap(2, 59)
        self.engineer.delete_lap(second)
        state = self.engineer.snapshot()
        self.assertEqual(state["reference"]["id"], first)
        self.assertFalse(state["coaching"]["comparison_available"])
        stored = self.engineer.analyze_stored(session, first)
        self.assertIsNone(stored["reference"])
        self.assertFalse(stored["comparison_available"])
        self.assertEqual(stored["corners"], [])

    def test_deleting_all_clean_laps_clears_best_and_fuel_without_erasing_frames(self):
        session, ids = self.three_laps()
        frames_before = self.engineer._db.execute("SELECT COUNT(*) FROM frames WHERE session_id=?", (session,)).fetchone()[0]
        for ident in ids:
            self.engineer.delete_lap(ident)
        state = self.engineer.snapshot()
        self.assertIsNone(state["reference"])
        self.assertEqual(state["corners"], [])
        self.assertFalse(state["coaching"]["comparison_available"])
        self.assertIsNone(state["fuel"]["consumption_l_per_lap"])
        self.assertEqual(self.engineer.session_detail(session)["laps"], [])
        self.assertEqual(len(self.engineer.session_detail(session, include_deleted=True)["laps"]), 3)
        self.assertEqual(self.engineer.list_sessions()[0]["deleted_laps"], 3)
        self.assertEqual(self.engineer.list_sessions()[0]["lap_count"], 0)
        self.assertEqual(self.engineer.list_sessions(True)[0]["lap_count"], 3)
        self.assertEqual(self.engineer._db.execute("SELECT COUNT(*) FROM frames WHERE session_id=?", (session,)).fetchone()[0], frames_before)

    def test_stored_analysis_chooses_distinct_clean_reference_and_never_changes_live_state(self):
        session, ids = self.three_laps()
        before = copy.deepcopy(self.engineer.snapshot())
        result = self.engineer.analyze_stored(session, ids[2])
        self.assertEqual(result["lap"]["id"], ids[2])
        self.assertEqual(result["reference"]["id"], ids[1])
        self.assertEqual(result["coaching"]["delta_s"], 4)
        self.assertTrue(result["comparison_available"])
        self.assertEqual(len(result["corners"]), 2)
        self.assertEqual(self.engineer.snapshot(), before)
        with self.assertRaises(ValueError):
            self.engineer.analyze_stored(session, ids[1], ids[1])

    def test_cross_session_deleted_and_invalid_references_are_refused(self):
        first_session, ids = self.three_laps()
        invalid = self.lap(4, issue={"wheels_out": 4})
        other_session = self.arm(car="formula_mod")
        other = self.lap(1, car_id="formula_mod")
        with self.assertRaises(ValueError):
            self.engineer.analyze_stored(first_session, ids[0], other)
        with self.assertRaises(ValueError):
            self.engineer.analyze_stored(other_session, ids[0])
        with self.assertRaises(ValueError):
            self.engineer.analyze_stored(first_session, ids[0], invalid)
        self.engineer.delete_lap(ids[1])
        with self.assertRaises(ValueError):
            self.engineer.analyze_stored(first_session, ids[0], ids[1])
        with self.assertRaises(ValueError):
            self.engineer.analyze_stored(first_session, ids[1])

    def test_invalid_lap_can_be_inspected_without_automatic_corner_coaching(self):
        session = self.arm()
        clean = self.lap(1)
        invalid = self.lap(2, issue={"wheels_out": 4})
        result = self.engineer.analyze_stored(session, invalid, clean)
        self.assertGreater(len(result["traces"]["current"]), 20)
        self.assertFalse(result["comparison_available"])
        self.assertEqual(result["corners"], [])
        self.assertTrue(any("excluded" in note.lower() for note in result["notes"]))

    def test_per_lap_conditions_are_saved_and_incompatible_reference_is_refused(self):
        session = self.arm()
        first = self.lap(1)
        self.lap(2, road_c=45)
        hot = self.lap(3, road_c=45)
        with self.assertRaises(ValueError):
            self.engineer.analyze_stored(session, hot, first)
        result = self.engineer.analyze_stored(session, hot)
        self.assertNotEqual((result.get("reference") or {}).get("id"), first)
        self.assertEqual(result["lap"]["metadata"]["road_c"], 45)

    def test_three_clean_laps_explain_fuel_rejections_without_loosening_comparison(self):
        session, ids = self.three_laps()
        for ident, fuel in zip(ids, (29.5, 27.766, 22.07)):
            stored = self.engineer._stored_lap(ident, session)
            metadata = dict(stored["meta"], fuel_l=fuel)
            self.engineer._db.execute("UPDATE laps SET metadata=? WHERE id=?", (json.dumps(metadata), ident))
        result = self.engineer.analyze_stored(session, ids[2])
        self.assertIsNone(result["reference"])
        self.assertFalse(result["comparison_available"])
        self.assertEqual(2, len(result["reference_rejections"]))
        nearest = next(item for item in result["reference_rejections"] if item["candidate_id"] == ids[1])
        self.assertEqual("fuel_l", nearest["condition"])
        self.assertAlmostEqual(5.696, nearest["difference"])
        self.assertEqual(5, nearest["tolerance"])
        self.assertEqual("L", nearest["unit"])
        self.assertIn("5.70 L more fuel", nearest["message"])
        self.assertIn(nearest["message"], result["notes"])
        # The older pair remains eligible; three clean laps do not guarantee
        # that every possible pair has comparable starting conditions.
        older = self.engineer.analyze_stored(session, ids[1])
        self.assertTrue(older["comparison_available"])
        self.assertEqual(ids[0], older["reference"]["id"])
        with self.assertRaisesRegex(ValueError, "5.70 L more fuel"):
            self.engineer.analyze_stored(session, ids[2], ids[1])

    def test_cold_session_deletion_does_not_change_current_live_reference_or_messages(self):
        old_session, ids = self.three_laps()
        new_session = self.arm(car="formula_mod")
        self.lap(1, car_id="formula_mod")
        before = self.engineer.snapshot()
        self.engineer.delete_lap(ids[1])
        self.assertEqual(self.engineer.snapshot(), before)
        self.assertNotEqual(old_session, new_session)

    def test_full_export_keeps_every_trace_point_and_original_frames(self):
        session = self.arm()
        for i in range(1, 600):
            self.engineer.update(frame(1, i / 600))
        self.engineer.update(frame(2, 0))
        ident = self.engineer.snapshot()["laps"][0]["id"]
        exported = self.engineer.export_session(session)
        self.assertEqual(len(exported["laps"]), 1)
        self.assertGreater(len(exported["laps"][0]["trace"]), 280)
        self.assertLessEqual(len(self.engineer.analyze_stored(session, ident)["traces"]["current"]), 281)
        self.assertGreater(len(exported["frames"]), 280)
        self.engineer.delete_lap(ident)
        hidden = self.engineer.export_session(session)
        self.assertEqual(hidden["laps"], [])
        self.assertEqual(len(hidden["frames"]), len(exported["frames"]))
        self.assertEqual(len(self.engineer.export_session(session, True)["laps"]), 1)
        json.dumps(exported, allow_nan=False)

    def test_unknown_identifiers_are_value_errors_and_do_not_modify_storage(self):
        session, ids = self.three_laps()
        for action in (
            lambda: self.engineer.session_detail("unknown"),
            lambda: self.engineer.analyze_stored(session, "unknown"),
            lambda: self.engineer.delete_lap("unknown"),
            lambda: self.engineer.restore_lap("unknown"),
            lambda: self.engineer.session_detail("' OR 1=1 --"),
        ):
            with self.assertRaises(ValueError):
                action()
        self.assertEqual(len(self.engineer.session_detail(session)["laps"]), 3)

    def test_old_database_is_migrated_without_losing_rows(self):
        legacy = self.path / "legacy"
        legacy.mkdir()
        database = legacy / "sessions.sqlite3"
        with contextlib.closing(sqlite3.connect(database)) as db:
            db.executescript("""
                CREATE TABLE sessions(id TEXT PRIMARY KEY,started REAL NOT NULL,source TEXT,car_id TEXT,track_id TEXT,layout TEXT,compound TEXT,metadata TEXT NOT NULL);
                CREATE TABLE laps(id TEXT PRIMARY KEY,session_id TEXT NOT NULL,lap_count INTEGER,duration_ms REAL,clean INTEGER NOT NULL,reason TEXT,summary TEXT NOT NULL,trace TEXT NOT NULL);
            """)
            db.execute("INSERT INTO sessions VALUES(?,?,?,?,?,?,?,?)", ("old-session", 1.0, "live", "gt3", "spa", "gp", "Medium", "{}"))
            db.execute("INSERT INTO laps VALUES(?,?,?,?,?,?,?,?)", ("old-lap", "old-session", 1, 60000, 1, "Observed clean", "{}", "[]"))
            db.commit()
        old = Engineer(legacy)
        try:
            self.assertEqual(old.session_detail("old-session")["laps"][0]["id"], "old-lap")
            columns = {row[1] for row in old._db.execute("PRAGMA table_info(laps)")}
            self.assertTrue({"deleted_at", "metadata"}.issubset(columns))
            old.delete_lap("old-lap")
            self.assertEqual(old.session_detail("old-session")["laps"], [])
            old.restore_lap("old-lap")
            self.assertEqual(len(old.session_detail("old-session")["laps"]), 1)
        finally:
            old.close()


if __name__ == "__main__":
    unittest.main()
