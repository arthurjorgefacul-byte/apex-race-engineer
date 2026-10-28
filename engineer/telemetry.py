"""Read-only original Assetto Corsa 1 telemetry and a deterministic driving demo.

ABI reference (AC 1.16, four-byte packing, Windows UTF-16 strings):
https://github.com/ac-custom-shaders-patch/acc-extension-apps/blob/master/apps/python/AccExtHelper/sim_info.py
These are AC1 pages, not the similarly named, extended ACC pages. No offsets
past the AC1 ABI are read. No game process memory, setup, or controls are written.
"""
from __future__ import annotations

import ctypes as C
import math
import os
import re
import struct
import time
from typing import Callable

I = C.c_int32
F = C.c_float
W = C.c_uint16  # ctypes.c_wchar is four bytes on Unix; the Windows ABI is two.


class Physics(C.LittleEndianStructure):
    _pack_ = 4
    _fields_ = [
        ("packetId", I), ("gas", F), ("brake", F), ("fuel", F),
        ("gear", I), ("rpms", I), ("steerAngle", F), ("speedKmh", F),
        ("velocity", F * 3), ("accG", F * 3), ("wheelSlip", F * 4),
        ("wheelLoad", F * 4), ("wheelsPressure", F * 4),
        ("wheelAngularSpeed", F * 4), ("tyreWear", F * 4),
        ("tyreDirtyLevel", F * 4), ("tyreCoreTemperature", F * 4),
        ("camberRAD", F * 4), ("suspensionTravel", F * 4),
        ("drs", F), ("tc", F), ("heading", F), ("pitch", F), ("roll", F),
        ("cgHeight", F), ("carDamage", F * 5), ("numberOfTyresOut", I),
        ("pitLimiterOn", I), ("abs", F), ("kersCharge", F), ("kersInput", F),
        ("autoShifterOn", I), ("rideHeight", F * 2), ("turboBoost", F),
        ("ballast", F), ("airDensity", F), ("airTemp", F), ("roadTemp", F),
        ("localAngularVel", F * 3), ("finalFF", F), ("performanceMeter", F),
        ("engineBrake", I), ("ersRecoveryLevel", I), ("ersPowerLevel", I),
        ("ersHeatCharging", I), ("ersIsCharging", I), ("kersCurrentKJ", F),
        ("drsAvailable", I), ("drsEnabled", I), ("brakeTemp", F * 4),
        ("clutch", F), ("tyreTempI", F * 4), ("tyreTempM", F * 4),
        ("tyreTempO", F * 4), ("isAIControlled", I),
        ("tyreContactPoint", F * 3 * 4), ("tyreContactNormal", F * 3 * 4),
        ("tyreContactHeading", F * 3 * 4), ("brakeBias", F),
        ("localVelocity", F * 3),
    ]


class Graphics(C.LittleEndianStructure):
    _pack_ = 4
    _fields_ = [
        ("packetId", I), ("status", I), ("session", I),
        ("currentTime", W * 15), ("lastTime", W * 15),
        ("bestTime", W * 15), ("split", W * 15),
        ("completedLaps", I), ("position", I), ("iCurrentTime", I),
        ("iLastTime", I), ("iBestTime", I), ("sessionTimeLeft", F),
        ("distanceTraveled", F), ("isInPit", I), ("currentSectorIndex", I),
        ("lastSectorTime", I), ("numberOfLaps", I), ("tyreCompound", W * 33),
        ("replayTimeMultiplier", F), ("normalizedCarPosition", F),
        ("carCoordinates", F * 3), ("penaltyTime", F), ("flag", I),
        ("idealLineOn", I), ("isInPitLine", I), ("surfaceGrip", F),
        ("mandatoryPitDone", I), ("windSpeed", F), ("windDirection", F),
    ]


