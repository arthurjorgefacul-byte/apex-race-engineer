"""Provenance-aware lap imports and small, cited coaching/track catalogues.

The knowledge notes are paraphrases of named primary teaching sources. CSV
imports are data, never executable objects; this module never reads pickle,
downloads files, changes a game setup, or promotes a real-world lap to an AC
target. Matching metadata is an eligibility check, not a promise of equal pace.
"""
from __future__ import annotations

import copy
import csv
import datetime
import functools
import hashlib
import io
import json
import math
import pathlib
import re
from urllib.parse import urlparse

ASSETS = pathlib.Path(__file__).resolve().parents[1] / "reference_data"
MAX_BYTES = 25 * 1024 * 1024
MAX_ROWS = 200000
_IDENTITY = ("game", "car_id", "track_id", "layout", "tyre_compound")
_CONDITION_LIMITS = {"ambient_c": 5.0, "road_c": 3.0, "surface_grip": 0.02,
                     "fuel_l": 5.0, "ballast_kg": 0.1, "restrictor_pct": 0.1,
                     "fuel_rate": 0.0, "tyre_wear_rate": 0.0}


def _number(value):
    if isinstance(value, bool):
        return None
    try:
        result = float(value)
        return result if math.isfinite(result) else None
    except (TypeError, ValueError, OverflowError):
        return None


@functools.lru_cache(maxsize=8)
def _asset(name):
    return json.loads((ASSETS / name).read_text(encoding="utf-8"))


def load_knowledge():
    """Return original short notes with sources, mechanisms and limits."""
    return copy.deepcopy(_asset("knowledge.json"))


def principles_for_corner(corner):
    """Select cited checks from measured corner deltas, without a diagnosis.

    Thresholds select an explanation to inspect; they do not establish why the
    difference occurred. No release/steering/coasting fault is inferred from
    scalar speed, brake-onset and throttle-pickup differences alone.
    """
    if not isinstance(corner, dict):
        return []
    selected = [("distance-before-verdict", "Section comparisons use the same track positions.")]
    loss = _number(corner.get("time_loss_s", corner.get("delta_s")))
    throttle = _number(corner.get("full_throttle_delta_m"))
    brake = _number(corner.get("braking_point_delta_m"))
    entry = _number(corner.get("entry_speed_kmh"))
    reference_entry = _number(corner.get("reference_entry_speed_kmh"))
    exit_speed = _number(corner.get("exit_speed_kmh"))
    reference_exit = _number(corner.get("reference_exit_speed_kmh"))
    if loss is not None and loss > 0.08:
        if throttle is not None and throttle > 5:
            selected.append(("exit-throttle", "Sustained throttle pickup was {:.0f} m later in a section that lost {:.2f} s.".format(throttle, loss)))
        if entry is not None and reference_entry is not None and entry - reference_entry > 2 and (
                throttle is not None and throttle > 5 or exit_speed is not None and reference_exit is not None and exit_speed - reference_exit < -2):
            selected.append(("entry-is-not-exit", "Entry was {:.1f} km/h faster, while the section lost {:.2f} s and the exit or throttle pickup was worse.".format(entry - reference_entry, loss)))
        if brake is not None and abs(brake) > 5:
            selected.append(("brake-pressure-not-percent", "Braking onset was {:.0f} m {} in a section that lost {:.2f} s; onset alone does not explain the loss.".format(abs(brake), "later" if brake > 0 else "earlier", loss)))
    knowledge = _asset("knowledge.json")
    sources = {source["id"]: source for source in knowledge["sources"]}
    principles = {principle["id"]: principle for principle in knowledge["principles"]}
    output = []
    for identifier, evidence in selected:
        note = copy.deepcopy(principles[identifier])
        note["evidence"] = evidence
        note["text"] = note["mechanism"] + " " + note["check"]
        note["sources"] = [copy.deepcopy(sources[source_id]) for source_id in note["source_ids"]]
        note["status"] = "inspection_hypothesis"
        output.append(note)
    return output


def track_info(track_id, layout):
    """Official names only; no guessed mapping to detected corner segments."""
    for track in _asset("catalog.json")["tracks"]:
        if track_id in track["ac_track_ids"] and layout in track["ac_layouts"]:
            return copy.deepcopy(track)
    return None


def _conditions(metadata):
    nested = metadata.get("conditions")
    values = dict(nested) if isinstance(nested, dict) else {}
    for name in _CONDITION_LIMITS:
        if name not in values and name in metadata:
            values[name] = metadata[name]
    return values


