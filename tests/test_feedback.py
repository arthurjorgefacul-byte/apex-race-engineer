import copy
import json
import pathlib
import tempfile
import unittest

from engineer.analysis import Engineer
from engineer.feedback import FeedbackAnalyzer
from engineer.live_engineer import LiveEngineer
from engineer.coaching_foundations import FoundationLibrary
from tests.test_analysis import frame


def corner(number=1, **overrides):
    value = {"number": number, "start_pos": .2 + (number - 1) * .3,
        "end_pos": .28 + (number - 1) * .3, "section_start_pos": .15,
        "section_end_pos": .5, "time_loss_s": .4,
        "braking_point_delta_m": -15, "full_throttle_delta_m": None,
        "entry_speed_kmh": 100, "reference_entry_speed_kmh": 104,
        "exit_speed_kmh": 130, "reference_exit_speed_kmh": 130}
    value.update(overrides)
    return value


def record(lap_id, corners, **overrides):
    value = {"session_id": "s", "lap_id": lap_id, "reference_id": "ref",
             "clean": True, "comparable": True, "corners": corners}
    value.update(overrides)
    return value


def snapshot(corners=None, history=None):
    return {"session": {"id": "s"}, "reference": {"id": "ref", "clean": True},
        "coaching": {"comparison_available": True, "comparison_lap_id": "latest",
                     "comparison_reference_id": "ref", "comparison_lap_number": 4},
        "corners": corners or [corner()], "comparison_history": history or []}