class Static(C.LittleEndianStructure):
    _pack_ = 4
    _fields_ = [
        ("smVersion", W * 15), ("acVersion", W * 15),
        ("numberOfSessions", I), ("numCars", I), ("carModel", W * 33),
        ("track", W * 33), ("playerName", W * 33), ("playerSurname", W * 33),
        ("playerNick", W * 33), ("sectorCount", I), ("maxTorque", F),
        ("maxPower", F), ("maxRpm", I), ("maxFuel", F),
        ("suspensionMaxTravel", F * 4), ("tyreRadius", F * 4),
        ("maxTurboBoost", F), ("airTemp", F), ("roadTemp", F),
        ("penaltiesEnabled", I), ("aidFuelRate", F), ("aidTireRate", F),
        ("aidMechanicalDamage", F), ("aidAllowTyreBlankets", I),
        ("aidStability", F), ("aidAutoClutch", I), ("aidAutoBlip", I),
        ("hasDRS", I), ("hasERS", I), ("hasKERS", I), ("kersMaxJ", F),
        ("engineBrakeSettingsCount", I), ("ersPowerControllerCount", I),
        ("trackSPlineLength", F), ("trackConfiguration", W * 33),
        ("ersMaxJ", F), ("isTimedRace", I), ("hasExtraLap", I),
        ("carSkin", W * 33), ("reversedGridPositions", I),
        ("pitWindowStart", I), ("pitWindowEnd", I),
    ]


PAGES = {"physics": Physics, "graphics": Graphics, "static": Static}
STATUS = {0: "menu", 1: "replay", 2: "live", 3: "paused"}


class TelemetryError(ValueError):
    """Data belongs to an unsupported game/ABI or is not a usable snapshot."""


def _text(value) -> str:
    return bytes(value).decode("utf-16-le", errors="replace").split("\0", 1)[0].strip()


def _number(value, low=-math.inf, high=math.inf):
    value = float(value)
    return value if math.isfinite(value) and low <= value <= high else None


def _array(values, low=-math.inf, high=math.inf):
    return [_number(value, low, high) for value in values]


