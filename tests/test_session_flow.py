"""Session race behavior: unknown data, current evidence and confirmed pit plans."""
import copy
import json
import pathlib
import tempfile
import unittest

from engineer.lifecycle import SessionLifecycle
from engineer.race import RaceDirector


class Clock:
    def __init__(self):
        self.now = 100.0

    def __call__(self):
        return self.now


class RaceDirectorTests(unittest.TestCase):
    def setUp(self):
        self.clock = Clock()
        self.race = RaceDirector(self.clock)
        self.frame = {"source": "live", "status": "live", "car_id": "fixture_gt3", "track_id": "fixture_track",
                      "layout": "gp", "session_type": "race", "lap_count": 3, "normalized_pos": .4,
                      "tyre_compound": "Soft"}
        self.rows = [self.row(0, "You", 2, .4, 91000, 0),
                     self.row(1, "Ahead", 1, .6, 89500, 8.2),
                     self.row(2, "Behind", 3, .2, 92500, -6.4)]
        self.sequence = 0

    def row(self, ident, name, position, progress, best, gap):
        return {"id": ident, "name": name, "car": "fixture_gt3", "race_position": position,
                "lap_count": 3, "normalized_pos": progress, "speed_kmh": 150, "lap_time_ms": 30000,
                "last_lap_ms": 92000, "best_lap_ms": best, "gap_to_player_s": gap,
                "tyre_compound": "Soft", "last_lap_valid": True, "in_pit": False}

    def ingest(self, rows=None, *, csp=True, **changes):
        rows = copy.deepcopy(self.rows if rows is None else rows)
        payload = {key: self.frame[key] for key in ("car_id", "track_id", "layout")}
        payload["cars"] = rows
        if csp:
            self.sequence += 1
            detail = {**{key: self.frame[key] for key in ("car_id", "track_id", "layout")},
                      "sequence": self.sequence, "session_nonce": "fixture-session", "cars": rows,
                      "session_type": 3 if self.frame["session_type"] == "race" else 1,
                      "player": {"lap_count": self.frame["lap_count"], "normalized_pos": self.frame["normalized_pos"]},
                      "compounds": [{"index": 0, "name": "Soft", "short_name": "S", "pit_value": 1},
                                    {"index": 1, "name": "Medium", "short_name": "M", "pit_value": 2}],
                      "pitstop": {"available": True, "mapping_verified": True, "min": 0, "max": 2}}
            detail.update(changes)
            payload["csp"] = detail
        self.race.ingest(payload, self.frame)

    def ask(self, topic, **options):
        return self.race.ask(topic, self.frame, {"laps": []}, **options)

    def test_full_grid_and_gap_signs(self):
        self.ingest()
        self.assertEqual([1, 0, 2], [row["id"] for row in self.race.snapshot(self.frame)["cars"]])
        ahead, behind = self.ask("gap_ahead"), self.ask("gap_behind")
        self.assertEqual(8.2, ahead["gap_s"])
        self.assertIn("ahead", ahead["text"])
        self.assertEqual(6.4, behind["gap_s"])
        self.assertIn("behind", behind["text"])
        self.assertTrue(ahead["estimated"])
        self.assertEqual("Ahead", self.ask("gap_leader")["driver"]["name"])

    def test_no_time_gap_is_invented_from_progress_or_speed(self):
        rows = copy.deepcopy(self.rows)
        for row in rows:
            row.pop("gap_to_player_s")
        self.ingest(rows, csp=False)
        answer = self.ask("gap_ahead")
        self.assertFalse(answer["available"])
        self.assertNotIn("gap_s", answer)
        self.assertIn("unavailable", answer["text"])

    def test_qualifying_has_best_laps_but_no_race_gap(self):
        self.frame["session_type"] = "qualifying"
        self.ingest()
        self.assertFalse(self.ask("gap_ahead")["available"])
        answer = self.ask("race_best")
        self.assertTrue(answer["available"])
        self.assertIn("Session fastest", answer["text"])

    def test_incoherent_gap_is_not_spoken_as_fact(self):
        rows = copy.deepcopy(self.rows)
        rows[1]["gap_to_player_s"] = -8
        self.ingest(rows)
        answer = self.ask("gap_ahead")
        self.assertFalse(answer["available"])
        self.assertNotIn("gap_s", answer)

    def test_stale_or_different_session_grid_is_unavailable(self):
        self.ingest()
        self.clock.now += 3.1
        self.assertFalse(self.race.snapshot(self.frame)["available"])
        self.assertEqual([], self.ask("others_best")["drivers"])
        self.clock.now -= 3.1
        other = dict(self.frame, track_id="another_track")
        self.assertEqual([], self.race.snapshot(other)["cars"])
        with self.assertRaisesRegex(ValueError, "does not match"):
            self.race.ingest({"car_id": "wrong", "track_id": "fixture_track", "layout": "gp", "cars": self.rows}, self.frame)

    def test_best_laps_are_not_last_laps_or_ghost_bests(self):
        rows = copy.deepcopy(self.rows)
        rows[1]["best_lap_from_ghost"] = True
        rows[2].pop("best_lap_ms")
        self.ingest(rows)
        answer = self.ask("others_best")
        self.assertFalse(answer["available"])
        self.assertEqual([], answer["drivers"])
        self.assertEqual(91000, self.ask("personal_best")["lap_time_ms"])

    def test_driver_best_query_uses_that_driver_only(self):
        self.ingest()
        result = self.ask("others_best", driver_name="behind")
        self.assertEqual([2], [row["id"] for row in result["drivers"]])
        self.assertEqual(92500, result["lap_time_ms"])

    def test_compound_question_does_not_change_any_pit_setting(self):
        self.ingest()
        answer = self.ask("change_compound")
        self.assertTrue(answer["requires_input"])
        self.assertEqual(["Soft", "Medium"], [choice["compound"] for choice in answer["choices"]])
        self.assertIsNone(self.race.command(self.frame))
        self.assertNotIn("recommendation", answer)

    def test_only_observed_clean_complete_same_compound_laps_feed_pace(self):
        rows = copy.deepcopy(self.rows)
        self.ingest(rows)
        self.assertEqual([], self.race.snapshot(self.frame)["compound_pace"])
        for count in range(4, 7):
            rows[0]["lap_count"] = count
            rows[0]["last_lap_ms"] = 90000 + (count - 4) * 1000
            self.ingest(rows)
        answer = self.ask("change_compound")
        self.assertEqual(3, answer["recommendation"]["samples"])
        self.assertEqual(91000, answer["recommendation"]["pace_ms"])
        rows[0].update(lap_count=7, last_lap_ms=80000, last_lap_valid=False)
        self.ingest(rows)
        self.assertEqual(3, self.ask("change_compound")["recommendation"]["samples"])
        rows[0].update(lap_count=8, last_lap_ms=79000, last_lap_valid=True, tyre_compound="Medium")
        self.ingest(rows)
        self.assertEqual(3, self.ask("change_compound")["recommendation"]["samples"])

    def test_recorded_lap_retains_its_recorded_compound(self):
        self.ingest()
        self.race.observe_analysis({"laps": [{"id": "one", "clean": True, "lap_time_ms": 92000,
                                              "metadata": {"tyre_compound": "Medium"}}]}, self.frame)
        self.assertEqual("Medium", self.race.snapshot(self.frame)["compound_pace"][0]["compound"])
        self.race.observe_analysis({"laps": [{"id": "one", "clean": True, "lap_time_ms": 92000,
                                              "metadata": {"tyre_compound": "Medium"}}]}, self.frame)
        self.assertEqual(1, self.race.snapshot(self.frame)["compound_pace"][0]["samples"])

    def test_unverified_pit_mapping_never_queues_command(self):
        self.ingest(pitstop={"available": True, "mapping_verified": False})
        answer = self.ask("change_compound", compound="Medium")
        self.assertFalse(answer["available"])
        self.assertIsNone(self.race.command(self.frame))
        self.assertIn("No setting was changed", answer["text"])

    def test_choice_is_queued_then_requires_matching_acknowledgement(self):
        self.ingest()
        answer = self.ask("change_compound", compound="Medium")
        self.assertEqual("queued", answer["pit_plan"]["status"])
        command = self.race.command(self.frame)
        self.assertEqual(2, command["pit_value"])
        self.ingest(ack={"id": "unrelated", "ok": True, "actual_value": 2})
        self.assertEqual("queued", self.race.snapshot(self.frame)["pit_plan"]["status"])
        self.ingest(ack={"id": command["id"], "ok": True, "actual_value": 2})
        self.assertEqual("applied", self.race.snapshot(self.frame)["pit_plan"]["status"])
        self.assertIsNone(self.race.command(self.frame))

    def test_wrong_ack_value_and_timeout_never_claim_applied(self):
        self.ingest()
        self.ask("change_compound", compound="Medium")
        command = self.race.command(self.frame)
        self.ingest(ack={"id": command["id"], "ok": True, "actual_value": 1})
        self.assertEqual("failed", self.race.snapshot(self.frame)["pit_plan"]["status"])
        self.ask("change_compound", compound="Soft")
        self.clock.now += 11
        self.assertEqual("unconfirmed", self.race.snapshot(self.frame)["pit_plan"]["status"])
        self.assertIsNone(self.race.command(self.frame))

    def test_csp_enrichment_requires_matching_lap_and_position(self):
        self.ingest(player={"lap_count": 3, "normalized_pos": .4, "lap_valid": False, "is_ai_controlled": False})
        self.assertEqual(False, self.race.player_enrichment(self.frame)["lap_valid"])
        self.assertEqual({"session_nonce": "fixture-session"}, self.race.player_enrichment(dict(self.frame, lap_count=4)))
        self.assertEqual({"session_nonce": "fixture-session"}, self.race.player_enrichment(dict(self.frame, normalized_pos=.8)))

    def test_duplicate_grid_ids_rejected(self):
        with self.assertRaisesRegex(ValueError, "Duplicate"):
            self.ingest([self.rows[0], self.rows[0]])


class SessionLifecycleTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.clock = Clock()
        self.lifecycle = SessionLifecycle(self.folder.name, self.clock)
        self.frame = {"source": "live", "status": "live", "car_id": "fixture_gt3", "track_id": "track",
                      "layout": "gp", "session_type": "practice", "session_index": 0, "lap_count": 3}

    def analysis(self, laps=None, **extra):
        return {"session": {"id": "session-one"}, "laps": laps or [], **extra}

    def lap(self, ident, seconds=90, clean=True, count=3):
        return {"id": str(ident), "lap_time_ms": seconds * 1000, "clean": clean,
                "lap_count": count, "fuel_used_l": 2 if clean else None}

    def test_requires_explicit_start_and_can_arm_before_game_arrives(self):
        self.assertEqual("home", self.lifecycle.snapshot()["phase"])
        self.assertIsNone(self.lifecycle.observe(self.frame))
        self.assertFalse(self.lifecycle.active)
        self.assertIsNone(self.lifecycle.start())
        self.assertTrue(self.lifecycle.snapshot()["armed"])
        self.assertEqual("started", self.lifecycle.observe(self.frame))
        self.lifecycle.attach(self.analysis())
        self.assertEqual("session-one", self.lifecycle.snapshot()["active_session_id"])
        self.assertEqual("session", self.lifecycle.snapshot()["phase"])

    def test_paused_is_kept_but_replay_does_not_start(self):
        self.lifecycle.start(dict(self.frame, status="replay"))
        self.assertFalse(self.lifecycle.active)
        self.assertEqual("started", self.lifecycle.observe(dict(self.frame, status="paused")))
        self.clock.now += 100
        self.assertIsNone(self.lifecycle.observe(dict(self.frame, status="paused")))
        self.assertTrue(self.lifecycle.active)

    def test_disconnect_is_debounced_and_recovery_cancels_timer(self):
        self.lifecycle.start(self.frame)
        self.assertIsNone(self.lifecycle.observe(None, self.analysis([self.lap("one")])))
        self.clock.now += 2.9
        self.assertIsNone(self.lifecycle.observe(None))
        self.assertTrue(self.lifecycle.active)
        self.lifecycle.observe(self.frame)
        self.clock.now += 10
        self.assertIsNone(self.lifecycle.observe(None))
        self.clock.now += 3
        self.assertEqual("ended", self.lifecycle.observe(None))
        self.assertEqual(1, self.lifecycle.last_debrief["metrics"]["clean_laps"])
        self.assertFalse(self.lifecycle.active)
        self.assertIsNone(self.lifecycle.observe(self.frame))

    def test_game_session_change_closes_without_mixing_new_context(self):
        for change in ({"track_id": "different"}, {"session_type": "race"}, {"session_index": 1}, {"lap_count": 0}):
            with self.subTest(change=change):
                self.lifecycle.start(self.frame)
                self.lifecycle.attach(self.analysis([self.lap("one")]))
                self.assertEqual("ended", self.lifecycle.observe({**self.frame, **change}))
                self.assertEqual("track", self.lifecycle.last_debrief["track_id"])
                self.assertFalse(self.lifecycle.active)
                self.assertFalse(self.lifecycle.armed)

    def test_unknown_optional_fields_are_not_a_session_change(self):
        self.lifecycle.start({key: value for key, value in self.frame.items() if key != "session_index"})
        self.assertIsNone(self.lifecycle.observe(self.frame))
        self.assertEqual("ended", self.lifecycle.observe(dict(self.frame, session_index=1)))

    def test_csp_session_nonce_detects_new_session_with_same_car_track_and_lap(self):
        self.lifecycle.start(dict(self.frame, session_nonce="one"))
        self.assertEqual("ended", self.lifecycle.observe(dict(self.frame, session_nonce="two")))

    def test_removed_lap_is_excluded_until_explicit_restore_even_from_stale_snapshot(self):
        self.lifecycle.start(self.frame)
        analysis = self.analysis([self.lap("one"), self.lap("two", 92)])
        self.lifecycle.attach(analysis)
        self.lifecycle.forget_lap("one")
        self.lifecycle.attach(analysis)
        self.assertEqual(1, len(self.lifecycle._laps))
        self.lifecycle.restore_lap({**self.lap("one"), "session_id": "other-session"})
        self.assertEqual(1, len(self.lifecycle._laps))
        self.lifecycle.restore_lap({**self.lap("one"), "session_id": "session-one"})
        self.assertEqual(2, self.lifecycle.end()["metrics"]["clean_laps"])

    def test_debrief_retains_latest_usable_comparison_after_final_partial_or_new_baseline(self):
        self.lifecycle.start(self.frame)
        feedback = {"available": True, "priorities": [{"instruction": "Carry the lower entry speed into detected corner 2.",
                                                        "evidence": ["Section loss 0.3 seconds."]}]}
        comparable = self.analysis([self.lap("one"), self.lap("two")], feedback=feedback,
                                  coaching={"comparison_lap_id": "two", "comparison_reference_id": "one",
                                            "comparison_lap_number": 4, "reference_lap_number": 3})
        self.lifecycle.attach(comparable)
        self.lifecycle.attach(self.analysis([], feedback={"available": False}))
        result = self.lifecycle.end()
        self.assertEqual(feedback["priorities"][0]["instruction"], result["priorities"][0]["instruction"])
        self.assertIn("Earlier completed-lap", result["priorities"][0]["evidence"][-1])
        self.assertTrue(result["feedback_basis"]["historical"])
        self.assertEqual("two", result["feedback_basis"]["comparison_lap_id"])
        self.assertEqual("one", result["feedback_basis"]["comparison_reference_id"])

    def test_removing_comparison_reference_invalidates_cached_corner_diagnosis(self):
        self.lifecycle.start(self.frame)
        comparable = self.analysis([self.lap("one"), self.lap("two")],
                                  feedback={"available": True, "priorities": [{"instruction": "Old corner diagnosis"}]},
                                  coaching={"comparison_lap_id": "two", "comparison_reference_id": "one"})
        self.lifecycle.attach(comparable)
        self.lifecycle.forget_lap("one")
        self.lifecycle.attach(comparable)
        result = self.lifecycle.end()
        self.assertNotIn("Old corner diagnosis", str(result["priorities"]))
        self.assertIsNone(result["feedback_basis"])

    def test_finished_flag_ends_without_waiting_for_disconnect(self):
        self.lifecycle.start(self.frame)
        self.assertEqual("ended", self.lifecycle.observe(dict(self.frame, session_finished=True)))
        self.assertEqual("Session finished", self.lifecycle.last_debrief["reason"])

    def test_debrief_includes_measured_metrics_strengths_and_priorities(self):
        self.lifecycle.start(self.frame)
        feedback = {"available": True, "strengths": [{"text": "Better exit at detected corner 2."}],
                    "priorities": [{"instruction": "Brake earlier at detected corner 3.", "evidence": ["Entry was 8 m later."]}],
                    "next_session_plan": ["Keep the braking marker for three clean laps."], "limitations": ["One comparison."]}
        analysis = self.analysis([self.lap("a", 92, count=3), self.lap("b", 90, count=4), self.lap("c", 89, False, count=5)], feedback=feedback)
        self.lifecycle.attach(analysis)
        self.clock.now += 180
        result = self.lifecycle.end(foundation={"coverage": {"elite_traces": 0}})
        self.assertEqual(3, result["metrics"]["session_laps"])
        self.assertEqual(2, result["metrics"]["clean_laps"])
        self.assertEqual(90000, result["metrics"]["best_lap_ms"])
        self.assertEqual(1, result["metrics"]["consistency_s"])
        self.assertEqual(4, result["metrics"]["fuel_used_l"])
        self.assertEqual(180, result["metrics"]["duration_s"])
        self.assertEqual(feedback["priorities"], result["priorities"])
        self.assertEqual(0, result["foundation"]["coverage"]["elite_traces"])
        self.assertEqual("debrief", self.lifecycle.snapshot()["phase"])

    def test_long_sessions_are_not_limited_to_live_cache_and_repeated_updates_dedupe(self):
        self.lifecycle.start(self.frame)
        for i in range(100):
            current = self.analysis([self.lap(str(n), 90, count=n) for n in range(max(0, i - 29), i + 1)])
            self.lifecycle.attach(current)
            self.lifecycle.attach(current)
        result = self.lifecycle.end()
        self.assertEqual(100, result["metrics"]["session_laps"])
        self.assertEqual(100, result["metrics"]["clean_laps"])

    def test_separate_recording_contexts_do_not_mix_consistency(self):
        self.lifecycle.start(self.frame)
        self.lifecycle.attach(self.analysis([self.lap("a", 90), self.lap("b", 92)]))
        other = self.analysis([self.lap("c", 100), self.lap("d", 102)])
        other["session"]["id"] = "compound-change"
        self.lifecycle.attach(other)
        result = self.lifecycle.end()
        self.assertEqual(4, result["metrics"]["session_laps"])
        self.assertEqual(1, result["metrics"]["consistency_s"])
        self.assertEqual(["session-one", "compound-change"], result["session_ids"])

    def test_persisted_debrief_reopens_on_home_and_does_not_resume_recording(self):
        self.lifecycle.start(self.frame)
        result = self.lifecycle.end(self.analysis([self.lap("one")]))
        restored = SessionLifecycle(self.folder.name, self.clock)
        self.assertEqual(result, restored.last_debrief)
        self.assertEqual("home", restored.snapshot()["phase"])
        self.assertFalse(restored.active)
        self.assertFalse(restored.armed)
        saved = list((pathlib.Path(self.folder.name) / "debriefs").glob("*.json"))
        self.assertEqual(2, len(saved))
        self.assertTrue(all(json.loads(item.read_text(encoding="utf-8"))["schema"] == 1 for item in saved))

    def test_cancel_armed_session_does_not_make_empty_debrief(self):
        self.lifecycle.start()
        self.assertIsNone(self.lifecycle.end())
        self.assertFalse(self.lifecycle.armed)
        self.assertIsNone(self.lifecycle.last_debrief)

    def test_no_clean_evidence_does_not_invent_corner_diagnosis(self):
        self.lifecycle.start(self.frame)
        result = self.lifecycle.end(self.analysis([self.lap("one", 90, False)]))
        self.assertEqual([], result["strengths"])
        self.assertIsNone(result["metrics"]["best_lap_ms"])
        self.assertIsNone(result["metrics"]["fuel_used_l"])
        self.assertIn("baseline", result["priorities"][0]["instruction"])


if __name__ == "__main__":
    unittest.main()
