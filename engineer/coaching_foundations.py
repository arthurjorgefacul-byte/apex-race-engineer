"""Sourced coaching context, not a table of invented optimal driving inputs.

Profiles identify useful questions for measured feedback. Published human laps
remain research context: no category match promotes them to a pace target.
No network access or game mutation occurs in this module.
"""
from __future__ import annotations

import copy
import functools
import hashlib
import json
import pathlib
import re

from .reference_data import ASSETS, load_knowledge, parse_csv

FOUNDATIONS = ASSETS / "foundations"


@functools.lru_cache(maxsize=1)
def _database():
    return json.loads((FOUNDATIONS / "profiles.json").read_text(encoding="utf-8"))


@functools.lru_cache(maxsize=1)
def _evidence_index():
    return json.loads((FOUNDATIONS / "human_evidence.json").read_text(encoding="utf-8"))


@functools.lru_cache(maxsize=1)
def _car_index():
    return {car_id: category["id"] for category in _database()["categories"] for car_id in category["car_ids"]}


@functools.lru_cache(maxsize=1)
def _knowledge():
    return load_knowledge()


@functools.lru_cache(maxsize=1)
def _source_index():
    sources = {item["id"]: item for item in _database()["sources"]}
    sources.update({item["id"]: item for item in _knowledge()["sources"]})
    return sources


def classify_car(car_id):
    """Return a known coarse category id or None; never guess from names.

    This separates car families, not homologation, BoP or equal performance.
    Formula and prototype families in particular span incompatible generations.
    Exact car identity must remain the preferred pace-comparison grouping.
    """
    return _car_index().get(car_id) if isinstance(car_id, str) else None


def _resolved_sources(identifiers):
    sources = _source_index()
    return [copy.deepcopy(sources[identifier]) for identifier in dict.fromkeys(identifiers) if identifier in sources]


def _category(frame):
    car_id = frame.get("car_id")
    for category in _database()["categories"]:
        if car_id in category["car_ids"]:
            result = copy.deepcopy(category)
            result.update({"identity_confidence": "catalogued_car_id", "car_id": car_id,
                           "physics_verified": False})
            return result
    # Explicit category metadata is useful for unfamiliar mods, but cannot
    # establish their actual ABS, aero, ERS, tyre or homologation behaviour.
    declared = frame.get("car_category")
    for category in _database()["categories"]:
        if declared == category["id"]:
            result = copy.deepcopy(category)
            result.update({"identity_confidence": "declared_category", "car_id": car_id,
                           "physics_verified": False})
            return result
    return {"id": "unknown", "name": "Unverified car category", "car_id": car_id,
            "identity_confidence": "unknown", "physics_verified": False,
            "priority_ids": [], "constraint_ids": [], "source_ids": [],
            "limits": ["A car name containing GT3, GT2 or Formula does not establish its specification."]}


def _track(frame):
    track_id, layout = frame.get("track_id"), frame.get("layout")
    for track in _database()["tracks"]:
        for binding in track["bindings"]:
            if track_id == binding["track_id"] and isinstance(layout, str) and layout in binding["layouts"]:
                result = copy.deepcopy(track)
                result.update({"track_id": track_id, "layout": layout, "matched": True,
                               "identity_confidence": binding["confidence"],
                               "normalized_corner_mapping": False, "physics_verified": False})
                return result
    # Allow a caller to supply an explicitly verified family for an unknown
    # mod. Never infer it by fuzzy names or a renamed folder.
    if frame.get("track_profile_verified") is True:
        for track in _database()["tracks"]:
            if frame.get("track_profile_id") == track["id"]:
                result = copy.deepcopy(track)
                result.update({"track_id": track_id, "layout": layout, "matched": True,
                               "identity_confidence": "caller_verified_family",
                               "normalized_corner_mapping": False, "physics_verified": False})
                return result
    blocked = next((item for item in _database().get("ambiguous_installed_ids", []) if item["track_id"] == track_id), None)
    return {"id": None, "name": track_id or "Unknown circuit", "track_id": track_id,
            "layout": layout, "matched": False, "identity_confidence": "unknown",
            "normalized_corner_mapping": False, "physics_verified": False,
            "characteristics": [], "priority_ids": [], "source_ids": [],
            "limits": [blocked["reason"] if blocked else "Exact circuit and layout have not been verified; use the driver's measured sections."]}


def _principles(category, track):
    output = []
    knowledge = _knowledge()
    for note in knowledge["principles"]:
        item = copy.deepcopy(note)
        item["phases"] = ["apex" if item["phase"] == "middle" else item["phase"]]
        item["text"] = item["check"]
        item["confidence"] = "sourced_general_principle"
        item["sources"] = _resolved_sources(item["source_ids"])
        output.append(item)
    identifiers = category.get("constraint_ids", []) + category.get("priority_ids", []) + track.get("priority_ids", [])
    notes = {note["id"]: note for note in _database()["principles"]}
    for identifier in dict.fromkeys(identifiers):
        item = copy.deepcopy(notes[identifier])
        item["text"] = item["check"]
        item["confidence"] = "engineering_inspection_priority"
        item["sources"] = _resolved_sources(item["source_ids"])
        output.append(item)
    return output


