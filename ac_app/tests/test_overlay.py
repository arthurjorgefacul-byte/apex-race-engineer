"""Behavioral tests for the embedded app without a running simulator."""

import ast
import importlib.util
import json
from pathlib import Path
import sys
import threading
import types
import unittest
from unittest import mock
import http.server
import builtins


SOURCE = Path(__file__).resolve().parents[1] / "RaceEngineer" / "RaceEngineer.py"


class FakeAC(types.ModuleType):
    def __init__(self):
        super().__init__("ac")
        self.main_thread = threading.get_ident()
        self.calls = []
        self.text = {}
        self.counter = 0
        self.listeners = {}
        self.visible = {}

    def _record(self, name, *args):
        if threading.get_ident() != self.main_thread:
            raise AssertionError("AC API called off the main thread")
        self.calls.append((name, args))

    def newApp(self, name):
        self._record("newApp", name)
        return 1

    def addLabel(self, app, text):
        self._record("addLabel", app, text)
        self.counter += 1
        self.text[self.counter] = text
        return self.counter

    def setText(self, label, text):
        self._record("setText", label, text)
        self.text[label] = text

    def addButton(self, app, text):
        self._record("addButton", app, text)
        self.counter += 1
        self.text[self.counter] = text
        return self.counter

    def addOnClickedListener(self, button, callback):
        self._record("addOnClickedListener", button)
        self.listeners[button] = callback

    def setVisible(self, control, visible):
        self._record("setVisible", control, visible)
        self.visible[control] = visible

    def __getattr__(self, name):
        known_apis = {"setTitle", "setSize", "setPosition", "setFontSize",
                      "setFontAlignment", "setFontColor", "setBackgroundColor",
                      "setBackgroundOpacity", "drawBorder"}
        if name not in known_apis:
            raise AttributeError("Unknown AC API: " + name)
        return lambda *args: self._record(name, *args)


class FakeResponse:
    def __init__(self, payload):
        self.payload = payload
        self.closed = False

    def read(self, size):
        return self.payload[:size]

    def close(self):
        self.closed = True


class FakeOpener:
    def __init__(self, payload=None, error=None):
        self.response = FakeResponse(payload or b"")
        self.error = error
        self.requests = []

    def open(self, request, timeout):
        self.requests.append((request, timeout))
        if self.error:
            raise self.error
        return self.response


