"""Deterministic live radio events and factual MFD questions for Assetto Corsa.

The module never changes a setup, invents a tyre target, or treats three wheels
outside the track as a definitive game-invalid lap. Corner instructions use
measured differences from the driver's own clean comparison. The caller owns
speech: urgent track-limit events may interrupt; other calls wait for safe gaps.
"""
from __future__ import annotations

import copy
import math
import statistics
import threading
import time

from engineer.feedback import FeedbackAnalyzer
from engineer.analysis import _HistoryAccess
from engineer.reference_data import principles_for_corner


def _num(value):
    if isinstance(value, bool):
        return None
    try:
        number = float(value)
        return number if math.isfinite(number) else None
    except (TypeError, ValueError, OverflowError):
        return None


def _array(value, positive=False):
    if not isinstance(value, (list, tuple)):
        return []
    result = [_num(item) for item in value]
    return [item for item in result if item is not None and (not positive or item > 0)]


def _lap(milliseconds):
    value = _num(milliseconds)
    return "unavailable" if value is None or value <= 0 else f"{int(value // 60000)}:{value % 60000 / 1000:06.3f}"


COMMANDS = (
    ("status", "Car and session"), ("fuel", "Fuel"), ("tyres", "Tyres"),
    ("pace", "Pace"), ("corners", "Corner losses"), ("next_corner", "Next corner"),
    ("last_lap", "Last lap"), ("strategy", "Race strategy"), ("energy", "Hybrid energy"),
    ("track_limits", "Track limits"), ("quiet", "Quiet radio"),
    ("more_updates", "More updates"), ("less_updates", "Fewer updates"),
    ("feedback", "Driving feedback"), ("personal_best", "Personal best"),
    ("race_best", "Race best"), ("others_best", "Other drivers' best"),
    ("gap_ahead", "Gap ahead"), ("gap_behind", "Gap behind"),
    ("gap_leader", "Gap to leader"), ("change_compound", "Tyre strategy"),
)

# Original AC's published AC_FLAG_TYPE, not ACC broadcast flag bits.
# CSP maintainer reference: acc-extension-apps/AccExtHelper/sim_info.py.
FLAGS = {0: "clear", 1: "blue", 2: "yellow", 3: "black", 4: "white", 5: "checkered", 6: "penalty"}


