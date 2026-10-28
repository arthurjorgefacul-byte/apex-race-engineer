# -*- coding: utf-8 -*-
"""Assetto Corsa overlay and guarded CSP relay; embedded Python 3.3 compatible."""

import os
import sys

# AC's embedded Python omits optional standard extension modules. Load only
# the matching official Python 3.3 socket extension shipped with this app.
# sys.maxsize avoids platform.architecture(), which would launch a subprocess.
_extension_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                              "stdlib", "x64" if sys.maxsize > 2 ** 32 else "x86")
if os.path.isdir(_extension_dir) and _extension_dir not in sys.path:
    sys.path.insert(0, _extension_dir)

import json
import collections
import math
import threading
import time
import textwrap
try:
    import http.client as _http_client
    _transport_error = None
except ImportError as _http_import_error:
    # The window should still load and explain an incomplete installation.
    _http_client = None
    _transport_error = str(_http_import_error)

import ac
try:
    import acsys
except ImportError:
    acsys = None


APP_NAME = "RaceEngineer"
OVERLAY_URL = "http://127.0.0.1:8777/api/overlay"
BRIDGE_URL = "http://127.0.0.1:8777/api/bridge"
RADIO_URL = "http://127.0.0.1:8777/api/radio"
TIMING_BRIDGE_ENABLED = True
BRIDGE_SAMPLE_SECONDS = 0.5
BRIDGE_STEP_SECONDS = 0.05
BRIDGE_CARS_PER_STEP = 2
BRIDGE_GRID_REFRESH_SECONDS = 1.0
BRIDGE_METADATA_SECONDS = 10.0
BRIDGE_CONTEXT_SECONDS = 5.0
BRIDGE_CAR_STALE_SECONDS = 3.0
MAX_BRIDGE_CARS = 64
UI_SECONDS = 0.2
POLL_SECONDS = 0.5
REQUEST_TIMEOUT_SECONDS = 0.8
STALE_SECONDS = 3.0
MAX_RESPONSE_BYTES = 65536
MAX_PENDING_RADIO = 4
RADIO_REQUEST_MAX_AGE = 5.0
RADIO_TOPICS = (("fuel", "Fuel"), ("tyres", "Tyres"), ("pace", "Pace"),
                ("next_corner", "Next corner"), ("corners", "Corner losses"),
                ("last_lap", "Last lap"), ("energy", "Energy"),
                ("track_limits", "Track limits"), ("strategy", "Strategy"),
                ("quiet", "Quiet"), ("more_updates", "More updates"),
                ("less_updates", "Fewer updates"), ("feedback", "Driving feedback"),
                ("personal_best", "Personal best"), ("race_best", "Race best"),
                ("others_best", "Others' best"), ("gap_ahead", "Gap ahead"),
                ("gap_behind", "Gap behind"), ("gap_leader", "Gap to leader"),
                ("change_compound", "Change tyres"))
