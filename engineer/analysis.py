"""Measured, local lap analysis for the original Assetto Corsa.

References are intentionally scoped to a recording session. A car name alone is
not a reliable identifier for a mod version, setup, tyre state or track surface.
All times and differences in the public snapshot are seconds unless named _ms.
"""

from __future__ import annotations

import bisect
import collections
import datetime as _datetime
import json
import math
import pathlib
import sqlite3
import statistics
import threading
import time
import uuid


def _number(value, default=None):
    if isinstance(value, bool):
        return default
    try:
        value = float(value)
        return value if math.isfinite(value) else default
    except (TypeError, ValueError, OverflowError):
        return default


def _safe(value):
    if isinstance(value, float) and not math.isfinite(value):
        return None
    if isinstance(value, dict):
        return {str(k): _safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, collections.deque)):
        return [_safe(v) for v in value]
    return value


def _mean(values):
    values = [_number(v) for v in (values or [])]
    values = [v for v in values if v is not None]
    return statistics.mean(values) if values else None


def _interp(samples, x, key="t"):
    if not samples:
        return None
    positions = [s["x"] for s in samples]
    i = bisect.bisect_left(positions, x)
    if i <= 0:
        return samples[0].get(key)
    if i >= len(samples):
        return samples[-1].get(key)
    a, b = samples[i - 1], samples[i]
    av, bv = a.get(key), b.get(key)
    if av is None or bv is None:
        return None
    gap = b["x"] - a["x"]
    return av if gap <= 0 else av + (bv - av) * ((x - a["x"]) / gap)


def _difference(current, reference):
    if current is None or reference is None:
        return None
    return round(current - reference, 4)


def _gear(value):
    """Keep normalized display gears categorical; never average shifts."""
    if isinstance(value, str):
        value = value.strip().upper()
        if value in ("R", "N"):
            return value
    number = _number(value)
    return str(int(number)) if number is not None and number == int(number) and 1 <= number <= 20 else None