def normalize(p: Physics, g: Graphics, s: Static, timestamp: float) -> dict:
    """Normalize one validated snapshot; preserve unknown or invalid channels as null."""
    ac_version, sm_version = _text(s.acVersion), _text(s.smVersion)
    # Only the current original AC1 family is supported by this exact ABI.
    if not re.fullmatch(r"1\.16(?:\.\d+)*(?:\s.*)?", ac_version):
        raise TelemetryError("This reader supports original Assetto Corsa 1.16 on PC; ACC/EVO and other versions need separate adapters.")
    sm_match = re.fullmatch(r"1\.(\d+)(?:\.\d+)*", sm_version)
    if not sm_match or not 0 <= int(sm_match[1]) <= 7:
        raise TelemetryError("Unrecognized shared-memory version; refusing to decode a possible ACC/EVO layout.")
    if g.status not in STATUS:
        raise TelemetryError("Unsupported session status in shared memory.")
    car, track = _text(s.carModel), _text(s.track)
    if not car or not track:
        raise TelemetryError("Waiting for a car and track session.")
    fuel = _number(p.fuel, 0, 10000)
    position = _number(g.normalizedCarPosition, 0, 1)
    speed = _number(p.speedKmh, 0, 1500)
    if fuel is None or position is None or speed is None or not 0 <= g.completedLaps < 100000:
        raise TelemetryError("Shared-memory values failed plausibility checks.")
    has_ers, has_kers = s.hasERS == 1, s.hasKERS == 1
    energy_capacity = _number(s.ersMaxJ if has_ers else s.kersMaxJ, 0.001, 1e10)
    energy = (has_ers or has_kers) and energy_capacity is not None
    charge = _number(p.kersCharge, 0, 1) if energy else None
    ers_available = has_ers and charge is not None
    power = int(p.ersPowerLevel) if ers_available and 0 <= p.ersPowerLevel < s.ersPowerControllerCount else None
    recovery = int(p.ersRecoveryLevel) if ers_available and 0 <= p.ersRecoveryLevel <= 100 else None
    warnings = ["Native AC1 does not expose a definitive lap-validity flag; observed track limits and pit status are used.",
                "Tyre wear is the game's raw channel, not a calibrated remaining-life percentage."]
    if (has_ers or has_kers) and charge is None:
        warnings.append("This car does not provide validated usable energy telemetry.")
    return {
        "timestamp": timestamp, "source": "live", "status": STATUS[g.status],
        "car_id": car, "track_id": track, "layout": _text(s.trackConfiguration),
        "driver": " ".join(filter(None, (_text(s.playerName), _text(s.playerSurname)))) or _text(s.playerNick),
        "packet_id": int(p.packetId), "graphics_packet_id": int(g.packetId),
        "ac_version": ac_version, "shared_memory_version": sm_version,
        "speed_kmh": speed, "rpm": int(p.rpms) if 0 <= p.rpms <= 100000 else None,
        "gear": "R" if p.gear == 0 else "N" if p.gear == 1 else str(p.gear - 1) if 2 <= p.gear <= 12 else "?",
        "gas": _number(p.gas, 0, 1), "brake": _number(p.brake, 0, 1),
        "steer": _number(p.steerAngle, -1.01, 1.01), "fuel_l": fuel,
        "lap_count": int(g.completedLaps), "lap_time_ms": max(0, int(g.iCurrentTime)),
        "last_lap_ms": max(0, int(g.iLastTime)), "best_lap_ms": max(0, int(g.iBestTime)), "lap_valid": None,
        "normalized_pos": position, "track_length_m": _number(s.trackSPlineLength, 1, 1000000),
        "in_pit": bool(g.isInPit or g.isInPitLine),
        "wheels_out": int(p.numberOfTyresOut) if 0 <= p.numberOfTyresOut <= 4 else None,
        "surface_grip": _number(g.surfaceGrip, 0, 2), "ambient_c": _number(p.airTemp, -100, 100),
        "road_c": _number(p.roadTemp, -100, 150), "tyre_compound": _text(g.tyreCompound),
        "tyre_pressure_psi": _array(p.wheelsPressure, 0, 150),
        "tyre_core_c": _array(p.tyreCoreTemperature, -100, 500),
        "tyre_inner_c": _array(p.tyreTempI, -100, 500),
        "tyre_middle_c": _array(p.tyreTempM, -100, 500), "tyre_outer_c": _array(p.tyreTempO, -100, 500),
        "tyre_wear_raw": _array(p.tyreWear), "wheel_slip": _array(p.wheelSlip),
        "local_velocity": _array(p.localVelocity), "local_angular_velocity": _array(p.localAngularVel),
        "acc_g": _array(p.accG), "abs": _number(p.abs, 0, 1), "tc": _number(p.tc, 0, 1),
        "brake_bias": _number(p.brakeBias, 0, 1), "kers_charge": charge,
        "ers_power_level": power, "ers_recovery_level": recovery,
        "has_ers": has_ers, "has_kers": has_kers,
        "energy_capacity_j": energy_capacity if energy else None,
        "kers_current_kj": _number(p.kersCurrentKJ, 0, 1e7) if charge is not None else None,
        "kers_input": _number(p.kersInput, 0, 1) if charge is not None else None,
        "ers_is_charging": bool(p.ersIsCharging) if ers_available and p.ersIsCharging in (0, 1) else None,
        "ers_heat_charging": bool(p.ersHeatCharging) if ers_available and p.ersHeatCharging in (0, 1) else None,
        "drs": _number(p.drs, 0, 1) if s.hasDRS == 1 else None,
        "drs_available": bool(p.drsAvailable) if s.hasDRS == 1 and p.drsAvailable in (0, 1) else None,
        "session_type": {0: "practice", 1: "qualifying", 2: "race", 3: "hotlap"}.get(g.session, "other"),
        "session_time_left_ms": _number(g.sessionTimeLeft, 0),
        "session_laps": int(g.numberOfLaps), "session_total_laps": int(g.numberOfLaps),
        "is_timed_race": bool(s.isTimedRace), "has_extra_lap": bool(s.hasExtraLap),
        "race_position": int(g.position),
        "coordinates": _array(g.carCoordinates), "flag": int(g.flag),
        "penalty_time_s": _number(g.penaltyTime, 0),
        "is_ai_controlled": bool(p.isAIControlled),
        "capabilities": {"energy": charge is not None, "ers": ers_available,
                         "opponents": False, "full_setup": False, "lap_validity": False,
                         "tyre_wear_calibrated": False},
        "warnings": warnings,
    }


