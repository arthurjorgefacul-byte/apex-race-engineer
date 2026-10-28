"""Race answers use fresh session timing and observed compound laps only."""
import copy
import unittest

from engineer.race import RaceDirector


def frame(**changes):
    result = {"source": "live", "status": "live", "session_type": "race", "car_id": "gt3",
              "track_id": "track", "layout": "gp", "lap_count": 2, "normalized_pos": .5,
              "tyre_compound": "Medium"}
    result.update(changes)
    return result


def cars():
    return [dict(id=0, name="You", car="gt3", race_position=2, lap_count=2, normalized_pos=.5,
                 tyre_compound="Medium", best_lap_ms=91000, last_lap_ms=92000, last_lap_valid=True, in_pit=False),
            dict(id=1, name="Alex", car="gt3", race_position=1, lap_count=2, normalized_pos=.52,
                 tyre_compound="Soft", best_lap_ms=89000, last_lap_ms=90000, last_lap_valid=True, in_pit=False, gap_to_player_s=1.2),
            dict(id=2, name="Jo", car="formula", race_position=3, lap_count=2, normalized_pos=.45,
                 tyre_compound="Soft", best_lap_ms=93000, last_lap_ms=94000, last_lap_valid=True, in_pit=False, gap_to_player_s=-2.3)]


def payload(rows=None, sequence=1, **csp_changes):
    result = {"car_id": "gt3", "track_id": "track", "layout": "gp", "cars": rows or cars()}
    csp = dict(result, sequence=sequence, session_nonce="session-a", session_type=3,
        player={"lap_count": 2, "normalized_pos": .5, "lap_valid": False, "is_ai_controlled": False, "in_pit": False},
        compounds=[{"index": 0, "name": "Soft", "short_name": "S", "pit_value": 1},
                   {"index": 1, "name": "Medium", "short_name": "M", "pit_value": 2}],
        pitstop={"mapping_verified": True, "available": True, "read_only": False, "min": 0, "max": 2})
    csp.update(csp_changes)
    result["csp"] = csp
    return result