class FeedbackTests(unittest.TestCase):
    def test_repeated_largest_loss_precedes_single_outlier(self):
        first, second = corner(), corner(2, time_loss_s=2)
        data = snapshot([first, second], [record("a", [corner(time_loss_s=.5)])])
        result = FeedbackAnalyzer.analyze(data)
        self.assertEqual(result["priorities"][0]["corner"], 1)
        self.assertEqual(result["priorities"][0]["observations"], 2)
        self.assertTrue(result["priorities"][0]["repeated"])
        self.assertIn("small move", result["instruction"])
        self.assertIn("one variable", result["next_session_plan"][-1])
        self.assertEqual(len(result["priorities"]), 2)

    def test_one_comparison_does_not_claim_repeat_or_optimal_braking(self):
        result = FeedbackAnalyzer.analyze(snapshot())
        self.assertEqual(result["priorities"][0]["confidence"], "single_comparison")
        self.assertIn("Recheck", result["instruction"])
        self.assertIn("One comparison", result["summary"])
        self.assertIn("onset position alone", " ".join(result["priorities"][0]["caveats"]))

    def test_duplicate_lap_does_not_inflate_evidence(self):
        history = [record("latest", [corner()]), record("latest", [corner()])]
        result = FeedbackAnalyzer.analyze(snapshot(history=history))
        self.assertEqual(result["priorities"][0]["observations"], 1)

    def test_other_session_reference_geometry_invalid_or_deleted_not_repeated(self):
        for changes in ({"session_id": "other"}, {"reference_id": "other"},
                        {"clean": False}, {"comparable": False}):
            with self.subTest(changes=changes):
                result = FeedbackAnalyzer.analyze(snapshot(history=[record("a", [corner()], **changes)]))
                self.assertEqual(result["priorities"][0]["observations"], 1)
        changed = corner(start_pos=.3)
        result = FeedbackAnalyzer.analyze(snapshot(history=[record("a", [changed])]))
        self.assertEqual(result["priorities"][0]["observations"], 1)

    def test_missing_reference_identity_cannot_claim_repeat(self):
        data = snapshot(history=[record("a", [corner()], reference_id=None)])
        data["reference"].pop("id")
        data["coaching"].pop("comparison_reference_id")
        self.assertEqual(FeedbackAnalyzer.analyze(data)["priorities"][0]["observations"], 1)

    def test_earlier_throttle_with_lower_exit_never_says_to_add_more(self):
        result = FeedbackAnalyzer.corner(corner(full_throttle_delta_m=-20,
            exit_speed_kmh=120, reference_exit_speed_kmh=130))
        self.assertEqual(result["phase"], "exit")
        self.assertIn("Settle the car", result["instruction"])
        self.assertIn("does not prove wheelspin", " ".join(result["caveats"]))
        self.assertNotIn("wheelspin", result["instruction"])

    def test_higher_entry_lower_exit_prioritizes_exit_not_more_speed(self):
        result = FeedbackAnalyzer.corner(corner(entry_speed_kmh=110,
            reference_entry_speed_kmh=100, exit_speed_kmh=120, reference_exit_speed_kmh=130))
        self.assertEqual(result["phase"], "entry")
        self.assertIn("stop pushing", result["instruction"])
        self.assertIn("calmer entry", result["experiment"])

    def test_later_throttle_instruction_has_stability_constraint(self):
        result = FeedbackAnalyzer.corner(corner(full_throttle_delta_m=20))
        self.assertEqual(result["phase"], "exit")
        self.assertIn("once the exit is settled", result["instruction"])
        self.assertIn("do not force throttle earlier", " ".join(result["caveats"]))

    def test_release_and_deceleration_are_measured_checks_not_diagnoses(self):
        release = FeedbackAnalyzer.corner(corner(brake_release_delta_m=15))
        self.assertEqual(release["phase"], "release")
        self.assertIn("does not establish an abrupt release", " ".join(release["caveats"]))
        braking = FeedbackAnalyzer.corner(corner(braking_point_delta_m=None,
            braking_deceleration_mps2=6, reference_braking_deceleration_mps2=7))
        self.assertEqual(braking["phase"], "braking")
        self.assertEqual(braking["measurements"]["mean_braking_deceleration_delta_mps2"], -1)
        self.assertIn("not brake efficiency", " ".join(braking["caveats"]))

    def test_gear_is_actual_or_unavailable_never_an_optimal_guess(self):
        missing = FeedbackAnalyzer.corner(corner())
        self.assertFalse(missing["measurements"]["gears"]["entry"]["available"])
        self.assertIn("gear comparison is unavailable", " ".join(missing["caveats"]))
        gear = FeedbackAnalyzer.corner(corner(braking_point_delta_m=None,
            exit_gear="4", reference_exit_gear="3", entry_gear=None, reference_entry_gear=None))
        self.assertEqual(gear["phase"], "exit")
        self.assertEqual(gear["measurements"]["gears"]["exit"], {"actual": "4", "reference": "3", "available": True})
        self.assertIn("Compare the exit gear", gear["instruction"])
        self.assertIn("reference gear 3", " ".join(gear["evidence"]))
        self.assertNotIn("optimal", gear["instruction"])

    def test_strength_requires_repeated_gain_not_one_lucky_section(self):
        gain = corner(time_loss_s=-.4)
        data = snapshot([gain], [record("a", [corner(time_loss_s=.1)])])
        self.assertEqual(FeedbackAnalyzer.analyze(data)["strengths"], [])
        data = snapshot([gain], [record("a", [corner(time_loss_s=-.3)])])
        self.assertEqual(len(FeedbackAnalyzer.analyze(data)["strengths"]), 1)
        self.assertEqual(FeedbackAnalyzer.analyze(data)["priorities"], [])

    def test_invalid_self_comparison_or_missing_data_never_instructs(self):
        for modify in (lambda value: value["coaching"].update(comparison_available=False),
                       lambda value: value["reference"].update(clean=False),
                       lambda value: value.update(lap={"clean": False})):
            data = snapshot()
            modify(data)
            result = FeedbackAnalyzer.analyze(data)
            self.assertFalse(result["available"])
            self.assertEqual(result["priorities"], [])
        self.assertFalse(FeedbackAnalyzer.analyze({})["available"])

    def test_foundations_explain_matching_phase_without_creating_targets(self):
        data = snapshot()
        foundation = {"category": "GT3", "coverage": "principles only", "principles": [
            {"id": "a", "phase": "braking", "text": "Keep braking repeatable.", "sources": [{"title": "Primary", "url": "https://example.org/a"}]},
            {"id": "b", "phase": "exit", "text": "Do not attach this to braking."}]}
        result = FeedbackAnalyzer.analyze(data, foundation)
        self.assertEqual(result["priorities"][0]["knowledge"][0]["id"], "a")
        self.assertEqual(len(result["priorities"][0]["knowledge"]), 1)
        self.assertEqual(result["foundation"]["category"], "GT3")
        self.assertNotIn("example.org", result["instruction"])

    def test_saved_clean_lap_without_reference_points_to_conditions_not_missing_laps(self):
        data = snapshot()
        data.update(lap={"id": "saved", "clean": True}, reference=None)
        data["coaching"]["comparison_available"] = False
        result = FeedbackAnalyzer.analyze(data)
        self.assertFalse(result["available"])
        self.assertEqual(result["instruction"], "No matching clean reference for this lap. Check conditions below.")
        self.assertIn("condition differences", result["next_session_plan"][0])
        self.assertNotIn("Build two", result["instruction"])

    def test_excluded_saved_lap_gets_specific_reason_and_live_initial_keeps_baseline_prompt(self):
        data = snapshot()
        data["lap"] = {"id": "excluded", "clean": False}
        result = FeedbackAnalyzer.analyze(data)
        self.assertFalse(result["available"])
        self.assertEqual(result["instruction"], "This lap is excluded; use a clean lap for measured coaching.")
        self.assertEqual(FeedbackAnalyzer.analyze({})["instruction"], "Build two comparable clean laps before choosing a technique change.")

    def test_pure_result_does_not_mutate_input_and_contains_finite_values(self):
        data = snapshot([corner(entry_rpm=float("nan"))])
        original = copy.deepcopy(data)
        result = FeedbackAnalyzer.analyze(data)
        self.assertEqual(data["corners"][0]["time_loss_s"], original["corners"][0]["time_loss_s"])
        self.assertNotIn("knowledge", data["corners"][0])
        json.dumps(result, allow_nan=False)

    def test_track_and_category_priorities_select_inspection_instead_of_generic_first_note(self):
        foundation = {"track": {"name": "Circuit", "matched": True, "priority_ids": ["linked"]},
            "category": {"name": "GT3", "priority_ids": ["load"], "constraint_ids": []},
            "principles": [{"id": "generic", "phases": ["all"], "text": "Generic first note."},
                {"id": "load", "phases": ["exit"], "text": "Inspect the pedal transition."},
                {"id": "linked", "phases": ["exit"], "text": "Check the linked section, not just one apex."}]}
        data = snapshot([corner(full_throttle_delta_m=20)])
        result = FeedbackAnalyzer.analyze(data, foundation)
        priority = result["priorities"][0]
        self.assertEqual([note["id"] for note in priority["knowledge"]], ["linked", "load"])
        self.assertIn("linked section", priority["experiment"])
        self.assertIn("linked section", result["next_session_plan"][1])
        self.assertEqual(priority["knowledge"][0]["context"]["track"], "Circuit")
        self.assertNotIn("linked section", result["instruction"])
        self.assertEqual(result["priorities"][0]["time_loss_s"], .4)

    def test_actual_foundation_changes_braking_inspection_without_inventing_targets(self):
        context = FoundationLibrary().context({"car_id": "ks_porsche_911_gt3_r_2016",
            "track_id": "ks_red_bull_ring", "layout": "layout_gp"})
        result = FeedbackAnalyzer.analyze(snapshot(), context)
        priority = result["priorities"][0]
        self.assertEqual(priority["knowledge"][0]["id"], "foundation-braking")
        self.assertEqual(priority["knowledge"][0]["scope"], "track_and_category")
        self.assertIn("deceleration and release", result["next_session_plan"][1])
        self.assertTrue(priority["knowledge"][0]["sources"])
        self.assertEqual(result["foundation"]["coverage"]["elite_traces"], 0)
        self.assertIn("own clean lap", priority["caveats"][0])

    def test_feedback_command_returns_short_instruction_and_on_request_details(self):
        result = LiveEngineer().ask("feedback", {}, snapshot())
        self.assertTrue(result["available"])
        self.assertIn("Recheck", result["text"])
        self.assertTrue(result["feedback"]["priorities"][0]["evidence"])
        self.assertLess(len(result["text"].split()), 35)


class FeedbackHistoryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.engineer = Engineer(pathlib.Path(self.temp.name))
        for i in range(70, 100):
            self.engineer.update(frame(0, i / 100, fuel=30, gear="3", rpm=7000))
        self.engineer.update(frame(1, 0, fuel=30, gear="3", rpm=7000))

    def tearDown(self):
        self.engineer.close()
        self.temp.cleanup()

    def lap(self, count, duration=60):
        for i in range(1, 100):
            self.engineer.update(frame(count, i / 100, duration=duration, fuel=30,
                                       gear="3", rpm=7000))
        return self.engineer.update(frame(count + 1, 0, duration=duration, fuel=30,
                                          gear="3", rpm=7000))

    def test_recording_history_is_clean_bounded_and_edit_rebuilds_it(self):
        first = self.lap(1)
        self.assertEqual(first["comparison_history"], [])
        latest = None
        for count in range(2, 12):
            latest = self.lap(count, duration=61)
        self.assertEqual(len(latest["comparison_history"]), 8)
        self.assertTrue(all(row["clean"] and row["comparable"] for row in latest["comparison_history"]))
        self.assertEqual(latest["coaching"]["comparison_reference_id"], first["reference"]["id"])
        removed = latest["comparison_history"][-1]["lap_id"]
        self.engineer.delete_lap(removed)
        edited = self.engineer.snapshot()
        self.assertNotIn(removed, [row["lap_id"] for row in edited["comparison_history"]])
        self.engineer.restore_lap(removed)
        self.assertIn(removed, [row["lap_id"] for row in self.engineer.snapshot()["comparison_history"]])

    def test_stored_comparison_history_is_independent_of_live_and_filters_reference(self):
        first = self.lap(1)
        second = self.lap(2, duration=61)
        third = self.lap(3, duration=62)
        live_id = third["coaching"]["comparison_lap_id"]
        with self.engineer.open_history() as history:
            saved = history.analyze_stored(third["session"]["id"], live_id, first["reference"]["id"])
        self.assertEqual(len(saved["comparison_history"]), 2)
        self.assertNotIn(first["reference"]["id"], [row["lap_id"] for row in saved["comparison_history"]])
        self.assertEqual(saved["coaching"]["comparison_reference_id"], first["reference"]["id"])
        self.assertEqual(self.engineer.snapshot()["coaching"]["comparison_lap_id"], live_id)
        self.engineer.reset()
        self.assertEqual(self.engineer.snapshot()["comparison_history"], [])


if __name__ == "__main__":
    unittest.main()
