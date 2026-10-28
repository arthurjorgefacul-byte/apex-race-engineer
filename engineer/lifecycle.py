"""Explicit recording sessions and compact, persistent post-session debriefs.

This class never reads or mutates the game or the recording database. The
companion supplies snapshots before disconnecting its analysis engine.
"""
from __future__ import annotations

import copy
import json
import math
import pathlib
import statistics
import time
import uuid


def _number(value):
    if isinstance(value, bool):
        return None
    try:
        number = float(value)
        return number if math.isfinite(number) else None
    except (ValueError, TypeError, OverflowError):
        return None


def _valid(frame):
    return bool(isinstance(frame, dict) and frame.get("status") in ("live", "paused")
                and not frame.get("is_replay") and not frame.get("replay")
                and frame.get("car_id") and frame.get("track_id"))


def _identity(frame):
    return tuple(frame.get(key) for key in ("source", "car_id", "track_id", "layout", "session_type", "session_index", "session_nonce"))


def _lap_time(value):
    return "%d:%06.3f" % (int(value // 60000), value % 60000 / 1000)


class SessionLifecycle:
    DISCONNECT_GRACE_S = 3.0

    def __init__(self, data_dir, clock=time.time):
        self.clock = clock
        self.directory = pathlib.Path(data_dir) / "debriefs"
        self.directory.mkdir(parents=True, exist_ok=True)
        self.active = False
        self.armed = False
        self.active_session_id = None
        self.last_debrief = None
        self.last_error = None
        self._phase = "home"
        self._context = None
        self._frame = None
        self._started = None
        self._missing_since = None
        self._lap_count = None
        self._analysis = {}
        self._foundation = {}
        self._laps = {}
        self._forgotten = set()
        self._session_ids = []
        self._feedback = {}
        self._feedback_context = {}
        latest = self.directory / "latest.json"
        try:
            saved = json.loads(latest.read_text(encoding="utf-8"))
            if isinstance(saved, dict) and saved.get("schema") == 1 and isinstance(saved.get("metrics"), dict):
                self.last_debrief = saved
                # Opening the application always starts at Home. The previous
                # debrief remains visible there without resuming recording.
        except (OSError, ValueError, TypeError):
            pass

    def start(self, frame=None):
        if self.active:
            return None
        self.armed = True
        self._phase = "home"
        if _valid(frame):
            return self._begin(frame)
        return None

    def _begin(self, frame):
        self.active, self.armed = True, False
        self._phase = "session"
        self._context = _identity(frame)
        self._frame = copy.deepcopy(frame)
        self._started = self.clock()
        self._missing_since = None
        self._lap_count = _number(frame.get("lap_count"))
        self.active_session_id = None
        self._laps, self._session_ids = {}, []
        self._forgotten.clear()
        self._analysis, self._foundation = {}, {}
        self._feedback, self._feedback_context = {}, {}
        return "started"

    def attach(self, analysis):
        """Accumulate summaries so a long session is not limited to 30 laps."""
        if not self.active or not isinstance(analysis, dict):
            return
        self._analysis = analysis
        session = analysis.get("session") or {}
        session_id = session.get("id")
        if session_id:
            self.active_session_id = str(session_id)
            if str(session_id) not in self._session_ids:
                self._session_ids.append(str(session_id))
        feedback = analysis.get("feedback") or {}
        coaching = analysis.get("coaching") or {}
        basis = {key: coaching.get(key) for key in ("comparison_lap_id", "comparison_reference_id", "comparison_lap_number", "reference_lap_number")}
        basis["session_id"] = session_id
        deleted_comparison = any(str(basis.get(key)) in self._forgotten for key in ("comparison_lap_id", "comparison_reference_id"))
        if feedback.get("available") is True and not deleted_comparison:
            if basis != self._feedback_context or feedback != self._feedback:
                self._feedback = copy.deepcopy(feedback)
                self._feedback_context = basis
        for lap in analysis.get("laps") or []:
            if not isinstance(lap, dict) or lap.get("deleted") or lap.get("deleted_at"):
                continue
            identifier = lap.get("id")
            if not identifier:
                identifier = (str(session_id), lap.get("lap_count", lap.get("lap_number")))
            key = str(identifier)
            if key in self._forgotten:
                continue
            candidate = dict(lap, _context_id=session_id)
            if self._laps.get(key) != candidate:
                self._laps[key] = copy.deepcopy(candidate)

    def forget_lap(self, lap_id):
        """Exclude a reversible driver deletion from this debrief's cache."""
        key = str(lap_id)
        self._forgotten.add(key)
        self._laps.pop(key, None)
        if any(str(self._feedback_context.get(field)) == key for field in ("comparison_lap_id", "comparison_reference_id")):
            self._feedback, self._feedback_context = {}, {}

    def restore_lap(self, summary):
        """Restore an edited lap even if it fell outside the live 30-lap cache."""
        if not self.active or not isinstance(summary, dict):
            return
        identifier, session_id = summary.get("id"), summary.get("session_id")
        if identifier is None or session_id not in self._session_ids or summary.get("deleted") or summary.get("deleted_at"):
            return
        key = str(identifier)
        self._forgotten.discard(key)
        self._laps[key] = {**copy.deepcopy(summary), "_context_id": session_id}

    def observe(self, frame, analysis=None, foundation=None):
        if isinstance(foundation, dict):
            self._foundation = foundation
        if self.active and isinstance(analysis, dict):
            self.attach(analysis)
        if not self.active:
            if self.armed and _valid(frame):
                return self._begin(frame)
            return None
        now = self.clock()
        if not _valid(frame):
            if self._missing_since is None:
                self._missing_since = now
            if now - self._missing_since >= self.DISCONNECT_GRACE_S:
                self.end(reason="Session disconnected")
                return "ended"
            return None
        self._missing_since = None
        context = _identity(frame)
        # Missing optional fields are not a new session. Once supplied, retain
        # them so a later verified session index/type change closes this one.
        changed = any(old is not None and new is not None and old != new
                      for old, new in zip(self._context, context))
        count = _number(frame.get("lap_count"))
        restarted = count is not None and self._lap_count is not None and count < self._lap_count
        finished = frame.get("session_finished") is True or frame.get("session_ended") is True or frame.get("results") is True
        if changed or restarted or finished:
            self.end(reason="Session finished" if finished else "New game session detected")
            return "ended"
        self._context = tuple(new if new is not None else old for old, new in zip(self._context, context))
        self._frame = copy.deepcopy(frame)
        if count is not None:
            self._lap_count = count
        return None

    def end(self, analysis=None, foundation=None, reason="Session ended by driver"):
        if not self.active:
            self.armed = False
            self._phase = "debrief" if self.last_debrief else "home"
            return self.last_debrief
        if isinstance(analysis, dict):
            self.attach(analysis)
        if isinstance(foundation, dict):
            self._foundation = foundation
        ended = self.clock()
        debrief = self._debrief(ended, reason)
        self.active, self.armed = False, False
        self.active_session_id = None
        self._phase = "debrief"
        self.last_debrief = debrief
        try:
            encoded = json.dumps(debrief, ensure_ascii=False, allow_nan=False, separators=(",", ":"))
            target = self.directory / (debrief["id"] + ".json")
            temporary = target.with_suffix(".tmp")
            temporary.write_text(encoded, encoding="utf-8")
            temporary.replace(target)
            latest = self.directory / "latest.json"
            temporary = self.directory / "latest.tmp"
            temporary.write_text(encoded, encoding="utf-8")
            temporary.replace(latest)
            self.last_error = None
        except (OSError, ValueError, TypeError) as exc:
            self.last_error = "Debrief could not be saved: " + str(exc)[:180]
        return debrief

    def _debrief(self, ended, reason):
        analysis = self._analysis or {}
        frame = self._frame or {}
        laps = list(self._laps.values())
        clean = [lap for lap in laps if lap.get("clean") is True and (_number(lap.get("lap_time_ms")) or 0) > 0]
        best = min((_number(lap.get("lap_time_ms")) for lap in clean), default=None)
        groups = {}
        for lap in clean:
            groups.setdefault(lap.get("_context_id"), []).append(_number(lap.get("lap_time_ms")) / 1000)
        comparable = max(groups.values(), key=len, default=[])
        consumption = [_number(lap.get("fuel_used_l")) for lap in clean]
        consumption = [value for value in consumption if value is not None and value >= 0]
        metrics = {"session_laps": len(laps), "clean_laps": len(clean), "excluded_laps": len(laps) - len(clean),
                   "best_lap_ms": best,
                   "consistency_s": round(statistics.pstdev(comparable), 3) if len(comparable) >= 2 else None,
                   "consistency_samples": len(comparable),
                   "consistency_note": "Standard deviation of clean lap times in the largest single recording context; traffic and conditions may still vary.",
                   "fuel_used_l": round(sum(consumption), 3) if consumption else None,
                   "fuel_note": "Sum of measured fuel use on complete clean laps only; excludes partial and invalid laps.",
                   "duration_s": round(max(0, ended - self._started), 1),
                   "duration_note": "Elapsed session time, including pauses."}
        feedback = self._feedback or {}
        strengths = copy.deepcopy(feedback.get("strengths") or [])[:3]
        if clean and not strengths:
            strengths.append({"text": "%d complete clean lap%s recorded for repeatable comparisons." % (len(clean), "" if len(clean) == 1 else "s")})
        ordered = sorted(clean, key=lambda lap: _number(lap.get("lap_count", lap.get("lap_number"))) or 0)
        if len(ordered) >= 2:
            first, last = _number(ordered[0].get("lap_time_ms")), _number(ordered[-1].get("lap_time_ms"))
            if first and last and first - last > 100 and ordered[0].get("_context_id") == ordered[-1].get("_context_id"):
                strengths.append({"text": "Your last clean lap was %.2f seconds faster than your first clean lap in the same recording context." % ((first - last) / 1000)})
        priorities = copy.deepcopy(feedback.get("priorities") or [])[:3]
        historical = bool(feedback and (analysis.get("feedback") or {}).get("available") is not True)
        if historical:
            basis_note = "Earlier completed-lap comparison; the final lap had no usable comparable reference. Recheck this experiment next session."
            for priority in priorities:
                if isinstance(priority, dict):
                    priority["evidence"] = list(priority.get("evidence") or []) + [basis_note]
        plans = copy.deepcopy(feedback.get("next_session_plan") or [])[:3]
        if not priorities:
            if not clean:
                priorities = [{"instruction": "Complete a full clean lap to establish a reliable baseline.",
                               "evidence": ["No complete clean lap was recorded in this session."]}]
            elif not feedback.get("available"):
                instruction = "Build a second comparable clean lap before choosing a corner experiment." if len(clean) == 1 else "Keep the compound, setup and conditions stable to obtain a usable corner comparison."
                priorities = [{"instruction": instruction,
                               "evidence": ["No usable comparable corner comparison was retained; lap times alone do not establish a cause."]}]
        if not plans:
            plans = ["Keep the same car, compound and setup for several clean laps; change one driving input at a time."]
        summary = "%d completed lap%s, %d clean." % (len(laps), "" if len(laps) == 1 else "s", len(clean))
        if best:
            summary += " Best clean lap %s." % _lap_time(best)
        foundation = self._foundation or analysis.get("foundation") or {}
        source_context = {key: copy.deepcopy(foundation.get(key)) for key in ("category", "track", "coverage", "best_driver_evidence") if key in foundation}
        source_context["limits"] = ["Research examples are not a verified elite target for this car, setup and conditions."]
        return {"schema": 1, "id": str(uuid.uuid4()), "started": self._started, "ended": ended,
                "session_id": self.active_session_id, "session_ids": list(self._session_ids),
                "source": frame.get("source", "live"), "demo": frame.get("source") == "demo",
                "car_id": frame.get("car_id"), "track_id": frame.get("track_id"), "layout": frame.get("layout"),
                "reason": reason, "summary": summary.strip(), "metrics": metrics,
                "strengths": strengths[:3], "priorities": priorities, "next_session_plan": plans,
                "feedback_basis": dict(self._feedback_context, historical=historical,
                                       scope="Latest usable completed-lap comparison in this driver session") if self._feedback_context else None,
                "foundation": source_context,
                "limitations": list(feedback.get("limitations") or []),
                "recording_note": "Only completed lap summaries are included. The unfinished final lap is not scored."}

    def snapshot(self):
        return {"phase": self._phase, "armed": self.armed, "active_session_id": self.active_session_id,
                "last_debrief": copy.deepcopy(self.last_debrief), "error": self.last_error}
