"""Bounded, pure coaching from measured comparisons with the driver's own lap.

Recommendations are controlled experiments, not an assertion of an optimal
line, gear, grip level or setup. Repetition requires distinct clean laps with
the same reference and detected section geometry. Primary-source foundations
can explain a matching phase, but cannot substitute for telemetry evidence.
"""
from __future__ import annotations

import math
import statistics


def _number(value):
    if isinstance(value, bool):
        return None
    try:
        result = float(value)
        return result if math.isfinite(result) else None
    except (TypeError, ValueError, OverflowError):
        return None


def _median(rows, key):
    values = [_number(row.get(key)) for row in rows]
    values = [value for value in values if value is not None]
    return statistics.median(values) if values else None


def _difference(rows, key, reference_key):
    differences = []
    for row in rows:
        value, reference = _number(row.get(key)), _number(row.get(reference_key))
        if value is not None and reference is not None:
            differences.append(value - reference)
    return statistics.median(differences) if differences else None


def _gear(value):
    if isinstance(value, str):
        value = value.strip().upper()
        return value if value in ("R", "N", "1", "2", "3", "4", "5", "6", "7", "8", "9", "10") else None
    number = _number(value)
    return str(int(number)) if number is not None and number == int(number) and 1 <= number <= 10 else None


def _same_section(first, other):
    if str(first.get("number")) != str(other.get("number")):
        return False
    for key in ("start_pos", "end_pos"):
        a, b = _number(first.get(key)), _number(other.get(key))
        if a is None or b is None or abs(a - b) > .01:
            return False
    return True


