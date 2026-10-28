import copy
import json
import unittest

from engineer.live_engineer import COMMANDS, LiveEngineer


class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now

    def advance(self, seconds):
        self.now += seconds


def frame(position=.1, **overrides):
    value = {
        "source": "live", "status": "live", "car_id": "gt3", "track_id": "test",
        "layout": "gp", "tyre_compound": "Medium", "normalized_pos": position,
        "track_length_m": 5000, "speed_kmh": 180, "lap_count": 1,
        "lap_valid": None, "is_ai_controlled": False, "in_pit": False, "wheels_out": 0,
        "session_type": "practice", "fuel_l": 30, "flag": 0,
        "tyre_pressure_psi": [26, 26.1, 26.2, 26.3], "tyre_core_c": [80] * 4,
        "kers_charge": None, "capabilities": {"energy": False},
    }
    value.update(overrides)
    return value


def trace(position=1, loss=.3):
    positions = [i / 1000 for i in range(int(position * 1000) + 1)]
    if not positions or positions[-1] < position:
        positions.append(position)
    result = []
    for x in positions:
        extra = loss * min(1, max(0, (x - .15) / .4))
        extra += loss * min(1, max(0, (x - .55) / .445))
        result.append({"x": x, "t": 60 * x + extra,
            "speed": 90 if loss and .2 <= x <= .28 else 100,
            "gas": .5 if .2 <= x <= .3 or .6 <= x <= .72 else 1,
            "brake": .6 if .15 <= x <= .2 or .55 <= x <= .6 else 0})
    return result


def analysis(position=.1, loss=.3, comparison=True, live=True):
    return {
        "session": {"id": "s1", "current_lap_eligible": True, "current_lap_flags": []},
        "reference": {"id": "r1", "clean": True, "lap_time_ms": 60000},
        "coaching": {"comparison_available": comparison, "live_comparison_available": live,
                     "current_delta_s": .2 if live else None},
        "fuel": {"consumption_l_per_lap": 2, "reserve_l": 2, "fuel_to_finish_l": None},
        "traces": {"reference": trace(loss=0), "active_reference": trace(loss=0), "live": trace(position, loss)},
        "corners": [
            {"number": 1, "start_pos": .2, "end_pos": .28, "section_start_pos": .15,
             "section_end_pos": .55, "reference_brake_pos": .15, "time_loss_s": .3,
             "full_throttle_delta_m": 15},
            {"number": 2, "start_pos": .6, "end_pos": .7, "section_start_pos": .55,
             "section_end_pos": 1, "reference_brake_pos": .55, "time_loss_s": .4,
             "braking_point_delta_m": -15},
        ],
        "laps": [{"id": "l2", "lap_time_ms": 61000, "clean": True, "delta_s": 1}],
    }