def compatibility(metadata, context):
    """Fail closed when identity, conditions or source verification is unknown.

    Numeric windows below are explicit APEX screening policy, not universal
    vehicle-physics tolerances. Setup/BoP and version labels must also match.
    Unknown metadata remains useful for inspection, never an automatic target.
    """
    reasons = []
    if metadata.get("kind") != "simulator" or metadata.get("game") != "assetto_corsa":
        reasons.append("Real-world and other-simulator laps are context only.")
    if metadata.get("context_only"):
        reasons.append("The source marks this reference as context only.")
    for name in _IDENTITY:
        reference = metadata.get(name)
        current = context.get(name)
        # An explicitly empty AC layout is the legitimate default layout.
        # A missing key/None remains unknown and must not match by accident.
        if not isinstance(reference, str) or (name != "layout" and not reference.strip()):
            reasons.append("Reference " + name + " is unknown.")
        elif not isinstance(current, str) or (name != "layout" and not current.strip()):
            reasons.append("Current " + name + " is unknown.")
        elif reference != current:
            reasons.append("Different " + name + ".")
    verification = metadata.get("verification") or {}
    for check in ("identity", "conditions", "clean_lap"):
        if verification.get(check) is not True:
            reasons.append("Source verification is missing: " + check + ".")
    for name in ("game_version", "car_version", "track_version", "setup_id", "bop"):
        reference, current = metadata.get(name), context.get(name)
        if not isinstance(reference, str) or not reference.strip() or not isinstance(current, str) or not current.strip():
            reasons.append("Verified matching " + name + " is unavailable.")
        elif reference != current:
            reasons.append("Different " + name + ".")
    a, b = _conditions(metadata), _conditions(context)
    if not a.get("weather") or not b.get("weather"):
        reasons.append("Weather is unknown.")
    elif a["weather"] != b["weather"]:
        reasons.append("Different weather.")
    for name, tolerance in _CONDITION_LIMITS.items():
        av, bv = _number(a.get(name)), _number(b.get(name))
        if av is None or bv is None:
            reasons.append("Condition unavailable: " + name + ".")
        elif abs(av - bv) > tolerance + 1e-9:
            reasons.append("Different " + name + ".")
    if metadata.get("lap_validity") not in ("observed_clean", "game_validated"):
        reasons.append("A complete clean lap has not been verified.")
    return {"compatible": not reasons, "context_only": bool(reasons), "reasons": reasons,
            "note": "Metadata screening only; traffic, tyre state, assists, mod physics and setup can still change achievable pace."}


def _validate_metadata(metadata):
    if not isinstance(metadata, dict):
        raise ValueError("Reference metadata must be an object.")
    # Round-trip is also a finite-value and bounded-serializability check.
    try:
        encoded = json.dumps(metadata, allow_nan=False)
    except (TypeError, ValueError) as exc:
        raise ValueError("Metadata must contain finite JSON values.") from exc
    if len(encoded) > 65536:
        raise ValueError("Reference metadata is too large.")
    result = json.loads(encoded)
    for name in ("kind", "game", "car_id", "track_id", "layout", "tyre_compound",
                 "recorded_at", "source_url", "license", "attribution"):
        if not isinstance(result.get(name), str) or (name != "layout" and not result[name].strip()) or len(result[name]) > 2000:
            raise ValueError("Required reference metadata: " + name + ".")
    if result["kind"] not in ("simulator", "real_world"):
        raise ValueError("Reference kind must be simulator or real_world.")
    try:
        datetime.date.fromisoformat(result["recorded_at"][:10])
    except ValueError as exc:
        raise ValueError("recorded_at must start with an ISO date.") from exc
    url = urlparse(result["source_url"])
    if url.scheme not in ("https", "http", "local") or not url.netloc:
        raise ValueError("source_url must identify a public source or local://user-export.")
    if result["license"].startswith("LicenseRef-") and result.get("rights_confirmed") is not True:
        raise ValueError("Confirm ownership or permission for a private reference export.")
    if result.get("conditions") is not None and not isinstance(result["conditions"], dict):
        raise ValueError("conditions must be an object.")
    if result.get("verification") is not None and not isinstance(result["verification"], dict):
        raise ValueError("verification must be an object.")
    if result["kind"] == "real_world" or result["game"] != "assetto_corsa":
        result["context_only"] = True
    return result


def _key(text):
    return re.sub(r"[^a-z0-9]", "", str(text).lower())


# Names and units are data-format conventions, not a copy of a coaching engine.
_COLUMNS = {
    "x": ("x", "1"), "normalizedpos": ("x", "1"),
    "normalizedsplineposition": ("x", "1"), "carposnorm": ("x", "1"),
    "laptimems": ("t", "ms"),
    "times": ("t", "s"), "t": ("t", "s"), "time": ("t", None), "laptime": ("t", None),
    "speedkmh": ("speed", "km/h"), "groundspeed": ("speed", None), "speed": ("speed", None),
    "gas": ("gas", "1"), "throttle": ("gas", None), "throttlepos": ("gas", "%"),
    "brake": ("brake", "1"), "brakepos": ("brake", "%"),
    "steer": ("steer", "1"), "latg": ("lat_g", "g"), "cgaccellateral": ("lat_g", "g"),
    "distance": ("distance", None), "distancem": ("distance", "m"),
}