class LiveEngineer:
    def __init__(self, settings=None, clock=time.time):
        self._clock = clock
        self._lock = threading.RLock()
        self._settings = {"quiet": False, "verbosity": "balanced", "fuel_reserve_l": 2.0,
                          "target_pressure_psi": None, "target_pressure_verified": False}
        self._generation = 0
        self._counter = 0
        self._context = None
        self._reference = None
        self._events = {}
        self._new_events = []
        self._cooldowns = {}
        self._last_snapshot = {}
        self._reset_observations()
        if settings:
            self.configure(settings)

    def _reset_observations(self):
        self._previous_status = None
        self._previous_pit = None
        self._previous_flag = None
        self._previous_position = None
        self._previous_count = None
        self._last_lap_reset = -1e12
        self._lap_serial = 0
        self._corners_called = set()
        self._invalid_called = False
        self._offtrack_since = None
        self._back_on_track_since = None
        self._offtrack_called = False
        self._pressure_since = None
        self._pressure_direction = None
        self._last_corner_call = -1e12
        self._reported_corners = set()
        self._corner_reports = []
        self._gain_history = {}
        self._last_periodic = self._clock()

    @property
    def settings(self):
        with self._lock:
            return dict(self._settings)

    def configure(self, settings):
        with self._lock:
            updated = dict(self._settings)
            if "quiet" in settings:
                if not isinstance(settings["quiet"], bool):
                    raise ValueError("Quiet must be true or false.")
                updated["quiet"] = settings["quiet"]
            if "verbosity" in settings:
                if settings["verbosity"] not in ("less", "balanced", "more"):
                    raise ValueError("Choose fewer, balanced or more updates.")
                updated["verbosity"] = settings["verbosity"]
            if "fuel_reserve_l" in settings:
                reserve = _num(settings["fuel_reserve_l"])
                if reserve is None or not 0 <= reserve <= 100:
                    raise ValueError("Fuel reserve must be between zero and 100 litres.")
                updated["fuel_reserve_l"] = reserve
            if "target_pressure_psi" in settings:
                target = _num(settings["target_pressure_psi"])
                if settings["target_pressure_psi"] is not None and (target is None or not 5 <= target <= 80):
                    raise ValueError("Enter a verified tyre target between five and 80 psi, or leave it unset.")
                updated["target_pressure_psi"] = target
                updated["target_pressure_verified"] = target is not None and settings.get("target_pressure_verified", True) is True
            elif "target_pressure_verified" in settings:
                updated["target_pressure_verified"] = settings["target_pressure_verified"] is True and updated["target_pressure_psi"] is not None
            if updated != self._settings:
                self._settings = updated
                self._generation += 1
                self._events.clear()
                self._new_events.clear()
                self._pressure_since = None
                self._pressure_direction = None
            return dict(self._settings)

    @staticmethod
    def _context_key(frame, analysis):
        return ((analysis.get("session") or {}).get("id"), frame.get("source"), frame.get("car_id"),
                frame.get("track_id"), frame.get("layout"), frame.get("tyre_compound"))

    def _emit(self, key, kind, text, now, priority=1, cooldown=30, lifetime=15, immediate=False, **detail):
        if self._settings["quiet"] and priority < 3:
            return None
        if now - self._cooldowns.get(key, -1e12) < cooldown:
            return None
        self._cooldowns[key] = now
        self._counter += 1
        event = {"id": f"live-{self._generation}-{self._counter}", "kind": kind, "text": text,
            "priority": priority, "severity": "critical" if priority >= 3 else "warning" if priority == 2 else "info",
            "timestamp": now, "expires": now + lifetime, "expires_at": now + lifetime,
            "immediate": bool(immediate), "speak_when": "now" if immediate else "safe_gap",
            "generation": self._generation, "source": "APEX live", **detail}
        self._events[event["id"]] = event
        self._new_events.append(event)
        return event

    def _purge(self, now):
        self._events = {key: event for key, event in self._events.items() if event["expires"] > now}

    def _lap_transition(self, frame, now):
        position, count = _num(frame.get("normalized_pos")), _num(frame.get("lap_count"))
        wrapped = position is not None and self._previous_position is not None and self._previous_position >= .97 and position <= .03
        advanced = count is not None and self._previous_count is not None and count > self._previous_count
        if wrapped or (advanced and now - self._last_lap_reset > 1):
            self._lap_serial += 1
            self._corners_called.clear()
            self._reported_corners.clear()
            self._corner_reports.clear()
            self._invalid_called = False
            self._last_lap_reset = now
            self._events = {key: event for key, event in self._events.items() if event["kind"] != "corner"}
        self._previous_count = count
        return position

    @staticmethod
    def _flag(frame):
        value = frame.get("flag_name", frame.get("flag"))
        if isinstance(value, str):
            clean = value.strip().lower().replace("ac_", "").replace("_flag", "")
            return clean if clean in FLAGS.values() else None
        number = _num(value)
        return FLAGS.get(int(number)) if number is not None and number == int(number) else None

    def _track_limits(self, frame, now):
        if frame.get("lap_valid") is False and not self._invalid_called:
            self._invalid_called = True
            self._emit("invalid-lap", "track_limits", "The game reports this lap is invalid.", now,
                       priority=3, cooldown=0, lifetime=6, immediate=True, definitive=True)
        outside = _num(frame.get("wheels_out"))
        if outside is None or frame.get("in_pit") or (_num(frame.get("speed_kmh")) or 0) < 10:
            self._offtrack_since = None
            return
        if outside >= 3:
            self._back_on_track_since = None
            if self._offtrack_since is None:
                self._offtrack_since = now
            debounce = .15 if outside >= 4 else .25
            if not self._offtrack_called and now - self._offtrack_since >= debounce:
                count = "All four" if outside >= 4 else "At least three"
                text = count + " wheels are outside the track. Check track limits; lap validity is not confirmed."
                if frame.get("lap_valid") is False:
                    text = "You are off track. The game also reports this lap is invalid."
                event = self._emit("offtrack", "track_limits", text, now, priority=3, cooldown=5,
                                   lifetime=5, immediate=True, definitive=frame.get("lap_valid") is False)
                if event:
                    self._offtrack_called = True
        else:
            self._offtrack_since = None
            if self._back_on_track_since is None:
                self._back_on_track_since = now
            if now - self._back_on_track_since >= .5:
                self._offtrack_called = False

    def _fuel_values(self, frame, analysis):
        fuel = analysis.get("fuel") or {}
        litres = _num(frame.get("fuel_l"))
        rate = _num(fuel.get("consumption_l_per_lap"))
        if rate is not None and rate <= 0:
            rate = None
        reserve = _num(fuel.get("reserve_l"))
        if reserve is None:
            reserve = self._settings["fuel_reserve_l"]
        usable = max(0, litres - reserve) / rate if litres is not None and rate else None
        total = litres / rate if litres is not None and rate else None
        need = _num(fuel.get("fuel_to_finish_l"))
        race = frame.get("session_type") in (2, "race", "Race", "RACE")
        if not race:
            need = None
        short = max(0, need - litres) if need is not None and litres is not None else None
        return {"litres": litres, "rate": rate, "reserve": reserve, "usable_laps": usable,
                "tank_laps": total, "need": need, "shortfall": short,
                "estimated": fuel.get("finish_estimated") is True}

    def _fuel_events(self, frame, analysis, now):
        fuel = self._fuel_values(frame, analysis)
        litres, total, usable = fuel["litres"], fuel["tank_laps"], fuel["usable_laps"]
        if litres is not None and (litres <= .2 or (total is not None and total < .75)):
            text = f"Fuel critically low: {litres:.1f} litres remaining."
            if total is not None:
                text += f" About {total:.1f} laps in the tank."
            self._emit("fuel-critical", "fuel", text, now, priority=3, cooldown=30, lifetime=12)
        elif usable is not None and usable < 1.5:
            self._emit("fuel-range", "fuel", f"Fuel check: {usable:.1f} laps remain above your {fuel['reserve']:.1f} litre reserve.",
                       now, priority=2, cooldown=45, lifetime=15)
        if fuel["shortfall"] is not None and fuel["shortfall"] > .5:
            self._emit("fuel-finish", "strategy", f"The current race-fuel estimate is {fuel['shortfall']:.1f} litres short, including your reserve. Check the strategy panel.",
                       now, priority=2, cooldown=75, lifetime=20)

    def _tyre_target(self):
        return self._settings["target_pressure_psi"] if self._settings["target_pressure_verified"] else None

    def _tyre_events(self, frame, now):
        target = self._tyre_target()
        pressures = _array(frame.get("tyre_pressure_psi"), positive=True)
        if target is None or len(pressures) != 4 or frame.get("in_pit") or (_num(frame.get("speed_kmh")) or 0) < 60:
            self._pressure_since = None
            return
        average = statistics.mean(pressures)
        delta = average - target
        direction = "high" if delta >= 1.5 else "low" if delta <= -1.5 else None
        if direction is None:
            self._pressure_since = self._pressure_direction = None
            return
        if direction != self._pressure_direction:
            self._pressure_direction, self._pressure_since = direction, now
        if self._pressure_since is not None and now - self._pressure_since >= 5:
            cooldown = {"less": 150, "balanced": 90, "more": 60}[self._settings["verbosity"]]
            self._emit("tyres-" + direction, "tyres", f"Mean tyre pressure is {average:.1f} psi, {abs(delta):.1f} {'above' if delta > 0 else 'below'} your selected hot target. Check once warm.",
                       now, priority=2, cooldown=cooldown, lifetime=20)

    @staticmethod
    def _reference_trace(analysis, active=False):
        traces = analysis.get("traces") or {}
        key = "active_reference" if active else "reference"
        return traces.get(key) if isinstance(traces.get(key), list) else []

    def _plans(self, analysis, geometry=False):
        reference = analysis.get("reference") or {}
        coaching = analysis.get("coaching") or {}
        if reference.get("clean", reference.get("valid")) is not True:
            return []
        if not coaching.get("comparison_available") and not (geometry and coaching.get("live_comparison_available")):
            return []
        trace = self._reference_trace(analysis, active=geometry)
        result = []
        for index, corner in enumerate(analysis.get("corners") or []):
            if not isinstance(corner, dict):
                continue
            begin, end = _num(corner.get("start_pos")), _num(corner.get("end_pos"))
            anchor = _num(corner.get("reference_brake_pos"))
            if anchor is None:
                anchor = _num(corner.get("section_start_pos"))
            if begin is None or end is None or not 0 <= begin < end <= 1:
                continue
            if anchor is None:
                anchor = max(0, begin - .03)
            braking = [point for point in trace if isinstance(point, dict) and _num(point.get("x")) is not None
                       and anchor <= _num(point.get("x")) <= begin and (_num(point.get("brake")) or 0) > .08]
            if braking:
                anchor = _num(braking[0].get("x"))
            number = corner.get("number", index + 1)
            result.append({"number": number, "label": "Detected C" + str(number), "anchor": anchor,
                "start_pos": begin, "end_pos": end, "loss_s": _num(corner.get("time_loss_s")),
                "section_start_pos": _num(corner.get("section_start_pos")),
                "section_end_pos": _num(corner.get("section_end_pos")),
                "braking_delta_m": _num(corner.get("braking_point_delta_m")),
                "throttle_delta_m": _num(corner.get("full_throttle_delta_m")),
                "entry_speed_kmh": _num(corner.get("entry_speed_kmh")),
                "reference_entry_speed_kmh": _num(corner.get("reference_entry_speed_kmh")),
                "reference_exit_speed_kmh": _num(corner.get("reference_exit_speed_kmh"))})
            result[-1]["evidence"] = dict(corner)
            result[-1]["feedback"] = next((item for item in (analysis.get("feedback") or {}).get("priorities", [])
                                            if str(item.get("corner")) == str(number)), None)
        return sorted(result, key=lambda item: item["anchor"])

    @staticmethod
    def _time_at(trace, position, key="t"):
        previous = None
        for point in trace:
            if not isinstance(point, dict):
                continue
            x, seconds = _num(point.get("x")), _num(point.get(key))
            if x is None or seconds is None:
                continue
            if x >= position:
                if abs(x - position) < .00001:
                    return seconds
                if previous is None:
                    return None
                old_x, old_t = previous
                if x <= old_x or x - old_x > .03:
                    return None
                return old_t + (seconds - old_t) * (position - old_x) / (x - old_x)
            previous = x, seconds
        return None

    def _next(self, frame, analysis, plans=None):
        position = _num(frame.get("normalized_pos"))
        plans = self._plans(analysis, geometry=True) if plans is None else plans
        if position is None or not 0 <= position <= 1 or not plans:
            return None
        plan = min(plans, key=lambda item: (item["anchor"] - position) % 1)
        delta = (plan["anchor"] - position) % 1
        length = _num(frame.get("track_length_m"))
        distance = delta * length if length is not None and length > 0 else None
        speed = _num(frame.get("speed_kmh"))
        seconds = distance / (speed / 3.6) if distance is not None and speed is not None and speed > 10 else None
        return {**plan, "distance_m": round(distance, 1) if distance is not None else None,
                "estimated_seconds": round(seconds, 1) if seconds is not None else None,
                "wraps_finish_line": plan["anchor"] < position}

    @staticmethod
    def _corner_text(plan, phase):
        label = plan["label"]
        feedback = plan.get("feedback") or (FeedbackAnalyzer.corner(plan["evidence"]) if plan.get("evidence") else None)
        if feedback and feedback.get("time_loss_s", 0) > .08:
            return feedback["instruction"], feedback["phase"]
        throttle, brake = plan["throttle_delta_m"], plan["braking_delta_m"]
        if throttle is not None and throttle > 8:
            return f"{label}: full throttle was {throttle:.0f} metres later last comparison. Prioritize a clean exit.", "exit"
        if brake is not None and abs(brake) > 8:
            side = "later" if brake > 0 else "earlier"
            return f"{label}: braking began {abs(brake):.0f} metres {side} last comparison. Use a consistent braking marker.", "entry"
        entry, reference = plan["entry_speed_kmh"], plan["reference_entry_speed_kmh"]
        if entry is not None and reference is not None and abs(entry - reference) > 3:
            return f"{label}: entry was {abs(entry-reference):.0f} kilometres per hour {'higher' if entry > reference else 'lower'} last comparison. Check the exit too.", "entry"
        if plan["loss_s"] is not None and plan["loss_s"] > .08:
            return f"{label}: its section lost {plan['loss_s']:.2f} seconds last comparison. Keep inputs consistent through the exit.", phase
        return None, phase

    def _corner_events(self, frame, analysis, now, position):
        if not self._human_clean(frame, analysis) or position is None:
            return
        spacing = {"less": 22, "balanced": 8, "more": 4}[self._settings["verbosity"]]
        if now - self._last_corner_call < spacing:
            return
        plans = self._plans(analysis)
        ahead = self._next(frame, analysis, plans)
        chosen, phase, expires = None, "approach", 8
        if ahead and not ahead["wraps_finish_line"] and str(ahead["number"]) not in self._corners_called:
            seconds = ahead["estimated_seconds"]
            if seconds is None:
                trace = self._reference_trace(analysis)
                start, end = self._time_at(trace, position), self._time_at(trace, ahead["anchor"])
                seconds = end - start if start is not None and end is not None else None
            lookahead = {"less": 6, "balanced": 8, "more": 10}[self._settings["verbosity"]]
            if seconds is not None and 3 <= seconds <= lookahead:
                chosen, expires = ahead, min(12, seconds + .5)
        if chosen is None and self._previous_position is not None and 0 <= position - self._previous_position < .08:
            for plan in plans:
                exit_mark = min(.999, plan["end_pos"] + .008)
                if self._previous_position < exit_mark <= position and str(plan["number"]) not in self._corners_called:
                    chosen, phase, expires = plan, "exit", 6
                    break
        if chosen is None:
            return
        threshold = .15 if self._settings["verbosity"] == "less" else .08
        if chosen["loss_s"] is None or chosen["loss_s"] < threshold:
            return
        text, measured_phase = self._corner_text(chosen, phase)
        if text:
            event = self._emit("corner-" + str(chosen["number"]), "corner", text, now, priority=1,
                cooldown=spacing, lifetime=expires, corner=chosen["number"], phase=measured_phase,
                trigger_phase=phase, lap_serial=self._lap_serial, reference_id=self._reference)
            if event:
                self._corners_called.add(str(chosen["number"]))
                self._last_corner_call = now

    @staticmethod
    def _human_clean(frame, analysis):
        session = analysis.get("session") or {}
        return (frame.get("is_ai_controlled") is False and frame.get("in_pit") is False
            and frame.get("lap_valid") is not False and (_num(frame.get("wheels_out")) is not None)
            and _num(frame.get("wheels_out")) <= 2 and session.get("current_lap_eligible") is True
            and not session.get("current_lap_flags"))

    @staticmethod
    def _full_gas_at(trace, begin, end):
        points = [p for p in trace if isinstance(p, dict) and _num(p.get("x")) is not None
                  and begin <= _num(p.get("x")) <= end]
        for index, point in enumerate(points[:-2]):
            following = points[index:index + 3]
            if all((_num(p.get("gas")) or 0) >= .9 for p in following):
                return _num(point.get("x"))
        return None

    def _live_corner_events(self, frame, analysis, now, position):
        if not self._human_clean(frame, analysis) or not (analysis.get("coaching") or {}).get("live_comparison_available"):
            return
        traces = analysis.get("traces") or {}
        live, reference = traces.get("live"), self._reference_trace(analysis, active=True)
        if not isinstance(live, list) or not reference or position is None:
            return
        length = _num(frame.get("track_length_m"))
        threshold = {"less": .30, "balanced": .15, "more": .10}[self._settings["verbosity"]]
        for plan in self._plans(analysis, geometry=True):
            number = str(plan["number"])
            if number in self._reported_corners:
                continue
            begin = plan["section_start_pos"]
            end = plan["section_end_pos"]
            if begin is None:
                begin = plan["anchor"]
            if end is None:
                end = plan["end_pos"] + .008
            # AC starts a new trace at the line. Report the final section just
            # before it, explicitly exposing the measured span in the result.
            end = min(end, .995)
            if not 0 <= begin < plan["end_pos"] < end <= position <= 1:
                continue
            times = [self._time_at(trace, x) for trace in (live, reference) for x in (begin, end)]
            if any(value is None for value in times):
                continue
            live_start, live_end, ref_start, ref_end = times
            if live_end <= live_start or ref_end <= ref_start:
                continue
            loss = (live_end - live_start) - (ref_end - ref_start)
            exit_speed = self._time_at(live, plan["end_pos"], "speed")
            ref_speed = self._time_at(reference, plan["end_pos"], "speed")
            full = self._full_gas_at(live, plan["end_pos"], end)
            ref_full = self._full_gas_at(reference, plan["end_pos"], end)
            throttle_delta = (full - ref_full) * length if full is not None and ref_full is not None and length else None
            report = {"number": plan["number"], "label": plan["label"], "time_loss_s": round(loss, 3),
                "section_start_pos": begin, "section_end_pos": end, "exit_speed_kmh": exit_speed,
                "reference_exit_speed_kmh": ref_speed, "full_throttle_delta_m": throttle_delta,
                "lap_serial": self._lap_serial, "reference_id": self._reference, "measured_live": True}
            parts = [[p for p in samples if isinstance(p, dict) and _num(p.get("x")) is not None
                      and plan["start_pos"] <= _num(p["x"]) <= plan["end_pos"] and _num(p.get("speed")) is not None]
                     for samples in (live, reference)]
            if all(parts):
                minima = [min(part, key=lambda p: p["speed"]) for part in parts]
                onsets = [_HistoryAccess._threshold_point(samples, begin, minimum["x"], "brake", .08)
                          for samples, minimum in zip((live, reference), minima)]
                braking = [_HistoryAccess._braking_evidence(samples, onset, minimum["x"])
                           for samples, onset, minimum in zip((live, reference), onsets, minima)]
                report["braking_point_delta_m"] = (onsets[0] - onsets[1]) * length if all(v is not None for v in onsets) and length else None
                releases = [item["release_pos"] for item in braking]
                report["brake_release_delta_m"] = (releases[0] - releases[1]) * length if all(v is not None for v in releases) and length else None
                report["braking_deceleration_mps2"] = braking[0]["mean_deceleration_mps2"]
                report["reference_braking_deceleration_mps2"] = braking[1]["mean_deceleration_mps2"]
            for prefix, samples in (("", live), ("reference_", reference)):
                report[prefix + "entry_speed_kmh"] = self._time_at(samples, plan["start_pos"], "speed")
                for phase, at in (("entry", plan["start_pos"]), ("exit", plan["end_pos"])):
                    report[prefix + phase + "_gear"] = _HistoryAccess._categorical_at(samples, at, "gear")
            self._reported_corners.add(number)
            self._corner_reports.append(report)
            history = self._gain_history.setdefault(number, [])
            history.append(loss)
            del history[:-3]
            text = None
            if loss > threshold:
                feedback = FeedbackAnalyzer.corner(dict(report,
                    start_pos=plan["start_pos"], end_pos=plan["end_pos"],
                    exit_speed_kmh=exit_speed, reference_exit_speed_kmh=ref_speed))
                text = feedback["instruction"] if feedback and feedback["phase"] != "whole_corner" else f"{plan['label']}: keep the section repeatable; {loss:.2f} seconds lost against your reference."
                report["feedback"] = feedback
            elif loss < -threshold and len(history) >= 2 and all(value < -threshold for value in history[-2:]):
                text = f"{plan['label']}: the last two measured sections were quicker than your clean reference; this one gained {abs(loss):.2f} seconds."
            if text:
                # Supersede any unspoken old-comparison reminder for this turn.
                self._events = {key: event for key, event in self._events.items()
                                if not (event.get("corner") == plan["number"] and event["kind"] == "corner")}
                self._emit("live-corner-" + number + "-" + str(self._lap_serial), "corner", text, now,
                    cooldown=0, lifetime=10, corner=plan["number"], phase=(report.get("feedback") or {}).get("phase", "exit"), trigger_phase="live_exit",
                    lap_serial=self._lap_serial, reference_id=self._reference, report=report)
                self._last_corner_call = now

    def _periodic_event(self, frame, analysis, now):
        interval = {"less": 60, "balanced": 30, "more": 15}[self._settings["verbosity"]]
        if now - self._last_periodic < interval or frame.get("is_ai_controlled") is not False:
            return
        if frame.get("in_pit") is not False or (_num(frame.get("speed_kmh")) or 0) < 40 or (_num(frame.get("wheels_out")) or 0) > 2:
            return
        # Do not create a status call on top of a more useful fresh event.
        if self._new_events:
            return
        parts = []
        delta = _num((analysis.get("coaching") or {}).get("current_delta_s"))
        if delta is not None and (analysis.get("coaching") or {}).get("live_comparison_available"):
            parts.append(f"Pace check: {abs(delta):.2f} seconds {'behind' if delta >= 0 else 'ahead of'} your clean reference.")
        fuel = self._fuel_values(frame, analysis)
        if fuel["usable_laps"] is not None:
            parts.append(f"Fuel range about {fuel['usable_laps']:.1f} laps above reserve.")
        if parts:
            self._emit("periodic", "status", " ".join(parts), now, cooldown=interval, lifetime=12, trigger_phase="periodic")
            self._last_periodic = now

    def _availability(self, frame, analysis):
        pressures = _array(frame.get("tyre_pressure_psi"), positive=True)
        charge = _num(frame.get("kers_charge"))
        energy = bool((frame.get("capabilities") or {}).get("energy")) and charge is not None and 0 <= charge <= 1
        fuel = self._fuel_values(frame, analysis)
        return {"fuel": fuel["litres"] is not None, "fuel_consumption": fuel["rate"] is not None,
            "tyres": bool(pressures or _array(frame.get("tyre_core_c"), positive=True)), "tyre_target": self._tyre_target() is not None,
            "energy": energy, "flags": self._flag(frame) is not None,
            "track_limits": _num(frame.get("wheels_out")) is not None or isinstance(frame.get("lap_valid"), bool),
            "definitive_lap_validity": isinstance(frame.get("lap_valid"), bool),
            "corner_comparison": bool(self._plans(analysis)),
            "live_corner_comparison": bool((analysis.get("coaching") or {}).get("live_comparison_available") and self._reference_trace(analysis, active=True)),
            "driving_coaching": bool(self._plans(analysis)) and frame.get("is_ai_controlled") is False,
            "strategy": fuel["need"] is not None}

    def update(self, frame, analysis):
        with self._lock:
            frame, analysis = frame or {}, analysis or {}
            now = self._clock()
            self._new_events = []
            context = self._context_key(frame, analysis)
            reference = (analysis.get("reference") or {}).get("id")
            if context != self._context:
                self._context, self._reference = context, reference
                self._generation += 1
                self._events.clear(); self._cooldowns.clear(); self._reset_observations()
            elif reference != self._reference:
                self._reference = reference
                self._generation += 1
                self._events.clear()
                self._corners_called.clear()
                self._reported_corners.clear()
                self._corner_reports.clear()
                self._gain_history.clear()
            status = frame.get("status", "disconnected")
            if self._previous_status is not None and status != self._previous_status:
                self._generation += 1
                self._events.clear()
                self._emit("status-" + status, "status", "Session " + status + (". Live coaching resumed." if status == "live" else ". Live coaching paused."), now, lifetime=4)
            self._previous_status = status
            if status != "live" or not self._human_clean(frame, analysis):
                self._events = {key: event for key, event in self._events.items() if event["kind"] != "corner"}
            if frame.get("lap_valid") is False or (analysis.get("session") or {}).get("current_lap_flags"):
                self._gain_history.clear()
            if status == "live":
                position = self._lap_transition(frame, now)
                pit = frame.get("in_pit") is True
                if self._previous_pit is not None and pit != self._previous_pit:
                    self._emit("pit-" + str(pit), "pit", "In the pits. Driving coaching is paused." if pit else "Leaving the pits. Build tyre temperature before judging a hot-pressure target.", now, cooldown=10, lifetime=12)
                self._previous_pit = pit
                self._track_limits(frame, now)
                flag = self._flag(frame)
                if flag != self._previous_flag and flag not in (None, "clear"):
                    text = {"yellow": "Yellow flag reported. Watch ahead and follow the game's instructions.",
                        "blue": "Blue flag reported. Check faster traffic.", "black": "Black flag reported. Check the game's race-control message.",
                        "white": "White flag reported. Be alert for a slow vehicle.", "checkered": "Checkered flag reported.",
                        "penalty": "A penalty flag is reported. Check the game's race-control message."}[flag]
                    self._emit("flag-" + flag, "flag", text, now, priority=2, cooldown=8, lifetime=10)
                self._previous_flag = flag
                self._fuel_events(frame, analysis, now)
                self._tyre_events(frame, now)
                self._live_corner_events(frame, analysis, now, position)
                self._corner_events(frame, analysis, now, position)
                self._periodic_event(frame, analysis, now)
                self._previous_position = position
            else:
                self._offtrack_since = self._back_on_track_since = self._pressure_since = None
                self._previous_position = None
            self._purge(now)
            self._last_snapshot = {"generation": self._generation, "status": status,
                "events": sorted(self._events.values(), key=lambda event: (-event["priority"], -event["timestamp"])),
                "new_events": list(self._new_events), "next_corner": self._next(frame, analysis),
                "corner_reports": list(self._corner_reports),
                "availability": self._availability(frame, analysis), "quiet": self._settings["quiet"],
                "verbosity": self._settings["verbosity"], "suggested_commands": self.suggested_commands(),
                "note": "Detected corner labels are not official circuit turn numbers. Calls describe measured comparisons, not an optimal lap."}
            return copy.deepcopy(self._last_snapshot)

    @staticmethod
    def suggested_commands():
        return [{"topic": topic, "label": label} for topic, label in COMMANDS]

    def snapshot(self):
        with self._lock:
            result = copy.deepcopy(self._last_snapshot)
            result.update(generation=self._generation, quiet=self._settings["quiet"], verbosity=self._settings["verbosity"])
            return result

    def ask(self, topic, frame, analysis):
        with self._lock:
            frame, analysis = frame or {}, analysis or {}
            topic = str(topic or "status").strip().lower().replace(" ", "_").replace("-", "_").rstrip("?")
            topic = {"tires": "tyres", "tire": "tyres", "nextcorner": "next_corner", "lastlap": "last_lap", "tracklimits": "track_limits"}.get(topic, topic)
            answer = {"topic": topic, "available": True, "kind": "answer", "severity": "info", "source": "APEX live", "suggested_commands": self.suggested_commands()}
            fuel = self._fuel_values(frame, analysis)
            availability = self._availability(frame, analysis)
            if topic == "status":
                status = frame.get("status", "disconnected")
                text = "Session " + status + "."
                if frame.get("car_id"):
                    text += " " + str(frame["car_id"]).replace("_", " ") + " at " + str(frame.get("track_id") or "unknown track").replace("_", " ") + "."
                if frame.get("is_ai_controlled"):
                    text += " AI driving is active; driving coaching is paused."
                elif frame.get("in_pit"):
                    text += " You are in the pits."
            elif topic == "fuel":
                text = "Fuel is unavailable from this feed."
                if fuel["litres"] is not None:
                    text = f"{fuel['litres']:.1f} litres remaining."
                    if fuel["rate"] is not None:
                        text += f" Measured use is {fuel['rate']:.2f} litres per lap: {fuel['usable_laps']:.1f} laps above your {fuel['reserve']:.1f} litre reserve."
                    else:
                        text += " Consumption needs a complete clean lap before I can estimate range."
                answer["available"] = availability["fuel"]
            elif topic == "tyres":
                raw_pressures = frame.get("tyre_pressure_psi")
                pressures = _array(raw_pressures, positive=True)
                temperatures = _array(frame.get("tyre_core_c"), positive=True)
                text = "Tyre readings are unavailable."
                if pressures:
                    if isinstance(raw_pressures, (list, tuple)) and len(raw_pressures) == 4:
                        values = []
                        for label, raw in zip(("FL", "FR", "RL", "RR"), raw_pressures):
                            value = _num(raw)
                            values.append(label + (f" {value:.1f}" if value is not None and value > 0 else " unavailable"))
                        text = "Pressures: " + ", ".join(values) + " psi."
                    else:
                        text = "Wheel pressure positions are unavailable."
                    if temperatures:
                        text += f" Mean core temperature {statistics.mean(temperatures):.0f} degrees Celsius."
                    target = self._tyre_target()
                    text += f" Your verified hot target is {target:.1f} psi; judge it once warm." if target is not None else " No verified pressure or temperature target is set."
                elif temperatures:
                    text = f"Mean core temperature {statistics.mean(temperatures):.0f} degrees Celsius. Pressures are unavailable; no temperature target is inferred."
                answer["available"] = bool(pressures or temperatures)
            elif topic == "pace":
                delta = _num((analysis.get("coaching") or {}).get("current_delta_s"))
                reference = analysis.get("reference") or {}
                if delta is not None and reference:
                    text = f"Current delta is {abs(delta):.3f} seconds {'behind' if delta >= 0 else 'ahead of'} your own clean reference."
                elif reference:
                    text = "Your clean reference is " + _lap(reference.get("lap_time_ms")) + ". A usable live delta is unavailable for this lap."
                else:
                    text = "No clean reference yet. Drive a complete clean lap after the initial partial lap."
                answer["available"] = bool(reference)
            elif topic == "corners":
                plans = [plan for plan in self._plans(analysis) if plan["loss_s"] is not None and plan["loss_s"] > .08]
                plans.sort(key=lambda plan: -plan["loss_s"])
                text = "No measured corner losses are available from a comparable clean lap."
                if plans:
                    text = "Last comparison: " + "; ".join(f"{plan['label']} cost {plan['loss_s']:.2f} seconds" for plan in plans[:3]) + ". The following straights are included."
                answer["available"], answer["corners"] = bool(plans), plans
            elif topic.startswith("corner_") and topic[7:].isdigit():
                number = int(topic[7:])
                corner = next((c for c in analysis.get("corners", []) if c.get("number") == number), None)
                text = "No comparable clean measurement is available for detected corner " + str(number) + "."
                if corner and (analysis.get("coaching") or {}).get("comparison_available"):
                    loss = _num(corner.get("time_loss_s"))
                    text = f"Last clean comparison, detected corner {number} and its exit: "
                    text += f"{abs(loss):.2f} seconds {'lost' if loss >= 0 else 'gained'}." if loss is not None else "section time unavailable."
                    speed = _num(corner.get("exit_speed_kmh"))
                    reference_speed = _num(corner.get("reference_exit_speed_kmh"))
                    if speed is not None and reference_speed is not None:
                        text += f" Exit speed {speed:.0f}, reference {reference_speed:.0f} kilometres per hour."
                    guidance = principles_for_corner(corner)
                    experiment = next((p for p in guidance if p["id"] != "distance-before-verdict"), None)
                    if experiment:
                        text += " To test: " + experiment["experiment"]
                    answer["guidance"] = guidance
                answer["available"] = bool(corner and (analysis.get("coaching") or {}).get("comparison_available"))
            elif topic == "next_corner":
                next_corner = self._next(frame, analysis)
                text = "The next detected corner needs a clean comparison and a valid track-position reading."
                if next_corner:
                    text = next_corner["label"] + " is next"
                    if next_corner["distance_m"] is not None:
                        text += f", approximately {next_corner['distance_m']:.0f} metres to its reference braking area"
                    text += "."
                    detail, _ = self._corner_text(next_corner, "approach")
                    if detail and frame.get("is_ai_controlled") is False:
                        text += " " + detail
                answer["available"], answer["next_corner"] = next_corner is not None, next_corner
            elif topic == "last_lap":
                laps = analysis.get("laps") or []
                if laps:
                    last = laps[0]
                    text = "Last recorded lap: " + _lap(last.get("lap_time_ms")) + "."
                    text += " Observed clean." if last.get("clean") else " Excluded: " + str(last.get("reason") or "validity not confirmed") + "."
                    delta = _num(last.get("delta_s"))
                    if delta is not None and last.get("clean"):
                        text += f" {abs(delta):.3f} seconds {'slower' if delta >= 0 else 'faster'} than its reference."
                elif (_num(frame.get("last_lap_ms")) or 0) > 0:
                    text = "The game's last lap is " + _lap(frame.get("last_lap_ms")) + ". No complete recorded comparison is available."
                else:
                    text = "No completed lap time is available yet."
                    answer["available"] = False
            elif topic == "feedback":
                feedback = FeedbackAnalyzer.analyze(analysis, analysis.get("foundation"))
                text = feedback["summary"]
                answer["available"], answer["feedback"] = feedback["available"], feedback
            elif topic == "strategy":
                if fuel["need"] is not None:
                    text = f"Current race-fuel estimate: {fuel['need']:.1f} litres including reserve."
                    if fuel["litres"] is None:
                        text += " Current fuel is unavailable, so I cannot assess coverage."
                    else:
                        text += f" You are {fuel['shortfall']:.1f} litres short." if fuel["shortfall"] and fuel["shortfall"] > 0 else " Current fuel covers that estimate."
                    if fuel["estimated"]:
                        text += " Timed-race finishing rules and leader progress can change the requirement."
                    text += " Mandatory stops and refuelling rules are not provided by this calculation."
                else:
                    text = "A race fuel plan needs measured consumption and known race length. Mandatory stops and tyre life are unavailable; I cannot invent a pit strategy."
                    answer["available"] = False
            elif topic == "energy":
                charge = _num(frame.get("kers_charge"))
                if availability["energy"] and charge is not None and 0 <= charge <= 1:
                    text = f"Hybrid charge {charge*100:.0f} percent."
                    for key, label in (("ers_power_level", "Deployment setting"), ("ers_recovery_level", "Recovery setting")):
                        value = _num(frame.get(key))
                        if value is not None:
                            text += f" {label} {value:g}."
                    text += " No validated deployment plan is available for this car."
                else:
                    text = "Usable hybrid-energy telemetry is unavailable for this car and feed."
                    answer["available"] = False
            elif topic == "track_limits":
                outside = _num(frame.get("wheels_out"))
                if frame.get("lap_valid") is False:
                    text = "The game explicitly reports this lap is invalid."
                elif outside is not None and outside >= 3:
                    text = f"{int(outside)} wheels are currently outside the track. This is a track-limit warning, not definitive lap invalidity."
                elif outside is not None:
                    text = f"{int(outside)} wheels currently outside; no current off-track warning."
                    if frame.get("lap_valid") is None:
                        text += " This feed does not confirm lap validity."
                else:
                    text = "Track-limit and definitive validity readings are unavailable."
                    answer["available"] = False
            elif topic in ("quiet", "more_updates", "less_updates"):
                action = {"quiet": True} if topic == "quiet" else {"quiet": False, "verbosity": "more" if topic == "more_updates" else "less"}
                self.configure(action)
                answer["action"] = action
                text = "Routine radio calls are quiet. Urgent track-limit and critical fuel alerts stay on." if topic == "quiet" else "More updates selected. Corner reminders still require measured clean references and safe gaps." if topic == "more_updates" else "Fewer updates selected. I will focus on larger measured losses and important alerts."
            else:
                text = "That command needs race timing from the companion." if topic in {"personal_best", "race_best", "others_best", "gap_ahead", "gap_behind", "gap_leader", "change_compound"} else "That command is unavailable. Choose status, fuel, tyres, pace, feedback, corners, next corner, last lap, strategy, energy or track limits."
                answer["available"] = False
            answer.update(text=text, generation=self._generation, quiet=self._settings["quiet"], verbosity=self._settings["verbosity"])
            return answer
