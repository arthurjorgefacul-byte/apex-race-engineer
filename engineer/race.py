"""Session race answers and acknowledged Quick Pit plans, never guessed data."""
import collections
import copy
import math
import re
import statistics
import time
import uuid


RACE_TOPICS = {"personal_best", "race_best", "others_best", "gap_ahead", "gap_behind", "gap_leader", "change_compound"}


def number(value, low=None, high=None):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        return None
    return value if (low is None or value >= low) and (high is None or value <= high) else None


def lap(value):
    return "%d:%06.3f" % (value // 60000, value % 60000 / 1000) if number(value, 1, 86400000) is not None else "unavailable"


def identity(frame):
    return tuple((frame or {}).get(k, "") for k in ("source", "car_id", "track_id", "layout"))


def race_session(frame):
    return (frame or {}).get("session_type") in (2, "race", "Race", "RACE")


def usable_session(frame):
    return isinstance(frame, dict) and frame.get("status") in ("live", "paused") and not frame.get("is_replay")


def label(value):
    return re.sub(r"[^a-z0-9]+", " ", str(value or "").lower()).strip()


class RaceDirector:
    def __init__(self, clock=time.time):
        self.clock = clock
        self.context = None
        self.received_at = None
        self.csp_received_at = None
        self.csp_sequence = None
        self.csp = {}
        self.rows = []
        self.previous = {}
        self.laps = collections.deque(maxlen=2048)
        self.own_seen = set()
        self.pit_plan = None
        self.pending_command = None
        self.notifications = collections.deque(maxlen=8)

    def reset(self, frame=None):
        self.context = identity(frame) if frame else None
        self.received_at = self.csp_received_at = self.csp_sequence = None
        self.csp = {}; self.rows = []; self.previous = {}; self.laps.clear(); self.own_seen.clear()
        self.pit_plan = self.pending_command = None; self.notifications.clear()

    @staticmethod
    def _rows(raw):
        if not isinstance(raw, list) or len(raw) > 64:
            raise ValueError("Invalid race timing grid.")
        result, seen = [], set()
        for item in raw:
            if not isinstance(item, dict):
                continue
            ident = number(item.get("id"), 0, 1000)
            if ident is None or ident != int(ident) or ident in seen:
                if ident in seen:
                    raise ValueError("Duplicate race timing identifier.")
                continue
            seen.add(ident)
            row = {"id": int(ident)}
            for key in ("name", "car", "tyre_compound"):
                if isinstance(item.get(key), str): row[key] = item[key][:100]
            for key, low, high in (("lap_count", 0, 100000), ("lap_time_ms", 0, 86400000),
                ("last_lap_ms", 0, 86400000), ("best_lap_ms", 0, 86400000), ("normalized_pos", 0, 1),
                ("speed_kmh", 0, 1500), ("race_position", 1, 1000), ("compound_index", 0, 31),
                ("gap_to_player_s", -86400, 86400)):
                value = number(item.get(key), low, high)
                if value is not None: row[key] = value
            for key in ("last_lap_valid", "in_pit", "is_ai_controlled", "best_lap_from_ghost"):
                if isinstance(item.get(key), bool): row[key] = item[key]
            if row.get("best_lap_from_ghost"):
                row.pop("best_lap_ms", None)
            result.append(row)
        return result

    def ingest(self, payload, frame):
        if not isinstance(payload, dict) or not isinstance(frame, dict):
            raise ValueError("Invalid race bridge payload.")
        if identity(frame) != self.context: self.reset(frame)
        if frame.get("source") == "demo" or not usable_session(frame):
            self.reset(frame)
            return
        if any((payload.get(k) or "") != (frame.get(k) or "") for k in ("car_id", "track_id", "layout")):
            raise ValueError("Race bridge does not match the current car and track.")
        now = self.clock()
        csp = payload.get("csp")
        if isinstance(csp, dict) and all((csp.get(k) or "") == (frame.get(k) or "") for k in ("car_id", "track_id", "layout")):
            sequence = (str(csp.get("session_nonce", ""))[:150], number(csp.get("sequence"), 0))
            if (sequence[1] is not None and sequence[1] == int(sequence[1]) and
                (self.csp_sequence is None or sequence[0] != self.csp_sequence[0] or sequence[1] > self.csp_sequence[1])):
                if self.csp_sequence and sequence[0] != self.csp_sequence[0]: self.reset(frame)
                self.csp_sequence = sequence; self.csp_received_at = now
                self.csp = copy.deepcopy(csp)
        use_csp = self.csp_received_at is not None and 0 <= now - self.csp_received_at < 3
        rows = self._rows(self.csp.get("cars", []) if use_csp else payload.get("cars", []))
        for row in rows:
            old = self.previous.get(row["id"])
            count, old_count = row.get("lap_count"), (old or {}).get("lap_count")
            # A current compound does not establish which compound produced a
            # reported session best. Only observed complete, valid laps count.
            if (old and count is not None and old_count is not None and count == old_count + 1
                and row.get("last_lap_valid") is True and row.get("in_pit") is False
                and not old.get("_pace_pit_lap") and not old.get("_pace_compound_changed")
                and row.get("tyre_compound") and old.get("tyre_compound") == row["tyre_compound"]
                and row.get("car") == old.get("car") and number(row.get("last_lap_ms"), 1)):
                self._record_lap({"driver_id": row["id"], "driver": row.get("name", "Driver"),
                    "car_id": row.get("car"), "compound": row["tyre_compound"],
                    "lap_time_ms": row["last_lap_ms"], "lap_count": count, "source": "CSP observed valid lap"})
            if old and count is not None and old_count is not None and count < old_count:
                # A restart can keep the same track/car and CSP app nonce.
                self.laps.clear(); self.own_seen.clear(); self.pit_plan = self.pending_command = None
            observed = dict(row)
            same_lap = old is not None and count is not None and count == old_count
            observed["_pace_pit_lap"] = row.get("in_pit") is True or (same_lap and old.get("_pace_pit_lap", False))
            observed["_pace_compound_changed"] = bool(same_lap and (old.get("_pace_compound_changed") or old.get("tyre_compound") != row.get("tyre_compound")))
            self.previous[row["id"]] = observed
        self.previous = {row["id"]: self.previous[row["id"]] for row in rows}
        self.rows = rows; self.received_at = now
        self._acknowledge()

    def player_enrichment(self, frame):
        if not self._fresh(frame) or not self._fresh_csp(): return {}
        result = {}
        session_index = number(self.csp.get("session_index"), 0, 1000)
        nonce = self.csp.get("session_nonce")
        if session_index is not None and session_index == int(session_index): result["session_index"] = int(session_index)
        if isinstance(nonce, str) and 0 < len(nonce) <= 150: result["session_nonce"] = nonce
        player = self.csp.get("player") or {}
        if not isinstance(player, dict): return result
        if (player.get("lap_count") != frame.get("lap_count") or number(player.get("normalized_pos"), 0, 1) is None
            or number(frame.get("normalized_pos"), 0, 1) is None
            or abs(player["normalized_pos"]-frame["normalized_pos"]) > .015): return result
        for key in ("lap_valid", "is_ai_controlled", "in_pit"):
            if isinstance(player.get(key), bool): result[key] = player[key]
        return result

    def observe_analysis(self, analysis, frame):
        if not usable_session(frame): return
        for row in analysis.get("laps", []):
            ident = row.get("id")
            if not ident or ident in self.own_seen: continue
            self.own_seen.add(ident)
            if row.get("clean") and number(row.get("lap_time_ms"), 1) and frame.get("tyre_compound"):
                # Use actual stored lap metadata, never relabel an old compound.
                compound = (row.get("metadata") or {}).get("tyre_compound", row.get("tyre_compound"))
                if compound is None: continue
                count = number(row.get("lap_count"), 0)
                self._record_lap({"driver_id": 0, "driver": "You", "car_id": frame.get("car_id"),
                    "compound": compound, "lap_time_ms": row["lap_time_ms"],
                    "lap_count": count + 1 if count is not None else None, "source": "APEX observed clean lap"})

    def _record_lap(self, record):
        # The local analysis and CSP bridge can observe the same player lap.
        # Count it once, retaining the compound measured for that lap.
        if record.get("lap_count") is not None and any(
            old.get("driver_id") == record["driver_id"] and old.get("lap_count") == record["lap_count"]
            for old in self.laps): return
        self.laps.append(record)

    def _fresh(self, frame):
        return usable_session(frame) and identity(frame) == self.context and self.received_at is not None and 0 <= self.clock()-self.received_at < 3

    def _fresh_csp(self):
        return self.csp_received_at is not None and 0 <= self.clock()-self.csp_received_at < 3

    def _acknowledge(self):
        if not self.pending_command: return
        ack = self.csp.get("ack") or {}
        if not isinstance(ack, dict): ack = {}
        command = self.pending_command
        if self._fresh_csp() and ack.get("id") == command["id"]:
            ok = ack.get("ok") is True and ack.get("actual_value") == command["pit_value"]
            self.pit_plan.update(status="applied" if ok else "failed", acknowledgement=copy.deepcopy(ack))
            self.notifications.append(("%s set for the next pit stop." % command["compound"]) if ok else
                                      "Tyre selection was not confirmed. Check the pit menu. " + str(ack.get("message", ""))[:100])
            self.pending_command = None
        elif self.clock() > command["expires_at"]:
            self.pit_plan["status"] = "unconfirmed"; self.pending_command = None
            self.notifications.append("No confirmation from the pit menu. Check the tyre selection.")

    def command(self, frame):
        if not self._fresh(frame) or not self._fresh_csp(): return None
        self._acknowledge()
        return copy.deepcopy(self.pending_command)

    def snapshot(self, frame):
        self._acknowledge()
        fresh = self._fresh(frame)
        rows = copy.deepcopy(self.rows) if fresh else []
        if not race_session(frame) or not self._fresh_csp() or self.csp.get("session_type") != 3:
            for row in rows: row.pop("gap_to_player_s", None)
        rows.sort(key=lambda row: (row.get("race_position", 9999), -(row.get("lap_count", 0)+row.get("normalized_pos", 0))))
        paces = []
        groups = collections.defaultdict(list)
        for record in self.laps if identity(frame) == self.context else []:
            groups[(record.get("car_id"), record["compound"])].append(record)
        for (car, compound), records in groups.items():
            records = records[-12:]; values = [r["lap_time_ms"] for r in records]
            paces.append({"car_id": car, "compound": compound, "pace_ms": round(statistics.median(values)),
                "best_lap_ms": min(values), "samples": len(values), "drivers": sorted(set(r["driver"] for r in records)),
                "same_car": car == frame.get("car_id"), "note": "Observed valid laps; traffic, fuel, driver and tyre age are not matched."})
        compounds = []
        if fresh and self._fresh_csp():
            raw_compounds = self.csp.get("compounds", [])
            for c in (raw_compounds if isinstance(raw_compounds, list) else [])[:32]:
                if not isinstance(c, dict) or number(c.get("index"), 0, 31) is None: continue
                if not isinstance(c.get("name"), str) or len(c["name"]) > 150: continue
                compounds.append({k: c[k] for k in ("index", "name", "short_name", "pit_value") if k in c})
        return {"available": fresh, "cars": rows, "drivers": rows, "player_id": 0,
            "source": "CSP" if fresh and self._fresh_csp() else "AC Python timing", "compound_pace": paces,
            "compounds": compounds, "pit_plan": copy.deepcopy(self.pit_plan) if identity(frame) == self.context else None,
            "pitstop": copy.deepcopy(self.csp.get("pitstop") or {}) if fresh and self._fresh_csp() else {},
            "note": "Positive gap: car ahead of you; negative: behind. CSP gaps are estimates from distance and speed, not official timing."}

    def ask(self, topic, frame, analysis, compound=None, driver_id=None, driver_name=None, recorded_best=None):
        state = self.snapshot(frame)
        rows = state["cars"]
        answer = {"topic": topic, "available": True, "kind": "answer", "severity": "info", "source": "APEX race"}
        if topic == "personal_best":
            candidates = [r.get("lap_time_ms") for r in analysis.get("laps", []) if r.get("clean")]
            candidates += [frame.get("best_lap_ms")] if usable_session(frame) else []
            candidates += [r.get("best_lap_ms") for r in rows if r["id"] == 0]
            values = [v for v in candidates if number(v, 1)]
            value = min(values) if values else None
            text = "Your session best is " + lap(value) + "." if value else "No valid session best is available yet."
            if recorded_best and number(recorded_best.get("lap_time_ms"), 1):
                text += " Your recorded best for this car and layout is " + lap(recorded_best["lap_time_ms"]) + ", across recorded conditions."
            answer.update(text=text, available=bool(value or recorded_best), lap_time_ms=value, recorded_best=recorded_best)
        elif topic in ("race_best", "others_best"):
            others = [r for r in rows if topic == "race_best" or r["id"] != 0]
            if driver_id is not None: others = [r for r in others if r["id"] == driver_id]
            if driver_name: others = [r for r in others if label(driver_name) in label(r.get("name"))]
            ordered = sorted([r for r in others if number(r.get("best_lap_ms"), 1)], key=lambda r: r["best_lap_ms"])
            if ordered:
                lead = ordered[0]
                if topic == "race_best":
                    text = ("Race" if race_session(frame) else "Session") + " fastest: " + lead.get("name", "Driver") + ", " + lap(lead["best_lap_ms"]) + "."
                else:
                    text = "Best laps: " + "; ".join(r.get("name", "Driver") + " " + lap(r["best_lap_ms"]) for r in ordered[:3]) + "."
                    if len(ordered) > 3: text += " Full list is in race timing."
                answer.update(text=text, drivers=ordered, lap_time_ms=lead["best_lap_ms"])
            else: answer.update(text="No current best-lap timing is available for those drivers.", available=False, drivers=[])
        elif topic in ("gap_ahead", "gap_behind", "gap_leader"):
            if not race_session(frame):
                answer.update(text="Race gaps are available during a race. Ask for the session fastest lap during practice or qualifying.", available=False)
            else:
                player = next((r for r in rows if r["id"] == 0), None)
                position = (player or {}).get("race_position", frame.get("race_position"))
                if not rows or not player or not number(position, 1):
                    answer.update(text="No fresh race-position timing. Enable the APEX session bridge.", available=False)
                else:
                    rank = position-1 if topic == "gap_ahead" else position+1 if topic == "gap_behind" else 1
                    target = next((r for r in rows if r.get("race_position") == rank and r["id"] != 0), None)
                    if topic == "gap_leader" and position == 1 or topic == "gap_ahead" and position == 1:
                        answer["text"] = "You are leading."
                    elif target is None:
                        answer.update(text="No car behind in the current timing." if topic == "gap_behind" else "No current timing for that car.", available=False)
                    elif number(target.get("gap_to_player_s")) is None:
                        answer.update(text=target.get("name", "That car") + " is in that position, but the time gap is unavailable.", available=False)
                    else:
                        gap = target["gap_to_player_s"]
                        expected = -1 if topic == "gap_behind" else 1
                        if gap*expected < -.05:
                            answer.update(text="Race positions and the estimated gap disagree. Waiting for coherent timing.", available=False)
                        else:
                            deficit = (target.get("lap_count", 0)+target.get("normalized_pos", 0))-(player.get("lap_count", 0)+player.get("normalized_pos", 0))
                            laps_apart = int(abs(deficit))
                            text = target.get("name", "The car") + ", about %.1f seconds %s." % (abs(gap), "behind" if expected < 0 else "ahead")
                            if laps_apart: text += " %d lap%s apart." % (laps_apart, "s" if laps_apart != 1 else "")
                            answer.update(text=text, gap_s=abs(gap), estimated=True, driver=target, laps_apart=laps_apart)
        elif topic == "change_compound":
            answer.update(compound_pace=state["compound_pace"], choices=[{"label":c["name"], "topic":"change_compound", "compound":c["name"]} for c in state["compounds"]])
            current = frame.get("tyre_compound")
            if not state["compounds"]:
                answer.update(text="I cannot read the available compounds. Enable the APEX CSP bridge; no tyre setting was changed.", available=False)
            elif not compound:
                paces = sorted([p for p in state["compound_pace"] if p["same_car"] and p["samples"] >= 3], key=lambda p:p["pace_ms"])
                text = "Which compound? Available: " + ", ".join(c["name"] for c in state["compounds"]) + "."
                if paces:
                    text += " Matching-car observed pace: " + "; ".join(p["compound"] + " " + lap(p["pace_ms"]) for p in paces) + ". Traffic and fuel are not matched."
                    text += " " + paces[0]["compound"] + " has the quickest observed matching-car pace; fuel and traffic may differ. Confirm a compound."
                    answer["recommendation"] = paces[0]
                else: text += " No reliable matching-car pace by compound yet."
                answer.update(text=text, requires_input=True)
            else:
                wanted = label(compound)
                matches = [c for c in state["compounds"] if wanted in (label(c["name"]), label(c.get("short_name"))) or wanted in label(c["name"]).split()]
                if len(matches) != 1:
                    answer.update(text="Specify one available compound: " + ", ".join(c["name"] for c in state["compounds"]) + ".", requires_input=True, available=False)
                else:
                    chosen = matches[0]; pit = self.csp.get("pitstop") or {}
                    if not isinstance(pit, dict): pit = {}
                    value = number(chosen.get("pit_value"), 0, 32)
                    lower, upper = number(pit.get("min")), number(pit.get("max"))
                    if (not pit.get("mapping_verified") or not pit.get("available") or pit.get("read_only") is True
                        or value is None or value != int(value) or lower is None or upper is None or not lower <= value <= upper):
                        answer.update(text=chosen["name"] + " requested. Pit-menu control is unavailable or unverified; select it in AC's pit menu. No setting was changed.", available=False)
                    else:
                        command = {"id": uuid.uuid4().hex, "index": chosen["index"], "compound": chosen["name"],
                            "pit_value": chosen["pit_value"], "car_id":frame.get("car_id"), "track_id":frame.get("track_id"), "layout":frame.get("layout") or "",
                            "session_nonce": self.csp.get("session_nonce"), "expires_at":self.clock()+10}
                        self.pending_command = command
                        self.pit_plan = dict(command, status="queued")
                        answer.update(text="Preparing " + chosen["name"] + " for the next stop. Waiting for the pit-menu confirmation.", pit_plan=copy.deepcopy(self.pit_plan))
        else: answer.update(text="That race command is unavailable.", available=False)
        return answer