def _unit(unit):
    unit = str(unit or "").strip().lower().replace(" ", "")
    return {"kmh": "km/h", "kph": "km/h", "km/h": "km/h", "m/s": "m/s",
            "ms": "ms", "s": "s", "sec": "s", "%": "%", "percent": "%",
            "1": "1", "ratio": "1", "g": "g", "m": "m", "meter": "m"}.get(unit)


def parse_csv(raw, metadata):
    """Parse one complete canonical or explicitly unit-labelled MoTeC CSV lap.

    Unsupported/ambiguous units fail instead of guessing percentages or speed.
    Steering angle in degrees is deliberately not treated as normalized input.
    """
    metadata = _validate_metadata(metadata)
    if isinstance(raw, str):
        raw = raw.encode("utf-8")
    if len(raw) > MAX_BYTES:
        raise ValueError("Reference CSV exceeds 25 MB.")
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise ValueError("Export CSV as UTF-8 text.") from exc
    if "\0" in text:
        raise ValueError("Binary files and pickle are not supported.")
    try:
        delimiter = csv.Sniffer().sniff(text[:16384], delimiters=",;\t").delimiter
    except csv.Error:
        delimiter = ","
    rows = csv.reader(io.StringIO(text), delimiter=delimiter)
    mapping = None
    headers = None
    for index, row in enumerate(rows):
        if index > 100:
            break
        candidates = {i: _COLUMNS[_key(header)] for i, header in enumerate(row) if _key(header) in _COLUMNS}
        fields = {value[0] for value in candidates.values()}
        if {"t", "speed", "gas", "brake"} <= fields and ("x" in fields or "distance" in fields):
            mapping, headers = candidates, row
            break
    if mapping is None:
        raise ValueError("CSV needs lap time, track position or distance, speed, throttle and brake columns.")
    if len({value[0] for value in mapping.values()}) != len(mapping):
        raise ValueError("Duplicate or ambiguous telemetry columns.")
    units = dict(metadata.get("column_units") or {})
    resolved = {index: _unit(units.get(headers[index])) or default for index, (field, default) in mapping.items()}
    trace = []
    for row_number, row in enumerate(rows):
        if row_number > MAX_ROWS:
            raise ValueError("Reference has too many samples.")
        if not row or not any(cell.strip() for cell in row):
            continue
        if not trace and all(_number(row[index]) is None for index in mapping if index < len(row)):
            # MoTeC normally has a units row immediately after the header.
            for index in mapping:
                if index < len(row) and _unit(row[index]):
                    resolved[index] = _unit(row[index])
            continue
        sample = {"steer": None, "lat_g": None}
        for index, (field, default) in mapping.items():
            value = _number(row[index]) if index < len(row) else None
            unit = resolved[index]
            if value is None:
                raise ValueError("Non-numeric or missing telemetry at data row " + str(row_number + 1) + ".")
            if field == "t":
                if unit not in ("ms", "s"):
                    raise ValueError("Lap time needs explicit s or ms units.")
                sample[field] = value / 1000 if unit == "ms" else value
            elif field == "speed":
                if unit not in ("km/h", "m/s"):
                    raise ValueError("Speed needs explicit km/h or m/s units.")
                sample[field] = value * 3.6 if unit == "m/s" else value
            elif field in ("gas", "brake"):
                if unit not in ("1", "%"):
                    raise ValueError("Pedal channels need explicit ratio or percent units.")
                sample[field] = value / 100 if unit == "%" else value
            elif field == "distance":
                length = _number(metadata.get("track_length_m"))
                if unit != "m" or not length or length <= 0:
                    raise ValueError("Distance needs metres and verified track_length_m metadata.")
                sample["x"] = value / length
            else:
                if field in ("x", "steer") and unit != "1":
                    raise ValueError("Position and steering inputs must be normalized ratios.")
                if field == "lat_g" and unit != "g":
                    raise ValueError("Lateral acceleration must be in g.")
                sample[field] = value
        if not 0 <= sample["x"] <= 1 or not 0 <= sample["t"] <= 3600 or not 0 <= sample["speed"] <= 1000:
            raise ValueError("Position, lap time or speed is outside the permitted range.")
        if not 0 <= sample["gas"] <= 1 or not 0 <= sample["brake"] <= 1:
            raise ValueError("Pedal values are outside 0..1 after unit conversion.")
        if sample["steer"] is not None and not -1 <= sample["steer"] <= 1:
            raise ValueError("Steering is outside normalized input range.")
        if trace:
            if sample["t"] <= trace[-1]["t"] or sample["x"] < trace[-1]["x"] - 1e-6:
                raise ValueError("Export one lap with increasing time and track position; wraps or reverse movement are not accepted.")
            if sample["x"] == trace[-1]["x"]:
                continue
            if sample["x"] - trace[-1]["x"] > 0.05:
                raise ValueError("Reference contains a large track-position gap.")
        trace.append(sample)
    if len(trace) < 50 or trace[0]["x"] > 0.01 or trace[-1]["x"] < 0.99 or trace[0]["t"] > 1.0:
        raise ValueError("Reference must cover one complete lap with at least 50 samples.")
    reported = _number(metadata.get("lap_time_ms"))
    measured = trace[-1]["t"] * 1000
    if reported is not None and (reported <= 0 or abs(reported - measured) > max(200, measured * 0.002)):
        raise ValueError("Reported lap time does not match the CSV trace.")
    return {"schema": 1, "metadata": metadata, "trace": trace,
            "lap_time_ms": reported if reported is not None else measured,
            "quality": {"samples": len(trace), "start_pos": trace[0]["x"], "end_pos": trace[-1]["x"],
                        "full_lap_observed": True, "steering_available": any(s["steer"] is not None for s in trace),
                        "official_turn_mapping": False,
                        "lap_time_source": "source_metadata" if reported is not None else "CSV endpoint estimate"},
            "provenance": {"csv_sha256": hashlib.sha256(raw).hexdigest(),
                           "importer": "APEX CSV reference importer v1"}}