class FeedbackAnalyzer:
    """Stateless analysis suitable for live snapshots or stored comparisons."""

    @staticmethod
    def _rows(analysis, corner):
        coaching = analysis.get("coaching") or {}
        session_id = (analysis.get("session") or {}).get("id")
        reference_id = coaching.get("comparison_reference_id") or (analysis.get("reference") or {}).get("id")
        if reference_id is None or session_id is None:
            return [corner]
        current_id = coaching.get("comparison_lap_id") or (analysis.get("lap") or {}).get("id")
        if current_id is None:
            current_id = "lap-" + str(coaching.get("comparison_lap_number", "current"))
        rows, seen = [], set()
        for record in (analysis.get("comparison_history") or [])[-8:]:
            if not isinstance(record, dict) or not record.get("clean") or not record.get("comparable"):
                continue
            if record.get("session_id") != session_id or record.get("reference_id") != reference_id:
                continue
            lap_id = record.get("lap_id")
            if not lap_id or lap_id in seen:
                continue
            matching = next((item for item in (record.get("corners") or [])[:80]
                             if isinstance(item, dict) and _same_section(corner, item)), None)
            if matching is not None:
                seen.add(lap_id)
                rows.append(matching)
        if current_id not in seen:
            rows.append(corner)
        return rows[-8:]

    @staticmethod
    def corner(corner, observations=None):
        """One phase-aware instruction plus measured evidence and constraints."""
        rows = observations or [corner]
        number = corner.get("number", "?")
        label = "Detected C" + str(number)
        losses = [_number(row.get("time_loss_s", row.get("delta_s"))) for row in rows]
        losses = [loss for loss in losses if loss is not None]
        if not losses:
            return None
        loss = statistics.median(losses)
        repeats = len(losses)
        repeated = repeats >= 2 and sum(value > .08 for value in losses) >= math.ceil(repeats * .67)
        repeated_gain = repeats >= 2 and sum(value < -.08 for value in losses) >= math.ceil(repeats * .67)
        brake = _median(rows, "braking_point_delta_m")
        release = _median(rows, "brake_release_delta_m")
        throttle = _median(rows, "full_throttle_delta_m")
        entry = _difference(rows, "entry_speed_kmh", "reference_entry_speed_kmh")
        exit_speed = _difference(rows, "exit_speed_kmh", "reference_exit_speed_kmh")
        deceleration = _difference(rows, "braking_deceleration_mps2", "reference_braking_deceleration_mps2")
        observed = {"section_loss_s": round(loss, 3), "brake_onset_delta_m": brake,
            "brake_release_delta_m": release, "full_throttle_delta_m": throttle,
            "entry_speed_delta_kmh": entry, "exit_speed_delta_kmh": exit_speed,
            "mean_braking_deceleration_delta_mps2": deceleration}
        evidence = [f"Measured section {'loss' if loss >= 0 else 'gain'} {abs(loss):.2f} s against the same reference."]
        caveats = ["Telemetry differences do not establish their cause; the reference is your own clean lap, not an optimum."]
        phase, instruction, experiment = "whole_corner", "Keep the section repeatable before changing technique.", "Repeat the same line and compare the same section."
        # A faster entry or earlier throttle coupled to a worse exit is not an
        # invitation to push harder. Prioritize the measured exit compromise.
        if loss > .08 and exit_speed is not None and exit_speed < -3 and ((entry is not None and entry > 3) or (throttle is not None and throttle < -8)):
            phase = "entry" if entry is not None and entry > 3 else "exit"
            instruction = "Prioritize a settled exit; stop pushing the entry speed." if phase == "entry" else "Settle the car before adding more throttle."
            experiment = "Keep the braking marker fixed; use a calmer entry and compare exit speed over two clean laps." if phase == "entry" else "Keep the line and gear fixed; make throttle pickup progressive and compare exit speed over two clean laps."
            caveats.append("Earlier throttle plus lower exit speed does not prove wheelspin; slip or stability evidence would be needed.")
        elif loss > .08 and throttle is not None and throttle > 8:
            phase = "exit"
            instruction = "Build throttle progressively once the exit is settled."
            experiment = "Keep braking and gear fixed; work toward the reference pickup only when the car is settled, then compare exit speed over two clean laps."
            caveats.append("Later full throttle can reflect line, traction, traffic or entry balance; do not force throttle earlier.")
        elif loss > .08 and release is not None and abs(release) > 8:
            phase = "release"
            instruction = "Make the brake release smooth and repeatable."
            experiment = "Keep brake onset fixed; compare a smoother release with the reference over two clean laps."
            caveats.append("A release-position difference alone does not establish an abrupt release or an incorrect trail-braking technique.")
        elif loss > .08 and brake is not None and abs(brake) > 8:
            phase = "braking"
            if brake < 0:
                instruction = "Test a small move toward your reference braking marker." if repeated else "Recheck your braking marker against the reference."
                experiment = "On the next two clean laps, make one small marker adjustment toward the reference; preserve a stable entry and stop if the exit worsens."
            else:
                instruction = "Bring braking back toward your reference marker."
                experiment = "Use the reference marker for two clean laps; compare entry stability and exit speed before moving it later."
            caveats.append("Brake-onset position alone does not prove that braking later or harder will be faster.")
        elif loss > .08 and deceleration is not None and abs(deceleration) > .5:
            phase = "braking"
            instruction = "Make the braking phase repeatable before moving the marker."
            experiment = "Hold onset and line fixed; compare speed reduction and release over two clean laps."
            caveats.append("Observed mean deceleration includes slopes and drag; it is not brake efficiency or available grip.")

        for key, value, unit in (("Brake onset", brake, "m"), ("Brake release", release, "m"),
                                 ("Sustained full throttle", throttle, "m")):
            if value is not None:
                evidence.append(f"{key} {abs(value):.0f} {unit} {'later' if value > 0 else 'earlier' if value < 0 else 'different'} than reference.")
        if entry is not None:
            evidence.append(f"Entry speed {abs(entry):.1f} km/h {'higher' if entry >= 0 else 'lower'}.")
        if exit_speed is not None:
            evidence.append(f"Exit speed {abs(exit_speed):.1f} km/h {'higher' if exit_speed >= 0 else 'lower'}.")
        if deceleration is not None:
            evidence.append(f"Observed mean braking deceleration {abs(deceleration):.2f} m/s² {'greater' if deceleration >= 0 else 'smaller'}.")
        gears = {}
        for gear_phase in ("entry", "exit"):
            actual, reference = _gear(corner.get(gear_phase + "_gear")), _gear(corner.get("reference_" + gear_phase + "_gear"))
            gears[gear_phase] = {"actual": actual, "reference": reference, "available": actual is not None and reference is not None}
            if actual is not None and reference is not None:
                evidence.append(f"{gear_phase.capitalize()} gear {actual}; reference gear {reference}.")
                if actual != reference and phase == "whole_corner" and loss > .08:
                    phase = gear_phase
                    instruction = "Compare the exit gear with your clean reference." if gear_phase == "exit" else "Compare the entry gear with your clean reference."
                    experiment = f"Keep marker and line fixed; safely test the reference {gear_phase} gear for two clean laps and compare the section."
            else:
                caveats.append(gear_phase.capitalize() + " gear comparison is unavailable in one or both recordings.")
        observed["gears"] = gears
        confidence = "repeated" if repeated else "single_comparison"
        return {"corner": number, "label": label, "phase": phase, "instruction": label + ": " + instruction,
            "action": instruction, "experiment": experiment, "evidence": evidence, "measurements": observed,
            "time_loss_s": round(loss, 3), "observations": repeats, "repeated": repeated,
            "repeated_gain": repeated_gain,
            "confidence": confidence, "caveats": caveats,
            "section_start_pos": corner.get("section_start_pos"), "section_end_pos": corner.get("section_end_pos")}

    @staticmethod
    def _knowledge(foundation, phase):
        result = []
        track = foundation.get("track") or {}
        category = foundation.get("category") or {}
        track_ids = set(track.get("priority_ids", [])) if isinstance(track, dict) and track.get("matched") else set()
        category_ids = set(category.get("priority_ids", [])) if isinstance(category, dict) else set()
        constraint_ids = set(category.get("constraint_ids", [])) if isinstance(category, dict) else set()
        for note in (foundation.get("principles") or [])[:40]:
            if not isinstance(note, dict):
                continue
            phases = note.get("phases", [note.get("phase")])
            if isinstance(phases, str):
                phases = [phases]
            if phase not in phases and "all" not in phases and "whole_corner" not in phases:
                continue
            text = note.get("text") or note.get("check") or note.get("mechanism")
            if text:
                identifier = note.get("id")
                scope = "track_and_category" if identifier in track_ids and identifier in category_ids else "track" if identifier in track_ids else "category" if identifier in category_ids else "capability_check" if identifier in constraint_ids else "general"
                score = {"track_and_category": 50, "track": 40, "category": 30, "general": 10, "capability_check": 5}[scope] + (5 if phase in phases else 0)
                result.append((score, {"id": identifier, "title": note.get("title"), "text": str(text),
                    "sources": note.get("sources") or [], "status": "supporting_principle", "scope": scope,
                    "context": {"track": track.get("name") if isinstance(track, dict) and identifier in track_ids else None,
                                "category": category.get("name") if isinstance(category, dict) and identifier in category_ids else None}}))
        result.sort(key=lambda item: item[0], reverse=True)
        return [item for _, item in result[:2]]

    @staticmethod
    def analyze(analysis, foundation=None):
        analysis, foundation = analysis or {}, foundation or {}
        coaching = analysis.get("coaching") or {}
        available = coaching.get("comparison_available", analysis.get("comparison_available")) is True
        reference = analysis.get("reference") or {}
        available = available and reference.get("clean", reference.get("valid")) is True
        lap = analysis.get("lap") or {}
        if lap and lap.get("clean") is not True:
            available = False
        priorities, strengths = [], []
        if available:
            for corner in (analysis.get("corners") or [])[:80]:
                if not isinstance(corner, dict):
                    continue
                result = FeedbackAnalyzer.corner(corner, FeedbackAnalyzer._rows(analysis, corner))
                if not result:
                    continue
                if result["time_loss_s"] > .08:
                    result["knowledge"] = FeedbackAnalyzer._knowledge(foundation, result["phase"])
                    contextual = next((note for note in result["knowledge"] if note["scope"] in ("track", "category", "track_and_category")), None)
                    if contextual:
                        # Context selects an inspection to pair with measured
                        # loss. It never supplies a marker, gear or lap target.
                        result["contextual_check"] = contextual["text"]
                        result["experiment"] += " Also check: " + contextual["text"]
                    priorities.append(result)
                elif result["time_loss_s"] < -.08 and result["repeated_gain"]:
                    strengths.append({"corner": result["corner"], "text": result["label"] + ": repeated measured section gains; preserve this approach.",
                                      "gain_s": abs(result["time_loss_s"]), "observations": result["observations"]})
        # Repeated losses take precedence; a single unusually slow section is
        # retained for inspection rather than presented as an established fault.
        priorities.sort(key=lambda item: (item["repeated"], item["time_loss_s"]), reverse=True)
        strengths.sort(key=lambda item: item["gain_s"], reverse=True)
        priorities, strengths = priorities[:3], strengths[:3]
        if priorities:
            instruction = priorities[0]["instruction"]
            summary = instruction + " " + ("This loss repeated against the same reference." if priorities[0]["repeated"] else "One comparison; confirm it before a major change.")
            plan = ["Warm the car and tyres, then record one clean baseline without changing the setup.",
                    priorities[0]["experiment"],
                    "Keep one variable at a time. Retain the change only if the measured section improves without compromising a clean exit."]
        else:
            if not available and lap:
                instruction = ("This lap is excluded; use a clean lap for measured coaching." if lap.get("clean") is not True else
                    "No matching clean reference for this lap. Check conditions below.")
            else:
                instruction = "Build two comparable clean laps before choosing a technique change." if not available else "Keep the lap repeatable; no clear measured section loss needs a new instruction."
            summary = instruction
            if not available and lap:
                plan = ["Select a clean lap and inspect its recorded reference and condition differences.",
                        "Compare laps with matching car, compound, setup and similar fuel and track conditions before changing technique."]
            else:
                plan = ["Record two complete clean laps with the same car, compound and stable conditions.",
                        "Keep the setup fixed; compare the same sections before choosing one technique change."]
        return {"available": bool(available), "summary": summary, "instruction": instruction,
            "priorities": priorities, "strengths": strengths, "next_session_plan": plan,
            "limitations": ["Detected corner numbers are not official circuit turn numbers.",
                "Own-reference coaching does not establish optimal gearing, braking points, tyre grip or setup.",
                "Repeated evidence uses distinct clean comparisons with the same reference and section geometry."],
            "foundation": {key: foundation.get(key) for key in ("category", "track", "coverage", "best_driver_evidence") if key in foundation}}
