"""Saved-session HTTP tests using temporary recordings and muted fake hardware.

These exercise the desktop's API boundary rather than a live game's files.
"""
import http.client
import contextlib
import json
import pathlib
import sqlite3
import tempfile
import threading
import time
import unittest
from unittest import mock

import app
from tests.test_analysis import frame
from tests.test_companion import MissingReader, SilentVoice


class DataApiTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.voice_patch = mock.patch.object(app, "Voice", SilentVoice)
        self.reader_patch = mock.patch.object(app, "ACReader", MissingReader)
        self.voice_patch.start()
        self.reader_patch.start()
        self.companion = app.Companion(pathlib.Path(self.temp.name), voice=False, backend="local")
        self.server = app.LocalServer(("127.0.0.1", 0), app.handler_for(self.companion))
        self.server_thread = threading.Thread(target=self.server.serve_forever,
                                              kwargs={"poll_interval": 0.01}, daemon=True)
        self.server_thread.start()
        self.port = self.server.server_port
        self.next_lap = 1

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.server_thread.join(timeout=1)
        self.companion.close()
        self.reader_patch.stop()
        self.voice_patch.stop()
        self.temp.cleanup()

    def request(self, path, body=None, headers=None):
        request_headers = dict(headers or {})
        raw = None
        if body is not None:
            raw = json.dumps(body).encode("utf-8")
            request_headers.setdefault("Content-Type", "application/json")
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        try:
            connection.request("POST" if body is not None else "GET", path,
                               body=raw, headers=request_headers)
            response = connection.getresponse()
            payload = json.loads(response.read())
            return response.status, payload
        finally:
            connection.close()

    def feed(self, sample):
        self.companion.telemetry = sample
        self.companion.analysis = self.companion.engineer.update(sample)

    def begin(self, **values):
        for i in range(70, 100):
            self.feed(frame(0, i / 100, fuel=100 - i / 100 * 1.5, **values))
        self.feed(frame(1, 0, fuel=98.5, **values))
        self.next_lap = 1

    def complete(self, duration=60, samples=100, issue=None, **values):
        count = self.next_lap
        for index in range(1, samples):
            position = index / samples
            changes = dict(values)
            if issue and index == samples // 2:
                changes.update(issue)
            self.feed(frame(count, position, duration,
                            fuel=100 - (count + position) * 1.5, **changes))
        self.feed(frame(count + 1, 0, duration, fuel=100 - (count + 1) * 1.5, **values))
        self.next_lap += 1
        return self.companion.analysis["laps"][0]

    def detail(self, session_id, include_deleted=False):
        suffix = "?include_deleted=1" if include_deleted else ""
        status, result = self.request("/api/sessions/" + session_id + suffix)
        self.assertEqual(status, 200, result)
        return result

    def seed(self):
        self.begin()
        first = self.complete(60)
        second = self.complete(61)
        return self.companion.analysis["session"]["id"], first, second

    def test_all_recorded_laps_are_visible_beyond_live_thirty_lap_window(self):
        self.begin()
        recorded = [self.complete(60 + (index % 2)) for index in range(33)]
        session_id = self.companion.analysis["session"]["id"]
        self.assertEqual(len(self.companion.analysis["laps"]), 30)
        status, listing = self.request("/api/sessions")
        self.assertEqual(status, 200, listing)
        sessions = listing["sessions"]
        self.assertIn(session_id, [session["id"] for session in sessions])
        detail = self.detail(session_id)
        self.assertEqual(len(detail["laps"]), 33)
        self.assertEqual({lap["id"] for lap in detail["laps"]}, {lap["id"] for lap in recorded})
        self.assertEqual(detail["session"]["car_id"], "test_gt3")
        self.assertEqual(detail["session"]["source"], "live")

    def test_delete_reference_is_recoverable_and_clears_live_advice(self):
        session_id, first, second = self.seed()
        self.assertEqual(self.companion.analysis["reference"]["id"], first["id"])
        stale_coaching = {message["id"] for message in self.companion.analysis["messages"]
                          if message.get("kind") == "coaching"}
        self.companion.pending = ("Obsolete reference advice", time.time())
        self.companion.seen_messages.add("obsolete-message")
        prior_clears = self.companion.voice.clears
        status, deleted = self.request("/api/laps/delete", {"session_id": session_id, "lap_id": first["id"]})
        self.assertEqual(status, 200, deleted)
        self.assertIsNone(self.companion.pending)
        self.assertGreater(self.companion.voice.clears, prior_clears)
        self.assertFalse(self.companion.seen_messages)
        self.assertFalse(stale_coaching.intersection(
            message["id"] for message in self.companion.analysis["messages"]))
        reference = self.companion.analysis["reference"]
        self.assertTrue(reference is None or reference["id"] == second["id"])
        self.assertNotIn(first["id"], [lap["id"] for lap in self.detail(session_id)["laps"]])
        tombstones = self.detail(session_id, True)["laps"]
        deleted_lap = next(lap for lap in tombstones if lap["id"] == first["id"])
        self.assertIsNotNone(deleted_lap.get("deleted_at"))
        status, restored = self.request("/api/laps/restore", {"session_id": session_id, "lap_id": first["id"]})
        self.assertEqual(status, 200, restored)
        self.assertIn(first["id"], [lap["id"] for lap in self.detail(session_id)["laps"]])
        self.assertEqual(self.companion.analysis["reference"]["id"], first["id"])

    def test_saved_history_can_be_reopened_and_analyzed_after_application_restart(self):
        session_id, first, second = self.seed()
        self.server.shutdown()
        self.server.server_close()
        self.server_thread.join(timeout=1)
        self.companion.close()
        self.companion = app.Companion(pathlib.Path(self.temp.name), voice=False, backend="local")
        self.server = app.LocalServer(("127.0.0.1", 0), app.handler_for(self.companion))
        self.server_thread = threading.Thread(target=self.server.serve_forever,
                                              kwargs={"poll_interval": 0.01}, daemon=True)
        self.server_thread.start()
        self.port = self.server.server_port
        self.assertFalse(self.companion.snapshot()["connected"])
        self.assertEqual(len(self.detail(session_id)["laps"]), 2)
        status, analysis = self.request("/api/analyze?session_id=" + session_id
                                        + "&lap_id=" + second["id"] + "&reference_id=" + first["id"])
        self.assertEqual(status, 200, analysis)
        self.assertEqual(analysis["coaching"]["delta_s"], 1)

    def test_stored_analysis_compares_selected_laps_and_rejects_deleted_reference(self):
        session_id, first, second = self.seed()
        live_reference = self.companion.analysis["reference"]["id"]
        live_lap = self.companion.telemetry["lap_count"]
        query = "/api/analyze?session_id=" + session_id + "&lap_id=" + second["id"] + "&reference_id=" + first["id"]
        status, result = self.request(query)
        self.assertEqual(status, 200, result)
        self.assertEqual(result["lap"]["id"], second["id"])
        self.assertEqual(result["reference"]["id"], first["id"])
        self.assertTrue(result["comparison_available"])
        self.assertTrue(result["corners"])
        self.assertTrue(all(corner["time_loss_s"] > 0 for corner in result["corners"]))
        # Detected corner spans intentionally exclude the straight before C1.
        self.assertTrue(0.7 < sum(corner["time_loss_s"] for corner in result["corners"]) <= 1.01)
        self.assertTrue(result["traces"]["current"])
        self.assertEqual(self.companion.analysis["reference"]["id"], live_reference)
        self.assertEqual(self.companion.telemetry["lap_count"], live_lap)
        self.assertEqual(self.request("/api/laps/delete", {"lap_id": first["id"]})[0], 200)
        self.assertEqual(self.request(query)[0], 400)

    def test_excluded_lap_is_inspectable_without_becoming_a_coaching_reference(self):
        session_id, first, _ = self.seed()
        bad = self.complete(62, issue={"wheels_out": 4})
        query = "/api/analyze?session_id=" + session_id + "&lap_id=" + bad["id"] + "&reference_id=" + first["id"]
        status, result = self.request(query)
        self.assertEqual(status, 200, result)
        self.assertFalse(result["lap"]["clean"])
        self.assertFalse(result["comparison_available"])
        self.assertTrue(result["traces"]["current"])
        self.assertEqual(result["corners"], [])
        reverse = "/api/analyze?session_id=" + session_id + "&lap_id=" + first["id"] + "&reference_id=" + bad["id"]
        self.assertEqual(self.request(reverse)[0], 400)

    def test_cross_session_or_source_reference_is_rejected(self):
        session_id, first, second = self.seed()
        self.companion.engineer.reset()
        self.begin(source="demo")
        other = self.complete(source="demo")
        other_session = self.companion.analysis["session"]["id"]
        self.assertNotEqual(session_id, other_session)
        query = "/api/analyze?session_id=" + other_session + "&lap_id=" + other["id"] + "&reference_id=" + first["id"]
        self.assertEqual(self.request(query)[0], 400)
        self.assertEqual(len(self.detail(session_id)["laps"]), 2)
        self.assertEqual(self.detail(other_session)["session"]["source"], "demo")

    def test_full_session_export_contains_recorded_frames_and_undownsampled_trace(self):
        self.begin()
        complete = self.complete(samples=1000)
        session_id = self.companion.analysis["session"]["id"]
        status, exported = self.request("/api/sessions/" + session_id + "/export")
        self.assertEqual(status, 200, exported)
        self.assertEqual(exported["session"]["id"], session_id)
        saved_lap = next(lap for lap in exported["laps"] if lap["id"] == complete["id"])
        self.assertGreater(len(saved_lap["trace"]), 280)
        self.assertGreater(len(saved_lap["trace"]), len(self.companion.analysis["traces"]["current"]))
        with contextlib.closing(sqlite3.connect(self.companion.engineer.database_path)) as database:
            frame_count = database.execute("SELECT COUNT(*) FROM frames WHERE session_id=?", (session_id,)).fetchone()[0]
        self.assertEqual(len(exported["frames"]), frame_count)
        self.assertGreater(frame_count, 500)
        json.dumps(exported, allow_nan=False)

    def test_unknown_or_malformed_selection_fails_without_mutating_recordings(self):
        session_id, first, _ = self.seed()
        self.assertEqual(self.request("/api/sessions/not-a-session")[0], 404)
        self.assertEqual(self.request("/api/analyze?session_id=" + session_id)[0], 400)
        for endpoint in ("/api/laps/delete", "/api/laps/restore"):
            self.assertEqual(self.request(endpoint, {})[0], 400)
            self.assertEqual(self.request(endpoint, {"lap_id": "unknown"})[0], 404)
            self.assertEqual(self.request(endpoint, {"lap_id": first["id"]},
                                          headers={"Origin": "https://outside.example"})[0], 403)
        self.assertEqual(len(self.detail(session_id)["laps"]), 2)


if __name__ == "__main__":
    unittest.main()