class OverlayTests(unittest.TestCase):
    def setUp(self):
        self.ac = FakeAC()
        self.previous_ac = sys.modules.get("ac")
        sys.modules["ac"] = self.ac
        spec = importlib.util.spec_from_file_location("race_engineer_overlay_test", SOURCE)
        self.app = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.app)
        self.app._start_worker = lambda: None
        self.app.acMain("1.16")

    def tearDown(self):
        self.app.acShutdown()
        if self.previous_ac is None:
            sys.modules.pop("ac", None)
        else:
            sys.modules["ac"] = self.previous_ac

    def snapshot(self, **changes):
        data = {"connected": True, "mode": "live", "title": "Race Engineer",
                "lap": "Lap 3 | 1:42.381", "delta": "+0.12s",
                "fuel": "Fuel 24.8 L | 7.3 laps", "tyres": "FL 26.0 FR 26.1 RL 25.9 RR 26.0 psi",
                "advice": "Turn 1: your brake release started earlier than your best lap.",
                "severity": "info"}
        data.update(changes)
        return data

    def publish(self, **changes):
        raw = json.dumps(self.snapshot(**changes)).encode("utf-8")
        self.app._opener = FakeOpener(raw)
        self.app._poll_once()
        self.app.acUpdate(0.2)

    def display(self, name):
        return self.ac.text[self.app._labels[name]]

    def enable_game(self, cars=None, count=None):
        """Supply only documented timing APIs, with non-hardcoded enum values."""
        fields = ("LapCount", "LapTime", "LastLap", "NormalizedSplinePosition", "SpeedKMH")
        enums = types.SimpleNamespace(**{name: index + 71 for index, name in enumerate(fields)})
        reverse = {getattr(enums, name): name for name in fields}
        self.app.acsys = types.SimpleNamespace(CS=enums)
        if cars is None:
            cars = {
                0: {"name": "Player", "car": "gt3_car", "connected": 1,
                    "signals": {"LapCount": 2, "LapTime": 22340, "LastLap": 98512,
                                "NormalizedSplinePosition": 0.25, "SpeedKMH": 183.4}},
                1: {"name": "Friend", "car": "formula_car", "connected": 1,
                    "signals": {"LapCount": 2, "LapTime": 21210, "LastLap": 97100,
                                "NormalizedSplinePosition": 0.29, "SpeedKMH": 199.2}}}

        def bind(name, implementation):
            def function(*args):
                self.ac._record(name, *args)
                return implementation(*args)
            setattr(self.ac, name, function)

        bind("getCarsCount", lambda: len(cars) if count is None else count)
        bind("getCarName", lambda car_id: cars[car_id]["car"])
        bind("getDriverName", lambda car_id: cars[car_id]["name"])
        bind("isConnected", lambda car_id: cars[car_id].get("connected", 1))
        bind("getCarState", lambda car_id, enum: cars[car_id]["signals"].get(reverse[enum], -1))
        bind("getTrackName", lambda car_id: "ks_monza")
        bind("getTrackConfiguration", lambda car_id: "")
        return cars

    def sample_grid(self, steps=1):
        # Sampling is deliberately amortized: at most two car slots per step.
        for unused in range(steps):
            self.app._collect_bridge()
        self.app._sample_bridge()

    def test_python_33_grammar(self):
        ast.parse(SOURCE.read_text(encoding="utf-8"), feature_version=(3, 3))

    def test_initial_window_has_honest_offline_state(self):
        self.assertEqual(self.display("status"), "COMPANION OFFLINE")
        self.assertEqual(self.display("fuel"), "Fuel  --")
        self.assertIn(("setSize", (1, 420, 240)), self.ac.calls)
        self.assertIn(("newApp", ("Race Engineer",)), self.ac.calls)

    def test_mfd_expands_and_collapses_with_all_controls_inside_the_window(self):
        self.assertEqual(len(self.app._menu_buttons), len(self.app.RADIO_TOPICS) + 1)
        self.assertFalse(self.app._menu_expanded)
        for topic, title in self.app.RADIO_TOPICS:
            self.assertEqual(self.ac.visible[self.app._menu_buttons[topic]], 0)
        toggle = self.app._menu_buttons["toggle"]
        self.ac.listeners[toggle]("clicked", 0)
        self.assertTrue(self.app._menu_expanded)
        self.assertIn(("setSize", (1, 420, self.app.MENU_EXPANDED_HEIGHT)), self.ac.calls)
        positions = {args[0]: args[1:] for name, args in self.ac.calls if name == "setPosition"}
        sizes = {args[0]: args[1:] for name, args in self.ac.calls if name == "setSize"}
        for topic, title in self.app.RADIO_TOPICS:
            button = self.app._menu_buttons[topic]
            self.assertEqual(self.ac.visible[button], 1)
            x, y = positions[button]
            width, height = sizes[button]
            self.assertLessEqual(x + width, 420)
            self.assertLessEqual(y + height, self.app.MENU_EXPANDED_HEIGHT)
        self.ac.listeners[toggle](0, 0)
        self.assertFalse(self.app._menu_expanded)
        self.assertIn(("setSize", (1, 420, 240)), self.ac.calls)

    def test_mfd_callbacks_only_queue_valid_bounded_deduplicated_commands(self):
        self.app._opener = FakeOpener(error=AssertionError("Click callback must not do HTTP"))
        for topic, title in self.app.RADIO_TOPICS:
            button = self.app._menu_buttons[topic]
            self.ac.listeners[button](0, 0)
        self.assertEqual(len(self.app._radio_pending), self.app.MAX_PENDING_RADIO)
        self.assertEqual([item[0] for item in self.app._radio_pending], [topic for topic, title in self.app.RADIO_TOPICS[-4:]])
        self.assertFalse(self.app._queue_radio(self.app.RADIO_TOPICS[-1][0]))
        self.assertFalse(self.app._queue_radio("outside-url"))
        self.assertEqual(self.app._opener.requests, [])
        self.assertTrue(self.app._wake.is_set())

    def test_radio_http_runs_on_worker_and_never_retries_a_spoken_request(self):
        self.assertTrue(self.app._queue_radio("fuel"))
        self.app._opener = FakeOpener(b'{"topic":"fuel","text":"Fuel: 5 laps remain.","available":true}')
        before = len(self.ac.calls)
        worker = threading.Thread(target=self.app._post_radio_once)
        worker.start()
        worker.join(timeout=1)
        self.assertFalse(worker.is_alive())
        self.assertEqual(len(self.ac.calls), before)
        self.assertEqual(len(self.app._radio_pending), 0)
        request, timeout = self.app._opener.requests[0]
        self.assertEqual(request.full_url, self.app.RADIO_URL)
        self.assertEqual(json.loads(request.data), {"topic": "fuel"})
        self.assertEqual(request.get_method(), "POST")
        self.assertTrue(self.app._opener.response.closed)
        self.assertFalse(self.app._post_radio_once())
        self.assertEqual(len(self.app._opener.requests), 1)
        self.assertIn("reply ready", self.app._radio_status)

    def test_radio_expired_or_clock_reversed_requests_are_never_sent(self):
        self.app._opener = FakeOpener(error=AssertionError("Expired request reached HTTP"))
        with mock.patch.object(self.app.time, "monotonic", return_value=100):
            self.app._queue_radio("fuel")
        with mock.patch.object(self.app.time, "monotonic", return_value=100 + self.app.RADIO_REQUEST_MAX_AGE + 1):
            self.assertFalse(self.app._post_radio_once())
        with mock.patch.object(self.app.time, "monotonic", return_value=100):
            self.app._queue_radio("tyres")
        with mock.patch.object(self.app.time, "monotonic", return_value=90):
            self.assertFalse(self.app._post_radio_once())
        self.assertEqual(self.app._opener.requests, [])

    def test_failed_or_malformed_radio_reply_keeps_live_data_and_is_not_retried(self):
        self.publish()
        for payload in (b"not-json", b"[]", b'{"topic":"tyres","text":"Wrong topic","available":true}',
                        b'{"topic":"fuel","text":"No flag"}', b" " * 65537):
            with self.subTest(payload=payload[:70]):
                self.app._queue_radio("fuel")
                self.app._opener = FakeOpener(payload)
                self.assertTrue(self.app._post_radio_once())
                self.assertIn("No reply", self.app._radio_status)
                self.assertFalse(self.app._post_radio_once())
                self.app.acUpdate(.2)
                self.assertEqual(self.display("status"), "LIVE TELEMETRY")
                self.assertEqual(len(self.app._opener.requests), 1)

    def test_worker_fetches_overlay_immediately_after_radio_command(self):
        order = []
        self.app._stop.clear()

        def finish():
            order.append("bridge")
            self.app._stop.set()
            self.app._wake.set()

        with mock.patch.object(self.app, "_post_radio_once", side_effect=lambda: order.append("radio")), \
                mock.patch.object(self.app, "_poll_once", side_effect=lambda: order.append("overlay")), \
                mock.patch.object(self.app, "_post_bridge_once", side_effect=finish):
            self.app._poll_loop()
        self.assertEqual(order, ["radio", "overlay", "bridge"])

    def test_expanded_menu_status_updates_once_then_stays_idle(self):
        self.app._toggle_menu()
        self.app.acUpdate(.2)
        self.ac.calls.clear()
        self.app._queue_radio("fuel")
        self.app.acUpdate(.2)
        writes = [call for call in self.ac.calls if call[0] == "setText"]
        self.assertEqual(writes, [("setText", (self.app._labels["menu_status"], "Asking: Fuel..."))])
        self.ac.calls.clear()
        self.app.acUpdate(.2)
        self.assertEqual(self.ac.calls, [])

    def test_fetch_runs_off_thread_but_ui_changes_only_on_update(self):
        self.app._opener = FakeOpener(json.dumps(self.snapshot()).encode("utf-8"))
        before = len(self.ac.calls)
        worker = threading.Thread(target=self.app._poll_once)
        worker.start()
        worker.join(timeout=1)
        self.assertFalse(worker.is_alive())
        self.assertEqual(len(self.ac.calls), before)
        self.assertEqual(self.display("status"), "COMPANION OFFLINE")
        self.app.acUpdate(0.2)
        self.assertEqual(self.display("status"), "LIVE TELEMETRY")
        self.assertEqual(self.display("lap"), "Lap 3 | 1:42.381")
        self.assertTrue(self.app._opener.response.closed)
        request, timeout = self.app._opener.requests[0]
        self.assertEqual(request.full_url, "http://127.0.0.1:8777/api/overlay")
        self.assertLessEqual(timeout, 1)

    def test_timeout_clears_previously_live_readings(self):
        self.publish()
        self.app._opener = FakeOpener(error=TimeoutError("local request timed out"))
        self.app._poll_once()
        self.app.acUpdate(0.2)
        self.assertEqual(self.display("status"), "COMPANION OFFLINE")
        self.assertEqual(self.display("lap"), "--")
        self.assertEqual(self.display("fuel"), "Fuel  --")
        self.assertNotIn("Turn 1", self.display("advice0"))

    def test_stale_worker_data_expires_even_without_another_poll(self):
        self.publish()
        self.app._latest["received_at"] -= self.app.STALE_SECONDS + 1
        self.app.acUpdate(0.2)
        self.assertEqual(self.display("status"), "COMPANION OFFLINE")
        self.assertEqual(self.display("delta"), "")

    def test_demo_cannot_be_mistaken_for_live(self):
        for connected in (True, False):
            self.publish(mode="demo", connected=connected)
            self.assertEqual(self.display("status"), "DEMO | SAMPLE TELEMETRY")
            self.assertIn("Demo data", self.display("footer"))

    def test_disconnected_game_does_not_show_returned_stale_measurements(self):
        self.publish(connected=False, advice="Enter a driving session.")
        self.assertEqual(self.display("status"), "WAITING FOR LIVE TELEMETRY")
        self.assertEqual(self.display("lap"), "--")
        self.assertEqual(self.display("tyres"), "Tyres  --")

    def test_bad_schema_or_oversized_response_clears_live(self):
        for raw in (b"not-json", b"[]", b'{"mode":"live","connected":"true"}',
                    b'{"mode":"unknown","connected":true}', b" " * 65537):
            with self.subTest(raw=raw[:70]):
                self.publish()
                self.app._opener = FakeOpener(raw)
                self.app._poll_once()
                self.app.acUpdate(0.2)
                self.assertEqual(self.display("status"), "COMPANION OFFLINE")

    def test_advice_is_bounded_and_no_control_characters_reach_labels(self):
        self.publish(advice="A" * 500 + "\n\x00\ttrailing text")
        lines = [self.display("advice" + str(i)) for i in range(3)]
        self.assertTrue(all(len(line) <= 52 for line in lines))
        self.assertTrue(lines[-1].endswith("..."))
        self.assertTrue(all(ord(ch) >= 32 for line in lines for ch in line))

    def test_unchanged_state_does_not_rewrite_the_ui(self):
        self.publish()
        before = len(self.ac.calls)
        self.app.acUpdate(0.2)
        self.assertEqual(len(self.ac.calls), before)

    def test_timing_sampling_stays_on_main_thread_and_posting_off_thread(self):
        self.enable_game()
        self.app._opener = FakeOpener(b'{"ok":true}')
        self.app.acUpdate(0.49)
        self.assertIsNone(self.app._bridge_packet)
        self.app.acUpdate(0.01)
        self.assertEqual(len(self.app._bridge_packet["cars"]), 2)
        self.assertEqual(len(self.app._opener.requests), 0)
        before = len(self.ac.calls)
        worker = threading.Thread(target=self.app._post_bridge_once)
        worker.start()
        worker.join(timeout=1)
        self.assertFalse(worker.is_alive())
        self.assertEqual(len(self.ac.calls), before)
        request, timeout = self.app._opener.requests[0]
        self.assertEqual(request.full_url, "http://127.0.0.1:8777/api/bridge")
        self.assertEqual(request.get_method(), "POST")
        self.assertLessEqual(timeout, 1)
        packet = json.loads(request.data.decode("utf-8"))
        self.assertEqual(packet["player_id"], 0)
        self.assertEqual(packet["car_id"], "gt3_car")
        self.assertEqual(packet["track_id"], "ks_monza")
        self.assertEqual(packet["layout"], "")
        self.assertEqual(packet["cars"][1]["lap_time_ms"], 21210)
        self.assertEqual(packet["cars"][1]["normalized_pos"], 0.29)
        self.assertEqual(set(packet["cars"][1]),
                         {"id", "name", "car", "lap_count", "lap_time_ms", "last_lap_ms",
                          "normalized_pos", "speed_kmh"})
        self.assertTrue(self.app._opener.response.closed)

    def test_bridge_skips_disconnected_slots_and_guards_failed_optional_fields(self):
        cars = self.enable_game()
        cars[1]["connected"] = 0
        cars[0]["signals"]["SpeedKMH"] = float("nan")
        cars[0]["signals"]["NormalizedSplinePosition"] = 1.4
        delattr(self.app.acsys.CS, "LastLap")
        self.ac.getDriverName = lambda car_id: -1
        self.ac.getTrackConfiguration = None
        self.sample_grid()
        packet = self.app._bridge_packet
        self.assertEqual(len(packet["cars"]), 1)
        self.assertEqual(packet["cars"][0]["lap_count"], 2)
        for field in ("speed_kmh", "normalized_pos", "last_lap_ms", "name"):
            self.assertNotIn(field, packet["cars"][0])
        self.assertNotIn("layout", packet)

    def test_bridge_limits_grid_size_and_handles_bad_car_slots(self):
        cars = {index: {"name": "Driver " + str(index), "car": "gt3", "connected": 1,
                        "signals": {"LapCount": 0}} for index in range(100)}
        cars[1]["car"] = "-1"
        del cars[2]["name"]
        self.enable_game(cars)
        self.sample_grid(32)
        packet = self.app._bridge_packet
        self.assertEqual(len(packet["cars"]), 63)
        self.assertNotIn(1, [car["id"] for car in packet["cars"]])
        self.assertEqual(max(car["id"] for car in packet["cars"]), 63)
        self.assertNotIn("name", next(car for car in packet["cars"] if car["id"] == 2))

    def test_bridge_never_replays_stale_or_already_sent_samples(self):
        self.enable_game()
        self.app._opener = FakeOpener(b'{"ok":true}')
        self.sample_grid()
        self.app._post_bridge_once()
        self.app._post_bridge_once()
        self.assertEqual(len(self.app._opener.requests), 1)
        self.sample_grid()
        self.app._bridge_received_at -= self.app.STALE_SECONDS + 1
        self.app._post_bridge_once()
        self.assertEqual(len(self.app._opener.requests), 1)

    def test_optional_bridge_failure_does_not_disconnect_working_overlay(self):
        self.enable_game()
        self.publish()
        self.sample_grid()
        self.app._opener = FakeOpener(error=TimeoutError("bridge timed out"))
        self.app._post_bridge_once()
        self.app.acUpdate(0.2)
        self.assertEqual(self.display("status"), "LIVE TELEMETRY")

    def test_missing_game_api_disables_only_the_optional_bridge(self):
        self.enable_game()
        self.ac.getCarsCount = None
        self.sample_grid()
        self.assertIsNone(self.app._bridge_packet)
        self.publish()
        self.assertEqual(self.display("status"), "LIVE TELEMETRY")
        self.app.acsys = None
        self.assertIsNone(self.app._collect_bridge())

    def test_only_changed_label_is_written_when_lap_time_changes(self):
        self.publish()
        self.ac.calls.clear()
        self.publish(lap="Lap 3 | 1:42.881")
        writes = [call for call in self.ac.calls if call[0] in ("setText", "setFontColor")]
        self.assertEqual(writes, [("setText", (self.app._labels["lap"], "Lap 3 | 1:42.881"))])

    def test_large_grid_sampling_never_bursts_across_all_cars_in_one_frame(self):
        cars = {index: {"name": "Driver " + str(index), "car": "gt3", "connected": 1,
                        "signals": {"LapCount": 3, "LapTime": 21000,
                                    "LastLap": 90000, "NormalizedSplinePosition": 0.2,
                                    "SpeedKMH": 160}} for index in range(64)}
        self.enable_game(cars)
        reads = {"getCarsCount", "getCarName", "getDriverName", "getCarState",
                 "isConnected", "getTrackName", "getTrackConfiguration"}
        highest = 0
        now = [1000.0]
        with mock.patch.object(self.app.time, "monotonic", side_effect=lambda: now[0]):
            for unused in range(150):
                self.ac.calls.clear()
                now[0] += 1.0 / 60
                self.app.acUpdate(1.0 / 60)
                calls = [call for call in self.ac.calls if call[0] in reads]
                highest = max(highest, len(calls))
                sampled_ids = {arguments[0] for name, arguments in calls if name == "getCarState"}
                self.assertLessEqual(len(sampled_ids), 2)
                self.assertLessEqual(len(calls), 20)
            self.assertEqual(len(self.app._bridge_packet["cars"]), 64)
        self.assertEqual(highest, 20)
        self.assertEqual(self.app._bridge_packet["performance"]["max_ac_reads_per_update"], 20)

    def test_slow_update_does_not_catch_up_by_sweeping_the_grid(self):
        cars = {index: {"name": "Driver " + str(index), "car": "gt3", "connected": 1,
                        "signals": {"LapCount": 0}} for index in range(64)}
        self.enable_game(cars)
        self.ac.calls.clear()
        self.app.acUpdate(2.0)
        ids = {args[0] for name, args in self.ac.calls if name == "getCarState"}
        self.assertLessEqual(len(ids), 2)
        self.assertEqual(self.app._bridge_packet["performance"]["frames_over_33_ms"], 1)
        self.assertEqual(self.app._bridge_packet["performance"]["frames_over_50_ms"], 1)
        self.assertEqual(self.app._bridge_packet["performance"]["frame_max_ms"], 2000)
        self.assertEqual(self.app._bridge_packet["performance"]["frame_p95_ms"], 2000)

    def test_cached_driver_identity_avoids_repeated_string_api_calls(self):
        self.enable_game()
        self.app._collect_bridge()
        self.ac.calls.clear()
        self.app._collect_bridge()
        names = [name for name, args in self.ac.calls]
        self.assertNotIn("getCarsCount", names)
        self.assertNotIn("getDriverName", names)
        self.assertNotIn("getCarName", names)
        self.assertNotIn("getTrackName", names)
        self.assertEqual(names.count("getCarState"), 10)

    def test_stale_cached_rows_expire_without_new_game_reads(self):
        self.enable_game()
        with mock.patch.object(self.app.time, "monotonic", return_value=1000.0):
            self.sample_grid()
        before = len(self.ac.calls)
        packet = self.app._bridge_snapshot(1000.0 + self.app.BRIDGE_CAR_STALE_SECONDS + 1)
        self.assertEqual(packet["cars"], [])
        self.assertEqual(len(self.ac.calls), before)

    def test_bridge_publication_itself_never_reads_game_apis(self):
        self.enable_game()
        self.app._collect_bridge()
        before = len(self.ac.calls)
        self.app._sample_bridge()
        self.assertEqual(len(self.ac.calls), before)

    def test_missing_embedded_socket_extension_still_loads_an_explanatory_window(self):
        original_import = builtins.__import__

        def without_http(name, *args, **kwargs):
            if name == "http.client":
                raise ImportError("No module named '_socket'")
            return original_import(name, *args, **kwargs)

        namespace = {"__file__": str(SOURCE), "__name__": "overlay_without_socket",
                     "__builtins__": dict(vars(builtins), __import__=without_http)}
        exec(compile(SOURCE.read_text(encoding="utf-8"), str(SOURCE), "exec"), namespace)
        namespace["_start_worker"] = lambda: None
        namespace["acMain"]("1.16.4")
        status_label = namespace["_labels"]["status"]
        self.assertEqual(self.ac.text[status_label], "OVERLAY DEPENDENCY MISSING")
        namespace["acShutdown"]()

    def test_direct_loopback_transport_does_not_redirect_or_open_other_urls(self):
        class Response:
            status = 302

            def close(self):
                pass

        connection = mock.Mock()
        connection.getresponse.return_value = Response()
        with mock.patch.object(self.app._http_client, "HTTPConnection", return_value=connection) as factory:
            with self.assertRaises(IOError):
                self.app._LoopbackHTTP().open(self.app._LocalRequest(self.app.OVERLAY_URL), 0.8)
            factory.assert_called_once_with("127.0.0.1", 8777, timeout=0.8)
            connection.close.assert_called_once()
        with self.assertRaises(ValueError):
            self.app._LoopbackHTTP().open(self.app._LocalRequest("http://outside.example/"), 0.8)

    def test_http_get_and_json_post_use_real_framing_with_a_local_server(self):
        responses = []
        snapshot = self.snapshot()

        class Handler(http.server.BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_GET(self):
                responses.append(("GET", self.path, self.headers.get("Connection")))
                raw = json.dumps(snapshot).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)

            def do_POST(self):
                body = self.rfile.read(int(self.headers["Content-Length"]))
                responses.append(("POST", self.path, json.loads(body)))
                raw = (b'{"topic":"fuel","text":"Fuel: 5 laps remain.","available":true}'
                       if self.path == "/api/radio" else b'{"ok":true}')
                self.send_response(200)
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)

        server = http.server.HTTPServer(("127.0.0.1", 0), Handler)
        worker = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True)
        worker.start()
        original_connection = self.app._http_client.HTTPConnection
        try:
            def local_server(host, port, timeout):
                self.assertEqual((host, port), ("127.0.0.1", 8777))
                return original_connection("127.0.0.1", server.server_port, timeout=timeout)

            self.app._opener = self.app._LoopbackHTTP()
            with mock.patch.object(self.app._http_client, "HTTPConnection", side_effect=local_server):
                self.app._poll_once()
                self.assertTrue(self.app._latest["reachable"])
                self.enable_game()
                self.sample_grid()
                self.app._post_bridge_once()
                self.app._queue_radio("fuel")
                self.app._post_radio_once()
            self.assertEqual(responses[0], ("GET", "/api/overlay", "close"))
            self.assertEqual(responses[1][0:2], ("POST", "/api/bridge"))
            self.assertEqual(responses[1][2]["car_id"], "gt3_car")
            self.assertEqual(responses[2], ("POST", "/api/radio", {"topic": "fuel"}))
            self.assertEqual(self.app._radio_status, "Fuel: reply ready")
        finally:
            server.shutdown()
            server.server_close()
            worker.join(timeout=1)


    def csp(self, sequence=1, **changes):
        packet = {"sequence": sequence, "session_nonce": "session-1", "car_id": "gt3_car",
                  "track_id": "ks_monza", "layout": "", "cars": [],
                  "player": {"lap_valid": False, "is_ai_controlled": False},
                  "compounds": [{"index": 0, "name": "Soft", "short_name": "S"}],
                  "pitstop": {"available": False, "mapping_verified": False}}
        packet.update(changes)
        return packet

    def pit_command(self, **changes):
        command = {"id": "request-1", "index": 0, "compound": "Soft", "car_id": "gt3_car",
                   "track_id": "ks_monza", "layout": "", "session_nonce": "session-1",
                   "expires_at": self.app.time.time() + 10}
        command.update(changes)
        return command

    def bind_csp(self, packet):
        def read(key):
            self.ac._record("ext_loadLua", key)
            return json.dumps(packet)

        def store(key, value):
            self.ac._record("ext_storeLua", key, value)
        self.ac.ext_loadLua = read
        self.ac.ext_storeLua = store

    def test_csp_sample_is_main_thread_only_and_keeps_definitive_provenance(self):
        self.bind_csp(self.csp())
        self.enable_game()
        self.sample_grid()
        self.assertIs(self.app._bridge_packet["csp"]["player"]["lap_valid"], False)
        self.assertFalse(self.app._bridge_packet["csp"]["pitstop"]["mapping_verified"])
        self.assertNotIn("pit_value", self.app._bridge_packet["csp"]["compounds"][0])
        before = len(self.ac.calls)
        self.app._opener = FakeOpener(b'{"ok":true}')
        worker = threading.Thread(target=self.app._post_bridge_once)
        worker.start()
        worker.join(timeout=1)
        self.assertEqual(len(self.ac.calls), before)
        body = json.loads(self.app._opener.requests[0][0].data)
        self.assertEqual(body["csp"]["sequence"], 1)

    def test_unchanged_csp_storage_expires_and_new_sequence_recovers(self):
        packet = self.csp()
        self.bind_csp(packet)
        with mock.patch.object(self.app.time, "monotonic", return_value=100):
            self.app._sample_bridge()
            self.assertIn("csp", self.app._bridge_packet)
        with mock.patch.object(self.app.time, "monotonic", return_value=104):
            self.app._sample_bridge()
            self.assertIsNone(self.app._bridge_packet)
            packet["sequence"] = 2
            self.app._sample_bridge()
            self.assertEqual(self.app._bridge_packet["csp"]["sequence"], 2)

    def test_csp_malformed_nonfinite_or_oversized_values_are_not_forwarded(self):
        for packet in (self.csp(sequence=True), self.csp(cars=[{}] * 65),
                       self.csp(player={"speed_kmh": float("nan")}), self.csp(session_nonce=123)):
            self.bind_csp(packet)
            self.app._sample_bridge()
            self.assertIsNone(self.app._bridge_packet)
        self.ac.ext_loadLua = lambda key: "x" * (self.app.MAX_RESPONSE_BYTES + 1)
        self.app._sample_bridge()
        self.assertIsNone(self.app._bridge_packet)

    def test_pit_command_only_queued_on_worker_and_stored_once_on_main_thread(self):
        self.bind_csp(self.csp())
        self.app._sample_bridge()
        command = self.pit_command()
        self.app._opener = FakeOpener(json.dumps(self.snapshot(pit_command=command)).encode())
        before = len(self.ac.calls)
        worker = threading.Thread(target=self.app._poll_once)
        worker.start()
        worker.join(timeout=1)
        self.assertEqual(len(self.ac.calls), before)
        self.assertEqual(self.app._pit_pending["id"], command["id"])
        self.assertTrue(self.app._forward_pit_request())
        writes = [args for name, args in self.ac.calls if name == "ext_storeLua"]
        self.assertEqual(len(writes), 1)
        self.assertEqual(writes[0][0], ".apex.pit.request")
        self.assertEqual(json.loads(writes[0][1]), command)
        self.app._poll_once()
        self.assertFalse(self.app._forward_pit_request())
        self.assertEqual(len([name for name, args in self.ac.calls if name == "ext_storeLua"]), 1)
        # A store relays a request; it never invents a successful tyre ACK.
        self.assertNotIn("ack", self.app._latest["snapshot"])

    def test_pit_command_rejects_expiry_wrong_context_demo_and_unknown_indices(self):
        self.bind_csp(self.csp())
        self.app._sample_bridge()
        for changes in ({"expires_at": self.app.time.time() - 1}, {"expires_at": self.app.time.time() + 100},
                        {"session_nonce": "other"}, {"car_id": "other"}, {"track_id": "other"},
                        {"layout": "other"}, {"index": True}, {"index": -1}):
            self.app._opener = FakeOpener(json.dumps(self.snapshot(pit_command=self.pit_command(**changes))).encode())
            self.app._poll_once()
            self.assertFalse(self.app._forward_pit_request())
        self.app._opener = FakeOpener(json.dumps(self.snapshot(mode="demo", pit_command=self.pit_command())).encode())
        self.app._poll_once()
        self.assertFalse(self.app._forward_pit_request())
        self.assertEqual(len([name for name, args in self.ac.calls if name == "ext_storeLua"]), 0)

    def test_pit_command_stale_overlay_or_csp_cannot_be_applied(self):
        self.bind_csp(self.csp())
        self.app._sample_bridge()
        self.app._opener = FakeOpener(json.dumps(self.snapshot(pit_command=self.pit_command())).encode())
        self.app._poll_once()
        self.app._latest["received_at"] -= 4
        self.assertFalse(self.app._forward_pit_request())
        self.app._poll_once()
        self.app._csp_received_at -= 4
        self.assertFalse(self.app._forward_pit_request())

    def test_csp_ack_is_relayed_unchanged_never_inferred_from_http_success(self):
        acknowledgement = {"id": "request-1", "ok": False, "requested_index": 0,
                           "message": "Game rejected the selected compound."}
        self.bind_csp(self.csp(ack=acknowledgement))
        self.app._sample_bridge()
        self.assertEqual(self.app._bridge_packet["csp"]["ack"], acknowledgement)

    def test_expanded_mfd_has_race_gaps_feedback_and_compound_topics(self):
        topics = {topic for topic, label in self.app.RADIO_TOPICS}
        self.assertTrue({"personal_best", "race_best", "others_best", "gap_ahead", "gap_behind", "gap_leader", "feedback", "change_compound"}.issubset(topics))


if __name__ == "__main__":
    unittest.main()