class _WindowsMapping:
    """Open an existing mapping read-only. Unlike mmap(tagname), never create one."""
    def __init__(self, name: str, size: int):
        if os.name != "nt":
            raise OSError("Live shared memory requires Windows.")
        api = C.WinDLL("kernel32", use_last_error=True)
        api.OpenFileMappingW.argtypes = [C.c_uint32, C.c_int, C.c_wchar_p]
        api.OpenFileMappingW.restype = C.c_void_p
        api.MapViewOfFile.argtypes = [C.c_void_p, C.c_uint32, C.c_uint32, C.c_uint32, C.c_size_t]
        api.MapViewOfFile.restype = C.c_void_p
        api.UnmapViewOfFile.argtypes = [C.c_void_p]
        api.UnmapViewOfFile.restype = C.c_int
        api.CloseHandle.argtypes = [C.c_void_p]
        api.CloseHandle.restype = C.c_int
        self.api, self.size, self.address, self.handle = api, size, None, None
        self.handle = api.OpenFileMappingW(4, False, name)  # FILE_MAP_READ
        if not self.handle:
            raise OSError(C.get_last_error(), "Assetto Corsa shared memory is not available.")
        self.address = api.MapViewOfFile(self.handle, 4, 0, 0, size)
        if not self.address:
            error = C.get_last_error()
            self.close()
            raise OSError(error, "Unable to map the original AC1 telemetry page.")

    def read(self, start=0, size=None) -> bytes:
        size = self.size if size is None else size
        if not self.address or start < 0 or start + size > self.size:
            raise OSError("Mapping is closed or read extends beyond its bounds.")
        return C.string_at(self.address + start, size)

    def close(self):
        if self.address:
            self.api.UnmapViewOfFile(self.address)
            self.address = None
        if self.handle:
            self.api.CloseHandle(self.handle)
            self.handle = None


def _ac_process_running() -> bool:
    """Check process names, without opening or reading the game process itself.

    An open mapping can survive its writer exiting. This also detects game exit
    from pause/replay, where packet movement cannot be used as a heartbeat.
    An unavailable process inventory is treated as unknown rather than absent.
    """
    if os.name != "nt":
        return False

    class Entry(C.Structure):
        _fields_ = [("dwSize", C.c_uint32), ("cntUsage", C.c_uint32),
                    ("processID", C.c_uint32), ("defaultHeapID", C.c_size_t),
                    ("moduleID", C.c_uint32), ("threads", C.c_uint32),
                    ("parentID", C.c_uint32), ("priority", C.c_int32),
                    ("flags", C.c_uint32), ("exe", W * 260)]

    api = C.WinDLL("kernel32", use_last_error=True)
    api.CreateToolhelp32Snapshot.argtypes = [C.c_uint32, C.c_uint32]
    api.CreateToolhelp32Snapshot.restype = C.c_void_p
    api.Process32FirstW.argtypes = [C.c_void_p, C.POINTER(Entry)]
    api.Process32FirstW.restype = C.c_int
    api.Process32NextW.argtypes = [C.c_void_p, C.POINTER(Entry)]
    api.Process32NextW.restype = C.c_int
    api.CloseHandle.argtypes = [C.c_void_p]
    api.CloseHandle.restype = C.c_int
    snapshot = api.CreateToolhelp32Snapshot(2, 0)
    if not snapshot or snapshot == C.c_void_p(-1).value:
        return True
    entry = Entry()
    entry.dwSize = C.sizeof(entry)
    try:
        available = api.Process32FirstW(snapshot, C.byref(entry))
        if not available:
            return True
        while available:
            if _text(entry.exe).lower() in {"acs.exe", "acs_x86.exe"}:
                return True
            available = api.Process32NextW(snapshot, C.byref(entry))
        return False
    finally:
        api.CloseHandle(snapshot)


