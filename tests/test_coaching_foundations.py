"""Sourced context, exact identities, actual licensed traces and honest gaps."""
import copy
import hashlib
import json
import tempfile
import unittest

from engineer.coaching_foundations import FOUNDATIONS, FoundationLibrary, classify_car


class CoachingFoundationTests(unittest.TestCase):
    def setUp(self):
        self.library = FoundationLibrary()

    @staticmethod
    def frame(**changes):
        result = {"game": "assetto_corsa", "car_id": "bmw_z4_gt3",
                  "track_id": "ks_red_bull_ring", "layout": "layout_gp"}
        result.update(changes)
        return result

    def test_major_tracks_categories_and_each_background_combination_have_coverage(self):
        catalog = self.library.catalog()
        self.assertGreaterEqual(len(catalog["tracks"]), 40)
        categories = {item["id"] for item in catalog["categories"]}
        self.assertTrue({"gt3", "formula", "gt2", "gt4", "prototype", "road"} <= categories)
        tracks = {item["id"] for item in catalog["tracks"]}
        self.assertTrue({"red-bull-ring", "barcelona", "monza", "spa", "silverstone",
                         "imola", "mugello", "nordschleife", "brands-hatch", "laguna-seca",
                         "suzuka", "interlagos", "bathurst"} <= tracks)
        coverage = self.library.coverage()
        self.assertEqual(len(coverage["combinations"]), coverage["track_profiles"] * coverage["category_profiles"])
        self.assertEqual(coverage["human_research_traces"], 9)
        self.assertEqual(coverage["independent_research_collections"], 1)
        self.assertEqual(coverage["elite_traces"], 0)
        self.assertTrue(all(item["elite_traces"] == 0 and not item["automatic_target"] for item in coverage["combinations"]))

    def test_exact_known_car_categories_do_not_guess_from_road_or_mod_names(self):
        self.assertEqual(classify_car("ks_mercedes_amg_gt3"), "gt3")
        self.assertEqual(classify_car("ks_porsche_911_gt3_rs"), "road")
        self.assertEqual(classify_car("bmw_m3_gt2"), "gt2")
        self.assertEqual(classify_car("dallara_f317"), "formula")
        self.assertEqual(classify_car("rss_formula_hybrid_2017"), "formula")
        self.assertIsNone(classify_car("my_unknown_gt3_mod"))
        self.assertIsNone(classify_car("gt3"))
        self.assertIsNone(classify_car(None))
        context = self.library.context(self.frame(car_id="my_unknown_gt3_mod"))
        self.assertEqual(context["category"]["id"], "unknown")
        declared = self.library.context(self.frame(car_id="my_mod", car_category="gt4"))
        self.assertEqual(declared["category"]["identity_confidence"], "declared_category")
        self.assertFalse(declared["category"]["physics_verified"])

    def test_known_track_requires_exact_layout_and_empty_default_is_legitimate(self):
        monza = self.library.context(self.frame(track_id="monza", layout=""))
        self.assertEqual(monza["track"]["id"], "monza")
        self.assertTrue(monza["track"]["matched"])
        for layout in (None, "layout_national", "unknown"):
            with self.subTest(layout=layout):
                context = self.library.context(self.frame(layout=layout))
                self.assertFalse(context["coverage"]["track_profile"])
                self.assertEqual(context["best_driver_evidence"], [])
        legacy = self.library.context(self.frame(track_id="ks_zandvoort", layout=""))
        self.assertEqual(legacy["track"]["id"], "zandvoort-legacy")
        self.assertIn("Modern banking", " ".join(legacy["track"]["limits"]))

    def test_contradictory_renamed_folders_are_not_trusted(self):
        for track_id in ("F1-Hungaroring", "F1-Melbourne", "F1-Yas Marina"):
            with self.subTest(track_id=track_id):
                context = self.library.context(self.frame(track_id=track_id, layout=""))
                self.assertFalse(context["track"]["matched"])
                self.assertIn("conflicts", context["track"]["limits"][0])
        # An explicit caller verification can establish a mod's family, but
        # cannot establish its physical equivalence or spatial corner mapping.
        context = self.library.context(self.frame(track_id="some_mod", layout="gp",
                                                  track_profile_id="spa", track_profile_verified=True))
        self.assertEqual(context["track"]["id"], "spa")
        self.assertFalse(context["track"]["physics_verified"])
        self.assertFalse(context["track"]["normalized_corner_mapping"])

    def test_mercedes_is_not_given_bmw_research_trace_as_its_best_driver(self):
        context = self.library.context(self.frame(car_id="ks_mercedes_amg_gt3"))
        self.assertEqual(context["coverage"]["category_human_traces"], 1)
        self.assertEqual(context["coverage"]["exact_car_human_traces"], 0)
        self.assertEqual(context["best_driver_evidence"], [])
        self.assertFalse(context["coverage"]["automatic_external_target"])
        context = self.library.context(self.frame(car_id="dallara_f317"))
        self.assertEqual(context["coverage"]["exact_car_human_traces"], 1)
        self.assertEqual(context["best_driver_evidence"][0]["metadata"]["driver_skill"], "unverified_research_participant")

    def test_nine_actual_traces_have_pinned_provenance_units_and_integrity(self):
        recordings = self.library.catalog()["human_evidence"]
        pairs = set()
        for item in recordings:
            with self.subTest(item=item["id"]):
                reference = self.library.evidence(item["id"])
                metadata, trace = reference["metadata"], reference["trace"]
                pairs.add((metadata["car_id"], metadata["track_id"]))
                self.assertEqual(metadata["game"], "assetto_corsa")
                self.assertEqual(metadata["license"], "CC-BY-4.0")
                self.assertEqual(metadata["dataset_revision"], "ad1df12c49cce10f159ba31967744750bb9edd8b")
                self.assertEqual(metadata["manifest_commit"], "9e79864e4c5cf9971dc9cc260d20113b54198d9d")
                self.assertEqual(metadata["source_type"], "human")
                self.assertTrue(metadata["context_only"])
                self.assertFalse(metadata["verification"]["conditions"])
                self.assertFalse(metadata["verification"]["clean_lap"])
                self.assertFalse(reference["automatic_target"])
                self.assertEqual(metadata["conditions"]["fuel_rate"], 0)
                self.assertEqual(metadata["conditions"]["tyre_wear_rate"], 0)
                self.assertIsNone(metadata["conditions"]["weather"])
                self.assertLessEqual(metadata["max_tyres_off_track"], 2)
                self.assertEqual(hashlib.sha256((FOUNDATIONS / item["csv_file"]).read_bytes()).hexdigest(), metadata["csv_sha256"])
                self.assertGreater(len(trace), 4000)
                self.assertLessEqual(trace[0]["x"], .01)
                self.assertGreaterEqual(trace[-1]["x"], .99)
                self.assertTrue(all(a["t"] < b["t"] and a["x"] < b["x"] for a, b in zip(trace, trace[1:])))
                self.assertTrue(all(0 <= point["gas"] <= 1 and 0 <= point["brake"] <= 1 for point in trace))
                self.assertIsNone(trace[0]["steer"])
                self.assertEqual(reference["provenance"]["csv_sha256"], metadata["csv_sha256"])
        self.assertEqual(len(pairs), 9)

    def test_sourced_constraints_support_phase_feedback_without_invented_targets(self):
        context = self.library.context(self.frame(car_id="rss_formula_hybrid_2017", track_id="monza", layout=""))
        for note in context["principles"]:
            self.assertTrue(note["phases"])
            self.assertTrue(note["text"])
            self.assertTrue(note["sources"])
            self.assertTrue(note["limitations"])
            self.assertTrue(all(source["url"].startswith("https://") for source in note["sources"]))
        ids = {note["id"] for note in context["principles"]}
        self.assertIn("foundation-capabilities", ids)
        self.assertIn("foundation-energy", ids)
        self.assertIn("foundation-braking", ids)
        self.assertFalse(context["track"]["normalized_corner_mapping"])
        for item in self.library.catalog()["tracks"]:
            for forbidden in ("braking_point_m", "optimal_gear", "target_speed_kmh", "target_lap_ms", "normalized_pos"):
                self.assertNotIn(forbidden, item)

    def test_radio_rules_are_cited_and_real_quotes_are_short(self):
        radio = self.library.context(self.frame())["radio"]
        rules = {item["id"] for item in radio["rules"]}
        self.assertTrue({"action-first", "low-workload", "one-voice", "driver-control", "qualified-evidence"} <= rules)
        self.assertEqual(len(radio["examples"]), 2)
        quotes_by_source = {}
        for item in radio["examples"]:
            quotes_by_source.setdefault(item["source_id"], []).extend(item["quote"].split())
        self.assertTrue(all(len(words) <= 25 for words in quotes_by_source.values()))
        self.assertTrue(all(item["url"].startswith("https://") for item in radio["sources"]))

    def test_sources_and_ids_resolve_throughout_the_database(self):
        catalog = self.library.catalog()
        sources = {item["id"] for item in catalog["sources"] + catalog["knowledge"]["sources"]}
        principles = {item["id"] for item in catalog["principles"]}
        for item in catalog["tracks"] + catalog["categories"] + catalog["principles"]:
            self.assertTrue(set(item.get("source_ids", [])) <= sources)
            self.assertTrue(set(item.get("priority_ids", []) + item.get("constraint_ids", [])) <= principles)
        self.assertEqual(len({item["id"] for item in catalog["tracks"]}), len(catalog["tracks"]))

    def test_no_cached_data_can_be_mutated_through_public_methods(self):
        context = self.library.context(self.frame())
        context["category"]["name"] = "changed"
        context["principles"][0]["sources"][0]["title"] = "changed"
        context["best_driver_evidence"][0]["metadata"]["car_id"] = "changed"
        again = self.library.context(self.frame())
        self.assertEqual(again["category"]["id"], "gt3")
        self.assertNotEqual(again["category"]["name"], "changed")
        self.assertNotEqual(again["principles"][0]["sources"][0]["title"], "changed")
        trace = self.library.evidence("acgym-rbr-bmw")
        trace["trace"][0]["speed"] = -1
        self.assertGreaterEqual(self.library.evidence("acgym-rbr-bmw")["trace"][0]["speed"], 0)

    def test_other_games_and_invalid_evidence_ids_never_match(self):
        context = self.library.context(self.frame(game="assetto_corsa_competizione"))
        self.assertFalse(context["track"]["matched"])
        self.assertEqual(context["best_driver_evidence"], [])
        for identifier in ("../../private", "acgym-../../private", "acgym-unknown", None):
            with self.subTest(identifier=identifier), self.assertRaises(ValueError):
                self.library.evidence(identifier)


if __name__ == "__main__":
    unittest.main()
