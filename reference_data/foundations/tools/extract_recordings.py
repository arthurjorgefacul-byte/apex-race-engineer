"""Offline, reproducible conversion of pinned CC-BY AssettoCorsaGym recordings.

Requires the existing unmodified GPL-3.0 gotzl/ldparser and its NumPy runtime on
PYTHONPATH. That reader is not copied into, or executed by, the companion.
Usage: python extract_recordings.py RESEARCH_DOWNLOAD_DIRECTORY
"""
import csv
import hashlib
import io
import json
import pathlib
import sys

import ldparser

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT.parents[1]))
from engineer.reference_data import parse_csv

REVISION = "ad1df12c49cce10f159ba31967744750bb9edd8b"
MANIFEST = "9e79864e4c5cf9971dc9cc260d20113b54198d9d"
CASES = [
    ("rbr_bmw", "red-bull-ring", "ks_red_bull_ring", "layout_gp", "bmw_z4_gt3", "gt3"),
    ("barcelona_bmw", "barcelona", "ks_barcelona", "layout_gp", "bmw_z4_gt3", "gt3"),
    ("monza_bmw", "monza", "monza", "", "bmw_z4_gt3", "gt3"),
    ("rbr_formula", "red-bull-ring", "ks_red_bull_ring", "layout_gp", "dallara_f317", "formula"),
    ("barcelona_formula", "barcelona", "ks_barcelona", "layout_gp", "dallara_f317", "formula"),
    ("monza_formula", "monza", "monza", "", "dallara_f317", "formula"),
    ("rbr_miata", "red-bull-ring", "ks_red_bull_ring", "layout_gp", "ks_mazda_miata", "road"),
    ("barcelona_miata", "barcelona", "ks_barcelona", "layout_gp", "ks_mazda_miata", "road"),
    ("monza_miata", "monza", "monza", "", "ks_mazda_miata", "road"),
]