class ACReader:
    """Poll existing AC1 pages without installing a game plugin.

    Packet IDs guard against a page changing during copying. Separate pages have
    independent clocks, so this is a coherent best-effort snapshot, not a promise
    of an atomic, physics-tick-synchronized native API.
    """
    def __init__(self, mapping_factory=None, clock: Callable = time.time, process_probe=None):
        self._factory = mapping_factory or _WindowsMapping
        self._process_probe = process_probe if process_probe is not None else (_ac_process_running if mapping_factory is None else None)
        self._clock, self._maps = clock, {}
        self._next_connect, self._last_changed, self._packet = 0.0, 0.0, None
        self._next_probe, self._process_alive = 0.0, True
        self._transient = False
        self.last_error = "Waiting for original Assetto Corsa on track."

    @property
    def connected(self) -> bool:
        """Whether existing telemetry pages and the game process remain available.

        Availability alone does not mean this poll produced a validated frame.
        Consumers should age any retained last frame independently.
        """
        return bool(self._maps) and self._process_alive

    @property
    def transient(self) -> bool:
        """True only when the latest read skipped an incoherent packet snapshot."""
        return self._transient

    def close(self):
        for mapping in self._maps.values():
            mapping.close()
        self._maps = {}
        self._packet = None
        self._transient = False

    def read(self) -> dict | None:
        self._transient = False
        now = self._clock()
        if self._process_probe is not None and now >= self._next_probe:
            self._process_alive = self._process_probe()
            self._next_probe = now + 1
        if not self._process_alive:
            self.close()
            self.last_error = "Waiting for original Assetto Corsa 1.16 on track (Windows PC)."
            return None
        if not self._maps:
            if now < self._next_connect:
                return None
            self._next_connect = now + 1
            try:
                for key, page in PAGES.items():
                    self._maps[key] = self._factory("Local\\acpmf_" + key, C.sizeof(page))
            except OSError:
                self.close()
                self.last_error = "Waiting for original Assetto Corsa 1.16 on track (Windows PC)."
                return None
        try:
            pmap, gmap, smap = (self._maps[key] for key in PAGES)
            for _ in range(4):
                pb, gb = pmap.read(0, 4), gmap.read(0, 4)
                sbytes = smap.read()
                pbytes, gbytes = pmap.read(), gmap.read()
                if (pb == pbytes[:4] == pmap.read(0, 4) and
                        gb == gbytes[:4] == gmap.read(0, 4) and sbytes == smap.read()):
                    break
            else:
                self._transient = True
                self.last_error = "Telemetry pages changed while being read; waiting for a coherent snapshot."
                return None
            p, g, s = Physics.from_buffer_copy(pbytes), Graphics.from_buffer_copy(gbytes), Static.from_buffer_copy(sbytes)
            frame = normalize(p, g, s, now)
            packet = (p.packetId, g.packetId)
            if packet != self._packet:
                self._packet, self._last_changed = packet, now
            elif frame["status"] == "live" and now - self._last_changed > 3:
                self.last_error = "Telemetry stopped updating. Waiting for Assetto Corsa."
                self.close()
                return None
            self.last_error = ""
            return frame
        except (OSError, ValueError, struct.error) as exc:
            self.last_error = str(exc)
            self.close()
            return None