@functools.lru_cache(maxsize=1)
def _bundled_reference():
    metadata = _asset("acgym_rbr_bmw_human.json")
    raw = (ASSETS / "acgym_rbr_bmw_human.csv").read_bytes()
    if hashlib.sha256(raw).hexdigest() != metadata["csv_sha256"]:
        raise ValueError("Bundled reference integrity check failed.")
    result = parse_csv(raw, metadata)
    result["id"] = "acgym-rbr-bmw-human-20240313-lap4"
    return result


class ReferenceLibrary:
    """Small JSON store separate from driver recordings; no external writes."""
    def __init__(self, data_dir):
        self.folder = pathlib.Path(data_dir) / "references"
        self.folder.mkdir(parents=True, exist_ok=True)

    def import_csv(self, path, metadata):
        path = pathlib.Path(path)
        if path.suffix.lower() != ".csv" or path.stat().st_size > MAX_BYTES:
            raise ValueError("Choose a CSV reference no larger than 25 MB.")
        result = parse_csv(path.read_bytes(), metadata)
        fingerprint = hashlib.sha256(json.dumps(result, sort_keys=True, allow_nan=False).encode()).hexdigest()
        result["id"] = "reference_" + fingerprint[:24]
        destination = self.folder / (result["id"] + ".json")
        temporary = destination.with_suffix(".tmp")
        temporary.write_text(json.dumps(result, allow_nan=False, separators=(",", ":")), encoding="utf-8")
        temporary.replace(destination)
        return result

    def get(self, reference_id):
        if not isinstance(reference_id, str) or not re.fullmatch(r"reference_[a-f0-9]{24}|acgym-rbr-bmw-human-20240313-lap4", reference_id):
            raise ValueError("Unknown reference identifier.")
        if reference_id == "acgym-rbr-bmw-human-20240313-lap4":
            return copy.deepcopy(_bundled_reference())
        path = self.folder / (reference_id + ".json")
        if not path.is_file():
            raise ValueError("Unknown reference identifier.")
        return json.loads(path.read_text(encoding="utf-8"))

    def compatibility(self, reference_id, context):
        return compatibility(self.get(reference_id)["metadata"], context)

    def catalog(self, context=None):
        result = copy.deepcopy(_asset("catalog.json"))
        result["knowledge"] = load_knowledge()
        result["references"] = []
        ids = ["acgym-rbr-bmw-human-20240313-lap4"] + [p.stem for p in sorted(self.folder.glob("reference_*.json"))]
        for reference_id in ids:
            reference = _bundled_reference() if reference_id == "acgym-rbr-bmw-human-20240313-lap4" else self.get(reference_id)
            item = {key: copy.deepcopy(reference[key]) for key in ("id", "metadata", "lap_time_ms", "quality", "provenance")}
            item["eligibility"] = compatibility(reference["metadata"], context or {})
            result["references"].append(item)
        result["note"] = "Published telemetry and real-world records are clearly labelled; no bundled external lap is automatically a target for your car."
        return result
