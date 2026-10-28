"""Read AC Pro Engineer v0.5.1's existing JSON protocols on loopback.

The native executable, not this adapter, computes the upstream advice/debrief.
Protocol definitions: core/src/broadcast/{session,udp}.rs in the bundled source.
"""
import copy
import hashlib
import json
import math
import socket
import time

from .telemetry import _ac_process_running


def numeric(value, low=-math.inf, high=math.inf):
    return value if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) and low <= value <= high else None


def vector(value, length=4, low=-math.inf, high=math.inf):
    if not isinstance(value, list) or len(value) != length:
        return [None] * length
    return [numeric(v, low, high) for v in value]


def text(value, limit=256):
    return " ".join(value.split())[:limit] if isinstance(value, str) else ""


def reject_constant(value):
    raise ValueError("Nonfinite JSON value: " + value)


class ProEngineerReader:
    def __init__(self, demo=False, port=8778, clock=time.time, process_check=_ac_process_running):
        self.demo = bool(demo)
        self.port = port
        self.clock = clock
        self.process_check = process_check
        self._process_checked_at = None
        self._process_alive = False
        self.socket = None
        self.raw = None
        self.summary = None
        self.raw_at = self.summary_at = 0.0
        self.sequences = {}
        self.sender = None
        self.last_error = "Start Pro Engineer and select Start after AC is running."
        self.transient = False

    @property
    def connected(self):
        return self._fresh() and self._game_running()

    def _game_running(self):
        if self.demo:
            return True
        now = self.clock()
        # Windows process inventory takes measurable time. Share one probe
        # between read/connected/snapshot and expire it within half a second.
        if self._process_checked_at is None or not 0 <= now - self._process_checked_at < 0.5:
            self._process_alive = bool(self.process_check())
            self._process_checked_at = now
        return self._process_alive

    def _fresh(self):
        now = self.clock()
        return bool(self.raw and self.summary and 0 <= now - self.raw_at < 2 and
                    0 <= now - self.summary_at < 2 and self.summary.get("flags", 0) & 2 and
                    not self.summary.get("flags", 0) & 512)

    def accept(self, raw, address=("127.0.0.1", 0)):
        """Validate a single real protocol datagram; also used by golden tests."""
        if address[0] != "127.0.0.1" or not 0 < len(raw) <= 65507:
            return False
        try:
            packet = json.loads(raw.decode("utf-8"), parse_constant=reject_constant)
            if not isinstance(packet, dict):
                return False
            summary = packet.get("magic") == "acpe"
            reading = packet.get("what") == "pro-engineer/session"
            if not summary and not reading:
                return False
            if type(packet.get("schema")) is not int or packet["schema"] != 1:
                self.last_error = "Unsupported Pro Engineer protocol version."
                return False
            if summary and (packet.get("game") != "assetto_corsa" or packet.get("app_version") != "0.5.1"):
                self.last_error = "This adapter requires Pro Engineer 0.5.1 configured for original Assetto Corsa."
                return False
            if reading and not all(isinstance(packet.get("reading", {}).get(k), dict) for k in ("car", "session", "fixed", "capabilities")):
                return False
            if summary and not isinstance(packet.get("flags"), int):
                return False
            sender = text(packet.get("driver") if summary else packet.get("from"))
            now = self.clock()
            quiet = now - max(self.raw_at, self.summary_at) > 3
            if quiet:
                self.sequences.clear()
                self.sender = None
                self.raw = self.summary = None
            if self.sender is not None and sender != self.sender:
                return False
            seq = numeric(packet.get("sequence"), 0, 2**64 - 1)
            if seq is None or int(seq) != seq:
                return False
            key = "summary" if summary else "reading"
            if reading and self.raw:
                before = self.raw["reading"].get("fixed", {})
                after = packet["reading"].get("fixed", {})
                identity = ("car_model", "track", "track_config")
                prior_session = self.raw["reading"]["session"]
                next_session = packet["reading"]["session"]
                prior_count = numeric(prior_session.get("completed_laps"))
                next_count = numeric(next_session.get("completed_laps"))
                prior_time = numeric(prior_session.get("current_lap_ms"))
                next_time = numeric(next_session.get("current_lap_ms"))
                restarted = (prior_count is not None and next_count is not None and next_count < prior_count)
                restarted = restarted or (next_count == prior_count and prior_time is not None and next_time is not None and next_time < prior_time - 1000)
                if restarted or any(before.get(k) != after.get(k) for k in identity):
                    self.summary = None
                    self.summary_at = 0
                    self.sequences.pop("summary", None)
            previous = self.sequences.get(key)
            wrapped = previous is not None and previous > 0xFFFF0000 and seq < 0x10000
            # The actual pinned 0.5.1 TUI sends sequence=0 on every summary:
            # its shared-memory writer increments a separate copy. The raw
            # session stream is correctly numbered. Use summary lap/timer
            # ordering for this specific release; do not invent a sequence.
            zero_summary = summary and seq == previous == 0
            if zero_summary and self.summary:
                old = (numeric(self.summary.get("lap_count")), numeric(self.summary.get("current_lap_ms")))
                new = (numeric(packet.get("lap_count")), numeric(packet.get("current_lap_ms")))
                if any(v is None for v in old + new) or new < old:
                    return False
            if previous is not None and seq <= previous and not wrapped and not zero_summary:
                return False
            self.sender = sender
            self.sequences[key] = seq
            if summary:
                self.summary, self.summary_at = packet, now
            else:
                self.raw, self.raw_at = packet, now
            return True
        except (UnicodeDecodeError, ValueError, AttributeError, TypeError):
            return False

    def read(self):
        self.transient = False
        if self.socket is None:
            try:
                self.socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                self.socket.bind(("127.0.0.1", self.port))
                self.socket.setblocking(False)
            except OSError as exc:
                if self.socket:
                    self.socket.close()
                self.socket = None
                self.last_error = "Cannot open local Pro Engineer receiver: " + str(exc)
                return None
        for _ in range(128):
            try:
                data, address = self.socket.recvfrom(65536)
            except BlockingIOError:
                break
            except OSError:
                break
            self.accept(data, address)
        if not self._fresh():
            self.last_error = "Waiting for fresh Pro Engineer telemetry and advice. Keep its window running; press Enter on Start for live driving."
            return None
        if not self._game_running():
            self.last_error = "Live mode requires a running original AC driving process. Demo data is not accepted as live."
            return None
        frame = self._normalize()
        if frame:
            self.last_error = ""
        return frame

    def _normalize(self):
        r = self.raw["reading"]
        c, s, f, caps = (r[k] for k in ("car", "session", "fixed", "capabilities"))
        status = {"Live": "live", "Paused": "paused", "Replay": "replay", "Off": "menu"}.get(s.get("status"))
        car, track = text(f.get("car_model")), text(f.get("track"))
        speed, fuel, position = numeric(c.get("speed_kmh"), 0, 1500), numeric(c.get("fuel_litres"), 0, 10000), numeric(s.get("track_position"), 0, 1)
        lap_count = numeric(s.get("completed_laps"), 0, 100000)
        if not status or not car or not track or any(x is None for x in (speed, fuel, position, lap_count)):
            self.last_error = "Native reading is missing required car/session data."
            return None
        # Two independent UDP streams may arrive in either order. Do not join
        # summaries from another lap/car onto a current reading.
        if self.summary.get("lap_count") != lap_count or abs((numeric(self.summary.get("speed_kmh")) or 0) - speed) > 160:
            self.transient = True
            return None
        gear = numeric(c.get("gear"), -1, 12)
        return {"timestamp": self.clock(), "source": "demo" if self.demo else "live", "backend": "pro_engineer",
            "status": status, "car_id": car, "track_id": track, "layout": text(f.get("track_config")),
            "driver": text(f.get("driver_name")), "packet_id": int(self.raw["sequence"]),
            "speed_kmh": speed, "rpm": numeric(c.get("rpm"), 0, 100000),
            "gear": "R" if gear == -1 else "N" if gear == 0 else str(int(gear)) if gear is not None else "?",
            "gas": numeric(c.get("throttle"), 0, 1), "brake": numeric(c.get("brake"), 0, 1),
            "steer": numeric(c.get("steer_angle"), -30, 30), "fuel_l": fuel,
            "lap_count": int(lap_count), "lap_time_ms": numeric(s.get("current_lap_ms"), 0),
            "last_lap_ms": numeric(s.get("last_lap_ms"), 0), "lap_valid": s.get("lap_is_valid") if caps.get("lap_validity") else None,
            "normalized_pos": position, "track_length_m": numeric(f.get("track_length_m"), 1, 1000000),
            "in_pit": bool(s.get("in_pit_lane")), "wheels_out": numeric(c.get("tyres_off_track"), 0, 4),
            "surface_grip": numeric(s.get("surface_grip"), 0, 2) if caps.get("track_grip") else None,
            "ambient_c": numeric(c.get("air_temp_c"), -100, 100), "road_c": numeric(c.get("road_temp_c"), -100, 150),
            "tyre_compound": text(s.get("compound")), "tyre_pressure_psi": vector(c.get("tyre_pressure_psi"), low=0, high=150),
            "tyre_core_c": vector(c.get("tyre_core_temp_c"), low=-100, high=500),
            "tyre_inner_c": vector(c.get("tyre_temp_inner_c")) if caps.get("tyre_edge_temps") else [None]*4,
            "tyre_middle_c": vector(c.get("tyre_temp_middle_c")),
            "tyre_outer_c": vector(c.get("tyre_temp_outer_c")) if caps.get("tyre_edge_temps") else [None]*4,
            "tyre_wear_raw": vector(c.get("tyre_wear")) if caps.get("tyre_wear") else [None]*4,
            "wheel_slip": vector(c.get("wheel_slip")), "acc_g": vector(c.get("acc_g"), 3),
            "brake_bias": numeric(c.get("brake_bias"), 0, 1), "brake_temp_c": vector(c.get("brake_temp_c")),
            "abs": numeric(c.get("abs"), 0, 1), "tc": numeric(c.get("tc"), 0, 1),
            "kers_charge": None, "has_ers": False, "has_kers": False, "ers_power_level": None, "ers_recovery_level": None, "drs": None,
            "session_type": str(s.get("kind", "Unknown")).lower(), "session_total_laps": numeric(s.get("total_laps"), 0),
            "session_time_left_ms": numeric(s.get("session_time_left_ms"), 0), "coordinates": vector(s.get("car_position_m"), 3),
            "race_position": numeric(s.get("position"), 0), "is_ai_controlled": False,
            "capabilities": {"energy": False, "ers": False, "opponents": False, "full_setup": False,
                             "lap_validity": bool(caps.get("lap_validity")), "tyre_wear_calibrated": False},
            "warnings": ["Engineering advice is computed by AC Pro Engineer 0.5.1.",
                         "The native JSON protocol does not publish ERS channels or definitive demo provenance; the managed source mode determines labelling."]}

    def snapshot(self):
        connected = self.connected
        s = self.summary if connected else {}
        messages = []
        advice_lines = s.get("advice", [])
        if not isinstance(advice_lines, list):
            advice_lines = []
        for advice in reversed(advice_lines[:8]):
            if not isinstance(advice, dict) or not text(advice.get("text")):
                continue
            line = text(advice["text"])
            severity = {0: "info", 1: "warning", 2: "critical"}.get(advice.get("severity"), "info")
            ident = hashlib.sha256((severity + line).encode()).hexdigest()[:16]
            messages.append({"id": "pro:" + ident, "text": line, "severity": severity,
                             "kind": "upstream", "timestamp": self.summary_at, "source": "AC Pro Engineer"})
        debrief = []
        native_laps = s.get("debrief", [])
        if not isinstance(native_laps, list):
            native_laps = []
        for lap in native_laps[:3]:
            if not isinstance(lap, dict):
                continue
            lap_lines = lap.get("lines", [])
            if not isinstance(lap_lines, list):
                lap_lines = []
            lines = [{"text": text(x.get("text")), "severity": numeric(x.get("severity"), 0, 2)}
                     for x in lap_lines[:8] if isinstance(x, dict)]
            debrief.append({"lap_number": numeric(lap.get("lap_number"), 0), "lap_time_ms": numeric(lap.get("lap_time_ms"), 0), "lines": lines})
        if debrief and debrief[0]["lines"]:
            lap = debrief[0]
            line = ". ".join(x["text"] for x in lap["lines"][:2] if x["text"])
            if line:
                ident = hashlib.sha256((str(lap["lap_number"]) + str(lap["lap_time_ms"]) + line).encode()).hexdigest()[:16]
                messages.insert(0, {"id": "pro:debrief:" + ident,
                    "text": "Lap {0}: {1}".format(lap["lap_number"], line),
                    "severity": "info", "kind": "upstream_debrief", "timestamp": self.summary_at,
                    "source": "AC Pro Engineer"})
        return copy.deepcopy({"connected": connected, "version": s.get("app_version"), "messages": messages,
            "debrief": debrief, "source": "AC Pro Engineer", "source_url": "https://github.com/Rgosh/ac-pro-engineer",
            "age_s": round(self.clock() - self.raw_at, 3) if self.raw_at else None,
            "stats": {k: numeric(s.get(k), -math.inf if k == "delta_seconds" else 0) for k in ("fuel_per_lap", "fuel_laps_remaining", "delta_seconds", "stint_laps")},
            "error": self.last_error if not connected else None})

    def close(self):
        if self.socket:
            self.socket.close()
        self.socket = None
        self.raw = self.summary = None
        self.raw_at = self.summary_at = 0
        self.sequences.clear()
        self.sender = None
