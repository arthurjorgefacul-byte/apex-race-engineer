"""Saved-data reads remain consistent without stalling the live recorder."""

import json
import pathlib
import sqlite3
import tempfile
import threading
import tracemalloc
import unittest
from unittest import mock

from engineer.analysis import Engineer, StoredSessionView
from tests.test_analysis import frame


class HistoryStreamingTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = pathlib.Path(self.temp.name)
        self.engineer = Engineer(self.path)
        self.engineer.update(frame(0, .99))
        self.engineer.update(frame(1, 0))
        self.ids = []
        for count, duration in ((1, 60), (2, 59)):
            for i in range(1, 100):
                self.engineer.update(frame(count, i / 100, duration))
            state = self.engineer.update(frame(count + 1, 0, duration))
            self.ids.append(state["laps"][0]["id"])
        self.session = self.engineer.snapshot()["session"]["id"]

    def tearDown(self):
        self.engineer.close()
        self.temp.cleanup()

    def test_streaming_export_matches_complete_export_with_and_without_removed_laps(self):
        self.engineer.delete_lap(self.ids[1])
        with self.engineer.open_history() as history:
            for include_deleted in (False, True):
                with self.subTest(include_deleted=include_deleted):
                    expected = history.export_session(self.session, include_deleted)
                    actual = json.loads(b"".join(history.iter_export_session_json(self.session, include_deleted)))
                    self.assertEqual(actual, expected)
                    self.assertEqual(len(actual["laps"]), 2 if include_deleted else 1)
                    self.assertGreater(len(actual["frames"]), 100)
                    self.assertEqual(actual["includes_deleted"], include_deleted)

    def test_readonly_view_has_its_own_connection_lock_and_no_live_engine_initialization(self):
        before = self.engineer.snapshot()
        with mock.patch.object(Engineer, "__init__", side_effect=AssertionError("Do not construct a live recorder")):
            with self.engineer.open_history() as history:
                self.assertIsInstance(history, StoredSessionView)
                self.assertNotIsInstance(history, Engineer)
                self.assertIsNot(history._db, self.engineer._db)
                self.assertIsNot(history._lock, self.engineer._lock)
                self.assertEqual(history._db.execute("PRAGMA query_only").fetchone()[0], 1)
                with self.assertRaises(sqlite3.OperationalError):
                    history._db.execute("DELETE FROM frames")
                self.assertEqual(len(history.session_detail(self.session)["laps"]), 2)
                saved = history.analyze_stored(self.session, self.ids[0], self.ids[1])
                self.assertTrue(saved["comparison_available"])
        self.assertEqual(self.engineer.snapshot(), before)

    def test_wal_snapshot_is_consistent_while_live_recording_and_lap_edits_continue(self):
        done = threading.Event()
        errors = []

        def write_live():
            try:
                self.engineer.delete_lap(self.ids[0])
                self.engineer.update(frame(3, .01))
                with self.engineer._lock:
                    self.engineer._db.commit()
            except Exception as error:
                errors.append(error)
            finally:
                done.set()

        with self.engineer.open_history() as history:
            before = history.export_session(self.session)
            chunks = history.iter_export_session_json(self.session)
            header = next(chunks)
            self.assertIn(b'"schema":1', header)
            # Simulate a long read/JSON operation holding the history view's
            # lock. It must not hold the recorder's separate lock or prevent a
            # WAL writer from committing a new frame or reversible lap edit.
            with history._lock:
                worker = threading.Thread(target=write_live, daemon=True)
                worker.start()
                self.assertTrue(done.wait(3), "Saved-data reading blocked live recording")
            worker.join(timeout=1)
            self.assertEqual(errors, [])
            streamed = json.loads(header + b"".join(chunks))
            self.assertEqual(streamed, before)
            self.assertEqual(len(history.session_detail(self.session)["laps"]), 2)
        with self.engineer.open_history() as latest:
            current = latest.export_session(self.session)
            self.assertEqual(len(current["laps"]), 1)
            self.assertGreater(len(current["frames"]), len(before["frames"]))

    def test_large_frame_export_is_incremental_and_memory_bounded(self):
        payload = json.dumps({"gas": .8, "brake": .1, "is_ai_controlled": False,
                              "test_context": "recorded sample " + "x" * 2048})
        self.engineer._db.executemany(
            "INSERT INTO frames VALUES (?,?,?,?,?,?)",
            ((self.session, 3000 + i / 10, 2, (i % 100) / 100, i * 100, payload)
             for i in range(6000)),
        )
        self.engineer._db.commit()
        total = largest = chunks = 0
        with self.engineer.open_history() as history:
            tracemalloc.start()
            try:
                for piece in history.iter_export_session_json(self.session):
                    self.assertIsInstance(piece, bytes)
                    total += len(piece)
                    largest = max(largest, len(piece))
                    chunks += 1
                _, peak = tracemalloc.get_traced_memory()
            finally:
                tracemalloc.stop()
        self.assertGreater(total, 12_000_000)
        self.assertGreater(chunks, 6000)
        self.assertLess(largest, 200_000, "Export was assembled as one large JSON response")
        self.assertLess(peak, 4_000_000, "Export retained a full-session payload in Python memory")

    def test_unknown_session_is_validated_before_iterator_or_http_headers(self):
        with self.engineer.open_history() as history:
            # No next() call: the outer method must fail synchronously, before
            # a caller starts an HTTP streaming response.
            with self.assertRaises(ValueError):
                history.iter_export_session_json("missing-session")
            with self.assertRaises(ValueError):
                history.iter_export_session_json("' OR 1=1 --")

    def test_abandoned_stream_and_history_context_close_cleanly(self):
        with self.engineer.open_history() as history:
            chunks = history.iter_export_session_json(self.session)
            next(chunks)
        # A disconnected client can leave the generator to be finalized after
        # the independent connection has already closed.
        chunks.close()
        history.close()
        with self.assertRaises(sqlite3.ProgrammingError):
            history._db.execute("SELECT 1")

    def test_legacy_database_gets_index_for_first_frame_per_lap_queries(self):
        self.engineer._db.execute("DROP INDEX frames_session_lap")
        self.engineer._db.commit()
        self.engineer.close()
        self.engineer = Engineer(self.path)
        columns = [row[2] for row in self.engineer._db.execute("PRAGMA index_info(frames_session_lap)")]
        self.assertEqual(columns, ["session_id", "lap_count"])
        plan = self.engineer._db.execute(
            "EXPLAIN QUERY PLAN SELECT payload FROM frames WHERE session_id=? AND lap_count=? ORDER BY rowid LIMIT 1",
            (self.session, 1),
        ).fetchall()
        self.assertIn("frames_session_lap", " ".join(str(row[3]) for row in plan))

    def test_readonly_open_never_creates_a_missing_recording_database(self):
        missing = self.path / "does-not-exist" / "sessions.sqlite3"
        with self.assertRaises(sqlite3.OperationalError):
            StoredSessionView(missing)
        self.assertFalse(missing.exists())
        self.assertFalse(missing.parent.exists())


if __name__ == "__main__":
    unittest.main()
