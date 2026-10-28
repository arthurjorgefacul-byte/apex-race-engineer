"""Local HTTP and service integration tests; no real voice or game access."""

import http.client
import json
import math
import pathlib
import tempfile
import threading
import time
import unittest
from unittest import mock

import app
from tests.test_analysis import frame


class SilentVoice:
    def __init__(self):
        self.available = True
        self.enabled = True
        self.calls = []
        self.closed = False
        self.clears = 0

    def say(self, text, force=False):
        if self.enabled or force:
            self.calls.append(text)
            return True
        return False

    def snapshot(self):
        return {"available": self.available, "enabled": self.enabled, "speaking": False, "error": None}

    def close(self):
        self.closed = True

    def clear(self):
        self.clears += 1


class MissingReader:
    def __init__(self):
        self.last_error = "Assetto Corsa is not running."
        self.closed = False

    def read(self):
        return None

    def close(self):
        self.closed = True


class CompanionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.voice_patch = mock.patch.object(app, "Voice", SilentVoice)
        self.reader_patch = mock.patch.object(app, "ACReader", MissingReader)
        self.voice_patch.start()
        self.reader_patch.start()
        self.companion = app.Companion(pathlib.Path(self.temp.name), voice=False)
        self.closed = False
        self.server = app.LocalServer(("127.0.0.1", 0), app.handler_for(self.companion))
        self.server_thread = threading.Thread(target=self.server.serve_forever, kwargs={"poll_interval": 0.02}, daemon=True)
        self.server_thread.start()
        self.port = self.server.server_port

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.server_thread.join(timeout=1)
        if not self.closed:
            self.companion.close()
        self.reader_patch.stop()
        self.voice_patch.stop()
        self.temp.cleanup()

    def request(self, path="/api/state", body=None, method=None, headers=None, raw=None):
        request_headers = dict(headers or {})
        if body is not None:
            raw = json.dumps(body).encode("utf-8")
            request_headers.setdefault("Content-Type", "application/json")
        if method is None:
            method = "POST" if body is not None or raw is not None else "GET"
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=2)
        try:
            connection.request(method, path, body=raw, headers=request_headers)
            response = connection.getresponse()
            result = response.read()
            return response.status, dict(response.getheaders()), result
        finally:
            connection.close()

    def json_request(self, path="/api/state", **kwargs):
        status, headers, raw = self.request(path, **kwargs)
        return status, json.loads(raw)

    def feed(self, current):
        self.companion.telemetry = current
        self.companion._sync_profile(current)
        self.companion.analysis = self.companion.engineer.update(current)

    def make_reference(self):
        for i in range(70, 100):
            self.feed(frame(0, i / 100))
        self.feed(frame(1, 0))
        for i in range(1, 100):
            self.feed(frame(1, i / 100))
        self.feed(frame(2, 0))

    @staticmethod
    def grid(**overrides):
        result = {
            "car_id": "test_gt3", "track_id": "test_track", "layout": "gp",
            "cars": [
                {"id": 0, "name": "Player", "car": "GT3", "lap_count": 2, "normalized_pos": 0.25, "speed_kmh": 140},
                {"id": 1, "name": "Friend", "car": "GT3", "lap_count": 2, "normalized_pos": 0.75, "speed_kmh": 155},
            ],
        }
        result.update(overrides)
        return result

    def test_waiting_service_and_overlay_are_explicitly_not_demo(self):
        status, state = self.json_request()
        self.assertEqual(status, 200)
        self.assertEqual(state["mode"], "live")
        self.assertFalse(state["connected"])
        self.assertIsNone(state["telemetry"])
        self.assertFalse(state["analysis"]["session"]["recording"])
        status, overlay = self.json_request("/api/overlay")
        self.assertEqual(status, 200)
        self.assertFalse(overlay["connected"])
        self.assertIn("Start a driving session", overlay["advice"])

    def test_external_origin_and_host_cannot_read_or_mutate_service(self):
        for headers in ({"Host": "attacker.example"}, {"Origin": "https://attacker.example"}):
            with self.subTest(headers=headers):
                status, _ = self.json_request(headers=headers)
                self.assertEqual(status, 403)
                status, _ = self.json_request("/api/action", body={"action": "demo"}, headers=headers)
                self.assertEqual(status, 403)
        self.assertEqual(self.companion.mode, "live")
        status, _ = self.json_request(headers={"Origin": "http://127.0.0.1:" + str(self.port)})
        self.assertEqual(status, 200)

    def test_settings_reject_invalid_or_nonfinite_values(self):
        bad_values = [
            {"voice": "yes"}, {"focus": "fast"}, {"fuel_reserve_l": -1},
            {"fuel_reserve_l": True}, {"fuel_reserve_l": False},
            {"fuel_reserve_l": math.nan}, {"fuel_reserve_l": math.inf},
            {"target_pressure_psi": 27}, {"target_pressure_psi": math.nan},
        ]
        for payload in bad_values:
            with self.subTest(payload=payload):
                status, result = self.json_request("/api/settings", body=payload)
                self.assertEqual(status, 400, result)
        self.assertEqual(self.companion.settings["fuel_reserve_l"], 2)
        self.assertFalse(self.companion.settings["voice"])

    def test_settings_persist_and_targets_follow_car_compound_and_source(self):
        self.feed(frame(0, 0.3))
        status, _ = self.json_request("/api/settings", body={"target_pressure_psi": 27.2, "fuel_reserve_l": 3, "focus": "stability"})
        self.assertEqual(status, 200)
        self.assertEqual(self.companion.settings["target_pressure_psi"], 27.2)
        self.feed(frame(0, 0.31, tyre_compound="Soft"))
        self.assertIsNone(self.companion.settings["target_pressure_psi"])
        self.feed(frame(0, 0.32, car_id="formula_mod"))
        self.assertIsNone(self.companion.settings["target_pressure_psi"])
        self.feed(frame(0, 0.33, source="demo"))
        self.assertIsNone(self.companion.settings["target_pressure_psi"])
        self.feed(frame(0, 0.34))
        self.assertEqual(self.companion.settings["target_pressure_psi"], 27.2)
        saved = json.loads(self.companion.settings_path.read_text(encoding="utf-8"))
        self.assertEqual(saved["fuel_reserve_l"], 3)
        self.assertEqual(saved["focus"], "stability")
        self.assertEqual(len(saved["pressure_profiles"]), 1)

    def test_mode_switch_clears_reference_telemetry_and_timing(self):
        self.make_reference()
        self.assertIsNotNone(self.companion.analysis["reference"])
        self.companion.receive_bridge(self.grid())
        status, _ = self.json_request("/api/action", body={"action": "demo"})
        self.assertEqual(status, 200)
        status, state = self.json_request()
        self.assertEqual(state["mode"], "demo")
        self.assertIsNone(state["telemetry"])
        self.assertIsNone(state["analysis"]["reference"])
        self.assertEqual(state["analysis"]["laps"], [])
        self.assertFalse(state["timing"]["available"])
        self.assertIsNone(self.companion.bridge)

    def test_actual_service_demo_can_switch_to_missing_live_game(self):
        self.companion.action("demo")
        self.companion.start()
        deadline = time.monotonic() + 1
        while not self.companion.snapshot()["connected"] and time.monotonic() < deadline:
            time.sleep(0.01)
        status, state = self.json_request()
        self.assertTrue(state["connected"])
        self.assertEqual(state["telemetry"]["source"], "demo")
        self.json_request("/api/action", body={"action": "live"})
        time.sleep(0.08)
        status, state = self.json_request()
        self.assertEqual(state["mode"], "live")
        self.assertFalse(state["connected"])
        self.assertIsNone(state["telemetry"])
        self.assertIsNone(state["analysis"]["reference"])

    def test_bridge_requires_current_matching_session_and_expires(self):
        self.feed(frame(2, 0.25))
        status, _ = self.json_request("/api/bridge", body=self.grid())
        self.assertEqual(status, 200)
        status, state = self.json_request()
        self.assertTrue(state["timing"]["available"])
        self.assertEqual([c["id"] for c in state["timing"]["cars"]], [1, 0])
        self.companion.bridge["received_at"] = time.time() - 4
        self.assertFalse(self.companion.timing_snapshot()["available"])
        for mismatch in ({"car_id": "other_car"}, {"track_id": "other_track"}, {"layout": "short"}):
            self.companion.receive_bridge(self.grid(**mismatch))
            self.assertFalse(self.companion.timing_snapshot()["available"])
        self.companion.receive_bridge(self.grid())
        self.companion.mode = "demo"
        self.assertFalse(self.companion.timing_snapshot()["available"])

    def test_bridge_rejects_duplicate_ids_and_sanitizes_bad_channels(self):
        self.feed(frame(2, 0.25))
        duplicate = self.grid(cars=[{"id": 0}, {"id": 0}])
        status, _ = self.json_request("/api/bridge", body=duplicate)
        self.assertEqual(status, 400)
        payload = self.grid(cars=[{"id": 0, "name": "A" * 300, "speed_kmh": math.nan, "normalized_pos": 9}, {"name": "no identifier"}])
        status, _ = self.json_request("/api/bridge", body=payload)
        self.assertEqual(status, 200)
        timing = self.companion.timing_snapshot()
        self.assertEqual(len(timing["cars"]), 1)
        self.assertLessEqual(len(timing["cars"][0]["name"]), 100)
        self.assertNotIn("speed_kmh", timing["cars"][0])
        self.assertNotIn("normalized_pos", timing["cars"][0])
        json.dumps(timing, allow_nan=False)

    def test_http_rejects_bad_payloads_without_mutation(self):
        status, _ = self.json_request("/api/action", body={"action": "unknown"})
        self.assertEqual(status, 400)
        status, _, _ = self.request("/api/action", raw=b"not-json", headers={"Content-Type": "application/json"})
        self.assertEqual(status, 400)
        status, _, _ = self.request("/api/action", raw=b"{}", headers={"Content-Type": "text/plain"})
        self.assertEqual(status, 415)
        status, _, _ = self.request("/api/action", raw=b"[]", headers={"Content-Type": "application/json"})
        self.assertEqual(status, 400)
        self.assertEqual(self.companion.mode, "live")

    def test_dashboard_and_snapshot_export_are_usable(self):
        status, headers, raw = self.request("/")
        self.assertEqual(status, 200)
        self.assertIn(b"Race Engineer", raw)
        self.assertIn("frame-ancestors 'none'", headers["Content-Security-Policy"])
        status, headers, raw = self.request("/api/export")
        self.assertEqual(status, 200)
        self.assertIn("attachment", headers["Content-Disposition"])
        json.dumps(json.loads(raw), allow_nan=False)

    def test_radio_is_mocked_and_close_before_start_is_safe(self):
        status, _ = self.json_request("/api/action", body={"action": "radio"})
        self.assertEqual(status, 200)
        self.assertEqual(len(self.companion.voice.calls), 1)
        self.companion.close()
        self.closed = True
        self.assertTrue(self.companion.voice.closed)
        self.assertTrue(self.companion.reader.closed)

    def voice_context(self, messages=True):
        c = self.companion
        c.settings["voice"] = True
        c.voice.enabled = True
        c.voice.calls.clear(); c.voice.clears = 0
        c.radio_seen.clear(); c.radio_queue.clear()
        c.pending = None; c.straight_since = None; c.last_auto_voice = 0
        c.last_voice_session = "session-one"; c.last_voice_status = "live"
        c.analysis = {"session": {"id": "session-one", "current_lap_eligible": True, "current_lap_flags": []}}
        c.radio_state = {"generation": 1, "new_events": [self.radio_event("one", "Measured corner advice.")] if messages else []}
        c.radio_generation = ("session-one", 1)
        return c

    @staticmethod
    def radio_event(ident, text, **kwargs):
        return dict(id=ident, kind="corner", text=text, timestamp=1000, expires_at=1030, priority=1, **kwargs)

    def test_unknown_driving_channels_suppress_automatic_voice(self):
        for missing in ({"steer": None}, {"brake": None}, {"speed_kmh": None}, {"acc_g": None},
                        {"acc_g": [None, 0, 0]}, {"steer": math.nan}, {"brake": math.inf}):
            with self.subTest(channels=missing), mock.patch("app.time.time", return_value=1000):
                c = self.voice_context(); c.straight_since = 990
                c._schedule_voice(frame(2, .1, is_ai_controlled=False, **missing))
                self.assertEqual(c.voice.calls, []); self.assertIsNone(c.straight_since)

    def test_pause_offtrack_and_ai_clear_corner_calls(self):
        for change in ({"status": "paused"}, {"status": "replay"}, {"wheels_out": 4}, {"is_ai_controlled": True}, {"in_pit": True}):
            with self.subTest(change=change), mock.patch("app.time.time", return_value=1000):
                c = self.voice_context(); c.straight_since = 990
                c._schedule_voice(frame(2, .1, **dict({"is_ai_controlled":False}, **change)))
                self.assertEqual(c.radio_queue, []); self.assertEqual(c.voice.calls, [])

    def test_voice_waits_for_sustained_straight_and_obeys_cooldown(self):
        c = self.voice_context(); driving = frame(2, .1, is_ai_controlled=False); clock = [1000.]
        with mock.patch("app.time.time", side_effect=lambda: clock[0]):
            c._schedule_voice(driving); self.assertEqual(c.voice.calls, [])
            clock[0] = 1000.6; c._schedule_voice(driving); self.assertEqual(c.voice.calls, [])
            clock[0] = 1001.1; c._schedule_voice(driving)
            self.assertEqual(c.voice.calls, ["Measured corner advice."]); self.assertEqual(c.radio_queue, [])
            c.radio_state["new_events"] = [self.radio_event("two", "Check fuel reserve.")]
            clock[0] = 1003; c._schedule_voice(driving); self.assertEqual(len(c.voice.calls), 1)
            clock[0] = 1009; c._schedule_voice(driving)
            self.assertEqual(c.voice.calls, ["Measured corner advice.", "Check fuel reserve."])

    def test_real_session_switch_discards_pending_and_queued_speech(self):
        c = self.voice_context(False); c.radio_queue = [self.radio_event("old", "Old car")]
        c.analysis["session"]["id"] = "session-two"
        with mock.patch("app.time.time", return_value=1000): c._schedule_voice(frame(0, .1, car_id="formula_mod"))
        self.assertEqual(c.last_voice_session, "session-two"); self.assertEqual(c.radio_queue, [])
        self.assertEqual(c.voice.clears, 1); self.assertEqual(c.voice.calls, [])

    def test_expired_pending_advice_is_not_spoken(self):
        c = self.voice_context(False)
        c.radio_queue = [dict(self.radio_event("old", "Outdated advice"), expires_at=945)]
        c.straight_since = 990
        with mock.patch("app.time.time", return_value=1000): c._schedule_voice(frame(2, .1, is_ai_controlled=False))
        self.assertEqual(c.voice.calls, []); self.assertEqual(c.radio_queue, [])


if __name__ == "__main__":
    unittest.main()