MENU_EXPANDED_HEIGHT = 267 + ((len(RADIO_TOPICS) + 2) // 3) * 30 + 11
CSP_SNAPSHOT_KEY = ".apex.bridge.snapshot"
CSP_PIT_REQUEST_KEY = ".apex.pit.request"
_RADIO_LABELS = dict(RADIO_TOPICS)
_MENU_HINT = "Choose a topic. Read the answer in the engineer panel."

_GREEN = (0.44, 0.91, 0.68, 1.0)
_WHITE = (0.92, 0.95, 0.98, 1.0)
_MUTED = (0.59, 0.66, 0.74, 1.0)
_AMBER = (1.0, 0.76, 0.39, 1.0)
_RED = (1.0, 0.43, 0.41, 1.0)

_app = None
_labels = {}
_last_render = None
_ui_elapsed = 0.0
_bridge_elapsed = 0.0
_bridge_step_elapsed = 0.0
_bridge_count = None
_bridge_cursor = 0
_bridge_next_grid = 0.0
_bridge_next_context = 0.0
_bridge_context = {}
_bridge_metadata = {}
_bridge_cars = {}
_ac_read_calls = 0
_metrics = {"updates": 0, "delta_sum": 0.0, "delta_max": 0.0,
            "slow_frames": 0, "over_50": 0, "work_sum": 0.0, "work_max": 0.0, "reads_max": 0}
_frame_times = collections.deque(maxlen=4096)
_lock = threading.Lock()
_stop = threading.Event()
_worker = None
_wake = threading.Event()
_radio_pending = collections.deque(maxlen=MAX_PENDING_RADIO)
_radio_status = _MENU_HINT
_menu_expanded = False
_menu_buttons = {}
_last_menu_status = None
_latest = {"reachable": False, "snapshot": None, "received_at": 0.0}
_bridge_packet = None
_bridge_received_at = 0.0
_bridge_serial = 0
_last_sent_bridge = -1
_csp_packet = None
_csp_sequence = None
_csp_received_at = 0.0
_pit_pending = None
_last_pit_command_id = None


class _LocalRequest:
    """A small request description; no urllib proxy, SSL or redirect imports."""
    def __init__(self, full_url, data=None, headers=None):
        self.full_url, self.data = full_url, data
        self.headers = dict(headers or {})

    def get_method(self):
        return "POST" if self.data is not None else "GET"


class _LocalResponse:
    def __init__(self, connection, response):
        self.connection, self.response = connection, response

    def read(self, size):
        return self.response.read(size)

    def close(self):
        try:
            self.response.close()
        finally:
            self.connection.close()


class _LoopbackHTTP:
    """Direct plain HTTP to fixed local paths; never follow redirects."""
    def open(self, request, timeout):
        if _http_client is None:
            raise IOError("Python 3.3 socket support is missing: " + str(_transport_error))
        paths = {OVERLAY_URL: "/api/overlay", BRIDGE_URL: "/api/bridge", RADIO_URL: "/api/radio"}
        path = paths.get(request.full_url)
        if path is None:
            raise ValueError("Only the local companion endpoints are permitted")
        connection = _http_client.HTTPConnection("127.0.0.1", 8777, timeout=timeout)

        def connect_literal():
            # socket.create_connection resolves even an ASCII numeric host
            # through IDNA on Python 3.3. AC omits unicodedata.pyd, which that
            # codec imports. A fixed IPv4 byte literal needs no DNS or codecs.
            sockets = _http_client.socket
            handle = sockets.socket(sockets.AF_INET, sockets.SOCK_STREAM)
            try:
                handle.settimeout(timeout)
                handle.connect((b"127.0.0.1", connection.port))
            except Exception:
                handle.close()
                raise
            connection.sock = handle

        connection.connect = connect_literal
        try:
            headers = dict(request.headers)
            headers["Connection"] = "close"
            connection.request(request.get_method(), path, body=request.data, headers=headers)
            response = connection.getresponse()
            if response.status != 200:
                response.close()
                raise IOError("Companion HTTP response " + str(response.status))
            return _LocalResponse(connection, response)
        except Exception:
            connection.close()
            raise


_opener = _LoopbackHTTP()


def _clean_text(value, limit=180):
    if value is None or isinstance(value, (dict, list, bool)):
        return ""
    value = str(value)
    value = " ".join(value.split())
    value = "".join(character for character in value
                    if ord(character) >= 32 and ord(character) != 127)
    # The standard AC font is more consistent with these simple glyphs.
    value = value.replace("\u2212", "-").replace("\u0394", "Delta")
    value = value.replace("\u2022", " | ").replace("\u00b7", " | ")
    if len(value) > limit:
        return value[:max(0, limit - 3)].rstrip() + "..."
    return value


def _decode_snapshot(raw):
    if len(raw) > MAX_RESPONSE_BYTES:
        raise ValueError("Overlay response is too large")
    snapshot = json.loads(raw.decode("utf-8"))
    if not isinstance(snapshot, dict):
        raise ValueError("Overlay response must be an object")
    if snapshot.get("mode") not in ("live", "demo"):
        raise ValueError("Overlay response has an unknown mode")
    if not isinstance(snapshot.get("connected"), bool):
        raise ValueError("Overlay response has no connection status")
    result = {"mode": snapshot["mode"], "connected": snapshot["connected"]}
    for field in ("title", "lap", "delta", "fuel", "tyres", "advice", "severity", "radio_hint"):
        result[field] = _clean_text(snapshot.get(field))
    if result["mode"] == "live" and result["connected"]:
        result["pit_command"] = _decode_pit_command(snapshot.get("pit_command"))
    return result


def _decode_pit_command(value):
    """Decode data only; validation is repeated on AC's main thread and Lua."""
    if not isinstance(value, dict):
        return None
    result = {}
    for key in ("id", "compound", "car_id", "track_id", "layout", "session_nonce"):
        text = value.get(key)
        if not isinstance(text, str) or len(text) > 160 or (not text and key != "layout"):
            return None
        result[key] = text
    result["index"] = _number(value.get("index"), maximum=31, integer=True)
    result["expires_at"] = _number(value.get("expires_at"))
    if result["index"] is None or result["expires_at"] is None:
        return None
    return result


def _poll_once():
    """Fetch and publish text only. This function never calls an AC API."""
    global _pit_pending
    try:
        request = _LocalRequest(
            OVERLAY_URL,
            headers={"Accept": "application/json", "User-Agent": "RaceEngineer-AC/1"})
        response = _opener.open(request, timeout=REQUEST_TIMEOUT_SECONDS)
        try:
            raw = response.read(MAX_RESPONSE_BYTES + 1)
        finally:
            response.close()
        snapshot = _decode_snapshot(raw)
        state = {"reachable": True, "snapshot": snapshot,
                 "received_at": time.monotonic()}
    except Exception:
        # Never leave old LIVE data on screen after a timeout or bad response.
        state = {"reachable": False, "snapshot": None, "received_at": 0.0}
    with _lock:
        _latest.update(state)
        pending = (state.get("snapshot") or {}).get("pit_command")
        _pit_pending = pending if pending and pending["id"] != _last_pit_command_id else None


def _safe_call(name, *arguments):
    """Optional read-only AC APIs may be absent or fail for individual slots."""
    global _ac_read_calls
    try:
        function = getattr(ac, name, None)
        if callable(function):
            _ac_read_calls += 1
            return function(*arguments)
    except Exception:
        pass
    return None


def _number(value, minimum=0.0, maximum=None, integer=False):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    if not math.isfinite(value) or value < minimum:
        return None
    if maximum is not None and value > maximum:
        return None
    if integer:
        if value != int(value):
            return None
        return int(value)
    return float(value)


def _optional_text(value, allow_empty=False):
    if not isinstance(value, str) or value.strip() == "-1":
        return None
    value = _clean_text(value, 100)
    if not value and not allow_empty:
        return None
    return value


def _car_signal(car_id, enum_name, maximum=None, integer=False):
    enums = getattr(acsys, "CS", None)
    enum_value = getattr(enums, enum_name, None)
    if enum_value is None:
        return None
    return _number(_safe_call("getCarState", car_id, enum_value),
                   maximum=maximum, integer=integer)


def _bridge_snapshot(now):
    """Copy already collected data; publishing must never trigger a grid sweep."""
    if not TIMING_BRIDGE_ENABLED or acsys is None or _bridge_count is None:
        return None
    cars = [dict(car) for car_id, (sampled, car) in sorted(_bridge_cars.items())
            if car_id < _bridge_count and 0.0 <= now - sampled <= BRIDGE_CAR_STALE_SECONDS]
    packet = {"timestamp": time.time(), "cars": cars, "player_id": 0}
    packet.update(_bridge_context)
    updates = _metrics["updates"]
    if updates:
        ordered = sorted(_frame_times)
        p95 = ordered[max(0, int(math.ceil(len(ordered) * 0.95)) - 1)] if ordered else 0.0
        packet["performance"] = {
            "frame_count": updates,
            "window_seconds": round(_metrics["delta_sum"], 3),
            "frame_mean_ms": round(1000.0 * _metrics["delta_sum"] / updates, 3),
            "frame_p95_ms": round(1000.0 * p95, 3),
            "frame_max_ms": round(1000.0 * _metrics["delta_max"], 3),
            "frames_over_33_ms": _metrics["slow_frames"],
            "frames_over_50_ms": _metrics["over_50"],
            "update_mean_ms": round(1000.0 * _metrics["work_sum"] / updates, 4),
            "update_max_ms": round(1000.0 * _metrics["work_max"], 4),
            "max_ac_reads_per_update": _metrics["reads_max"],
            "sampled_cars": len(cars),
        }
    return packet


def _collect_bridge():
    """Read at most two cars on AC's main thread, never a whole-grid burst.

    Names and car/track identity change slowly and are cached. Each car row is
    sampled at a slightly different time; stale rows expire instead of being
    advertised as simultaneous precise race timing.
    """
    global _bridge_count, _bridge_cursor, _bridge_next_grid, _bridge_next_context
    if not TIMING_BRIDGE_ENABLED or acsys is None:
        return None
    now = time.monotonic()
    if now >= _bridge_next_grid:
        count = _number(_safe_call("getCarsCount"), integer=True)
        _bridge_count = min(count, MAX_BRIDGE_CARS) if count is not None else None
        _bridge_next_grid = now + BRIDGE_GRID_REFRESH_SECONDS
    if _bridge_count is None:
        _bridge_cars.clear()
        return None
    if now >= _bridge_next_context:
        context = {}
        for field, api, allow_empty in (("car_id", "getCarName", False),
                                         ("track_id", "getTrackName", False),
                                         ("layout", "getTrackConfiguration", True)):
            value = _optional_text(_safe_call(api, 0), allow_empty=allow_empty)
            if value is not None:
                context[field] = value
        if context != _bridge_context:
            _bridge_cars.clear()
            _bridge_metadata.clear()
            _bridge_cursor = 0
        _bridge_context.clear()
        _bridge_context.update(context)
        _bridge_next_context = now + BRIDGE_CONTEXT_SECONDS
    for unused in range(min(_bridge_count, BRIDGE_CARS_PER_STEP)):
        car_id = _bridge_cursor % _bridge_count
        _bridge_cursor = (car_id + 1) % _bridge_count
        connection = _safe_call("isConnected", car_id)
        if connection is not None and connection != 1:
            _bridge_cars.pop(car_id, None)
            _bridge_metadata.pop(car_id, None)
            continue
        cached = _bridge_metadata.get(car_id)
        if cached is None or now - cached[0] >= BRIDGE_METADATA_SECONDS:
            raw_model = _safe_call("getCarName", car_id)
            if raw_model == -1 or raw_model == "-1":
                _bridge_cars.pop(car_id, None)
                _bridge_metadata.pop(car_id, None)
                continue
            cached = (now, _optional_text(raw_model),
                      _optional_text(_safe_call("getDriverName", car_id)))
            _bridge_metadata[car_id] = cached
        car = {"id": car_id}
        model, name = cached[1], cached[2]
        if model is not None:
            car["car"] = model
        if name is not None:
            car["name"] = name
        for field, enum_name, maximum, integer in (
                ("lap_count", "LapCount", None, True),
                ("lap_time_ms", "LapTime", None, True),
                ("last_lap_ms", "LastLap", None, True),
                ("normalized_pos", "NormalizedSplinePosition", 1.0, False),
                ("speed_kmh", "SpeedKMH", 10000.0, False)):
            value = _car_signal(car_id, enum_name, maximum, integer)
            if value is not None:
                car[field] = value
        # A named occupied slot can still show unknown timing. A completely
        # unsupported slot must not become a fabricated entry in the grid.
        if len(car) > 1:
            _bridge_cars[car_id] = (now, car)
        else:
            _bridge_cars.pop(car_id, None)
    return _bridge_snapshot(now)


def _sample_bridge():
    global _bridge_packet, _bridge_received_at, _bridge_serial
    _sample_csp()
    packet = _bridge_snapshot(time.monotonic())
    if _csp_packet is not None and 0 <= time.monotonic() - _csp_received_at <= STALE_SECONDS:
        if packet is None:
            packet = {"timestamp": time.time(), "cars": [], "player_id": 0}
            packet.update({key: _csp_packet[key] for key in ("car_id", "track_id", "layout") if key in _csp_packet})
        packet["csp"] = _csp_packet
    with _lock:
        _bridge_packet = packet
        _bridge_received_at = time.monotonic() if packet is not None else 0.0
        _bridge_serial += 1
    # Five-second aggregate windows; never attach a raw per-frame event stream.
    if _metrics["delta_sum"] >= 5.0:
        for key in _metrics:
            _metrics[key] = 0
        _frame_times.clear()


def _sample_csp():
    """Main thread only. Expire unchanged shared storage if Lua stops updating."""
    global _csp_packet, _csp_sequence, _csp_received_at
    raw = _safe_call("ext_loadLua", CSP_SNAPSHOT_KEY)
    if not isinstance(raw, str) or not raw or len(raw) > MAX_RESPONSE_BYTES:
        _csp_packet = None
        return
    try:
        packet = json.loads(raw)
        if not isinstance(packet, dict) or not isinstance(packet.get("session_nonce"), str):
            raise ValueError("Invalid CSP context")
        sequence = _number(packet.get("sequence"), integer=True)
        if sequence is None or not isinstance(packet.get("cars"), list) or len(packet["cars"]) > MAX_BRIDGE_CARS:
            raise ValueError("Invalid CSP sequence or grid")
        # Ignore non-standard NaN/Infinity accepted by Python's JSON parser.
        json.dumps(packet, allow_nan=False)
        identity = (packet["session_nonce"], sequence)
        if identity != _csp_sequence:
            _csp_packet, _csp_sequence, _csp_received_at = packet, identity, time.monotonic()
    except (ValueError, TypeError, OverflowError):
        _csp_packet = None


def _forward_pit_request():
    """Main-thread relay only; Lua validates the target and confirms the game.

    A successful store is not a successful tyre change. The companion must wait
    for the matching Lua acknowledgement before reporting an accepted preset.
    """
    global _pit_pending, _last_pit_command_id
    with _lock:
        command, latest = _pit_pending, dict(_latest)
        _pit_pending = None
    if not command or command["id"] == _last_pit_command_id:
        return False
    snapshot = latest.get("snapshot") or {}
    if not latest.get("reachable") or snapshot.get("mode") != "live" or not snapshot.get("connected"):
        return False
    if not 0 <= time.monotonic() - latest.get("received_at", 0) <= STALE_SECONDS:
        return False
    if not _csp_packet or not 0 <= time.monotonic() - _csp_received_at <= STALE_SECONDS:
        return False
    if any(command.get(key) != _csp_packet.get(key) for key in ("car_id", "track_id", "layout", "session_nonce")):
        return False
    remaining = command["expires_at"] - time.time()
    if not 0 < remaining <= 15:
        return False
    store = getattr(ac, "ext_storeLua", None)
    if not callable(store):
        return False
    try:
        store(CSP_PIT_REQUEST_KEY, json.dumps(command, ensure_ascii=True, allow_nan=False, separators=(",", ":")))
        _last_pit_command_id = command["id"]
        return True
    except Exception:
        return False


def _post_bridge_once():
    """Worker only. An optional bridge failure cannot hide a healthy overlay."""
    global _last_sent_bridge
    with _lock:
        packet = _bridge_packet
        received_at = _bridge_received_at
        serial = _bridge_serial
    age = time.monotonic() - received_at
    if packet is None or age < 0.0 or age > STALE_SECONDS or serial <= _last_sent_bridge:
        return
    try:
        body = json.dumps(packet, ensure_ascii=True, allow_nan=False,
                          separators=(",", ":")).encode("utf-8")
        request = _LocalRequest(
            BRIDGE_URL, data=body,
            headers={"Content-Type": "application/json", "Accept": "application/json",
                     "User-Agent": "RaceEngineer-AC/1"})
        response = _opener.open(request, timeout=REQUEST_TIMEOUT_SECONDS)
        try:
            response.read(4096)
        finally:
            response.close()
        _last_sent_bridge = serial
    except Exception:
        # Try the newest main-thread sample on the next cycle; never queue up
        # old packets or mark a working text overlay disconnected for this.
        pass


def _queue_radio(topic, *unused):
    """AC callback: queue a bounded command; never wait for network work."""
    global _radio_status
    if topic not in _RADIO_LABELS or _stop.is_set() or _transport_error:
        return False
    now = time.monotonic()
    with _lock:
        if any(item[0] == topic for item in _radio_pending):
            return False
        _radio_pending.append((topic, now))
        _radio_status = "Asking: " + _RADIO_LABELS[topic] + "..."
    _wake.set()
    return True


def _post_radio_once():
    """Worker only. Send each requested topic once, dropping expired clicks.

    A failed request is never retried automatically: it may already have been
    answered/spoken by the companion before the connection failed.
    """
    global _radio_status
    topic = None
    now = time.monotonic()
    with _lock:
        while _radio_pending:
            candidate, queued_at = _radio_pending.popleft()
            if 0.0 <= now - queued_at <= RADIO_REQUEST_MAX_AGE:
                topic = candidate
                break
        if topic is None:
            return False
    try:
        body = json.dumps({"topic": topic}, separators=(",", ":")).encode("utf-8")
        request = _LocalRequest(RADIO_URL, data=body,
                                headers={"Content-Type": "application/json", "Accept": "application/json",
                                         "User-Agent": "RaceEngineer-AC/1"})
        response = _opener.open(request, timeout=REQUEST_TIMEOUT_SECONDS)
        try:
            raw = response.read(MAX_RESPONSE_BYTES + 1)
        finally:
            response.close()
        if len(raw) > MAX_RESPONSE_BYTES:
            raise ValueError("Radio response is too large")
        answer = json.loads(raw.decode("utf-8"))
        if not isinstance(answer, dict) or answer.get("topic") != topic or not isinstance(answer.get("available"), bool) or not _clean_text(answer.get("text")):
            raise ValueError("Invalid radio response")
        status = _RADIO_LABELS[topic] + ": reply ready"
    except Exception:
        status = "No reply. Check the desktop companion."
    with _lock:
        _radio_status = status
    return True


def _poll_loop():
    while not _stop.is_set():
        _wake.clear()
        # Fetch immediately after the POST so the companion's answer is shown
        # without waiting for a later polling cycle. Answer expiry is owned by
        # the companion, alongside its live/demo/session validity checks.
        _post_radio_once()
        _poll_once()
        _post_bridge_once()
        with _lock:
            pending = bool(_radio_pending)
        if not pending:
            _wake.wait(POLL_SECONDS)


def _start_worker():
    global _worker
    if _worker is not None and _worker.is_alive():
        return
    _stop.clear()
    _worker = threading.Thread(target=_poll_loop, name="RaceEngineerTelemetry")
    _worker.daemon = True
    _worker.start()


def _label(name, text, x, y, size, width, color):
    label = ac.addLabel(_app, text)
    ac.setPosition(label, x, y)
    ac.setSize(label, width, 24)
    ac.setFontSize(label, size)
    ac.setFontAlignment(label, "left")
    ac.setFontColor(label, color[0], color[1], color[2], color[3])
    _labels[name] = label


def _radio_callback(topic):
    def clicked(*arguments):
        _queue_radio(topic)
    return clicked


def _toggle_menu(*unused):
    """AC's click callback runs on its main thread, like other UI writes."""
    global _menu_expanded, _last_menu_status
    _menu_expanded = not _menu_expanded
    ac.setSize(_app, 420, MENU_EXPANDED_HEIGHT if _menu_expanded else 240)
    ac.setText(_menu_buttons["toggle"], "ENGINEER MENU -" if _menu_expanded else "ENGINEER MENU +")
    for topic, label in RADIO_TOPICS:
        ac.setVisible(_menu_buttons[topic], 1 if _menu_expanded else 0)
    ac.setVisible(_labels["menu_status"], 1 if _menu_expanded else 0)
    _last_menu_status = None


def _create_menu():
    global _menu_expanded, _last_menu_status
    _menu_expanded = False
    _last_menu_status = None
    _menu_buttons.clear()
    toggle = ac.addButton(_app, "ENGINEER MENU +")
    _menu_buttons["toggle"] = toggle
    ac.setPosition(toggle, 260, 136)
    ac.setSize(toggle, 146, 21)
    ac.setFontSize(toggle, 10)
    ac.addOnClickedListener(toggle, _toggle_menu)
    _label("menu_status", _MENU_HINT, 14, 241, 10, 392, _MUTED)
    ac.setVisible(_labels["menu_status"], 0)
    for index, (topic, title) in enumerate(RADIO_TOPICS):
        button = ac.addButton(_app, title)
        _menu_buttons[topic] = button
        ac.setPosition(button, 14 + (index % 3) * 132, 267 + (index // 3) * 30)
        ac.setSize(button, 125, 24)
        ac.setFontSize(button, 11)
        ac.addOnClickedListener(button, _radio_callback(topic))
        ac.setVisible(button, 0)


def _render_menu_status():
    global _last_menu_status
    if not _menu_expanded:
        return
    with _lock:
        text = _radio_status
    if text != _last_menu_status:
        ac.setText(_labels["menu_status"], text)
        _last_menu_status = text


def _advice_lines(text):
    lines = textwrap.wrap(_clean_text(text), width=52,
                          break_long_words=True, break_on_hyphens=False)
    if len(lines) > 3:
        lines = lines[:3]
        lines[2] = lines[2][:49].rstrip() + "..."
    return (lines + ["", "", ""])[:3]


def _view(state, now):
    """Build a display model without touching the game UI."""
    fresh = (state.get("reachable") and
             0.0 <= now - state.get("received_at", 0.0) <= STALE_SECONDS)
    snapshot = state.get("snapshot") if fresh else None
    if not snapshot:
        if _transport_error:
            return {"status": "OVERLAY DEPENDENCY MISSING", "status_color": _AMBER,
                    "lap": "--", "delta": "", "fuel": "Fuel  --", "tyres": "Tyres  --",
                    "advice": _advice_lines("Reinstall the complete overlay folder, including its Python 3.3 socket files."),
                    "advice_color": _WHITE, "footer": "Missing local socket support | See the app README."}
        return {"status": "COMPANION OFFLINE", "status_color": _AMBER,
                "lap": "--", "delta": "", "fuel": "Fuel  --", "tyres": "Tyres  --",
                "advice": _advice_lines("Start the Race Engineer companion on your desktop."),
                "advice_color": _WHITE, "footer": "Local connection | 127.0.0.1:8777"}

    demo = snapshot["mode"] == "demo"
    connected = snapshot["connected"] is True
    if demo:
        status, status_color = "DEMO | SAMPLE TELEMETRY", _AMBER
        footer = "Demo data | Select Live in the companion to drive."
    elif connected:
        status, status_color = "LIVE TELEMETRY", _GREEN
        footer = "Local telemetry | Detailed review in the companion."
    else:
        return {"status": "WAITING FOR LIVE TELEMETRY", "status_color": _MUTED,
                "lap": "--", "delta": "", "fuel": "Fuel  --", "tyres": "Tyres  --",
                "advice": _advice_lines(snapshot["advice"] or
                                        "Enter a driving session in Assetto Corsa."),
                "advice_color": _WHITE,
                "footer": "Companion running | Waiting for fresh game data."}

    severity = snapshot["severity"].lower()
    advice_color = _RED if severity in ("critical", "error", "danger") else _WHITE
    if severity in ("warning", "warn"):
        advice_color = _AMBER
    return {"status": status, "status_color": status_color,
            "lap": _clean_text(snapshot["lap"] or "Lap --", 27),
            "delta": _clean_text(snapshot["delta"], 16),
            "fuel": _clean_text(snapshot["fuel"] or "Fuel  --", 55),
            "tyres": _clean_text(snapshot["tyres"] or "Tyres  --", 58),
            "advice": _advice_lines(snapshot["advice"] or "Learning your laps. Keep driving."),
            "advice_color": advice_color, "footer": snapshot.get("radio_hint") or footer}


def _render(view):
    global _last_render
    if view == _last_render:
        return
    previous = _last_render or {}
    for name in ("status", "lap", "delta", "fuel", "tyres", "footer"):
        if view[name] != previous.get(name):
            ac.setText(_labels[name], view[name])
    status_color = view["status_color"]
    if status_color != previous.get("status_color"):
        ac.setFontColor(_labels["status"], status_color[0], status_color[1],
                        status_color[2], status_color[3])
    advice_color = view["advice_color"]
    for index in range(3):
        label = _labels["advice" + str(index)]
        if view["advice"][index] != previous.get("advice", [None, None, None])[index]:
            ac.setText(label, view["advice"][index])
        if advice_color != previous.get("advice_color"):
            ac.setFontColor(label, advice_color[0], advice_color[1],
                            advice_color[2], advice_color[3])
    _last_render = view


def acMain(ac_version):
    global _app, _last_render, _ui_elapsed, _bridge_elapsed, _bridge_step_elapsed
    global _bridge_count, _bridge_cursor, _bridge_next_grid, _bridge_next_context
    global _radio_status
    global _csp_packet, _csp_sequence, _csp_received_at, _pit_pending, _last_pit_command_id
    _app = ac.newApp("Race Engineer")
    ac.setTitle(_app, "Race Engineer")
    ac.setSize(_app, 420, 240)
    ac.setPosition(_app, 20, 240)
    ac.setBackgroundColor(_app, 0.045, 0.060, 0.075)
    ac.setBackgroundOpacity(_app, 0.92)
    ac.drawBorder(_app, 0)
    _labels.clear()
    _label("status", "COMPANION OFFLINE", 14, 31, 10, 392, _AMBER)
    _label("lap", "--", 14, 54, 20, 260, _WHITE)
    _label("delta", "", 282, 60, 14, 124, _GREEN)
    _label("fuel", "Fuel  --", 14, 88, 13, 392, _WHITE)
    _label("tyres", "Tyres  --", 14, 112, 12, 392, _MUTED)
    _label("heading", "ENGINEER", 14, 140, 10, 392, _GREEN)
    for index in range(3):
        _label("advice" + str(index), "", 14, 159 + index * 18, 12, 392, _WHITE)
    _label("footer", "", 14, 221, 9, 392, _MUTED)
    _create_menu()
    _last_render = None
    _ui_elapsed = 0.0
    _bridge_elapsed = 0.0
    _bridge_step_elapsed = 0.0
    _bridge_count = None
    _bridge_cursor = 0
    _bridge_next_grid = 0.0
    _bridge_next_context = 0.0
    _bridge_context.clear()
    _bridge_metadata.clear()
    _bridge_cars.clear()
    _csp_packet, _csp_sequence, _csp_received_at = None, None, 0.0
    _pit_pending, _last_pit_command_id = None, None
    for key in _metrics:
        _metrics[key] = 0
    _frame_times.clear()
    with _lock:
        _radio_pending.clear()
        _radio_status = _MENU_HINT
        initial = dict(_latest)
    _render(_view(initial, time.monotonic()))
    _start_worker()
    return APP_NAME


def acUpdate(delta_t):
    global _ui_elapsed, _bridge_elapsed, _bridge_step_elapsed, _ac_read_calls
    if _app is None:
        return
    if not isinstance(delta_t, (int, float)) or not math.isfinite(delta_t):
        return
    started = time.perf_counter()
    _ac_read_calls = 0
    elapsed = max(0.0, delta_t)
    _metrics["updates"] += 1
    _metrics["delta_sum"] += elapsed
    _metrics["delta_max"] = max(_metrics["delta_max"], elapsed)
    _frame_times.append(elapsed)
    if elapsed > 0.033:
        _metrics["slow_frames"] += 1
    if elapsed > 0.05:
        _metrics["over_50"] += 1
    _ui_elapsed += elapsed
    _bridge_elapsed += elapsed
    _bridge_step_elapsed += elapsed
    if _bridge_step_elapsed >= BRIDGE_STEP_SECONDS:
        # Never catch up with a burst after a slow frame.
        _bridge_step_elapsed = 0.0
        _collect_bridge()
        _forward_pit_request()
    if _bridge_elapsed >= BRIDGE_SAMPLE_SECONDS:
        _bridge_elapsed = 0.0
        publish_bridge = True
    else:
        publish_bridge = False
    if _ui_elapsed >= UI_SECONDS:
        _ui_elapsed = 0.0
        with _lock:
            state = dict(_latest)
        # AC invokes acUpdate on its main thread. All UI writes stay here/acMain.
        _render(_view(state, time.monotonic()))
        _render_menu_status()
    work = time.perf_counter() - started
    _metrics["work_sum"] += work
    _metrics["work_max"] = max(_metrics["work_max"], work)
    _metrics["reads_max"] = max(_metrics["reads_max"], _ac_read_calls)
    if publish_bridge:
        _sample_bridge()


def acShutdown():
    # Do not block AC's shutdown waiting on a network operation.
    _stop.set()
    _wake.set()