class LiveEngineerTests(unittest.TestCase):
    def setUp(self):
        self.clock = Clock()
        self.engineer = LiveEngineer(clock=self.clock)

    def update(self, position=.1, data=None, **overrides):
        return self.engineer.update(frame(position, **overrides), data or analysis(position))

    @staticmethod
    def events(result, kind):
        return [event for event in result["new_events"] if event["kind"] == kind]

    def test_track_limits_debounce_is_cautious_and_rearms(self):
        self.update(wheels_out=3)
        self.clock.advance(.2)
        self.assertFalse(self.events(self.update(wheels_out=3), "track_limits"))
        self.clock.advance(.1)
        calls = self.events(self.update(wheels_out=3), "track_limits")
        self.assertEqual(len(calls), 1)
        self.assertFalse(calls[0]["definitive"])
        self.assertTrue(calls[0]["immediate"])
        self.assertIn("not confirmed", calls[0]["text"])
        self.clock.advance(6)
        self.assertFalse(self.events(self.update(wheels_out=3), "track_limits"))
        self.update(wheels_out=0)
        self.clock.advance(.6)
        self.update(wheels_out=0)
        self.update(wheels_out=4)
        self.clock.advance(.2)
        self.assertEqual(len(self.events(self.update(wheels_out=4), "track_limits")), 1)

    def test_game_invalid_is_definitive_once_per_lap(self):
        calls = self.events(self.update(lap_valid=False), "track_limits")
        self.assertEqual(len(calls), 1)
        self.assertTrue(calls[0]["definitive"])
        self.clock.advance(10)
        self.assertFalse(self.events(self.update(lap_valid=False), "track_limits"))
        self.update(.99, lap_valid=False)
        self.clock.advance(.1)
        calls = self.events(self.update(.01, lap_count=2, lap_valid=False), "track_limits")
        self.assertEqual(len(calls), 1)

    def test_pit_or_missing_wheels_never_fabricates_offtrack_warning(self):
        self.update(wheels_out=4, in_pit=True)
        self.clock.advance(2)
        self.assertFalse(self.events(self.update(wheels_out=4, in_pit=True), "track_limits"))
        self.clock.advance(2)
        self.assertFalse(self.events(self.update(wheels_out=None), "track_limits"))

    def test_flags_are_original_ac_enums_and_only_changes_emit(self):
        self.assertIn("Yellow", self.events(self.update(flag=2), "flag")[0]["text"])
        self.clock.advance(12)
        self.assertFalse(self.events(self.update(flag=2), "flag"))
        self.assertIn("Blue", self.events(self.update(flag=1), "flag")[0]["text"])
        self.assertFalse(self.events(self.update(flag=128), "flag"))

    def test_fuel_warnings_use_fresh_fuel_and_reserve_and_safe_gap(self):
        result = self.update(fuel_l=1)
        event = self.events(result, "fuel")[0]
        self.assertEqual(event["priority"], 3)
        self.assertFalse(event["immediate"])
        self.assertEqual(event["speak_when"], "safe_gap")
        self.clock.advance(1)
        self.assertFalse(self.events(self.update(fuel_l=1), "fuel"))
        answer = self.engineer.ask("fuel", frame(fuel_l=5), analysis())
        self.assertIn("1.5 laps above", answer["text"])

    def test_strategy_never_assumes_race_or_unknown_fuel_coverage(self):
        data = analysis()
        data["fuel"]["fuel_to_finish_l"] = 35
        self.assertFalse(self.events(self.update(data=data), "strategy"))
        answer = self.engineer.ask("strategy", frame(session_type="race", fuel_l=None), data)
        self.assertIn("cannot assess", answer["text"])
        self.assertNotIn("covers", answer["text"])
        calls = self.events(self.update(data=data, session_type=2), "strategy")
        self.assertIn("5.0 litres short", calls[0]["text"])

    def test_unknown_consumption_and_energy_are_explicit(self):
        data = analysis()
        data["fuel"]["consumption_l_per_lap"] = None
        answer = self.engineer.ask("fuel", frame(), data)
        self.assertIn("needs a complete clean lap", answer["text"])
        for charge in (None, float("nan"), 1.1, -.1):
            current = frame(kers_charge=charge, capabilities={"energy": True})
            self.assertFalse(self.engineer.ask("energy", current, data)["available"])
            self.assertFalse(self.engineer.update(current, data)["availability"]["energy"])
        answer = self.engineer.ask("energy", frame(kers_charge=.6, capabilities={"energy": True}), data)
        self.assertIn("60 percent", answer["text"])
        self.assertIn("No validated deployment plan", answer["text"])

    def test_tyre_target_must_be_configured_verified_and_sustained(self):
        self.update(tyre_pressure_psi=[30] * 4)
        self.clock.advance(6)
        self.assertFalse(self.events(self.update(tyre_pressure_psi=[30] * 4), "tyres"))
        self.engineer.configure({"target_pressure_psi": 26, "target_pressure_verified": False})
        self.update(tyre_pressure_psi=[30] * 4)
        self.clock.advance(6)
        self.assertFalse(self.events(self.update(tyre_pressure_psi=[30] * 4), "tyres"))
        self.engineer.configure({"target_pressure_psi": 26})
        self.update(tyre_pressure_psi=[30] * 4)
        self.clock.advance(5.1)
        calls = self.events(self.update(tyre_pressure_psi=[30] * 4), "tyres")
        self.assertEqual(len(calls), 1)
        self.assertIn("your selected hot target", calls[0]["text"])
        self.assertIn("once warm", calls[0]["text"])

    def test_partial_tyre_reading_preserves_wheel_identity(self):
        answer = self.engineer.ask("tyres", frame(tyre_pressure_psi=[26, None, 27, 28]), analysis())
        self.assertIn("FR unavailable, RL 27.0", answer["text"])
        answer = self.engineer.ask("tyres", frame(tyre_pressure_psi=None), analysis())
        self.assertTrue(answer["available"])
        self.assertIn("80 degrees", answer["text"])
        self.assertIn("Pressures are unavailable", answer["text"])

    def test_multiple_upcoming_corners_not_one_priority_repeated(self):
        first = self.events(self.update(.1), "corner")
        self.assertEqual(first[0]["corner"], 1)
        self.assertEqual(first[0]["trigger_phase"], "approach")
        self.clock.advance(12)
        self.assertFalse(self.events(self.update(.1), "corner"))
        second = self.events(self.update(.5), "corner")
        self.assertEqual(second[0]["corner"], 2)
        self.assertIn("Recheck your braking marker", second[0]["text"])
        self.assertLess(len(second[0]["text"].split()), 20)

    def test_ai_unknown_controls_invalid_and_partial_laps_get_no_corner_coaching(self):
        for overrides in ({"is_ai_controlled": True}, {"is_ai_controlled": None},
                          {"wheels_out": None}, {"in_pit": None}, {"lap_valid": False}):
            engineer = LiveEngineer(clock=self.clock)
            self.assertFalse(self.events(engineer.update(frame(**overrides), analysis()), "corner"))
        data = analysis()
        data["session"]["current_lap_eligible"] = False
        self.assertFalse(self.events(self.update(data=data), "corner"))
        data["session"]["current_lap_flags"] = ["AI-controlled lap"]
        self.assertFalse(self.events(self.update(data=data), "corner"))

    def test_live_exit_reports_measure_active_reference_not_historical_one(self):
        data = analysis(.56, comparison=False)
        data["traces"]["reference"] = [{"x": p["x"], "t": p["t"] * 4} for p in trace(loss=0)]
        result = self.update(.56, data=data)
        reports = result["corner_reports"]
        self.assertEqual(len(reports), 1)
        self.assertAlmostEqual(reports[0]["time_loss_s"], .3, places=3)
        calls = self.events(result, "corner")
        self.assertEqual(calls[0]["trigger_phase"], "live_exit")
        self.assertEqual(reports[0]["exit_speed_kmh"] - reports[0]["reference_exit_speed_kmh"], -10)
        self.assertLess(len(calls[0]["text"].split()), 25)
        self.clock.advance(12)
        self.assertFalse(self.events(self.update(.56, data=data), "corner"))
        result = self.update(.996, data=analysis(.996, comparison=False))
        self.assertEqual(len(result["corner_reports"]), 2)
        self.assertEqual(result["corner_reports"][-1]["section_end_pos"], .995)
        self.assertEqual(self.events(result, "corner")[0]["corner"], 2)

    def test_live_report_requires_comparability_and_complete_trace(self):
        data = analysis(.56, live=False)
        self.assertFalse(self.update(.56, data=data)["corner_reports"])
        data = analysis(.56)
        data["traces"]["live"] = [point for point in data["traces"]["live"] if point["x"] > .3]
        self.assertFalse(self.update(.56, data=data)["corner_reports"])
        data = analysis(.56)
        data["traces"]["active_reference"] = []
        self.assertFalse(self.update(.56, data=data)["corner_reports"])

    def test_gain_call_requires_repeat_observations_and_resets_with_reference(self):
        data = analysis(.56, loss=-.3, comparison=False)
        self.assertFalse(self.events(self.update(.56, data=data), "corner"))
        self.update(.99, data=analysis(.99, loss=-.3, comparison=False))
        self.clock.advance(1)
        self.update(.01, data=analysis(.01, loss=-.3, comparison=False), lap_count=2)
        self.clock.advance(35)
        calls = self.events(self.update(.56, data=data, lap_count=2), "corner")
        self.assertIn("last two measured sections", calls[0]["text"])
        data["reference"]["id"] = "new-best"
        self.clock.advance(1)
        self.assertFalse(self.events(self.update(.56, data=data, lap_count=2), "corner"))

    def test_split_lap_counter_does_not_reset_corner_calls_twice(self):
        self.update(.99)
        self.clock.advance(.02)
        wrapped = self.update(.001, lap_count=1)
        serial = self.engineer._lap_serial
        self.clock.advance(.06)
        self.update(.002, lap_count=2)
        self.assertEqual(self.engineer._lap_serial, serial)
        self.assertEqual(wrapped["corner_reports"], [])

    def test_reference_session_pause_and_ai_clear_pending_corner_events(self):
        before = self.update(.1)
        generation = before["generation"]
        self.assertTrue([e for e in before["events"] if e["kind"] == "corner"])
        ai = self.update(.11, is_ai_controlled=True)
        self.assertFalse([e for e in ai["events"] if e["kind"] == "corner"])
        paused = self.update(status="paused")
        self.assertGreater(paused["generation"], generation)
        data = analysis()
        data["reference"]["id"] = "new-reference"
        changed = self.update(data=data)
        self.assertGreater(changed["generation"], paused["generation"])
        data["session"]["id"] = "s2"
        changed_session = self.update(data=data)
        self.assertGreater(changed_session["generation"], changed["generation"])
        self.assertTrue(all(e["generation"] == changed_session["generation"] for e in changed_session["events"]))

    def test_periodic_updates_have_selected_interval_and_factual_content(self):
        data = analysis(comparison=False)
        self.update(data=data)
        self.clock.advance(29)
        self.assertFalse(self.events(self.update(data=data), "status"))
        self.clock.advance(1)
        calls = self.events(self.update(data=data), "status")
        self.assertEqual(calls[0]["trigger_phase"], "periodic")
        self.assertIn("Pace check", calls[0]["text"])
        self.assertIn("Fuel range", calls[0]["text"])
        self.assertFalse(calls[0]["immediate"])
        self.engineer.configure({"verbosity": "less"})
        self.clock.advance(31)
        self.assertFalse(self.events(self.update(data=data), "status"))
        self.clock.advance(29)
        self.assertEqual(len(self.events(self.update(data=data), "status")), 1)

    def test_quiet_and_more_less_commands_change_preferences_and_generation(self):
        before = self.update()
        answer = self.engineer.ask("quiet", frame(), analysis())
        self.assertEqual(answer["action"], {"quiet": True})
        self.assertGreater(answer["generation"], before["generation"])
        self.assertFalse(self.events(self.update(), "corner"))
        self.assertTrue(self.events(self.update(fuel_l=.1), "fuel"))
        self.assertEqual(self.engineer.ask("more_updates", frame(), analysis())["action"], {"quiet": False, "verbosity": "more"})
        self.assertEqual(self.engineer.ask("less_updates", frame(), analysis())["verbosity"], "less")

    def test_events_expire_and_snapshot_is_detached_and_json_finite(self):
        result = self.update(flag=2, kers_charge=float("nan"))
        result["events"].clear()
        self.assertTrue(self.engineer.snapshot()["events"])
        self.clock.advance(13)
        result = self.update(.12, data=analysis(.12, comparison=False), flag=0)
        self.assertFalse(result["events"])
        json.dumps(result, allow_nan=False)

    def test_all_mfd_topics_return_answers_unknown_topics_are_unavailable(self):
        for topic, _ in COMMANDS:
            with self.subTest(topic=topic):
                answer = self.engineer.ask(topic, frame(), analysis())
                self.assertTrue(answer["text"])
                self.assertEqual(answer["kind"], "answer")
                self.assertEqual(answer["source"], "APEX live")
                json.dumps(answer, allow_nan=False)
        self.assertFalse(self.engineer.ask("invent_setup", frame(), analysis())["available"])
        self.assertFalse(self.engineer.ask("next_corner", frame(normalized_pos=None), analysis())["available"])

    def test_preferences_reject_nonfinite_and_boolean_numeric_values(self):
        for preferences in ({"fuel_reserve_l": True}, {"fuel_reserve_l": float("nan")},
                            {"target_pressure_psi": float("inf")}, {"quiet": "false"},
                            {"verbosity": "constant"}):
            with self.subTest(preferences=preferences), self.assertRaises(ValueError):
                self.engineer.configure(preferences)


if __name__ == "__main__":
    unittest.main()