class _HistoryAccess:
    """Read-only queries and pure lap calculations shared by storage views."""

    @staticmethod
    def _identifier(value, label):
        if not isinstance(value, str) or not value or len(value) > 200:
            raise ValueError("Unknown " + label + ".")
        return value

    @staticmethod
    def _json_value(raw, fallback):
        try:
            value = json.loads(raw)
            return value if isinstance(value, type(fallback)) else fallback
        except (TypeError, ValueError):
            return fallback

    def _require_session(self, session_id):
        self._identifier(session_id, "session")
        row = self._db.execute("SELECT * FROM sessions WHERE id=?", (session_id,)).fetchone()
        if row is None:
            raise ValueError("Unknown session.")
        return row

    def _stored_lap(self, lap_id, session_id=None, include_deleted=False, include_trace=True):
        self._identifier(lap_id, "lap")
        row = self._db.execute(
            "SELECT laps.*, sessions.metadata AS session_metadata FROM laps "
            "JOIN sessions ON sessions.id=laps.session_id WHERE laps.id=?", (lap_id,),
        ).fetchone()
        if row is None or (session_id is not None and row["session_id"] != session_id):
            raise ValueError("Unknown lap in this session.")
        if row["deleted_at"] is not None and not include_deleted:
            raise ValueError("This lap was removed from analysis. Restore it first.")
        summary = self._json_value(row["summary"], {})
        summary.update({
            "id": row["id"], "session_id": row["session_id"],
            "lap_count": row["lap_count"], "lap_number": row["lap_count"] + 1,
            "lap_time_ms": row["duration_ms"],
            "lap_time_s": row["duration_ms"] / 1000 if row["duration_ms"] else None,
            "clean": bool(row["clean"]), "valid": bool(row["clean"]),
            "reason": row["reason"], "deleted_at": row["deleted_at"],
            "deleted": row["deleted_at"] is not None,
        })
        metadata = self._json_value(row["session_metadata"], {})
        specific = self._json_value(row["metadata"], {})
        if not specific:
            # v0.1 stored per-session conditions but not per-lap metadata.
            # Recover the lap's starting fuel/tyre readings from its first
            # recorded frame; preserve the older session conditions as known.
            first = self._db.execute(
                "SELECT payload FROM frames WHERE session_id=? AND lap_count=? "
                "ORDER BY rowid LIMIT 1", (row["session_id"], row["lap_count"]),
            ).fetchone()
            if first:
                payload = self._json_value(first["payload"], {})
                if payload.get("fuel_l") is not None:
                    metadata["fuel_l"] = payload["fuel_l"]
                metadata["tyre_core_c_mean"] = _mean(payload.get("tyre_core_c"))
            metadata["historical_metadata_note"] = "Per-lap conditions unavailable in this older recording."
        metadata.update(specific)
        return {"summary": _safe(summary), "meta": _safe(metadata),
                "trace": _safe(self._json_value(row["trace"], [])) if include_trace else []}

    def list_sessions(self, include_deleted=False):
        """Persistent sessions, newest first; no 30-lap live-cache limit."""
        with self._lock:
            rows = self._db.execute("""
                SELECT s.*, COUNT(l.id) AS total_laps,
                    SUM(CASE WHEN l.id IS NOT NULL AND l.deleted_at IS NULL THEN 1 ELSE 0 END) AS visible_laps,
                    SUM(CASE WHEN l.clean=1 AND l.deleted_at IS NULL THEN 1 ELSE 0 END) AS clean_laps,
                    SUM(CASE WHEN l.clean=0 AND l.deleted_at IS NULL THEN 1 ELSE 0 END) AS excluded_laps,
                    SUM(CASE WHEN l.deleted_at IS NOT NULL THEN 1 ELSE 0 END) AS deleted_laps,
                    MIN(CASE WHEN l.clean=1 AND l.deleted_at IS NULL THEN l.duration_ms END) AS best_lap_ms
                FROM sessions s LEFT JOIN laps l ON l.session_id=s.id
                GROUP BY s.id ORDER BY s.started DESC, s.rowid DESC
            """).fetchall()
            result = []
            for row in rows:
                item = {key: row[key] for key in (
                    "id", "started", "source", "car_id", "track_id", "layout", "compound",
                    "total_laps", "clean_laps", "excluded_laps", "deleted_laps", "best_lap_ms",
                )}
                item["lap_count"] = row["total_laps"] if include_deleted else row["visible_laps"]
                item["best_lap_s"] = row["best_lap_ms"] / 1000 if row["best_lap_ms"] else None
                item["metadata"] = self._json_value(row["metadata"], {})
                item["active"] = row["id"] == self._session_id
                result.append(_safe(item))
            return result

    def session_detail(self, session_id, include_deleted=False):
        with self._lock:
            self._require_session(session_id)
            session = next(s for s in self.list_sessions(include_deleted) if s["id"] == session_id)
            predicate = "" if include_deleted else " AND deleted_at IS NULL"
            rows = self._db.execute(
                "SELECT id FROM laps WHERE session_id=?" + predicate + " ORDER BY lap_count, rowid",
                (session_id,),
            ).fetchall()
            laps = [self._stored_lap(row["id"], session_id, include_deleted, include_trace=False)["summary"] for row in rows]
            return {"session": session, "laps": laps, "recording": {
                "database": str(self.database_path), "format": "SQLite", "full_history": True,
            }}

    def analyze_stored(self, session_id, lap_id, reference_id=None):
        """Analyze a saved lap without replacing the live car or reference."""
        with self._lock:
            self._require_session(session_id)
            lap = self._stored_lap(lap_id, session_id)
            reference = None
            reference_rejections = []
            notes = ["Stored analysis; live recording and its reference are unchanged."]

            def rejection(candidate, condition):
                selected = _number(lap["meta"].get(condition))
                other = _number(candidate["meta"].get(condition))
                thresholds = {"road_c": 3.0, "ambient_c": 5.0, "surface_grip": 0.02,
                              "fuel_l": 5.0, "tyre_core_c_mean": 10.0}
                tolerance = (max(10, selected * .01) if selected is not None else None) if condition == "track_length_m" else thresholds.get(condition)
                signed = other - selected if selected is not None and other is not None else None
                difference = abs(signed) if signed is not None else None
                number = candidate["summary"].get("lap_number")
                selected_number = lap["summary"].get("lap_number")
                units = {"road_c": "°C", "ambient_c": "°C", "surface_grip": "ratio",
                         "fuel_l": "L", "tyre_core_c_mean": "°C", "track_length_m": "m"}
                if condition == "fuel_l" and difference is not None:
                    message = "Lap %s starts with %.2f L %s fuel than Lap %s (%g L comparison band)." % (
                        number, difference, "more" if signed > 0 else "less", selected_number, tolerance)
                elif difference is not None and tolerance is not None:
                    names = {"road_c": "road temperature", "ambient_c": "air temperature",
                             "surface_grip": "surface grip", "tyre_core_c_mean": "mean tyre core temperature",
                             "track_length_m": "recorded track length"}
                    message = "Lap %s has %s %.3g %s %s than Lap %s (%g %s comparison band)." % (
                        number, names.get(condition, str(condition).replace("_", " ")), difference,
                        units.get(condition, ""), "higher" if signed > 0 else "lower", selected_number,
                        tolerance, units.get(condition, ""))
                else:
                    message = "Lap %s has different %s from Lap %s." % (number, str(condition).replace("_", " "), selected_number)
                return {"candidate_id": candidate["summary"]["id"], "lap_number": number,
                        "condition": condition, "selected_value": selected, "candidate_value": other,
                        "difference": difference, "signed_difference": signed, "tolerance": tolerance,
                        "unit": units.get(condition), "message": message}

            if reference_id is not None:
                if reference_id == lap_id:
                    raise ValueError("Choose a different lap as the reference.")
                reference = self._stored_lap(reference_id, session_id)
                if not reference["summary"]["clean"]:
                    raise ValueError("A reference must be a clean, non-deleted lap from this session.")
                comparable, condition = self._comparable(lap["meta"], reference["meta"])
                if not comparable:
                    raise ValueError(rejection(reference, condition)["message"])
            else:
                candidates = self._db.execute(
                    "SELECT id FROM laps WHERE session_id=? AND id<>? AND clean=1 "
                    "AND deleted_at IS NULL AND duration_ms>0 ORDER BY duration_ms, rowid",
                    (session_id, lap_id),
                ).fetchall()
                for row in candidates:
                    candidate = self._stored_lap(row["id"], session_id)
                    comparable, condition = self._comparable(lap["meta"], candidate["meta"])
                    if comparable:
                        reference = candidate
                        break
                    reference_rejections.append(rejection(candidate, condition))
            if not reference:
                if reference_rejections:
                    notes.append("No comparable reference for Lap %s. Other clean laps exceed the comparison bands:" % lap["summary"].get("lap_number"))
                    notes.extend(item["message"] for item in reference_rejections[:8])
                    if len(reference_rejections) > 8:
                        notes.append("%d further rejected references are listed in reference_rejections." % (len(reference_rejections) - 8))
                else:
                    notes.append("No other clean lap is available as a reference.")
            if not lap["summary"]["clean"]:
                notes.append("This lap was excluded. Its trace is available for inspection; automatic corner coaching is withheld.")
            available = bool(reference and lap["summary"]["clean"] and lap["trace"] and reference["trace"])
            corners = self._compare_corners(lap, reference) if available else []
            delta = (lap["summary"]["lap_time_s"] - reference["summary"]["lap_time_s"]) if available else None
            history = []
            if available:
                # This is a separate read-only view for HTTP history. Bound
                # evidence work to eight laps, independently of recording.
                rows = self._db.execute(
                    "SELECT id FROM laps WHERE session_id=? AND clean=1 AND deleted_at IS NULL "
                    "AND id<>? ORDER BY lap_count DESC,rowid DESC LIMIT 8", (session_id, reference["summary"]["id"]),
                ).fetchall()
                for row in reversed(rows):
                    other = lap if row["id"] == lap_id else self._stored_lap(row["id"], session_id)
                    if self._comparable(other["meta"], reference["meta"])[0]:
                        compared = corners if row["id"] == lap_id else self._compare_corners(other, reference)
                        history.append(self._comparison_record(session_id, other, reference, compared))
            return _safe({
                "session": next(session for session in self.list_sessions() if session["id"] == session_id),
                "lap": dict(lap["summary"], metadata=lap["meta"]),
                "reference": dict(reference["summary"], metadata=reference["meta"]) if reference else None,
                "traces": {"current": self._display_trace(lap["trace"]),
                           "reference": self._display_trace(reference["trace"]) if reference else [],
                           "axis": "normalized_track_position"},
                "corners": corners, "comparison_available": available,
                "reference_rejections": reference_rejections,
                "comparison_history": history,
                "coaching": {"comparison_available": available, "delta_s": round(delta, 4) if delta is not None else None,
                    "comparison_lap_id": lap["summary"]["id"],
                    "comparison_reference_id": reference["summary"]["id"] if reference else None,
                    "comparison_lap_number": lap["summary"]["lap_number"],
                    "reference_lap_number": reference["summary"]["lap_number"] if reference else None,
                    "priorities": sorted([c for c in corners if c["time_loss_s"] > 0.08], key=lambda c: c["time_loss_s"], reverse=True)[:3]},
                "notes": notes,
            })

    def export_session(self, session_id, include_deleted=False):
        """Full stored traces and frame payloads, never the live downsample."""
        with self._lock:
            detail = self.session_detail(session_id, include_deleted)
            laps = []
            for summary in detail["laps"]:
                stored = self._stored_lap(summary["id"], session_id, include_deleted)
                laps.append(dict(stored["summary"], metadata=stored["meta"], trace=stored["trace"]))
            frames = self._db.execute(
                "SELECT timestamp,lap_count,position,elapsed_ms,payload FROM frames "
                "WHERE session_id=? ORDER BY rowid", (session_id,),
            ).fetchall()
            return _safe({"schema": 1, "session": detail["session"], "laps": laps,
                "frames": [{"timestamp": f["timestamp"], "lap_count": f["lap_count"],
                            "normalized_pos": f["position"], "lap_time_ms": f["elapsed_ms"],
                            "telemetry": self._json_value(f["payload"], {})} for f in frames],
                "includes_deleted": bool(include_deleted),
                "note": "Deletion removes laps from analysis, not original frame recordings. Raw frames include the original complete session."})

    def iter_export_session_json(self, session_id, include_deleted=False):
        """Return a UTF-8 JSON iterator, validating the session immediately.

        Use a StoredSessionView for a stable WAL snapshot and an independent
        connection. The caller may obtain this iterator before sending HTTP
        headers: unknown identifiers raise here, not on the first iteration.
        Frame payloads are fetched 256 at a time rather than materializing an
        entire session, which may span hours and hundreds of megabytes.
        """
        with self._lock:
            self._require_session(session_id)
            session = next(item for item in self.list_sessions(include_deleted) if item["id"] == session_id)
            predicate = "" if include_deleted else " AND deleted_at IS NULL"
            lap_cursor = self._db.execute(
                "SELECT id FROM laps WHERE session_id=?" + predicate + " ORDER BY lap_count,rowid",
                (session_id,),
            )
            frame_cursor = self._db.execute(
                "SELECT timestamp,lap_count,position,elapsed_ms,payload FROM frames "
                "WHERE session_id=? ORDER BY rowid", (session_id,),
            )

        def encoded(value):
            return json.dumps(_safe(value), ensure_ascii=False, allow_nan=False,
                              separators=(",", ":")).encode("utf-8")

        def chunks():
            try:
                yield b'{"schema":1,"session":' + encoded(session) + b',"laps":['
                first = True
                for row in lap_cursor:
                    stored = self._stored_lap(row["id"], session_id, include_deleted)
                    yield (b"" if first else b",") + encoded(dict(stored["summary"], metadata=stored["meta"], trace=stored["trace"]))
                    first = False
                yield b'],"frames":['
                first = True
                while True:
                    batch = frame_cursor.fetchmany(256)
                    if not batch:
                        break
                    for frame in batch:
                        yield (b"" if first else b",") + encoded({"timestamp": frame["timestamp"], "lap_count": frame["lap_count"],
                            "normalized_pos": frame["position"], "lap_time_ms": frame["elapsed_ms"],
                            "telemetry": self._json_value(frame["payload"], {})})
                        first = False
                yield b'],"includes_deleted":' + encoded(bool(include_deleted)) + b',"note":' + encoded(
                    "Deletion removes laps from analysis, not original frame recordings. Raw frames include the original complete session.") + b"}"
            finally:
                for cursor in (lap_cursor, frame_cursor):
                    try:
                        cursor.close()
                    except sqlite3.Error:
                        # A disconnected HTTP client can close the context
                        # before Python finalizes the abandoned generator.
                        pass

        return chunks()

    @staticmethod
    def _comparable(a, b):
        thresholds = {"road_c": 3.0, "ambient_c": 5.0, "surface_grip": 0.02,
                      "fuel_l": 5.0, "tyre_core_c_mean": 10.0}
        for key, tolerance in thresholds.items():
            av, bv = _number(a.get(key)), _number(b.get(key))
            if av is not None and bv is not None and abs(av - bv) > tolerance:
                return False, key
        la, lb = _number(a.get("track_length_m")), _number(b.get("track_length_m"))
        if la and lb and abs(la - lb) > max(10, la * 0.01):
            return False, "track_length_m"
        return True, None

    @staticmethod
    def _corner_spans(trace):
        active = []
        for s in trace:
            if s["speed"] > 25 and (abs(s.get("lat_g") or 0) >= 0.32 or abs(s.get("steer") or 0) >= 0.09):
                active.append(s["x"])
        if not active:
            return []
        groups = [[active[0], active[0]]]
        for x in active[1:]:
            if x - groups[-1][1] <= 0.013:
                groups[-1][1] = x
            else:
                groups.append([x, x])
        return [(max(0, a - 0.002), min(1, b + 0.002)) for a, b in groups if b - a >= 0.006]

    @staticmethod
    def _threshold_point(trace, begin, end, key, threshold, sustained=False):
        candidates = [s for s in trace if begin <= s["x"] <= end]
        for i, s in enumerate(candidates):
            if s.get(key, 0) < threshold:
                continue
            if sustained:
                following = candidates[i:i + 3]
                if len(following) < 3 or any(p.get(key, 0) < threshold for p in following):
                    continue
            return s["x"]
        return None

    @staticmethod
    def _categorical_at(trace, position, key):
        if not trace:
            return None
        point = min(trace, key=lambda item: abs(item["x"] - position))
        return point.get(key) if abs(point["x"] - position) <= .015 else None

    @staticmethod
    def _braking_evidence(trace, onset, apex):
        """Observed brake release and speed-derived deceleration, not grip."""
        if onset is None:
            return {"release_pos": None, "mean_deceleration_mps2": None}
        points = [p for p in trace if onset <= p["x"] <= apex]
        release = None
        for i, point in enumerate(points):
            following = points[i:i + 2]
            if len(following) == 2 and all(_number(p.get("brake")) is not None and p["brake"] < .05 for p in following):
                release = point["x"]
                break
        braking = [p for p in points if release is None or p["x"] <= release]
        deceleration = None
        if len(braking) >= 2:
            first, last = braking[0], braking[-1]
            dt = last["t"] - first["t"]
            if dt >= .1:
                measured = (first["speed"] - last["speed"]) / 3.6 / dt
                deceleration = round(measured, 3) if measured >= 0 else None
        return {"release_pos": release, "mean_deceleration_mps2": deceleration}

    def _compare_corners(self, lap, reference):
        current, ref = lap["trace"], reference["trace"]
        spans = self._corner_spans(ref)
        length = _number(lap["meta"].get("track_length_m"))
        lookback = min(0.08, 220 / length) if length and length > 50 else 0.04
        anchors = []
        for i, (begin, end) in enumerate(spans):
            lower = spans[i - 1][1] if i else 0.0
            anchors.append(max(lower, begin - lookback))
        output = []
        for i, ((begin, end), anchor) in enumerate(zip(spans, anchors)):
            next_anchor = anchors[i + 1] if i + 1 < len(anchors) else 1.0
            cur_part = [s for s in current if begin <= s["x"] <= end]
            ref_part = [s for s in ref if begin <= s["x"] <= end]
            if not cur_part or not ref_part or next_anchor <= anchor:
                continue
            cur_min = min(cur_part, key=lambda s: s["speed"])
            ref_min = min(ref_part, key=lambda s: s["speed"])
            cur_brake = self._threshold_point(current, anchor, cur_min["x"], "brake", 0.08)
            ref_brake = self._threshold_point(ref, anchor, ref_min["x"], "brake", 0.08)
            cur_gas = self._threshold_point(current, cur_min["x"], next_anchor, "gas", 0.9, True)
            ref_gas = self._threshold_point(ref, ref_min["x"], next_anchor, "gas", 0.9, True)
            cur_braking = self._braking_evidence(current, cur_brake, cur_min["x"])
            ref_braking = self._braking_evidence(ref, ref_brake, ref_min["x"])
            cur_release, ref_release = cur_braking["release_pos"], ref_braking["release_pos"]
            cur_dt = _interp(current, next_anchor) - _interp(current, anchor)
            ref_dt = _interp(ref, next_anchor) - _interp(ref, anchor)
            output.append({
                "number": i + 1, "label": "C" + str(i + 1),
                "name": "Detected corner " + str(i + 1),
                "start_pos": round(begin, 5), "end_pos": round(end, 5),
                "section_start_pos": round(anchor, 5), "section_end_pos": round(next_anchor, 5),
                "time_loss_s": round(cur_dt - ref_dt, 4),
                "delta_s": round(cur_dt - ref_dt, 4),
                "entry_speed_kmh": round(_interp(current, begin, "speed"), 1),
                "reference_entry_speed_kmh": round(_interp(ref, begin, "speed"), 1),
                "minimum_speed_kmh": round(cur_min["speed"], 1),
                "reference_minimum_speed_kmh": round(ref_min["speed"], 1),
                "exit_speed_kmh": round(_interp(current, end, "speed"), 1),
                "reference_exit_speed_kmh": round(_interp(ref, end, "speed"), 1),
                "braking_point_delta_m": round((cur_brake - ref_brake) * length, 1) if length and cur_brake is not None and ref_brake is not None else None,
                "full_throttle_delta_m": round((cur_gas - ref_gas) * length, 1) if length and cur_gas is not None and ref_gas is not None else None,
                "entry_gear": _gear(self._categorical_at(current, begin, "gear")),
                "reference_entry_gear": _gear(self._categorical_at(ref, begin, "gear")),
                "exit_gear": _gear(self._categorical_at(current, end, "gear")),
                "reference_exit_gear": _gear(self._categorical_at(ref, end, "gear")),
                "entry_rpm": _number(_interp(current, begin, "rpm")),
                "reference_entry_rpm": _number(_interp(ref, begin, "rpm")),
                "exit_rpm": _number(_interp(current, end, "rpm")),
                "reference_exit_rpm": _number(_interp(ref, end, "rpm")),
                "brake_release_delta_m": round((cur_release-ref_release)*length, 1) if length and cur_release is not None and ref_release is not None else None,
                "braking_deceleration_mps2": cur_braking["mean_deceleration_mps2"],
                "reference_braking_deceleration_mps2": ref_braking["mean_deceleration_mps2"],
                "braking_evidence_note": "Mean deceleration is derived from observed speed and time from brake onset to release or minimum speed; it does not measure tyre grip.",
                "reference_lap_number": reference["summary"]["lap_number"],
                "includes_exit_straight": True,
            })
        return output

    @staticmethod
    def _display_trace(trace):
        if not trace:
            return []
        stride = max(1, math.ceil(len(trace) / 280))
        selected = trace[::stride]
        if selected[-1] is not trace[-1]:
            selected = selected + [trace[-1]]
        return [{k: round(v, 5) if isinstance(v, float) else v for k, v in s.items()} for s in selected]

    @staticmethod
    def _comparison_record(session_id, lap, reference, corners):
        return {"session_id": session_id, "lap_id": lap["summary"]["id"],
            "reference_id": reference["summary"]["id"], "lap_number": lap["summary"]["lap_number"],
            "clean": True, "comparable": True, "corners": list(corners)}