@functools.lru_cache(maxsize=12)
def _load_trace(evidence_id):
    item = next((item for item in _evidence_index()["recordings"] if item["id"] == evidence_id), None)
    if item is None:
        raise ValueError("Unknown foundation evidence identifier.")
    path = (FOUNDATIONS / item["csv_file"]).resolve()
    # These are curated relative paths, never a user-selected arbitrary file.
    if not path.is_relative_to(FOUNDATIONS.resolve()):
        raise ValueError("Invalid foundation evidence path.")
    raw = path.read_bytes()
    if hashlib.sha256(raw).hexdigest() != item["metadata"]["csv_sha256"]:
        raise ValueError("Foundation evidence integrity check failed.")
    result = parse_csv(raw, item["metadata"])
    result.update({"id": evidence_id, "driver_skill": "unverified_research_participant",
                   "automatic_target": False})
    return result


class FoundationLibrary:
    """A local, immutable corpus used directly by feedback and debrief code."""
    def __init__(self, data_dir=None):
        # Kept for API consistency and future private driver notes. Reading the
        # curated corpus does not create folders or alter driver recordings.
        self.data_dir = pathlib.Path(data_dir) if data_dir is not None else None

    def category_for_car(self, car_id):
        return _category({"car_id": car_id})

    def context(self, frame):
        frame = frame if isinstance(frame, dict) else {}
        if frame.get("game", "assetto_corsa") != "assetto_corsa":
            frame = {}
        category, track = _category(frame), _track(frame)
        evidence = []
        category_evidence = []
        for recording in _evidence_index()["recordings"]:
            if not track["matched"] or recording["track_profile_id"] != track["id"]:
                continue
            if recording["category_id"] == category["id"]:
                category_evidence.append(recording)
            metadata = recording["metadata"]
            if (metadata["car_id"] == frame.get("car_id") and metadata["track_id"] == frame.get("track_id")
                    and metadata["layout"] == frame.get("layout")):
                item = copy.deepcopy(recording)
                item["exact_car_layout"] = True
                item["automatic_target"] = False
                evidence.append(item)
        principles = _principles(category, track)
        used = category.get("source_ids", []) + track.get("source_ids", [])
        for note in principles:
            used.extend(note.get("source_ids", []))
        if evidence or category_evidence:
            used.extend(["acgym-dataset", "acgym-paper", "acgym-manifest"])
        result = {"schema": 1, "category": category, "track": track, "principles": principles,
                  "coverage": {"track_profile": bool(track["matched"]),
                               "category_profile": category["id"] != "unknown",
                               "exact_car_human_traces": len(evidence),
                               "category_human_traces": len(category_evidence),
                               "elite_traces": 0, "automatic_external_target": False,
                               "profile_scope": "circuit-family background and category inspection priorities",
                               "research_collection_count": 1 if evidence or category_evidence else 0,
                               "note": "Human research recordings are examples, not verified elite laps; exact versions, setup and conditions are not matched."},
                  "best_driver_evidence": evidence,
                  "radio": copy.deepcopy(_database()["radio"]),
                  "sources": _resolved_sources(used),
                  "limits": ["Priorities explain measured feedback; they do not establish a fault without telemetry.",
                             "No preset braking distance, gear, tyre target or optimal lap time is supplied.",
                             "Official real-world geometry and tyre descriptions do not calibrate an AC mod.",
                             "Detected corner segments are not mapped to official turn numbers."]}
        result["radio"]["sources"] = _resolved_sources(result["radio"]["source_ids"])
        return result

    def evidence(self, evidence_id):
        """Full attributed trace, fetched only when explicitly requested."""
        if not isinstance(evidence_id, str) or not re.fullmatch(r"acgym-[a-z0-9-]+", evidence_id):
            raise ValueError("Unknown foundation evidence identifier.")
        return copy.deepcopy(_load_trace(evidence_id))

    def coverage(self, frame=None):
        if frame is not None:
            return self.context(frame)["coverage"]
        data = _database()
        combinations = []
        for track in data["tracks"]:
            for category in data["categories"]:
                recordings = [item for item in _evidence_index()["recordings"]
                              if item["track_profile_id"] == track["id"] and item["category_id"] == category["id"]]
                combinations.append({"track_profile_id": track["id"], "category_id": category["id"],
                                     "foundation_profile": True, "human_research_traces": len(recordings),
                                     "elite_traces": 0, "automatic_target": False})
        return {"schema": 1, "track_profiles": len(data["tracks"]),
                "category_profiles": len(data["categories"]),
                "human_research_traces": len(_evidence_index()["recordings"]),
                "independent_research_collections": 1, "elite_traces": 0,
                "combinations": combinations}

    def catalog(self):
        data = copy.deepcopy(_database())
        data["coverage"] = self.coverage()
        data["human_evidence"] = copy.deepcopy(_evidence_index()["recordings"])
        data["knowledge"] = load_knowledge()
        return data
