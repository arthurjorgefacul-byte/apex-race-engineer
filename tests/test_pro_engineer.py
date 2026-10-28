"""Protocol fixtures captured from the checksum-verified native v0.5.1 demo."""
import copy
import json
import pathlib
import unittest

from engineer.pro_engineer import ProEngineerReader

FIXTURES = pathlib.Path(__file__).parent / "fixtures"


def golden(kind):
    return json.loads((FIXTURES / ("pro-engineer-0.5.1-" + kind + ".json")).read_text(encoding="utf-8"))


class ProtocolTests(unittest.TestCase):
    def setUp(self):
        self.now = 1000.0
        self.reader = ProEngineerReader(demo=True, port=0, clock=lambda: self.now, process_check=lambda: True)

    def tearDown(self):
        self.reader.close()

    def send(self, kind, obj=None, address=("127.0.0.1", 1234)):
        return self.reader.accept(json.dumps(obj or golden(kind)).encode(), address)

    def pair(self):
        self.assertTrue(self.send("reading", address=("127.0.0.1", 1111)))
        self.assertTrue(self.send("summary", address=("127.0.0.1", 2222)))

    def test_actual_native_demo_packets_normalize_and_preserve_advice(self):
        self.pair()
        frame = self.reader.read()
        self.assertEqual(frame["source"], "demo")
        self.assertEqual(frame["backend"], "pro_engineer")
        self.assertEqual(frame["car_id"], "ks_ferrari_sf70h")
        self.assertEqual(frame["track_id"], "monza")
        self.assertEqual(frame["gas"], golden("reading")["reading"]["car"]["throttle"])
        self.assertEqual(self.reader.snapshot()["messages"][-1]["text"], golden("summary")["advice"][0]["text"])
        self.assertEqual(self.reader.snapshot()["messages"][-1]["source"], "AC Pro Engineer")
        self.assertIsNone(frame["kers_charge"])
        self.assertFalse(frame["capabilities"]["energy"])

    def test_live_requires_actual_ac_process_and_never_relabels_demo(self):
        self.pair()
        self.reader.demo = False
        self.reader.process_check = lambda: False
        self.assertIsNone(self.reader.read())
        self.assertFalse(self.reader.snapshot()["connected"])

    def test_process_probe_is_shared_and_expires_when_game_exits(self):
        self.pair()
        self.reader.demo = False
        calls = []
        alive = [True]
        self.reader.process_check = lambda: calls.append(1) or alive[0]
        self.assertIsNotNone(self.reader.read())
        for _ in range(20):
            self.assertTrue(self.reader.connected)
            self.assertTrue(self.reader.snapshot()["connected"])
        self.assertEqual(len(calls), 1)
        alive[0] = False
        self.now += 0.51
        self.assertIsNone(self.reader.read())
        self.assertFalse(self.reader.connected)
        self.assertEqual(len(calls), 2)

    def test_unknown_schema_game_and_release_are_rejected(self):
        for key, value in (("schema", 2), ("game", "assetto_corsa_competizione"), ("app_version", "99.0")):
            packet = golden("summary")
            packet[key] = value
            self.assertFalse(self.send("summary", packet))

    def test_remote_sender_nan_bad_json_and_missing_shape_are_rejected(self):
        self.assertFalse(self.send("reading", address=("192.168.1.2", 11)))
        self.assertFalse(self.reader.accept(b'{"value": NaN}'))
        self.assertFalse(self.reader.accept(b"[]"))
        self.assertFalse(self.reader.accept(b"{"))
        self.assertFalse(self.reader.accept(b" " * 65508))
        bad = golden("reading")
        del bad["reading"]["car"]
        self.assertFalse(self.send("reading", bad))

    def test_duplicate_and_reordered_packets_do_not_refresh_liveness(self):
        self.pair()
        stamp = self.reader.raw_at
        self.now += 0.5
        self.assertFalse(self.send("reading"))
        self.assertEqual(stamp, self.reader.raw_at)
        packet = golden("reading")
        packet["sequence"] -= 1
        self.assertFalse(self.send("reading", packet))

    def test_actual_release_zero_summary_sequence_uses_lap_timer_order(self):
        self.pair()
        packet = golden("summary")
        self.assertEqual(packet["sequence"], 0)
        packet["current_lap_ms"] += 120
        self.now += 0.12
        self.assertTrue(self.send("summary", packet))
        packet["current_lap_ms"] -= 240
        self.assertFalse(self.send("summary", packet))

    def test_stale_data_and_advice_disappear(self):
        self.pair()
        self.now += 2.1
        self.assertIsNone(self.reader.read())
        self.assertEqual(self.reader.snapshot()["messages"], [])
        self.assertEqual(self.reader.snapshot()["debrief"], [])

    def test_sender_changes_and_missing_connected_flag_cannot_mix(self):
        self.pair()
        bad = golden("reading")
        bad["from"] = "another driver"
        bad["sequence"] += 1
        self.assertFalse(self.send("reading", bad))
        bad = golden("summary")
        bad["flags"] &= ~2
        bad["sequence"] += 1
        self.assertTrue(self.send("summary", bad))
        self.assertIsNone(self.reader.read())

    def test_adjacent_lap_datagrams_are_transient_not_joined(self):
        self.pair()
        packet = golden("reading")
        packet["sequence"] += 1
        packet["reading"]["session"]["completed_laps"] += 1
        self.send("reading", packet)
        self.assertIsNone(self.reader.read())
        self.assertTrue(self.reader.transient)
        self.assertTrue(self.reader.connected)

    def test_remote_watcher_output_is_not_the_drivers_local_live_session(self):
        self.send("reading")
        packet = golden("summary")
        packet["flags"] |= 512
        self.send("summary", packet)
        self.assertIsNone(self.reader.read())
        self.assertFalse(self.reader.connected)

    def test_capabilities_gate_default_validity_and_tread_values(self):
        packet = golden("reading")
        packet["reading"]["capabilities"].update(lap_validity=False, tyre_edge_temps=False)
        self.send("reading", packet)
        self.send("summary")
        frame = self.reader.read()
        self.assertIsNone(frame["lap_valid"])
        self.assertEqual(frame["tyre_inner_c"], [None]*4)

    def test_same_car_session_restart_clears_old_zero_sequence_summary(self):
        self.pair()
        reading = golden("reading")
        reading["sequence"] += 1
        reading["reading"]["session"].update(completed_laps=0, current_lap_ms=0)
        summary = golden("summary")
        summary.update(lap_count=0, current_lap_ms=0)
        self.assertTrue(self.send("reading", reading))
        self.assertTrue(self.send("summary", summary))
        self.assertEqual(self.reader.read()["lap_count"], 0)

    def test_native_demo_lap_timer_wrap_with_fixed_lap_count_recovers(self):
        self.pair()
        reading = golden("reading")
        reading["sequence"] += 1
        reading["reading"]["session"]["current_lap_ms"] = 0
        summary = golden("summary")
        summary["current_lap_ms"] = 0
        self.assertTrue(self.send("reading", reading))
        self.assertTrue(self.send("summary", summary))
        self.assertTrue(self.reader.connected)
        self.assertEqual(self.reader.read()["lap_time_ms"], 0)

    def test_native_debrief_and_malformed_optional_content(self):
        self.send("reading")
        packet = golden("summary")
        packet["debrief"] = [{"lap_number": 1, "lap_time_ms": 80000, "lines": [{"text": "Measured native debrief", "severity": 1}]}]
        self.send("summary", packet)
        self.assertEqual(self.reader.snapshot()["debrief"][0]["lines"][0]["text"], "Measured native debrief")
        packet["sequence"] += 1
        packet.update(advice={}, debrief={})
        self.send("summary", packet)
        self.assertEqual(self.reader.snapshot()["messages"], [])


if __name__ == "__main__":
    unittest.main()