class StoredSessionView(_HistoryAccess):
    """Independent, read-only and transaction-consistent saved-session view.

    Opening this view never constructs a live Engineer, migrates schemas,
    changes a reference, or shares the recorder's connection or lock. A long
    export keeps a WAL read snapshot; recording can continue concurrently.
    Always close the view, preferably with ``with engineer.open_history()``.
    """

    def __init__(self, database_path: pathlib.Path, active_session_id=None):
        self.database_path = pathlib.Path(database_path).resolve()
        self.data_dir = self.database_path.parent
        self._session_id = active_session_id
        self._lock = threading.RLock()
        self._closed = False
        self._db = sqlite3.connect(self.database_path.as_uri() + "?mode=ro", uri=True,
                                   check_same_thread=False)
        self._db.row_factory = sqlite3.Row
        try:
            self._db.execute("PRAGMA query_only=ON")
            self._db.execute("BEGIN")
            # BEGIN is deferred; a real read establishes the snapshot now,
            # before a recorder or lap edit may commit a different version.
            self._db.execute("SELECT id FROM sessions LIMIT 1").fetchone()
        except Exception:
            self._db.close()
            self._closed = True
            raise

    def __enter__(self):
        if self._closed:
            raise RuntimeError("This history view is closed.")
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        self.close()

    def close(self):
        with self._lock:
            if not self._closed:
                self._db.rollback()
                self._db.close()
                self._closed = True