def convert(downloads):
    provenance = []
    for name in ("more_download_provenance.json", "final_download_provenance.json"):
        provenance.extend(json.loads((downloads / name).read_text()))
    old = json.loads((downloads / "download_provenance.json").read_text())
    old["id"] = "rbr_bmw"
    provenance.append(old)
    provenance = {item["id"]: item for item in provenance}
    recordings = []
    (ROOT / "traces").mkdir(exist_ok=True)
    for name, profile, track_id, layout, car_id, category in CASES:
        source = downloads / ("rbr_bmw_z4_human_OP.ld" if name == "rbr_bmw" else name + ".ld")
        origin = provenance[name]
        if hashlib.sha256(source.read_bytes()).hexdigest() != origin["sha256"]:
            raise ValueError("Source integrity mismatch: " + name)
        recording = ldparser.ldData.fromfile(str(source))
        if recording.head.vehicleid != car_id or recording.head.venue != track_id:
            raise ValueError("Source header identity mismatch: " + name)
        channels = {channel.name: channel.data for channel in recording.channs}
        candidates = []
        for lap_index in sorted(set(channels["Session Lap Count"])):
            if lap_index <= 0:
                continue  # Never select an outlap/initial partial lap.
            indices = [i for i, value in enumerate(channels["Session Lap Count"]) if int(value) == lap_index]
            # Some channels wrap a sample before the lap-counter channel.
            while indices and channels["Car Pos Norm"][indices[0]] > .9:
                indices.pop(0)
            for j in range(1, len(indices)):
                if channels["Car Pos Norm"][indices[j - 1]] > .9 and channels["Car Pos Norm"][indices[j]] < .1:
                    indices = indices[:j]
                    break
            if not indices or indices[-1] + 2 >= len(channels["Last Lap Time"]):
                continue
            if (max(channels["Num Tires Off Track"][indices]) > 2 or
                    max(channels["Lap Invalidated"][indices]) != 0 or
                    max(channels["In Pit"][indices]) != 0):
                continue
            # Read the completed lap's explicitly recorded time after the
            # counter changes, not the previous lap's time or sample count.
            following = indices[-1] + 1
            while following < len(channels["Session Lap Count"]) and int(channels["Session Lap Count"][following]) == lap_index:
                following += 1
            if following >= len(channels["Last Lap Time"]):
                continue
            lap_ms = float(channels["Last Lap Time"][following]) * 1000
            data = io.StringIO(newline="")
            writer = csv.writer(data)
            writer.writerow(["normalized_pos", "lap_time_ms", "speed_kmh", "gas", "brake", "lat_g", "steer_deg"])
            for i in indices:
                writer.writerow([format(float(channels["Car Pos Norm"][i]), ".10f"),
                                 format(float(channels["Lap Time"][i]) * 1000, ".6f"),
                                 format(float(channels["Ground Speed"][i]), ".6f"),
                                 format(float(channels["Throttle Pos"][i]) / 100, ".8f"),
                                 format(float(channels["Brake Pos"][i]) / 100, ".8f"),
                                 format(float(channels["CG Accel Lateral"][i]), ".8f"),
                                 format(float(channels["Steering Angle"][i]), ".6f")])
            raw = data.getvalue().encode("utf-8")
            def channel_value(key, divisor=1):
                return float(channels[key][indices[0]]) / divisor if key in channels else None
            metadata = dict(kind="simulator", game="assetto_corsa", context_only=True,
                            title="Published human research lap - " + car_id + " / " + track_id,
                            car_id=car_id, track_id=track_id, layout=layout,
                            tyre_compound=recording.head.short_comment.replace("Tires: ", ""),
                            recorded_at=recording.head.datetime.isoformat(), recorded_timezone="not supplied",
                            source_url=origin["url"], source_sha256=origin["sha256"],
                            dataset_url="https://huggingface.co/datasets/dasgringuen/assettoCorsaGym",
                            dataset_revision=REVISION, manifest_commit=MANIFEST,
                            license="CC-BY-4.0", license_url="https://creativecommons.org/licenses/by/4.0/",
                            attribution="AssettoCorsaGym, Adrian Remonda et al., NeurIPS 2024",
                            lap_time_ms=lap_ms, lap_number=int(lap_index) + 1, lap_validity="observed_clean",
                            verification=dict(identity=True, conditions=False, clean_lap=False),
                            game_version=None, car_version=None, track_version=None, setup_id=None, bop=None,
                            conditions=dict(ambient_c=channel_value("Air Temp"), road_c=channel_value("Road Temp"),
                                            surface_grip=channel_value("Surface Grip", 100), fuel_l=channel_value("Fuel Level"),
                                            fuel_rate=channel_value("AID Fuel Rate"), tyre_wear_rate=channel_value("AID Tire Wear Rate"),
                                            ballast_kg=channel_value("Ballast"), restrictor_pct=None, weather=None),
                            driver="KC Sim Racer (dataset label)", driver_skill="unverified_research_participant",
                            source_recording="20240313_OP", source_type="human", penalties_enabled=channel_value("Penalties Enabled"),
                            max_tyres_off_track=int(max(channels["Num Tires Off Track"][indices])),
                            csv_sha256=hashlib.sha256(raw).hexdigest(),
                            transformations=["Selected fastest complete observed-clean lap in this one recording only; not a dataset-wide or expert record.",
                                             "Dropped lagging lap-counter wrap samples; no trajectory or driving inputs were invented.",
                                             "Converted seconds to milliseconds and pedal percentages to ratios; steering degrees remain an extra unnormalized column."],
                            parser_source="https://github.com/gotzl/ldparser", parser_license="GPL-3.0",
                            retrieved_at="2026-10-27")
            try:
                parsed = parse_csv(raw, metadata)
            except ValueError:
                continue
            candidates.append((lap_ms, raw, metadata, parsed["quality"]))
        if not candidates:
            raise ValueError("No complete observed-clean lap found: " + name)
        lap_ms, raw, metadata, quality = min(candidates, key=lambda candidate: candidate[0])
        (ROOT / "traces" / (name + ".csv")).write_bytes(raw)
        recordings.append(dict(id="acgym-" + name.replace("_", "-"), track_profile_id=profile,
                               category_id=category, csv_file="traces/" + name + ".csv", metadata=metadata,
                               quality=quality, automatic_target=False,
                               selection="fastest_observed_clean_lap_in_this_recording_only",
                               elite_verified=False, independent_collection="AssettoCorsaGym"))
        print(name, "lap", metadata["lap_number"], "ms", lap_ms, "samples", quality["samples"])
    (ROOT / "human_evidence.json").write_text(json.dumps(dict(schema=1, recordings=recordings), indent=2), encoding="utf-8")


if __name__ == "__main__":
    convert(pathlib.Path(sys.argv[1]))