class DemoSource:
    """Synthetic demo only: four corners, 32–35 second laps, midlap connection.

    Inject a clock to replay deterministically without sleeps. Time and distance
    share one monotonic timeline, while each lap changes braking/exit behavior.
    """
    DURATIONS = (32.0, 34.0, 33.0, 35.0)
    CORNERS = (0.17, 0.40, 0.64, 0.84)

    def __init__(self, clock: Callable = time.time):
        self._clock = clock
        self.reset()

    def reset(self):
        self._start = self._clock()

    def read(self) -> dict:
        now = self._clock()
        elapsed = max(0.0, now - self._start) + 12.0
        cycle_length = sum(self.DURATIONS)
        cycle = int(elapsed // cycle_length)
        remainder = elapsed % cycle_length
        index = 0
        while remainder >= self.DURATIONS[index]:
            remainder -= self.DURATIONS[index]
            index += 1
        lap = cycle * len(self.DURATIONS) + index
        duration = self.DURATIONS[index]
        pos = remainder / duration
        # Wider low-speed/slow-release phases make the later laps measurably slower.
        nearest = min(self.CORNERS, key=lambda centre: abs(pos - centre))
        offset = pos - nearest
        width = 0.034 + index * 0.0018
        turn = math.exp(-0.5 * (offset / width) ** 2)
        braking = math.exp(-0.5 * ((offset + 0.040 + index * 0.002) / 0.020) ** 2)
        speed = 212.0 - (114 + index * 2.7) * turn
        gas = max(0.0, min(1.0, 1.0 - braking * 1.18 - turn * (0.63 + 0.035 * index)))
        brake = max(0.0, min(1.0, braking * 0.88))
        steer = turn * (0.30 + index * 0.012) * (1 if nearest in self.CORNERS[::2] else -1)
        fuel = max(0.0, 38.0 - (lap + pos) * 1.42)
        warmup = min(1, elapsed / 75)
        heat = 67 + warmup * 19 + turn * 3
        pressure = 23.5 + warmup * 2.5
        tyres = [heat + 2, heat, heat - 1, heat - 2]
        return {
            "timestamp": now, "source": "demo", "status": "live",
            "car_id": "demo_gt3", "track_id": "demo_circuit", "layout": "four_corners",
            "driver": "Demo driver", "packet_id": int(elapsed * 60),
            "speed_kmh": speed, "rpm": int(3500 + speed * 17),
            "gear": str(3 if speed < 130 else 4 if speed < 170 else 5),
            "gas": gas, "brake": brake, "steer": steer, "fuel_l": fuel,
            "lap_count": lap, "lap_time_ms": round(remainder * 1000),
            "last_lap_ms": round(self.DURATIONS[(index - 1) % len(self.DURATIONS)] * 1000) if lap else 0,
            "lap_valid": True, "normalized_pos": pos, "track_length_m": 1450.0,
            "in_pit": False, "wheels_out": 0, "surface_grip": 1.0,
            "ambient_c": 24.0, "road_c": 32.0, "tyre_compound": "Synthetic medium",
            "tyre_pressure_psi": [pressure + 0.25, pressure, pressure + 0.1, pressure - 0.1],
            "tyre_core_c": tyres, "tyre_inner_c": [v + 5 for v in tyres],
            "tyre_middle_c": [v + 1 for v in tyres], "tyre_outer_c": [v - 3 for v in tyres],
            "tyre_wear_raw": [max(0.0, 100.0 - (lap + pos) * 0.4)] * 4,
            "wheel_slip": [turn * 0.08, turn * 0.09, gas * turn * 0.14, gas * turn * 0.16],
            "local_velocity": [speed / 3.6, 0.0, 0.0],
            "local_angular_velocity": [0.0, steer * speed / 28, 0.0],
            "acc_g": [steer * 4, -brake * 1.4, 1.0], "abs": 0.0, "tc": 0.0,
            "brake_bias": 0.63, "kers_charge": None, "ers_power_level": None,
            "ers_recovery_level": None, "has_ers": False, "has_kers": False, "drs": None,
            "session_type": "practice", "session_time_left_ms": None,
            "coordinates": [200 * math.cos(pos * math.tau), 0, 200 * math.sin(pos * math.tau)],
            "is_ai_controlled": False,
            "capabilities": {"energy": False, "ers": False, "opponents": False,
                             "full_setup": False, "lap_validity": True, "tyre_wear_calibrated": False},
            "warnings": ["DEMO: generated telemetry, not connected to your game.",
                         "Tyre wear is an uncalibrated raw channel."],
        }