class Engineer(_HistoryAccess):
    """Thread-safe session recorder, clean-lap comparator and radio events."""

    def __init__(self, data_dir: pathlib.Path):
        self.data_dir = pathlib.Path(data_dir)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.database_path = self.data_dir / "sessions.sqlite3"
        self._lock = threading.RLock()
        self._db = sqlite3.connect(str(self.database_path), check_same_thread=False)
        self._db.row_factory = sqlite3.Row
        self._db.execute("PRAGMA journal_mode=WAL")
        self._db.execute("PRAGMA synchronous=NORMAL")
        self._db.executescript("""
            CREATE TABLE IF NOT EXISTS sessions (
                id TEXT PRIMARY KEY, started REAL NOT NULL, source TEXT,
                car_id TEXT, track_id TEXT, layout TEXT, compound TEXT,
                metadata TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS frames (
                session_id TEXT NOT NULL, timestamp REAL, lap_count INTEGER,
                position REAL, elapsed_ms REAL, payload TEXT NOT NULL);
            CREATE INDEX IF NOT EXISTS frames_session ON frames(session_id);
            CREATE INDEX IF NOT EXISTS frames_session_lap ON frames(session_id,lap_count);
            CREATE TABLE IF NOT EXISTS laps (
                id TEXT PRIMARY KEY, session_id TEXT NOT NULL, lap_count INTEGER,
                duration_ms REAL, clean INTEGER NOT NULL, reason TEXT,
                summary TEXT NOT NULL, trace TEXT NOT NULL);
            CREATE INDEX IF NOT EXISTS laps_session ON laps(session_id);
        """)
        columns = {row[1] for row in self._db.execute("PRAGMA table_info(laps)")}
        if "deleted_at" not in columns:
            self._db.execute("ALTER TABLE laps ADD COLUMN deleted_at REAL")
        if "metadata" not in columns:
            self._db.execute("ALTER TABLE laps ADD COLUMN metadata TEXT NOT NULL DEFAULT '{}'")
        self._db.commit()
        self.settings = {
            "focus": "balanced", "fuel_reserve_l": 2.0,
            "target_pressure_psi": None, "voice": True,
        }
        self._message_counter = 0
        self._last_commit = time.monotonic()
        self._cooldowns = {}
        self._clear()

    def _clear(self):
        self._identity = None
        self._session_id = None
        self._session_started = None
        self._last = None
        self._latest = None
        self._samples = []
        self._collecting = False
        self._flags = set()
        self._start_fuel = None
        self._start_meta = None
        self._reference = None
        self._comparison_reference = None
        self._comparison_current = None
        self._laps = collections.deque(maxlen=30)
        self._messages = collections.deque(maxlen=16)
        self._fuel_samples = collections.deque(maxlen=8)
        self._corners = []
        self._comparison_history = collections.deque(maxlen=8)
        self._clean_laps = 0
        self._excluded_laps = 0
        self._last_record_t = None
        self._boundary_pending = None
        self._pending_rows = 0
        self._last_note = "Waiting for telemetry."

    def configure(self, settings: dict):
        with self._lock:
            focus = settings.get("focus")
            if focus in ("balanced", "stability", "rotation"):
                self.settings["focus"] = focus
            if "fuel_reserve_l" in settings:
                reserve = _number(settings["fuel_reserve_l"])
                if reserve is not None and 0 <= reserve <= 100:
                    self.settings["fuel_reserve_l"] = reserve
            if "target_pressure_psi" in settings:
                pressure = _number(settings["target_pressure_psi"])
                self.settings["target_pressure_psi"] = (
                    pressure if pressure is not None and 1 <= pressure <= 100 else None
                )
            if "voice" in settings:
                self.settings["voice"] = bool(settings["voice"])
            return self.snapshot()

    def reset(self):
        """Start fresh analysis; preserve the recorded database on disk."""
        with self._lock:
            self._db.commit()
            self._clear()
            self._cooldowns.clear()

    def close(self):
        with self._lock:
            self._db.commit()
            self._db.close()

    def open_history(self):
        """Capture a separate saved-data view without locking live analysis."""
        with self._lock:
            database_path, active_session_id = self.database_path, self._session_id
        return StoredSessionView(database_path, active_session_id)









    def _rebuild_session_analysis(self, session_id):
        """Re-evaluate references and fuel after a reversible lap edit."""
        rows = self._db.execute(
            "SELECT id FROM laps WHERE session_id=? AND deleted_at IS NULL ORDER BY lap_count,rowid",
            (session_id,),
        ).fetchall()
        laps = [self._stored_lap(row["id"], session_id) for row in rows]
        reference = comp_ref = comp_current = None
        corners = []
        history = collections.deque(maxlen=8)
        fuel = []
        for lap in laps:
            summary = lap["summary"]
            summary["delta_s"] = None
            if not summary["clean"] or not lap["trace"] or not summary["lap_time_s"]:
                continue
            used = _number(summary.get("fuel_used_l"))
            if used is not None and used > 0:
                fuel.append(used)
            if reference and self._comparable(lap["meta"], reference["meta"])[0]:
                summary["delta_s"] = round(summary["lap_time_s"] - reference["summary"]["lap_time_s"], 4)
                comp_ref, comp_current = reference, lap
                corners = self._compare_corners(lap, reference)
                history.append(self._comparison_record(session_id, lap, reference, corners))
                if summary["lap_time_s"] < reference["summary"]["lap_time_s"]:
                    reference = lap
            else:
                reference = comp_ref = comp_current = lap
                corners = self._compare_corners(lap, lap)
                history.clear()
        for lap in laps:
            self._db.execute("UPDATE laps SET summary=? WHERE id=?", (
                json.dumps(_safe(lap["summary"]), separators=(",", ":")), lap["summary"]["id"],
            ))
        if session_id == self._session_id:
            self._laps = collections.deque((lap["summary"] for lap in reversed(laps)), maxlen=30)
            self._reference, self._comparison_reference, self._comparison_current = reference, comp_ref, comp_current
            self._corners = corners
            self._comparison_history = history
            self._fuel_samples = collections.deque(fuel[-8:], maxlen=8)
            self._clean_laps = sum(bool(lap["summary"]["clean"]) for lap in laps)
            self._excluded_laps = len(laps) - self._clean_laps

    def _set_deleted(self, lap_id, deleted):
        with self._lock:
            lap = self._stored_lap(lap_id, include_deleted=True)
            timestamp = time.time() if deleted else None
            self._db.execute("UPDATE laps SET deleted_at=? WHERE id=?", (timestamp, lap_id))
            self._rebuild_session_analysis(lap["summary"]["session_id"])
            self._db.commit()
            if lap["summary"]["session_id"] == self._session_id:
                self._messages.clear()
                self._cooldowns.clear()
                self._last_note = "Lap removed from analysis." if deleted else "Lap restored to analysis."
                self._last_note += " Reference and fuel estimates recalculated."
                self._message("info", self._last_note, cooldown=0)
            return self._stored_lap(lap_id, include_deleted=True)["summary"]

    def delete_lap(self, lap_id):
        """Reversibly exclude a recorded lap; never erase original frames."""
        return self._set_deleted(lap_id, True)

    def restore_lap(self, lap_id):
        return self._set_deleted(lap_id, False)

    @staticmethod
    def _key(frame):
        return tuple(str(frame.get(k) or "") for k in (
            "source", "car_id", "track_id", "layout", "tyre_compound",
            "session_type", "session_index", "car_version", "setup_hash",
        ))

    @staticmethod
    def _meta(frame):
        return {k: frame.get(k) for k in (
            "source", "car_id", "track_id", "layout", "tyre_compound",
            "road_c", "ambient_c", "surface_grip", "track_length_m",
            "fuel_l", "car_version", "setup_hash",
        )} | {"tyre_core_c_mean": _mean(frame.get("tyre_core_c"))}

    def _new_session(self, frame):
        self._db.commit()
        self._clear()
        self._identity = self._key(frame)
        self._session_id = str(uuid.uuid4())
        self._session_started = _number(frame.get("timestamp"), time.time())
        metadata = self._meta(frame)
        self._db.execute(
            "INSERT INTO sessions VALUES (?,?,?,?,?,?,?,?)",
            (self._session_id, self._session_started, frame.get("source", "live"),
             frame.get("car_id"), frame.get("track_id"), frame.get("layout"),
             frame.get("tyre_compound"), json.dumps(_safe(metadata))),
        )
        self._last_note = "Finish this lap, then drive a complete clean lap to establish a reference."
        self._message("learning", self._last_note, cooldown=0)

    def _message(self, kind, text, severity="info", cooldown=45):
        now = time.time()
        token = kind + ":" + text
        if now - self._cooldowns.get(token, -1e12) < cooldown:
            return
        self._cooldowns[token] = now
        self._message_counter += 1
        self._messages.append({
            "id": "m" + str(self._message_counter), "text": text,
            "kind": kind, "severity": severity, "timestamp": now,
        })

    def _mark_frame(self, frame):
        required = ("speed_kmh", "gas", "brake", "steer", "normalized_pos", "lap_time_ms")
        if any(_number(frame.get(key)) is None for key in required):
            self._flags.add("Driver input or position unavailable")
        if frame.get("is_ai_controlled"):
            self._flags.add("AI-controlled lap")
        if frame.get("in_pit"):
            self._flags.add("Pit or out lap")
        if frame.get("lap_valid") is False:
            self._flags.add("Game reported an invalid lap")
        if (_number(frame.get("wheels_out"), 0) or 0) > 2:
            self._flags.add("Off track")

    @staticmethod
    def _sample(frame):
        x = _number(frame.get("normalized_pos"))
        t = _number(frame.get("lap_time_ms"))
        if x is None or t is None or not 0 <= x <= 1 or t < 0:
            return None
        g = frame.get("acc_g") or []
        return {
            "x": x, "t": t / 1000.0,
            "speed": _number(frame.get("speed_kmh"), 0),
            "gas": _number(frame.get("gas"), 0),
            "brake": _number(frame.get("brake"), 0),
            "steer": _number(frame.get("steer"), 0),
            "lat_g": _number(g[0], 0) if len(g) else 0,
            "gear": _gear(frame.get("gear")),
            "rpm": _number(frame.get("rpm")) if (_number(frame.get("rpm")) or 0) >= 0 else None,
        }

    def _append(self, frame, force=False):
        sample = self._sample(frame)
        if sample is None:
            self._flags.add("Track position or lap timer unavailable")
            return
        if self._samples:
            prev = self._samples[-1]
            if sample["t"] < prev["t"] - 0.2:
                self._flags.add("Lap timer moved backwards")
                return
            if sample["x"] < prev["x"] - 0.002:
                self._flags.add("Reverse movement or track-position discontinuity")
                return
            if sample["x"] <= prev["x"]:
                return
            if not force and sample["t"] - prev["t"] < 0.099:
                return
            if sample["t"] - prev["t"] > 2.0:
                self._flags.add("Telemetry gap over two seconds")
        self._samples.append(sample)
        if len(self._samples) > 60000:
            self._flags.add("Lap exceeds recording limit")
            self._samples = self._samples[::2]

    def _record_frame(self, frame):
        timestamp = _number(frame.get("timestamp"), time.time())
        lap_t = _number(frame.get("lap_time_ms"))
        lap_count = int(_number(frame.get("lap_count"), 0))
        marker = (lap_count, int((lap_t or 0) / 100))
        if marker == self._last_record_t:
            return
        self._last_record_t = marker
        fields = (
            "speed_kmh", "gas", "brake", "steer", "rpm", "gear", "fuel_l",
            "tyre_pressure_psi", "tyre_core_c", "tyre_wear_raw", "wheel_slip",
            "kers_charge", "ers_power_level", "ers_recovery_level", "drs",
            "in_pit", "wheels_out", "lap_valid", "abs", "tc", "brake_bias",
            "is_ai_controlled",
        )
        payload = {k: frame.get(k) for k in fields}
        self._db.execute(
            "INSERT INTO frames VALUES (?,?,?,?,?,?)",
            (self._session_id, timestamp, lap_count, frame.get("normalized_pos"),
             lap_t, json.dumps(_safe(payload), separators=(",", ":"))),
        )
        self._pending_rows += 1
        now = time.monotonic()
        if now - self._last_commit >= 2 or self._pending_rows >= 100:
            self._db.commit()
            self._last_commit = now
            self._pending_rows = 0

    def _begin_lap(self, frame, buffered):
        self._collecting = True
        self._samples = []
        self._flags = set()
        # Position and timer may wrap before the lap counter catches up. Only
        # low-timer points after that physical wrap belong to the new trace.
        fresh = [point for point in buffered
                 if (_number(point.get("normalized_pos"), 1) or 0) <= 0.08
                 and (_number(point.get("lap_time_ms"), 1e9) or 0) <= 1000]
        first = fresh[0] if fresh else frame
        self._start_fuel = _number(first.get("fuel_l"))
        self._start_meta = self._meta(first)
        for point in fresh or [frame]:
            self._mark_frame(point)
            self._append(point, force=not self._samples)

    def _boundary(self, frame, sample, previous, count):
        """Join AC's independently advancing finish-line signals.

        Real AC recordings show position/timer wrap about 60 ms before the
        graphics lap counter. Require all three signals in a bounded window,
        rather than poisoning every later lap when one packet arrives first.
        A first out-lap may wrap its timer without incrementing count at all;
        it can arm a fresh trace but can never become a completed reference.
        """
        pending = self._boundary_pending
        prior_count = int(_number((self._last or {}).get("lap_count"), count))
        wrapped = previous["x"] >= 0.97 and sample["x"] <= 0.03
        increased = count == prior_count + 1
        if pending is None:
            if count > prior_count + 1:
                self._flags.add("Missing complete lap telemetry")
                self._collecting = False
                return False
            if not wrapped and not increased:
                return False
            near_line = sample["x"] <= 0.03 or (previous["x"] >= 0.97 and sample["x"] >= 0.97)
            if not near_line:
                self._flags.add("Lap counter discontinuity")
                self._collecting = False
                return False
            pending = {
                "count": prior_count, "started": _number(frame.get("timestamp"), time.time()),
                "previous": self._last, "was_collecting": self._collecting,
                "wrapped": wrapped, "counter": increased, "timer": False, "frames": [],
            }
            self._boundary_pending = pending
        else:
            pending["wrapped"] = pending["wrapped"] or wrapped
            pending["counter"] = pending["counter"] or count == pending["count"] + 1
        elapsed = max(0.0, _number(frame.get("timestamp"), time.time()) - pending["started"])
        old_timer = _number((pending["previous"] or {}).get("lap_time_ms"), 0)
        pending["timer"] = pending["timer"] or (old_timer > 1000 and sample["t"] <= 0.75)
        pending["frames"].append(frame)
        confirmed = (pending["wrapped"] and pending["counter"] and pending["timer"]
                     and count == pending["count"] + 1 and sample["x"] <= 0.03 and elapsed <= 0.5)
        first_out_lap = (not pending["was_collecting"] and pending["wrapped"] and pending["timer"]
                         and not pending["counter"] and count == pending["count"] and elapsed >= 0.5
                         and sample["x"] <= 0.08)
        if confirmed or first_out_lap:
            if confirmed and pending["was_collecting"]:
                self._finish_lap(frame, completed_count=pending["count"])
            else:
                self._last_note = "Out lap complete. Recording a full lap for your reference."
            self._begin_lap(frame, pending["frames"])
            self._boundary_pending = None
        elif elapsed > 0.5 or len(pending["frames"]) > 40 or count not in (pending["count"], pending["count"] + 1):
            self._flags.add("Finish-line signals did not synchronize")
            self._collecting = False
            self._samples.clear()
            self._boundary_pending = None
            self._last_note = "Waiting for a synchronized finish-line crossing before recording a complete lap."
        return True

    def update(self, frame: dict):
        with self._lock:
            frame = _safe(dict(frame or {}))
            status = frame.get("status", "disconnected")
            self._latest = frame
            if status != "live":
                if status not in ("paused",):
                    self._collecting = False
                    self._samples.clear()
                    self._last = None
                    self._boundary_pending = None
                    self._last_note = "Recording paused. Complete a fresh lap before comparison."
                return self.snapshot()
            identity = self._key(frame)
            count = int(_number(frame.get("lap_count"), 0))
            prior_count = int(_number((self._last or {}).get("lap_count"), count))
            if identity != self._identity or count < prior_count:
                self._new_session(frame)
                self._latest = frame
            self._record_frame(frame)
            sample = self._sample(frame)
            last = self._sample(self._last) if self._last else None
            if last and sample:
                prior_count = int(_number(self._last.get("lap_count"), count))
                if self._boundary(frame, sample, last, count):
                    self._last = frame
                    self._fuel_alert(frame)
                    return self.snapshot()
                if sample["x"] - last["x"] > 0.025:
                    length = _number(frame.get("track_length_m"))
                    dt = max(0.0, sample["t"] - last["t"])
                    if length and length > 50:
                        allowed = (max(sample["speed"], last["speed"]) + 120) / 3.6 * dt / length + 0.025
                        if sample["x"] - last["x"] > allowed:
                            self._flags.add("Teleport or large position jump")
                if sample["x"] < last["x"] - 0.02:
                    self._flags.add("Reverse movement or teleport")
                before_fuel = _number(self._last.get("fuel_l"))
                after_fuel = _number(frame.get("fuel_l"))
                if before_fuel is not None and after_fuel is not None and after_fuel > before_fuel + 0.15:
                    self._flags.add("Refuelling detected")
            self._mark_frame(frame)
            if self._collecting:
                self._append(frame)
            self._last = frame
            self._fuel_alert(frame)
            return self.snapshot()


    def _finish_lap(self, frame, completed_count=None):
        trace = list(self._samples)
        count = completed_count if completed_count is not None else int(_number((self._last or {}).get("lap_count"), 0))
        duration_ms = _number(frame.get("last_lap_ms"))
        if not duration_ms or duration_ms < 1000:
            duration_ms = None
            self._flags.add("Completed lap time unavailable")
        if len(trace) < 20 or not trace or trace[0]["x"] > 0.04 or trace[-1]["x"] < 0.96:
            self._flags.add("Incomplete lap trace")
        if trace and duration_ms is not None:
            if duration_ms / 1000 < trace[-1]["t"] - 0.5:
                self._flags.add("Completed lap time inconsistent with trace")
            start, end = dict(trace[0]), dict(trace[-1])
            start.update(x=0.0, t=0.0)
            end.update(x=1.0, t=duration_ms / 1000)
            trace = [start] + [s for s in trace if 0 < s["x"] < 1] + [end]
        clean = not self._flags and duration_ms is not None
        meta = self._start_meta or self._meta(frame)
        fuel_end = _number(frame.get("fuel_l"))
        used = None
        if self._start_fuel is not None and fuel_end is not None:
            difference = self._start_fuel - fuel_end
            if clean and difference > 0.001 and difference <= self._start_fuel:
                used = difference
                self._fuel_samples.append(used)
        summary = {
            "id": str(uuid.uuid4()), "lap_number": count + 1,
            "lap_count": count, "lap_time_s": duration_ms / 1000 if duration_ms else None,
            "lap_time_ms": duration_ms, "clean": clean, "valid": clean,
            "reason": "; ".join(sorted(self._flags)) if self._flags else "Observed clean",
            "fuel_used_l": round(used, 4) if used is not None else None,
            "source": frame.get("source", "live"), "timestamp": time.time(),
            "game_validity_available": frame.get("lap_valid") is not None,
            "delta_s": None,
        }
        lap = {"summary": summary, "trace": trace, "meta": meta}
        if clean:
            self._clean_laps += 1
            reference = self._reference
            comparable, condition = self._comparable(meta, reference["meta"]) if reference else (False, None)
            if reference and comparable:
                summary["delta_s"] = round(summary["lap_time_s"] - reference["summary"]["lap_time_s"], 4)
                self._comparison_reference = reference
                self._comparison_current = lap
                self._corners = self._compare_corners(lap, reference)
                self._comparison_history.append(self._comparison_record(self._session_id, lap, reference, self._corners))
                self._lap_feedback(lap, reference)
                if summary["lap_time_s"] < reference["summary"]["lap_time_s"]:
                    self._reference = lap
            else:
                self._reference = lap
                self._comparison_reference = lap
                self._comparison_current = lap
                self._corners = self._compare_corners(lap, lap)
                self._comparison_history.clear()
                explanation = "Conditions changed; a new clean baseline is set." if reference else "Clean reference saved. Your next clean lap will show where time changed."
                self._last_note = explanation
                self._message("reference", explanation, cooldown=0)
        else:
            self._excluded_laps += 1
            self._last_note = "Lap excluded: " + summary["reason"] + "."
            self._message("lap", self._last_note, severity="warning", cooldown=0)
        self._laps.appendleft(summary)
        self._db.execute(
            "INSERT INTO laps (id,session_id,lap_count,duration_ms,clean,reason,summary,trace,metadata) VALUES (?,?,?,?,?,?,?,?,?)",
            (summary["id"], self._session_id, count, duration_ms, int(clean),
             summary["reason"], json.dumps(_safe(summary)),
             json.dumps(_safe(trace), separators=(",", ":")), json.dumps(_safe(meta))),
        )
        self._db.commit()
        self._last_commit = time.monotonic()
        self._pending_rows = 0




    def _lap_feedback(self, lap, reference):
        summary = lap["summary"]
        delta = summary["delta_s"]
        losses = sorted((c for c in self._corners if c["time_loss_s"] >= 0.08), key=lambda c: c["time_loss_s"], reverse=True)
        if losses:
            corner = losses[0]
            text = "Measured losses against your clean reference: " + "; ".join(
                "detected corner {0} and exit {1:.2f} seconds".format(c["number"], c["time_loss_s"])
                for c in losses[:3]) + "."
            gas_delta, brake_delta = corner["full_throttle_delta_m"], corner["braking_point_delta_m"]
            if gas_delta is not None and gas_delta > 3:
                text += " Full throttle came {0:.0f} metres later; try a more consistent exit next lap.".format(gas_delta)
            elif brake_delta is not None and abs(brake_delta) > 3:
                text += " Braking began {0:.0f} metres {1}; compare the exit before changing your brake point.".format(abs(brake_delta), "later" if brake_delta > 0 else "earlier")
            else:
                entry_diff = corner["entry_speed_kmh"] - corner["reference_entry_speed_kmh"]
                if abs(entry_diff) > 2:
                    text += " Entry speed was {0:.0f} kilometres per hour {1}.".format(abs(entry_diff), "higher" if entry_diff > 0 else "lower")
        elif delta is not None and delta < -0.03:
            text = "Clean lap: {0:.2f} seconds faster than your reference.".format(-delta)
        elif delta is not None:
            text = "Clean lap: {0:.2f} seconds {1} your reference. No detected corner lost more than eight hundredths.".format(abs(delta), "behind" if delta >= 0 else "ahead of")
        else:
            text = "Clean lap recorded."
        self._last_note = text
        self._message("coaching", text, cooldown=0)

    def _fuel(self, frame):
        consumption = statistics.median(self._fuel_samples) if self._fuel_samples else None
        fuel = _number(frame.get("fuel_l"))
        reserve = self.settings["fuel_reserve_l"]
        typical = statistics.median([l["lap_time_s"] for l in self._laps if l["clean"] and l["lap_time_s"]]) if any(l["clean"] and l["lap_time_s"] for l in self._laps) else None
        total_laps = _number(frame.get("session_total_laps"))
        session_type = frame.get("session_type")
        race = session_type in (2, "race", "Race", "RACE")
        to_finish = None
        estimated = False
        progress = _number(frame.get("normalized_pos"), 0) or 0
        if race and total_laps and total_laps > 0:
            to_finish = max(0.0, total_laps - (_number(frame.get("lap_count"), 0) or 0) - progress)
        elif race and typical:
            remaining_ms = _number(frame.get("session_time_left_ms"))
            if remaining_ms is not None and remaining_ms >= 0:
                # Conservative timed-race allowance. AC server extra-lap rules
                # and the leader's progress can change the actual finish.
                to_finish = max(0.0, math.ceil(remaining_ms / (typical * 1000)) + 1 - progress)
                estimated = True
        need = to_finish * consumption + reserve if to_finish is not None and consumption else None
        return {
            "fuel_l": fuel,
            "consumption_l_per_lap": round(consumption, 4) if consumption else None,
            "laps_remaining": round(max(0.0, fuel - reserve) / consumption, 2) if fuel is not None and consumption else None,
            "laps_in_tank": round(fuel / consumption, 2) if fuel is not None and consumption else None,
            "reserve_l": reserve, "samples": len(self._fuel_samples),
            "laps_to_finish": round(to_finish, 2) if to_finish is not None else None,
            "fuel_to_finish_l": round(need, 2) if need is not None else None,
            "shortfall_l": round(max(0.0, need - fuel), 2) if need is not None and fuel is not None else None,
            "finish_estimated": estimated,
            "note": "Timed-race estimate includes an extra lap; server rules and leader position may differ." if estimated else "Consumption measured from complete clean laps; remaining laps preserve your reserve.",
        }

    def _fuel_alert(self, frame):
        fuel = self._fuel(frame)
        if fuel["laps_remaining"] is not None and fuel["laps_remaining"] < 1.5:
            self._message("fuel", "Fuel warning. Fewer than one and a half laps remain above your reserve.", "warning", cooldown=60)
        if fuel["shortfall_l"] and fuel["shortfall_l"] > 0.5:
            self._message("fuel", "Current fuel is below the estimated race requirement. Check the fuel panel.", "warning", cooldown=90)

    def _setup(self, frame):
        result = []
        pressure_target = self.settings["target_pressure_psi"]
        pressures = [_number(v) for v in (frame.get("tyre_pressure_psi") or [])]
        pressures = [p for p in pressures if p is not None and p > 0]
        if pressure_target is not None and pressures:
            mean = statistics.mean(pressures)
            result.append({"title": "Your pressure target", "kind": "measured",
                "text": "Mean pressure is {0:.1f} psi; your selected target is {1:.1f} psi. Compare once tyres have stabilized before adjusting cold pressure.".format(mean, pressure_target)})
        else:
            result.append({"title": "Tyre target required", "kind": "needs_profile",
                "text": "No universal tyre pressure or temperature target is assumed. Enter a verified hot-pressure target for this car and compound."})
        focus = self.settings["focus"]
        text = {
            "balanced": "Keep a baseline setup and change one setting at a time after several repeatable clean laps.",
            "stability": "Your preference is predictable braking and traction. Log which corner phase feels unstable; compare a controlled setup change against the baseline.",
            "rotation": "Your preference is easier rotation. Log whether the restriction occurs on entry, mid-corner or exit; do not change the differential or aero without car-specific setup data.",
        }[focus]
        result.append({"title": "Driver preference: " + focus, "kind": "preference", "text": text})
        result.append({"title": "Setup context", "kind": "limitation",
            "text": "Exact setup values and automatic setup changes are not available in this build. Steering corrections alone are not diagnosed as understeer or oversteer."})
        return result


    def snapshot(self):
        with self._lock:
            frame = self._latest or {}
            reference = self._reference
            current = self._sample(frame)
            current_delta = None
            if reference and current and self._collecting and not self._flags and frame.get("status") == "live":
                comparable, _ = self._comparable(self._start_meta or self._meta(frame), reference["meta"])
                if comparable:
                    current_delta = round(current["t"] - _interp(reference["trace"], current["x"]), 4)
            comp_ref = self._comparison_reference
            comp_current = self._comparison_current
            reference_info = None
            if reference:
                reference_info = dict(reference["summary"])
                reference_info.update({"label": "Own clean session best", "scope": "session", "metadata": reference["meta"],
                    "note": "Same car, track layout and compound in this recording session. Setup and mod version are not independently verified."})
            source = frame.get("source", "live")
            result = {
                "session": {
                    "id": self._session_id, "started": self._session_started,
                    "source": source, "car_id": frame.get("car_id"),
                    "track_id": frame.get("track_id"), "layout": frame.get("layout"),
                    "status": frame.get("status", "disconnected"),
                    "recording": frame.get("status") == "live",
                    "lap_number": int(_number(frame.get("lap_count"), 0)) + 1,
                    "collecting_complete_lap": self._collecting,
                    "clean_laps": self._clean_laps, "excluded_laps": self._excluded_laps,
                    "current_lap_eligible": self._collecting and not self._flags,
                    "current_lap_flags": sorted(self._flags),
                    "current_delta_s": current_delta, "recording_hz": 10,
                    "note": self._last_note,
                },
                "laps": list(self._laps), "reference": reference_info,
                "corners": list(self._corners), "messages": list(self._messages),
                "comparison_history": list(self._comparison_history),
                "fuel": self._fuel(frame),
                "coaching": {
                    "current_delta_s": current_delta, "delta_s": current_delta,
                    "status": "comparing" if reference else "learning",
                    "summary": self._last_note,
                    "priorities": sorted([c for c in self._corners if c["time_loss_s"] > 0.08], key=lambda c: c["time_loss_s"], reverse=True)[:3],
                    "reference_lap_time_s": comp_ref["summary"]["lap_time_s"] if comp_ref else None,
                    "reference_lap_number": comp_ref["summary"]["lap_number"] if comp_ref else None,
                    "comparison_lap_number": comp_current["summary"]["lap_number"] if comp_current else None,
                    "comparison_lap_id": comp_current["summary"]["id"] if comp_current else None,
                    "comparison_reference_id": comp_ref["summary"]["id"] if comp_ref else None,
                    "comparison_available": bool(comp_ref and comp_current and comp_ref["summary"]["id"] != comp_current["summary"]["id"]),
                    "live_comparison_available": current_delta is not None,
                    "corner_labels": "Detected corners, not official circuit turn numbers",
                },
                "setup": self._setup(frame),
                "traces": {
                    "current": self._display_trace(comp_current["trace"] if comp_current else self._samples),
                    "reference": self._display_trace(comp_ref["trace"] if comp_ref else []),
                    "live": self._display_trace(self._samples),
                    "active_reference": self._display_trace(reference["trace"] if reference else []),
                    "axis": "normalized_track_position",
                },
                "recording": {"database": str(self.database_path), "format": "SQLite", "demo_separate": True},
            }
            return _safe(result)