class RaceDirectorTests(unittest.TestCase):
    def setUp(self):
        self.now = 1000
        self.race = RaceDirector(clock=lambda: self.now)
        self.frame = frame()
        self.race.ingest(payload(), self.frame)

    def ask(self, topic, **values):
        return self.race.ask(topic, self.frame, {}, **values)

    def test_personal_best_uses_clean_session_and_keeps_record_scope_explicit(self):
        result = self.race.ask("personal_best", self.frame, {"laps": [
            {"clean": False, "lap_time_ms": 70000}, {"clean": True, "lap_time_ms": 90000}]},
            recorded_best={"lap_time_ms": 88000})
        self.assertEqual(result["lap_time_ms"], 90000)
        self.assertIn("recorded best", result["text"])
        self.assertIn("across recorded conditions", result["text"])

    def test_fastest_race_and_others_driver_selection(self):
        self.assertEqual(self.ask("race_best")["drivers"][0]["name"], "Alex")
        result = self.ask("others_best", driver_name="jo")
        self.assertEqual([r["name"] for r in result["drivers"]], ["Jo"])
        self.assertNotIn(0, [r["id"] for r in self.ask("others_best")["drivers"]])
        self.assertFalse(self.ask("others_best", driver_id=77)["available"])

    def test_ghost_best_not_race_record(self):
        rows = cars(); rows[1].update(best_lap_from_ghost=True, best_lap_ms=10000)
        self.race.ingest(payload(rows, sequence=2), self.frame)
        self.assertEqual(self.ask("race_best")["drivers"][0]["id"], 0)

    def test_signed_gaps_neighbours_and_leader(self):
        self.assertEqual(self.ask("gap_ahead")["gap_s"], 1.2)
        self.assertEqual(self.ask("gap_leader")["driver"]["name"], "Alex")
        self.assertEqual(self.ask("gap_behind")["gap_s"], 2.3)
        self.assertTrue(self.ask("gap_ahead")["estimated"])

    def test_gap_disagreement_and_missing_time_are_unavailable(self):
        rows = cars(); rows[1]["gap_to_player_s"] = -1
        self.race.ingest(payload(rows, sequence=2), self.frame)
        self.assertFalse(self.ask("gap_ahead")["available"])
        rows[1].pop("gap_to_player_s")
        self.race.ingest(payload(rows, sequence=3), self.frame)
        self.assertFalse(self.ask("gap_ahead")["available"])

    def test_no_practice_or_qualifying_time_gap_claims(self):
        self.frame["session_type"] = "practice"
        result = self.ask("gap_ahead")
        self.assertFalse(result["available"])
        self.assertNotIn("gap_to_player_s", self.race.snapshot(self.frame)["cars"][1])
        self.assertIn("Session fastest", self.ask("race_best")["text"])

    def test_csp_nonrace_gap_never_used_even_during_transition_to_race(self):
        self.race.ingest(payload(sequence=2, session_type=2), self.frame)
        self.assertFalse(self.ask("gap_ahead")["available"])

    def test_python_fallback_never_claims_csp_gap(self):
        other = payload(); other.pop("csp")
        self.now += 4
        self.race.ingest(other, self.frame)
        self.assertFalse(self.ask("gap_ahead")["available"])
        self.assertEqual(self.race.snapshot(self.frame)["source"], "AC Python timing")

    def test_leading_and_last_position(self):
        rows = cars(); rows[0]["race_position"] = 1; rows[1]["race_position"] = 2
        self.race.ingest(payload(rows, sequence=2), self.frame)
        self.assertEqual(self.ask("gap_leader")["text"], "You are leading.")
        self.assertEqual(self.ask("gap_ahead")["text"], "You are leading.")
        rows[0]["race_position"] = 3; rows[2]["race_position"] = 1
        self.race.ingest(payload(rows, sequence=3), self.frame)
        self.assertFalse(self.ask("gap_behind")["available"])

    def test_stale_sequence_does_not_extend_csp_freshness(self):
        self.now += 2
        self.race.ingest(payload(), self.frame)
        self.now += 1.1
        self.race.ingest(payload(), self.frame)
        self.assertFalse(self.race.snapshot(self.frame)["compounds"])
        self.assertFalse(self.ask("gap_ahead")["available"])

    def test_out_of_order_csp_sequence_cannot_replace_current(self):
        self.race.ingest(payload(sequence=5), self.frame)
        self.race.ingest(payload(sequence=4, compounds=[]), self.frame)
        self.assertEqual(len(self.race.snapshot(self.frame)["compounds"]), 2)

    def test_replay_disconnected_demo_and_other_track_cannot_use_live_timing(self):
        for changes in ({"status": "replay"}, {"status": "disconnected"}, {"is_replay": True}, {"track_id": "other"}):
            self.assertFalse(self.race.snapshot(frame(**changes))["available"])
            self.assertIsNone(self.race.command(frame(**changes)))
        demo = frame(source="demo")
        self.race.ingest(payload(), demo)
        self.assertFalse(self.race.snapshot(demo)["available"])

    def test_wrong_bridge_identity_and_duplicate_car_ids_rejected(self):
        wrong = payload(); wrong["track_id"] = "elsewhere"
        with self.assertRaises(ValueError): self.race.ingest(wrong, self.frame)
        with self.assertRaises(ValueError): self.race.ingest(payload([cars()[0], cars()[0]], sequence=2), self.frame)

    def test_coherent_player_enrichment_only(self):
        self.assertEqual(self.race.player_enrichment(self.frame)["lap_valid"], False)
        self.assertNotIn("lap_valid", self.race.player_enrichment(frame(lap_count=3)))
        self.assertNotIn("lap_valid", self.race.player_enrichment(frame(normalized_pos=.8)))
        self.race.ingest(payload(sequence=2, session_index=4), self.frame)
        enrichment = self.race.player_enrichment(frame(lap_count=3))
        self.assertEqual(enrichment["session_index"], 4)
        self.assertEqual(enrichment["session_nonce"], "session-a")
        self.now += 4
        self.assertNotIn("lap_valid", self.race.player_enrichment(self.frame))

    def test_compound_prompt_asks_and_does_not_assign_session_best_to_tyres(self):
        result = self.ask("change_compound")
        self.assertTrue(result["requires_input"])
        self.assertEqual(result["compound_pace"], [])
        self.assertEqual(len(result["choices"]), 2)
        self.assertIsNone(self.race.command(self.frame))

    def test_observed_valid_laps_group_exact_cars_and_require_three_for_advice(self):
        rows = cars()
        for count in range(3, 6):
            for row in rows: row["lap_count"] = count
            self.race.ingest(payload(copy.deepcopy(rows), sequence=count), self.frame)
        result = self.ask("change_compound")
        self.assertEqual(result["recommendation"]["car_id"], "gt3")
        self.assertEqual(result["recommendation"]["compound"], "Soft")
        self.assertEqual(result["recommendation"]["samples"], 3)
        self.assertEqual(len(result["compound_pace"]), 3)
        self.assertIn("Traffic and fuel are not matched", result["text"])
        self.assertIn("Soft has the quickest observed matching-car pace", result["text"])
        self.assertIn("Confirm a compound", result["text"])
        self.assertTrue(result["requires_input"])
        self.assertIsNone(self.race.command(self.frame))

    def test_invalid_pit_compound_change_and_missed_laps_not_pace_samples(self):
        for index, changes in enumerate(({"last_lap_valid": False}, {"in_pit": True}, {"tyre_compound": "Hard"}, {"lap_count": 9})):
            race = RaceDirector(clock=lambda: self.now)
            race.ingest(payload(), self.frame)
            rows = cars()
            for row in rows: row.update(lap_count=3, **changes) if "lap_count" not in changes else row.update(changes)
            race.ingest(payload(rows, sequence=2), self.frame)
            self.assertEqual(race.snapshot(self.frame)["compound_pace"], [], index)

    def test_csp_and_apex_same_player_lap_not_counted_twice(self):
        rows = cars(); rows[0]["lap_count"] = 3
        self.race.ingest(payload(rows, sequence=2), self.frame)
        self.race.observe_analysis({"laps": [{"id": "a", "lap_count": 2, "lap_time_ms": 92000,
            "clean": True, "metadata": {"tyre_compound": "Medium"}}]}, self.frame)
        pace = next(p for p in self.race.snapshot(self.frame)["compound_pace"] if p["compound"] == "Medium")
        self.assertEqual(pace["samples"], 1)

    def test_old_lap_without_compound_metadata_never_relabelled(self):
        self.race.observe_analysis({"laps": [{"id": "a", "lap_count": 2, "lap_time_ms": 92000, "clean": True}]}, self.frame)
        self.assertFalse(self.race.snapshot(self.frame)["compound_pace"])

    def test_pit_or_compound_change_observed_earlier_in_lap_excludes_pace(self):
        for intermediate in ({"in_pit": True}, {"tyre_compound": "Hard"}):
            race = RaceDirector(clock=lambda: self.now)
            race.ingest(payload(), self.frame)
            rows = cars()
            for row in rows: row.update(intermediate)
            race.ingest(payload(copy.deepcopy(rows), sequence=2), self.frame)
            rows = cars()
            race.ingest(payload(copy.deepcopy(rows), sequence=3), self.frame)
            for row in rows: row["lap_count"] = 3
            race.ingest(payload(rows, sequence=4), self.frame)
            self.assertEqual(race.snapshot(self.frame)["compound_pace"], [])

    def test_request_is_queued_then_confirmed_only_on_matching_readback(self):
        result = self.ask("change_compound", compound="S")
        self.assertEqual(result["pit_plan"]["status"], "queued")
        self.assertNotIn("set for", result["text"])
        command = self.race.command(self.frame)
        self.race.ingest(payload(sequence=2, ack={"id": command["id"], "ok": True, "actual_value": 1}), self.frame)
        self.assertEqual(self.race.snapshot(self.frame)["pit_plan"]["status"], "applied")
        self.assertIsNone(self.race.command(self.frame))
        self.assertEqual(len(self.race.notifications), 1)

    def test_unverified_mapping_readonly_or_out_of_range_never_queues(self):
        for changes in ({"mapping_verified": False}, {"available": False}, {"read_only": True}, {"max": 0}):
            current = payload(sequence=2)
            current["csp"]["pitstop"].update(changes)
            self.race.ingest(current, self.frame)
            self.assertFalse(self.ask("change_compound", compound="Soft")["available"])
            self.assertIsNone(self.race.command(self.frame))

    def test_failed_mismatched_ack_and_expiry_do_not_claim_applied(self):
        self.ask("change_compound", compound="Soft")
        command = self.race.command(self.frame)
        self.race.ingest(payload(sequence=2, ack={"id": command["id"], "ok": True, "actual_value": 2}), self.frame)
        self.assertEqual(self.race.snapshot(self.frame)["pit_plan"]["status"], "failed")
        self.ask("change_compound", compound="Soft")
        self.now += 11
        self.assertEqual(self.race.snapshot(self.frame)["pit_plan"]["status"], "unconfirmed")

    def test_session_nonce_change_resets_queued_commands_and_compound_history(self):
        self.ask("change_compound", compound="Soft")
        self.race.ingest(payload(sequence=1, session_nonce="session-b"), self.frame)
        self.assertIsNone(self.race.command(self.frame))
        self.assertIsNone(self.race.snapshot(self.frame)["pit_plan"])

    def test_bad_nested_optional_data_degrades_without_crash(self):
        self.race.ingest(payload(sequence=2, player=[], compounds=None, pitstop=[], ack=[]), self.frame)
        self.assertNotIn("lap_valid", self.race.player_enrichment(self.frame))
        self.assertFalse(self.ask("change_compound")["available"])


if __name__ == "__main__":
    unittest.main()
