"""Reference provenance, unit conversion and fail-closed compatibility tests."""
import copy
import csv
import hashlib
import io
import json
import pathlib
import tempfile
import unittest

from engineer.reference_data import ASSETS, ReferenceLibrary, compatibility, load_knowledge, parse_csv, principles_for_corner, track_info


def metadata(**changes):
    result = {
        "kind": "simulator", "game": "assetto_corsa", "car_id": "test_gt3",
        "track_id": "test_track", "layout": "gp", "tyre_compound": "Medium",
        "recorded_at": "2026-10-27T10:00:00-03:00", "source_url": "local://user-export",
        "license": "LicenseRef-UserOwned", "rights_confirmed": True,
        "attribution": "Driver's own test export", "lap_time_ms": 60000,
        "game_version": "1.16.4", "car_version": "verified-test-v1",
        "track_version": "verified-track-v1", "setup_id": "baseline-sha256",
        "bop": "stock-no-server-adjustment", "lap_validity": "observed_clean",
        "verification": {"identity": True, "conditions": True, "clean_lap": True},
        "conditions": {"weather": "dry", "ambient_c": 23., "road_c": 28.,
                       "surface_grip": .98, "fuel_l": 30., "ballast_kg": 0.,
                       "restrictor_pct": 0., "fuel_rate": 1., "tyre_wear_rate": 1.},
    }
    result.update(changes)
    return result


def canonical_csv():
    text = io.StringIO(newline="")
    writer = csv.writer(text)
    writer.writerow(["normalized_pos", "lap_time_ms", "speed_kmh", "gas", "brake", "steer", "lat_g"])
    for index in range(601):
        position = index / 600
        writer.writerow([position, index * 100, 108, .7, .1, .2, .5])
    return text.getvalue().encode()


class ReferenceDataTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.library = ReferenceLibrary(pathlib.Path(self.temp.name))

    def tearDown(self):
        self.temp.cleanup()

    def test_bundled_human_lap_is_real_attributed_context_not_a_mercedes_target(self):
        reference = self.library.get("acgym-rbr-bmw-human-20240313-lap4")
        source = reference["metadata"]
        self.assertEqual(source["license"], "CC-BY-4.0")
        self.assertEqual(source["dataset_revision"], "ad1df12c49cce10f159ba31967744750bb9edd8b")
        self.assertEqual(source["car_id"], "bmw_z4_gt3")
        self.assertEqual(source["tyre_compound"], "Slick Medium (M)")
        self.assertTrue(source["context_only"])
        self.assertFalse(source["verification"]["conditions"])
        self.assertAlmostEqual(reference["lap_time_ms"], 98773.333333, places=3)
        self.assertGreater(len(reference["trace"]), 4900)
        self.assertFalse(reference["quality"]["steering_available"])
        self.assertEqual(hashlib.sha256((ASSETS / "acgym_rbr_bmw_human.csv").read_bytes()).hexdigest(), source["csv_sha256"])
        current = metadata(car_id="ks_mercedes_amg_gt3", track_id="ks_red_bull_ring", layout="layout_gp")
        eligibility = compatibility(source, current)
        self.assertFalse(eligibility["compatible"])
        self.assertIn("Different car_id.", eligibility["reasons"])

    def test_canonical_import_is_complete_and_metadata_matching_is_explicit(self):
        reference = parse_csv(canonical_csv(), metadata())
        self.assertEqual(reference["lap_time_ms"], 60000)
        self.assertEqual(len(reference["trace"]), 601)
        self.assertEqual(reference["trace"][0]["x"], 0)
        self.assertEqual(reference["trace"][-1]["x"], 1)
        self.assertEqual(reference["trace"][123]["gas"], .7)
        self.assertTrue(compatibility(reference["metadata"], metadata())["compatible"])
        self.assertFalse(compatibility(reference["metadata"], {})["compatible"])

    def test_explicit_empty_default_layout_matches_but_missing_layout_is_unknown(self):
        reference = parse_csv(canonical_csv(), metadata(layout=""))
        self.assertTrue(compatibility(reference["metadata"], metadata(layout=""))["compatible"])
        current = metadata(layout="")
        current.pop("layout")
        self.assertFalse(compatibility(reference["metadata"], current)["compatible"])
        self.assertFalse(compatibility(reference["metadata"], metadata(layout="gp"))["compatible"])

    def test_motec_preamble_and_units_convert_metres_speed_and_pedals(self):
        text = io.StringIO(newline="")
        writer = csv.writer(text)
        writer.writerow(["Format", "MoTeC CSV File"])
        writer.writerow(["Venue", "test_track"])
        writer.writerow(["Time", "Distance", "Ground Speed", "Throttle Pos", "Brake Pos", "Steering Angle"])
        writer.writerow(["s", "m", "m/s", "%", "%", "deg"])
        for index in range(601):
            writer.writerow([index / 10, index * 3, 30, 70, 10, 60])
        parsed = parse_csv(text.getvalue(), metadata(track_length_m=1800))
        self.assertEqual(parsed["trace"][100]["speed"], 108)
        self.assertEqual(parsed["trace"][100]["gas"], .7)
        self.assertEqual(parsed["trace"][100]["brake"], .1)
        self.assertIsNone(parsed["trace"][100]["steer"])
        self.assertFalse(parsed["quality"]["steering_available"])

    def test_similar_class_or_track_name_does_not_make_a_reference_compatible(self):
        original = metadata()
        changes = [
            {"game": "assetto_corsa_competizione"}, {"car_id": "another_gt3"},
            {"track_id": "similar_named_track_mod"}, {"layout": "short"},
            {"tyre_compound": "Soft"}, {"car_version": "changed-physics"},
            {"game_version": "1.16.3"}, {"setup_id": "different"},
            {"bop": "changed-restrictor"},
        ]
        for change in changes:
            with self.subTest(change=change):
                current = copy.deepcopy(original)
                current.update(change)
                self.assertFalse(compatibility(original, current)["compatible"])

    def test_conditions_and_verification_cannot_be_silently_invented(self):
        original = metadata()
        for condition in ("weather", "ambient_c", "road_c", "surface_grip", "fuel_l",
                          "ballast_kg", "restrictor_pct", "fuel_rate", "tyre_wear_rate"):
            with self.subTest(missing=condition):
                current = copy.deepcopy(original)
                current["conditions"].pop(condition)
                self.assertFalse(compatibility(original, current)["compatible"])
        changed = copy.deepcopy(original)
        changed["conditions"]["road_c"] = 40
        self.assertFalse(compatibility(original, changed)["compatible"])
        source = metadata(verification={"identity": True, "conditions": True, "clean_lap": False})
        self.assertFalse(compatibility(source, original)["compatible"])

    def test_real_world_lap_is_never_promoted_by_matching_labels(self):
        source = metadata(kind="real_world")
        parsed = parse_csv(canonical_csv(), source)
        self.assertTrue(parsed["metadata"]["context_only"])
        self.assertFalse(compatibility(parsed["metadata"], metadata())["compatible"])
        for benchmark in self.library.catalog()["benchmarks"]:
            self.assertEqual(benchmark["kind"], "real_world")
            self.assertTrue(benchmark["context_only"])

    def test_missing_provenance_permission_and_bad_values_are_rejected(self):
        for field in ("source_url", "license", "attribution", "recorded_at", "car_id", "layout"):
            bad = metadata()
            bad.pop(field)
            with self.subTest(field=field), self.assertRaises(ValueError):
                parse_csv(canonical_csv(), bad)
        for change in ({"rights_confirmed": False}, {"source_url": "javascript:alert(1)"},
                       {"recorded_at": "yesterday"}, {"conditions": []},
                       {"verification": []}, {"lap_time_ms": float("nan")}):
            with self.subTest(change=change), self.assertRaises(ValueError):
                parse_csv(canonical_csv(), metadata(**change))

    def test_non_numeric_partial_multilap_and_mismatched_time_are_rejected(self):
        raw = canonical_csv()
        bad_inputs = [raw.replace(b"108", b"=IMPORTXML(1)", 1),
                      raw.replace(b"108", b"NaN", 1),
                      raw.splitlines()[0] + b"\n" + b"\n".join(raw.splitlines()[101:]),
                      raw + b"0,100,108,0.7,0.1,0.2,0.5\n",
                      b"\x80\x04\x00pickle"]
        for bad in bad_inputs:
            with self.subTest(prefix=bad[:40]), self.assertRaises(ValueError):
                parse_csv(bad, metadata())
        with self.assertRaises(ValueError):
            parse_csv(raw, metadata(lap_time_ms=59000))
        ambiguous = raw.replace(b"speed_kmh", b"Speed")
        with self.assertRaisesRegex(ValueError, "explicit"):
            parse_csv(ambiguous, metadata())

    def test_import_storage_has_stable_ids_and_rejects_path_traversal(self):
        path = pathlib.Path(self.temp.name) / "lap.csv"
        path.write_bytes(canonical_csv())
        first = self.library.import_csv(path, metadata())
        second = self.library.import_csv(path, metadata())
        self.assertEqual(first["id"], second["id"])
        self.assertEqual(self.library.get(first["id"]), first)
        self.assertEqual(len(self.library.catalog(metadata())["references"]), 2)
        for identifier in ("../../settings", "reference_../file", "not-an-id"):
            with self.subTest(identifier=identifier), self.assertRaises(ValueError):
                self.library.get(identifier)

    def test_catalog_is_lightweight_and_does_not_expose_mutable_cached_data(self):
        first = self.library.catalog()
        self.assertNotIn("trace", first["references"][0])
        first["references"][0]["metadata"]["car_id"] = "changed"
        first["knowledge"]["principles"][0]["title"] = "changed"
        second = self.library.catalog()
        self.assertEqual(second["references"][0]["metadata"]["car_id"], "bmw_z4_gt3")
        self.assertNotEqual(second["knowledge"]["principles"][0]["title"], "changed")

    def test_official_corner_names_are_not_guessed_track_positions(self):
        track = track_info("ks_red_bull_ring", "layout_gp")
        self.assertEqual(track["official_turn_count"], 10)
        self.assertEqual(track["corners"][0]["name"], "Niki Lauda Turn")
        self.assertEqual(track["corners"][2]["name"], "Interwetten Turn")
        self.assertFalse(track["mapping_verified"])
        self.assertTrue(all(corner["normalized_pos"] is None for corner in track["corners"]))
        self.assertIsNone(track_info("ks_red_bull_ring", "layout_national"))

    def test_coaching_notes_have_known_primary_sources_and_explicit_limits(self):
        knowledge = load_knowledge()
        sources = {source["id"]: source for source in knowledge["sources"]}
        self.assertGreaterEqual(len(knowledge["principles"]), 8)
        for principle in knowledge["principles"]:
            self.assertTrue(principle["mechanism"])
            self.assertTrue(principle["experiment"])
            self.assertTrue(principle["limitations"])
            self.assertTrue(all(identifier in sources for identifier in principle["source_ids"]))
        self.assertTrue(all(source["url"].startswith("https://") for source in sources.values()))

    def test_corner_principles_use_actual_deltas_and_resolve_primary_sources(self):
        corner = {"time_loss_s": .4, "entry_speed_kmh": 153, "reference_entry_speed_kmh": 149,
                  "exit_speed_kmh": 118, "reference_exit_speed_kmh": 121,
                  "full_throttle_delta_m": 15, "braking_point_delta_m": 9}
        notes = principles_for_corner(corner)
        ids = {note["id"] for note in notes}
        self.assertIn("entry-is-not-exit", ids)
        self.assertIn("exit-throttle", ids)
        self.assertIn("brake-pressure-not-percent", ids)
        self.assertNotIn("smooth-inputs", ids)
        self.assertNotIn("brake-release-balance", ids)
        self.assertNotIn("coast-with-context", ids)
        for note in notes:
            self.assertEqual(note["status"], "inspection_hypothesis")
            self.assertTrue(note["text"])
            self.assertTrue(note["evidence"])
            self.assertTrue(note["sources"])
            self.assertTrue(all(source["url"].startswith("https://") for source in note["sources"]))
        faster = dict(corner, time_loss_s=-.2)
        self.assertEqual([note["id"] for note in principles_for_corner(faster)], ["distance-before-verdict"])
        self.assertEqual([note["id"] for note in principles_for_corner({})], ["distance-before-verdict"])
        self.assertEqual(principles_for_corner(None), [])


if __name__ == "__main__":
    unittest.main()
