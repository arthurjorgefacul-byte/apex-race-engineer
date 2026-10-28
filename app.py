"""Race Engineer: local companion and read-only telemetry service."""
import argparse
import copy
import collections
import datetime
import json
import logging
import math
import os
import pathlib
import re
import threading
import time
import urllib.parse
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from engineer.telemetry import ACReader, DemoSource
from engineer.analysis import Engineer
from engineer.voice import Voice
from engineer.recognition import PushToTalk
from engineer.pro_engineer import ProEngineerReader
from engineer.pro_engineer_runtime import ProEngineerRuntime
from engineer.live_engineer import LiveEngineer
from engineer.reference_data import ReferenceLibrary, load_knowledge, track_info, principles_for_corner
from engineer.coaching_foundations import FoundationLibrary
from engineer.feedback import FeedbackAnalyzer
from engineer.race import RaceDirector, RACE_TOPICS
from engineer.lifecycle import SessionLifecycle

VERSION = "0.5.1"
ROOT = pathlib.Path(__file__).resolve().parent
DEFAULTS = {"voice": True, "focus": "balanced", "fuel_reserve_l": 2.0,
            "target_pressure_psi": None, "backend": "local", "verbosity": "balanced", "quiet": False,
            "ptt_enabled": True, "ptt_key": "F10", "ptt_binding": None}


def clean_json(value):
    if isinstance(value, float) and not math.isfinite(value):
        return None
    if isinstance(value, dict):
        return {str(k): clean_json(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [clean_json(v) for v in value]
    return value


def format_lap(value):
    if not isinstance(value, (int, float)) or value <= 0:
        return "--:--.---"
    return "%d:%06.3f" % (int(value // 60000), value % 60000 / 1000)


class Companion:
    def __init__(self, data_dir, demo=False, voice=True, backend=None):
        self.data_dir = pathlib.Path(data_dir)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.settings_path = self.data_dir / "settings.json"
        self.settings = dict(DEFAULTS)
        try:
            saved = json.loads(self.settings_path.read_text(encoding="utf-8"))
            self.settings.update({k: v for k, v in saved.items() if k in DEFAULTS})
        except (OSError, ValueError, AttributeError):
            pass
        self.pressure_profiles = saved.get("pressure_profiles", {}) if 'saved' in locals() and isinstance(saved, dict) else {}
        if not isinstance(self.pressure_profiles, dict):
            self.pressure_profiles = {}
        self.settings["target_pressure_psi"] = None
        if backend in ("local", "pro_engineer"):
            self.settings["backend"] = backend
        if self.settings.get("backend") not in ("local", "pro_engineer"):
            self.settings["backend"] = "local"
        self.profile_key = None
        if not voice:
            self.settings["voice"] = False
        self.reader = ACReader()
        self.upstream_reader = None
        self.runtime = None
        self.demo = DemoSource()
        self.engineer = Engineer(self.data_dir)
        self.engineer.configure(self.settings)
        self.live_engineer = LiveEngineer(self.settings)
        self.references = ReferenceLibrary(self.data_dir)
        self.foundations = FoundationLibrary()
        self.foundation = {}
        self.foundation_key = None
        self.feedback_key = None
        self.feedback = {}
        self.race = RaceDirector()
        self.lifecycle = SessionLifecycle(self.data_dir)
        self.radio_state = {}
        self.radio_history = collections.deque(maxlen=50)
        self.radio_queue = []
        self.reply_queue = collections.deque(maxlen=8)
        self.reply_started_at = 0
        self.last_urgent_at = 0
        self.radio_generation = None
        self.radio_seen = set()
        self.last_reply = None
        self.voice = Voice()
        self.voice.enabled = bool(self.settings["voice"])
        self.ptt = PushToTalk(lambda topic: self.ask_engineer({"topic": topic}))
        self.mode = "demo" if demo else "live"
        self.telemetry = None
        self.analysis = self.engineer.snapshot()
        self.lock = threading.RLock()
        self.stop_event = threading.Event()
        self.error = None
        self.seen_messages = set()
        self.pending = None
        self.last_auto_voice = 0
        self.last_voice_session = None
        self.last_voice_status = None
        self.straight_since = None
        self.bridge = None
        self.last_frame_at = None
        self.input_history = collections.deque(maxlen=900)
        self.thread = threading.Thread(target=self.run, daemon=True, name="telemetry")

    def start(self):
        self.thread.start()

    def run(self):
        while not self.stop_event.is_set():
            try:
                with self.lock:
                    if self.settings["backend"] == "pro_engineer":
                        if self.upstream_reader is None:
                            self.upstream_reader = ProEngineerReader(demo=self.mode == "demo")
                        frame = self.upstream_reader.read()
                        managed = self.runtime.snapshot() if self.runtime else {}
                        if not managed.get("running") or bool(managed.get("demo")) != (self.mode == "demo"):
                            frame = None
                            self.upstream_reader.last_error = "Start the bundled engine for the selected Live/Demo source."
                        active_reader = self.upstream_reader
                    else:
                        frame = self.demo.read() if self.mode == "demo" else self.reader.read()
                        active_reader = self.reader
                    if frame and frame.get("status") == "menu":
                        frame = None
                    if frame is None and self.mode == "live" and getattr(active_reader, "transient", False) and getattr(active_reader, "connected", False) and self.last_frame_at is not None and time.time() - self.last_frame_at < 0.5:
                        self.stop_event.wait(0.01)
                        continue
                    self.telemetry = frame
                    if frame:
                        enrichment = self.race.player_enrichment(frame)
                        frame.update(enrichment)
                        if isinstance(enrichment.get("lap_valid"), bool):
                            frame.setdefault("capabilities", {})["lap_validity"] = True
                            frame["warnings"] = [w for w in frame.get("warnings", []) if not w.startswith("Native AC1 does not expose")]
                        self._sync_profile(frame)
                    transition = self.lifecycle.observe(frame, self.analysis, self.foundation)
                    if transition == "started":
                        self._begin_recording()
                    elif transition == "ended":
                        self._session_ended()
                    if frame:
                        self._foundation_for(frame)
                    if frame and self.lifecycle.active:
                        self._update_analysis(frame)
                        self.last_frame_at = time.time()
                    elif not frame:
                        self.pending = None
                        self.radio_queue.clear()
                    self._dispatch_replies()
                    self.error = None
            except Exception as exc:
                logging.exception("Telemetry update failed")
                with self.lock:
                    self.error = str(exc)[:250]
                    self.telemetry = None
                    self.pending = None
                    self._reset_radio()
                    if hasattr(self.voice, "clear"):
                        self.voice.clear()
                    self.analysis = self.engineer.update({"status": "disconnected", "source": self.mode, "timestamp": time.time()})
                self.stop_event.wait(0.5)
            self.stop_event.wait(0.04)

    def _foundation_for(self, frame):
        key = tuple(frame.get(k) for k in ("source", "car_id", "track_id", "layout"))
        if key != self.foundation_key:
            self.foundation = self.foundations.context(frame)
            self.foundation_key = key
            self.feedback_key = None
        return self.foundation

    def _decorate_analysis(self):
        self.analysis["foundation"] = self.foundation
        coaching = self.analysis.get("coaching") or {}
        key = (coaching.get("comparison_lap_id"), coaching.get("comparison_reference_id"),
               len(self.analysis.get("comparison_history") or []), self.foundation_key)
        if key != self.feedback_key:
            self.feedback = FeedbackAnalyzer.analyze(self.analysis, self.foundation)
            self.feedback_key = key
        self.analysis["feedback"] = self.feedback

    def _begin_recording(self):
        self.engineer.reset()
        self.input_history.clear()
        self._reset_radio()
        self.feedback_key = None
        self.analysis = self.engineer.snapshot()

    def _update_analysis(self, frame):
        self.analysis = self.engineer.update(frame)
        self._apply_upstream()
        self._decorate_analysis()
        self.lifecycle.attach(self.analysis)
        self.race.observe_analysis(self.analysis, frame)
        self._input_trace(frame)
        self.radio_state = self.live_engineer.update(frame, self.analysis)
        self._schedule_voice(frame)

    def _session_ended(self, speak=True):
        self.radio_queue.clear()
        self.reply_queue.clear()
        self.pending = None
        self.last_frame_at = None
        self.analysis.setdefault("session", {}).update(recording=False, status="ended")
        debrief = self.lifecycle.snapshot().get("last_debrief") or {}
        if speak and debrief:
            text = str(debrief.get("summary") or "Session complete. Your debrief is ready on Home.")
            priorities = debrief.get("priorities") or []
            if priorities:
                first = priorities[0]
                text += " " + str(first.get("instruction") or first.get("action") or "") if isinstance(first, dict) else " " + str(first)
            self._queue_reply({"topic": "debrief", "text": text[:650], "available": True})

    def session_action(self, payload):
        with self.lock:
            if payload.get("action") == "start":
                if self.lifecycle.active:
                    return self.lifecycle.snapshot()
                transition = self.lifecycle.start(self.telemetry)
                if transition == "started":
                    self._begin_recording()
                    self._update_analysis(self.telemetry)
            elif payload.get("action") == "end":
                was_active = self.lifecycle.active
                self.lifecycle.end(self.analysis, self.foundation)
                if was_active:
                    self._session_ended()
            else:
                raise ValueError("Choose start or end for the session.")
            return self.lifecycle.snapshot()

    def _queue_reply(self, answer):
        now = time.time()
        answer = dict(answer, timestamp=now, id="request-" + str(time.time_ns()), source="Driver request",
                      speech_status="queued" if self.settings["voice"] else "muted")
        self.last_reply = answer
        self.radio_history.append(answer)
        if self.settings["voice"]:
            self.reply_queue.append(dict(answer, expires_at=now + 30))
        return answer

    def _reply_active(self):
        return time.time() - self.reply_started_at < 1.0 or bool(self.reply_started_at and self.voice.snapshot().get("speaking"))

    def _dispatch_replies(self):
        now = time.time()
        while self.race.notifications:
            self._queue_reply({"topic": "change_compound", "text": self.race.notifications.popleft(), "available": True})
        while self.reply_queue and self.reply_queue[0]["expires_at"] <= now:
            expired = self.reply_queue.popleft()
            if self.last_reply and self.last_reply["id"] == expired["id"]:
                self.last_reply["speech_status"] = "expired"
        if not self.settings["voice"]:
            self.reply_queue.clear()
            return
        microphone = self.ptt.snapshot()
        if microphone.get("starting") or microphone.get("listening") or now - self.last_urgent_at < 2.5 or self._reply_active():
            return
        if self.reply_queue:
            answer = self.reply_queue.popleft()
            started = self.voice.say(answer.get("speech") or answer.get("text", ""), force=True)
            if started:
                self.reply_started_at = now
            if self.last_reply and self.last_reply["id"] == answer["id"]:
                self.last_reply["speech_status"] = "started" if started else "unavailable"

    def _input_trace(self, frame):
        values = [frame.get(k) for k in ("speed_kmh", "gas", "brake")]
        if frame.get("status") != "live" or not all(isinstance(v, (int, float)) and math.isfinite(v) for v in values):
            return
        self.input_history.append((time.time(), *values))
        now = time.time()
        points = [p for p in self.input_history if now - p[0] <= 25]
        self.analysis.setdefault("traces", {})["rolling"] = [
            {"x": max(0, 1 - (now - t) / 25), "speed": speed, "gas": gas, "brake": brake}
            for t, speed, gas, brake in points[::max(1, len(points)//280)]]

    def _apply_upstream(self):
        if self.settings["backend"] != "pro_engineer" or self.upstream_reader is None:
            return
        upstream = self.upstream_reader.snapshot()
        self.analysis["upstream"] = upstream
        self.analysis["apex_messages"] = self.analysis.get("messages", [])
        self.analysis["messages"] = list(upstream.get("messages") or [])
        self.analysis["coaching"]["advice_source"] = "AC Pro Engineer"
        notes = []
        for message in self.analysis["messages"]:
            notes.append({"title": "AC Pro Engineer · " + str(message.get("severity", "info")),
                          "text": message.get("text", ""), "kind": "upstream", "source": "AC Pro Engineer"})
        self.analysis["setup"] = notes + self.analysis.get("setup", [])

    def engine_snapshot(self):
        pro = self.settings["backend"] == "pro_engineer"
        external = self.upstream_reader.snapshot() if pro and self.upstream_reader else {}
        runtime = self.runtime.snapshot() if self.runtime else {"running": False}
        return {"backend": self.settings["backend"], "name": "AC Pro Engineer" if pro else "APEX live engineer",
                "upstream": external, "runtime": runtime,
                "source_url": "https://github.com/Rgosh/ac-pro-engineer" if pro else None}

    def start_engine(self):
        if self.settings["backend"] != "pro_engineer":
            raise ValueError("Choose Pro Engineer first.")
        if self.upstream_reader is None:
            self.upstream_reader = ProEngineerReader(demo=self.mode == "demo")
        if self.runtime is None:
            self.runtime = ProEngineerRuntime(ROOT, self.data_dir)
        return self.runtime.start(demo=self.mode == "demo", hidden=self.mode == "demo")

    def _clear_upstream(self):
        if self.upstream_reader:
            self.upstream_reader.close()
            self.upstream_reader = None
        if self.runtime:
            self.runtime.stop()

    def _sync_profile(self, frame):
        key = json.dumps([frame.get("source"), frame.get("car_id"), frame.get("tyre_compound")], separators=(",", ":"))
        if key != self.profile_key:
            self.profile_key = key
            target = self.pressure_profiles.get(key)
            self.settings["target_pressure_psi"] = target if isinstance(target, (int, float)) and math.isfinite(target) and 5 <= target <= 80 else None
            self.engineer.configure(self.settings)
            self.live_engineer.configure(self.settings)

    def _sync_radio_generation(self):
        session_id = (self.analysis.get("session") or {}).get("id")
        if self.last_voice_session is not None and session_id != self.last_voice_session:
            self.radio_history.clear()
            self.last_reply = None
        generation = (session_id, self.radio_state.get("generation"))
        if generation != self.radio_generation:
            self.radio_queue.clear()
            self.radio_seen.clear()
            self.pending = None
            self.straight_since = None
            if not self._reply_active():
                self.voice.clear()
            self.radio_generation = generation
        self.last_voice_session = session_id

    def _reset_radio(self):
        self.radio_queue.clear()
        self.reply_queue.clear()
        self.radio_seen.clear()
        self.radio_history.clear()
        self.last_reply = None
        self.radio_state = self.live_engineer.update({"status": "disconnected"}, {})
        self.radio_generation = None

    def _schedule_voice(self, frame):
        now = time.time()
        self._sync_radio_generation()
        previous = self.last_voice_status
        self.last_voice_status = frame.get("status")
        if frame.get("status") != "live" or frame.get("is_ai_controlled"):
            self.radio_queue.clear()
            self.pending = None
            self.straight_since = None
            if previous == "live" and not self._reply_active():
                self.voice.clear()
            return
        for event in self.radio_state.get("new_events", []):
            if event["id"] not in self.radio_seen:
                self.radio_seen.add(event["id"])
                self.radio_history.append(dict(event))
                self.radio_queue.append(dict(event))
        # Keep the tested upstream engine's own advice, without replaying it
        # each telemetry tick or replacing the live, per-corner APEX calls.
        if self.settings["backend"] == "pro_engineer" and not self.settings["quiet"]:
            for message in self.analysis.get("messages", []):
                ident = str(message.get("id", message.get("text", "")))
                if ident not in self.seen_messages:
                    self.seen_messages.add(ident)
                    if message.get("kind") not in ("info", "status"):
                        self.radio_queue.append(dict(message, id="pro-" + ident, priority=1,
                            timestamp=now, expires_at=now+25, immediate=False, source="AC Pro Engineer"))
        self.radio_queue = [e for e in self.radio_queue if e.get("expires_at", now+1) > now][-24:]
        session = self.analysis.get("session") or {}
        corners_allowed = (frame.get("is_ai_controlled") is False and frame.get("in_pit") is False
                           and isinstance(frame.get("wheels_out"), (int, float)) and frame["wheels_out"] <= 2
                           and session.get("current_lap_eligible") is True and not session.get("current_lap_flags"))
        self.radio_queue = [e for e in self.radio_queue if e.get("kind") != "corner" or corners_allowed]
        if not self.settings["voice"]:
            self.radio_queue.clear()
            return
        microphone = self.ptt.snapshot()
        if microphone.get("listening") or microphone.get("starting"):
            return
        urgent = next((e for e in self.radio_queue if e.get("immediate")), None)
        if urgent:
            if self.voice.say(urgent.get("text", ""), force=True):
                self.last_auto_voice = now
                self.last_urgent_at = now
            self.radio_queue.remove(urgent)
            return
        if self.reply_queue or self._reply_active():
            return
        steer, brake, speed = (frame.get(k) for k in ("steer", "brake", "speed_kmh"))
        lateral = (frame.get("acc_g") or [None])[0]
        known = all(isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)
                    for v in (steer, brake, speed, lateral))
        straight = known and abs(steer) < .12 and brake < .05 and abs(lateral) < .25 and speed > 45 and (frame.get("wheels_out") or 0) < 3
        if straight and self.straight_since is None:
            self.straight_since = now
        if not straight:
            self.straight_since = None
        safe = frame.get("in_pit") or (self.straight_since is not None and now-self.straight_since >= .7)
        gap = {"less": 15, "balanced": 7, "more": 4}[self.settings["verbosity"]]
        if self.radio_queue and safe and now-self.last_auto_voice >= gap and not self.voice.snapshot().get("speaking"):
            event = max(self.radio_queue, key=lambda e: (e.get("priority", 1), -e.get("timestamp", now)))
            if self.voice.say(event.get("text", "")):
                self.last_auto_voice = now
                self.radio_queue.remove(event)

    def ask_engineer(self, payload):
        topic = payload.get("topic", payload.get("question", "status"))
        if not isinstance(topic, str) or not topic.strip() or len(topic) > 300:
            raise ValueError("Choose an engineer question or enter up to 300 characters.")
        question = topic.strip().lower()
        specific_corner = re.search(r"\bcorner[ _#]*(\d{1,2})\b", question)
        if specific_corner:
            question = "corner_" + specific_corner.group(1)
        compound = payload.get("compound")
        if question.startswith("compound_"):
            compound = question[9:].capitalize()
            question = "change_compound"
        named_compound = re.search(r"\b(soft|medium|hard|intermediate|wet)s?\b", question)
        if named_compound and (question == named_compound.group(0) or re.search(r"\b(put|fit|change|switch|box)\b", question)):
            compound = named_compound.group(1).capitalize()
            question = "change_compound"
        commands = {"status", "fuel", "tyres", "pace", "corners", "next_corner", "last_lap", "feedback",
                    "strategy", "energy", "track_limits", "quiet", "more_updates", "less_updates"} | RACE_TOPICS
        if question not in commands and not re.fullmatch(r"corner_\d{1,2}", question):
            terms = [("my fastest", "personal_best"), ("my best", "personal_best"), ("personal best", "personal_best"),
                     ("race fastest", "race_best"), ("fastest lap of", "race_best"), ("fastest race", "race_best"),
                     ("others", "others_best"), ("other drivers", "others_best"), ("opponent", "others_best"),
                     ("fastest lap", "race_best"), ("leader", "gap_leader"),
                     ("gap ahead", "gap_ahead"), ("gap behind", "gap_behind"),
                     ("car ahead", "gap_ahead"), ("in front", "gap_ahead"), ("car behind", "gap_behind"),
                     ("change compound", "change_compound"), ("different compound", "change_compound"),
                     ("change tyre", "change_compound"), ("change tire", "change_compound"),
                     ("feedback", "feedback"), ("losing time", "feedback"),
                     ("next", "next_corner"), ("last lap", "last_lap"), ("track limit", "track_limits"),
                     ("quiet", "quiet"), ("more update", "more_updates"), ("less update", "less_updates"),
                     ("fuel", "fuel"), ("tire", "tyres"), ("tyre", "tyres"), ("pace", "pace"),
                     ("corner", "corners"), ("strategy", "strategy"), ("energy", "energy"), ("ers", "energy"),
                     ("status", "status")]
            question = next((command for term, command in terms if term in question), "unknown")
        with self.lock:
            if question == "change_compound" and compound is None:
                match = re.search(r"\b(soft|medium|hard|intermediate|wet)s?\b", topic.lower())
                if match:
                    compound = match.group(1).capitalize()
            self.radio_state = self.live_engineer.update(self.telemetry or {}, self.analysis)
            if question in RACE_TOPICS:
                answer = self.race.ask(question, self.telemetry or {}, self.analysis, compound=compound,
                                       driver_id=payload.get("driver_id"), driver_name=payload.get("driver_name"))
            else:
                answer = self.live_engineer.ask(question, self.telemetry or {}, self.analysis)
            if question == "quiet":
                self.configure({"quiet": True})
                self.radio_queue.clear()
                self.voice.clear()
            elif question in ("more_updates", "less_updates"):
                self.configure({"quiet": False, "verbosity": "more" if question == "more_updates" else "less"})
            self.radio_state = self.live_engineer.snapshot()
            self._sync_radio_generation()
            answer = self._queue_reply(answer)
            # Requested answers are independent of automatic coaching's safe
            # straight/interval gate. They can be heard stopped or paused too.
            if question == "quiet":
                self.reply_queue.clear()
                answer["speech_status"] = "silent"
            self._dispatch_replies()
            return answer

    def reference_context(self):
        return dict(self.telemetry or {}, game="assetto_corsa")

    def snapshot(self):
        with self.lock:
            return clean_json(copy.deepcopy({"version": VERSION, "mode": self.mode,
                "connected": self.telemetry is not None, "telemetry": self.telemetry,
                "analysis": self.analysis, "settings": self.settings,
                "lifecycle": self.lifecycle.snapshot(), "foundation": self.foundation,
                "radio": dict(self.radio_state, history=list(self.radio_history), reply=self.last_reply, queued=len(self.radio_queue)+len(self.reply_queue)),
                "voice": self.voice.snapshot(), "error": self.error,
                "push_to_talk": self.ptt.snapshot(),
                "telemetry_error": getattr(self.upstream_reader if self.settings["backend"] == "pro_engineer" else self.reader, "last_error", ""),
                "engine": self.engine_snapshot(),
                "timing": self.timing_snapshot(),
                "data_dir": str(self.data_dir), "server_time": time.time()}))

    def timing_snapshot(self):
        if self.mode != "live" or not self.bridge or time.time() - self.bridge["received_at"] >= 3:
            return {"available": False, "cars": [], "note": "Enable the APEX in-game bridge for current race timing."}
        if any((self.bridge["payload"].get(k) or "") != ((self.telemetry or {}).get(k) or "") for k in ("car_id", "track_id", "layout")):
            return {"available": False, "cars": [], "note": "Waiting for matching game session timing."}
        return self.race.snapshot(self.telemetry or {})

    def receive_bridge(self, payload):
        cars = payload.get("cars")
        if not isinstance(cars, list) or len(cars) > 64:
            raise ValueError("Invalid timing grid.")
        clean = []
        for car in cars:
            if not isinstance(car, dict):
                continue
            item = {}
            for key in ("name", "car"):
                item[key] = str(car.get(key) or "")[:100]
            for key, lower, upper in (("id", 0, 1000), ("lap_count", 0, 100000), ("lap_time_ms", 0, 86400000),
                                      ("last_lap_ms", 0, 86400000), ("normalized_pos", 0, 1), ("speed_kmh", 0, 1500)):
                value = car.get(key)
                if isinstance(value, (float, int)) and not isinstance(value, bool) and math.isfinite(value) and lower <= value <= upper:
                    item[key] = value
            if "id" in item:
                clean.append(item)
        if len({c["id"] for c in clean}) != len(clean):
            raise ValueError("Duplicate timing identifiers.")
        performance = {}
        if isinstance(payload.get("performance"), dict):
            for key in ("frame_count", "window_seconds", "frame_mean_ms", "frame_p95_ms", "frame_max_ms",
                        "frames_over_33_ms", "frames_over_50_ms", "update_mean_ms", "update_max_ms",
                        "max_ac_reads_per_update", "sampled_cars"):
                value = payload["performance"].get(key)
                if isinstance(value, (float, int)) and not isinstance(value, bool) and math.isfinite(value) and 0 <= value <= 1e9:
                    performance[key] = value
        with self.lock:
            self.bridge = {"received_at": time.time(), "payload": {"cars": clean,
                "car_id": str(payload.get("car_id") or "")[:100],
                "track_id": str(payload.get("track_id") or "")[:100],
                "layout": str(payload.get("layout") or "")[:100], "player_id": 0, "performance": performance}}
            if self.telemetry and self.mode == "live" and all((payload.get(k) or "") == (self.telemetry.get(k) or "") for k in ("car_id", "track_id", "layout")):
                self.race.ingest(payload, self.telemetry)

    def performance_snapshot(self):
        with self.lock:
            bridge = self.bridge or {}
            fresh = 0 <= time.time() - bridge.get("received_at", 0) < 3
            return {"available": fresh and bool(bridge.get("payload", {}).get("performance")),
                    "overlay": dict(bridge.get("payload", {}).get("performance", {})) if fresh else {},
                    "note": "AC Python update intervals are a frame pacing proxy. Overlay update cost excludes rendering, other apps and GPU work."}

    def overlay(self):
        # The game requests seven text fields. Do not copy/serialize the full
        # stored lap graphs and native debrief merely to construct those fields.
        with self.lock:
            state = {"connected": self.telemetry is not None, "mode": self.mode,
                     "telemetry": self.telemetry, "analysis": self.analysis}
        frame = state.get("telemetry") or {}
        analysis = state.get("analysis") or {}
        fuel = analysis.get("fuel") or {}
        coaching = analysis.get("coaching") or {}
        messages = list(self.radio_history)
        msg = next((m for m in reversed(messages) if m.get("expires_at", m.get("timestamp", 0)+20) > time.time()), {})
        if self.last_reply and time.time() - self.last_reply["timestamp"] < 20 and msg.get("priority", 0) < 3:
            msg = self.last_reply
        delta = coaching.get("delta_s", coaching.get("current_delta_s"))
        if delta is None:
            delta = (analysis.get("session") or {}).get("current_delta_s")
        pressures = frame.get("tyre_pressure_psi") or []
        pressure_text = " / ".join("%.1f" % p if isinstance(p, (float, int)) and p > 0 else "--" for p in pressures)
        consumption = fuel.get("consumption_l_per_lap", fuel.get("l_per_lap"))
        remaining = fuel.get("laps_remaining")
        fuel_text = "--"
        if frame.get("fuel_l") is not None:
            fuel_text = "%.1f L" % frame["fuel_l"]
            if isinstance(remaining, (int, float)):
                fuel_text += " | %.1f laps" % remaining
            elif isinstance(consumption, (int, float)):
                fuel_text += " | %.2f L/lap" % consumption
        if not state["connected"]:
            advice = "Start a driving session, or choose Demo in the companion."
        elif frame.get("status") != "live":
            advice = "Session %s. Coaching paused." % frame.get("status", "waiting")
        else:
            advice = msg.get("text") or ("Engineer active. Ask for fuel, tyres, pace or the next corner." if analysis.get("reference") else "Drive a complete clean lap to build your reference.")
        if frame.get("status") != "live":
            delta_text = "Coaching paused"
        elif (analysis.get("session") or {}).get("current_lap_flags"):
            delta_text = "Lap excluded"
        else:
            delta_text = "%+.3f s" % delta if isinstance(delta, (int, float)) else "Build reference"
        ptt = self.ptt.snapshot()
        binding = self.settings.get("ptt_binding") or {}
        binding_label = "wheel button %d" % (binding.get("button", 0) + 1) if binding.get("kind") == "joystick" else self.settings["ptt_key"]
        radio_hint = "MIC LISTENING | Ask for gaps, fuel or feedback" if ptt["listening"] else "MIC STARTING | Keep holding the button" if ptt["starting"] else "Hold %s for engineer voice | English commands" % binding_label if self.settings["ptt_enabled"] else "Engineer quick menu | Voice input disabled"
        return {"connected": state["connected"], "mode": state["mode"],
                "title": (frame.get("car_id") or "Waiting for Assetto Corsa").replace("_", " "),
                "lap": format_lap(frame.get("lap_time_ms")),
                "delta": delta_text,
                "fuel": fuel_text, "tyres": (pressure_text + " psi") if pressure_text else "No tyre data",
                "advice": advice, "severity": msg.get("severity", "info"),
                "radio_hint": radio_hint,
                "pit_command": self.race.command(frame),
                "session_phase": self.lifecycle.snapshot()["phase"],
                "timestamp": time.time(), "status": frame.get("status", "disconnected")}

    def configure(self, payload):
        updated = dict(self.settings)
        for field in ("quiet", "ptt_enabled"):
            if field in payload:
                if not isinstance(payload[field], bool):
                    raise ValueError("Quiet must be true or false.")
                updated[field] = payload[field]
        allowed_keys = {"F%d" % i for i in range(1, 25)} | set("ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789") | {"Space", "Insert", "Home", "End", "PageUp", "PageDown"}
        if "ptt_key" in payload:
            if payload["ptt_key"] not in allowed_keys:
                raise ValueError("Choose a supported keyboard key for push-to-talk.")
            updated["ptt_key"] = payload["ptt_key"]
            if "ptt_binding" not in payload:
                updated["ptt_binding"] = {"kind": "keyboard", "key": payload["ptt_key"]}
        if "ptt_binding" in payload:
            binding = payload["ptt_binding"]
            if not isinstance(binding, dict):
                raise ValueError("Choose a keyboard key or learn a wheel button.")
            if binding.get("kind") == "keyboard" and binding.get("key") in allowed_keys:
                updated["ptt_binding"] = {"kind": "keyboard", "key": binding["key"]}
                updated["ptt_key"] = binding["key"]
            elif binding.get("kind") == "joystick":
                for name, limit in (("device_id", 15), ("button", 31)):
                    value = binding.get(name)
                    if type(value) is not int or not 0 <= value <= limit:
                        raise ValueError("Choose an available wheel and button.")
                result = {k: binding[k] for k in ("kind", "device_id", "button")}
                result["device_name"] = str(binding.get("device_name") or "Wheel")[:100]
                for name in ("vendor", "product"):
                    value = binding.get(name)
                    if value is not None and (type(value) is not int or not 0 <= value <= 65535):
                        raise ValueError("Invalid wheel identity.")
                    result[name] = value
                updated["ptt_binding"] = result
            else:
                raise ValueError("Choose a keyboard key or learn a wheel button.")
        if "verbosity" in payload:
            if payload["verbosity"] not in ("less", "balanced", "more"):
                raise ValueError("Choose fewer, balanced or more updates.")
            updated["verbosity"] = payload["verbosity"]
        if "voice" in payload:
            if not isinstance(payload["voice"], bool):
                raise ValueError("Voice must be true or false.")
            updated["voice"] = payload["voice"]
        if "focus" in payload:
            if payload["focus"] not in ("balanced", "stability", "rotation"):
                raise ValueError("Unknown driving preference.")
            updated["focus"] = payload["focus"]
        if payload.get("target_pressure_psi") not in (None, "") and not self.profile_key:
            raise ValueError("Connect a car before saving its tyre target.")
        for key, lower, upper in (("fuel_reserve_l", 0, 100), ("target_pressure_psi", 5, 80)):
            if key not in payload:
                continue
            value = payload[key]
            if key == "target_pressure_psi" and value in (None, ""):
                updated[key] = None
            else:
                if isinstance(value, bool):
                    raise ValueError("Enter a numeric value.")
                value = float(value)
                if not math.isfinite(value) or not lower <= value <= upper:
                    raise ValueError("Value is outside the allowed range.")
                updated[key] = value
        with self.lock:
            self.settings = updated
            if "target_pressure_psi" in payload and self.profile_key:
                self.pressure_profiles[self.profile_key] = updated["target_pressure_psi"]
            self.voice.enabled = updated["voice"]
            self.engineer.configure(updated)
            self.live_engineer.configure(updated)
            if not updated["ptt_enabled"]:
                self.ptt.stop()
            temp = self.settings_path.with_suffix(".tmp")
            temp.write_text(json.dumps(dict(updated, pressure_profiles=self.pressure_profiles), indent=2), encoding="utf-8")
            temp.replace(self.settings_path)

    def edit_lap(self, payload, restore=False):
        lap_id = payload.get("lap_id")
        if not isinstance(lap_id, str) or not lap_id or len(lap_id) > 100:
            raise ValueError("Choose a recorded lap first.")
        with self.lock:
            # Validate ownership before making any change.
            lap = self.engineer._stored_lap(lap_id, include_deleted=True)["summary"]
            if payload.get("session_id") and payload["session_id"] != lap["session_id"]:
                raise ValueError("This lap belongs to a different session.")
            result = (self.engineer.restore_lap if restore else self.engineer.delete_lap)(lap_id)
            if not restore:
                self.lifecycle.forget_lap(lap_id)
            else:
                self.lifecycle.restore_lap(result)
            self.analysis = self.engineer.snapshot()
            self.feedback_key = None
            self._decorate_analysis()
            self.pending = None
            self.radio_queue.clear()
            self.radio_generation = None
            self.voice.clear() if hasattr(self.voice, "clear") else None
            # Native Pro Engineer has independent history. Retain its seen IDs
            # so an edit cannot replay the same upstream advice.
            if self.settings["backend"] != "pro_engineer":
                self.seen_messages.clear()
            self._apply_upstream()
            return result

    def action(self, name):
        with self.lock:
            if name in ("engine_pro", "engine_local", "demo", "live", "reset"):
                if self.lifecycle.active:
                    self.lifecycle.end(self.analysis, self.foundation)
                    self._session_ended(speak=False)
                elif self.lifecycle.snapshot().get("armed"):
                    self.lifecycle.end(self.analysis, self.foundation)
                self._reset_radio()
                self.race.reset()
            if name in ("engine_pro", "engine_local"):
                backend = "pro_engineer" if name == "engine_pro" else "local"
                if backend != self.settings["backend"]:
                    self._clear_upstream()
                    self.engineer.reset()
                    self.input_history.clear()
                    self.analysis = self.engineer.snapshot()
                    self.telemetry = None
                    self.settings["backend"] = backend
                    self.pending = None
                    self.bridge = None
                    self.last_frame_at = None
                    self.seen_messages.clear()
                    self.voice.clear() if hasattr(self.voice, "clear") else None
                    self.configure({})
                if backend == "pro_engineer":
                    self.start_engine()
            elif name == "engine_start":
                self.start_engine()
            elif name in ("demo", "live"):
                if self.mode != name:
                    self._clear_upstream()
                    self.mode = name
                    self.engineer.reset()
                    self.input_history.clear()
                    self.analysis = self.engineer.snapshot()
                    self.telemetry = None
                    self.pending = None
                    self.bridge = None
                    if hasattr(self.voice, "clear"):
                        self.voice.clear()
                    self.last_frame_at = None
                    self.profile_key = None
                    self.settings["target_pressure_psi"] = None
                    self.engineer.configure(self.settings)
                    self.seen_messages.clear()
                    if name == "demo" and hasattr(self.demo, "reset"):
                        self.demo.reset()
                    if self.settings["backend"] == "pro_engineer":
                        self.start_engine()
            elif name == "mute":
                self.configure({"voice": not self.settings["voice"]})
            elif name == "radio":
                if self.voice.available:
                    self.voice.say("Radio check. Your race engineer is ready. I will compare your clean laps and call out the biggest opportunities.", force=True)
                else:
                    raise ValueError("Offline voice requires Windows.")
            elif name == "brief":
                message = self.overlay()["advice"]
                self.voice.say(message, force=True)
            elif name == "reset":
                self.engineer.reset()
                self.input_history.clear()
                self.analysis = self.engineer.snapshot()
                self.pending = None
                if hasattr(self.voice, "clear"):
                    self.voice.clear()
                self.seen_messages.clear()
            elif name == "stop":
                self.stop_event.set()
            else:
                raise ValueError("Unknown action.")

    def close(self):
        self.stop_event.set()
        self.voice.close()
        self.ptt.close()
        if self.thread.is_alive():
            self.thread.join(timeout=.45)
        with self.lock:
            if self.lifecycle.active:
                self.lifecycle.end(self.analysis, self.foundation)
        self.reader.close()
        self._clear_upstream()
        if hasattr(self.engineer, "close"):
            self.engineer.close()


class LocalServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True


def handler_for(companion):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def trusted(self):
            allowed = {"127.0.0.1:%s" % self.server.server_port, "localhost:%s" % self.server.server_port}
            if self.headers.get("Host", "") not in allowed:
                self.reply({"error": "Only local requests are allowed."}, 403)
                return False
            origin = self.headers.get("Origin")
            if origin and origin not in {"http://" + h for h in allowed}:
                self.reply({"error": "This origin is not allowed."}, 403)
                return False
            return True

        def reply(self, value, status=200):
            raw = json.dumps(clean_json(value), allow_nan=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(raw)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.end_headers()
            self.wfile.write(raw)

        def do_GET(self):
            if not self.trusted():
                return
            parsed = urllib.parse.urlparse(self.path)
            path = parsed.path
            query = urllib.parse.parse_qs(parsed.query)
            if path == "/api/sessions" or path.startswith("/api/sessions/") or path == "/api/analyze":
                try:
                    include_deleted = query.get("include_deleted", ["0"])[0] == "1"
                    # WAL snapshot on a separate read-only connection. Large
                    # history reads must not block the live recording lock.
                    with companion.engineer.open_history() as history:
                        if path == "/api/sessions":
                            result = {"sessions": history.list_sessions(include_deleted), "data_dir": str(companion.data_dir)}
                        elif path == "/api/analyze":
                            session_id = query.get("session_id", [""])[0]
                            lap_id = query.get("lap_id", [""])[0]
                            if not session_id or not lap_id:
                                raise ValueError("Choose a session and lap to analyze.")
                            result = history.analyze_stored(session_id, lap_id, query.get("reference_id", [None])[0])
                            for corner in result.get("corners", []):
                                corner["guidance"] = principles_for_corner(corner)
                            result["foundation"] = companion.foundations.context(result.get("session") or {})
                            result["feedback"] = FeedbackAnalyzer.analyze(result, result["foundation"])
                        else:
                            parts = path.split("/")
                            if len(parts) not in (4, 5) or (len(parts) == 5 and parts[4] != "export"):
                                return self.reply({"error": "Not found"}, 404)
                            session_id = urllib.parse.unquote(parts[3])
                            if len(parts) == 5:
                                chunks = history.iter_export_session_json(session_id, include_deleted)
                                self.protocol_version = "HTTP/1.1"
                                self.close_connection = True
                                self.send_response(200)
                                self.send_header("Content-Type", "application/json; charset=utf-8")
                                self.send_header("Content-Disposition", 'attachment; filename="APEX-session.json"')
                                self.send_header("Transfer-Encoding", "chunked")
                                self.send_header("Connection", "close")
                                self.send_header("Cache-Control", "no-store")
                                self.send_header("X-Content-Type-Options", "nosniff")
                                self.end_headers()
                                try:
                                    for chunk in chunks:
                                        if chunk:
                                            self.wfile.write(("%X\r\n" % len(chunk)).encode("ascii") + chunk + b"\r\n")
                                    self.wfile.write(b"0\r\n\r\n")
                                except (ConnectionError, OSError):
                                    logging.info("Session export connection closed before completion")
                                return
                            result = history.session_detail(session_id, include_deleted)
                    return self.reply(result)
                except ValueError as exc:
                    return self.reply({"error": str(exc)}, 404 if str(exc).startswith("Unknown session") else 400)
            if path == "/api/references":
                return self.reply(companion.references.catalog(companion.reference_context()))
            if path.startswith("/api/references/"):
                try:
                    return self.reply(companion.references.get(urllib.parse.unquote(path.split("/")[-1])))
                except ValueError as exc:
                    return self.reply({"error": str(exc)}, 404)
            if path == "/api/state":
                return self.reply(companion.snapshot())
            if path == "/api/overlay":
                return self.reply(companion.overlay())
            if path == "/api/health":
                return self.reply({"ok": True, "app": "RaceEngineer", "version": VERSION, "pid": os.getpid()})
            if path == "/api/performance":
                return self.reply(companion.performance_snapshot())
            if path == "/api/export":
                raw = json.dumps(companion.snapshot(), indent=2, allow_nan=False).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Disposition", 'attachment; filename="race-engineer-session.json"')
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)
                return
            if path == "/api/source":
                source = ROOT / "SOURCE.zip"
                if not source.is_file():
                    return self.reply({"error": "The source bundle is included in the packaged release."}, 404)
                raw = source.read_bytes()
                self.send_response(200)
                self.send_header("Content-Type", "application/zip")
                self.send_header("Content-Disposition", 'attachment; filename="APEX-source.zip"')
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)
                return
            files = {"/": ("index.html", "text/html; charset=utf-8"),
                     "/app.js": ("app.js", "application/javascript; charset=utf-8"),
                     "/style.css": ("style.css", "text/css; charset=utf-8"),
                     "/favicon.svg": ("favicon.svg", "image/svg+xml")}
            if path not in files:
                return self.reply({"error": "Not found"}, 404)
            filename, mime = files[path]
            raw = (ROOT / "web" / filename).read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", mime)
            self.send_header("Content-Length", str(len(raw)))
            self.send_header("Cache-Control", "no-cache")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'")
            self.end_headers()
            self.wfile.write(raw)

        def do_POST(self):
            # Consume a bounded body before an early rejection. On Windows,
            # closing with unread bytes can reset an otherwise valid response.
            self.connection.settimeout(3)
            try:
                length = int(self.headers.get("Content-Length", "0"))
                if length < 1 or length > 65536:
                    return self.reply({"error": "Invalid request size."}, 413)
                raw = self.rfile.read(length)
                if not self.trusted():
                    return
                if self.headers.get("Content-Type", "").split(";")[0] != "application/json":
                    return self.reply({"error": "JSON content is required."}, 415)
                body = json.loads(raw)
                if not isinstance(body, dict):
                    raise ValueError("Expected an object.")
                if self.path == "/api/action":
                    companion.action(body.get("action"))
                elif self.path == "/api/settings":
                    companion.configure(body)
                elif self.path == "/api/radio":
                    return self.reply(companion.ask_engineer(body))
                elif self.path == "/api/session":
                    return self.reply(companion.session_action(body))
                elif self.path == "/api/ptt":
                    if body.get("action") == "start":
                        if not companion.settings["ptt_enabled"]:
                            raise ValueError("Enable push-to-talk in Settings first.")
                        companion.voice.clear()
                        companion.ptt.start()
                    elif body.get("action") == "stop":
                        companion.ptt.stop()
                    else:
                        raise ValueError("Choose start or stop for push-to-talk.")
                    return self.reply(companion.ptt.snapshot())
                elif self.path == "/api/references/import":
                    try:
                        result = companion.references.import_csv(body.get("path", ""), body.get("metadata"))
                        return self.reply({"ok": True, "reference": result})
                    except (OSError, ValueError) as exc:
                        return self.reply({"error": str(exc)[:250]}, 400)
                elif self.path == "/api/bridge":
                    companion.receive_bridge(body)
                elif self.path in ("/api/laps/delete", "/api/laps/restore"):
                    try:
                        lap = companion.edit_lap(body, restore=self.path.endswith("restore"))
                        return self.reply({"ok": True, "lap": lap})
                    except ValueError as exc:
                        return self.reply({"error": str(exc)}, 404 if str(exc).startswith("Unknown lap") else 400)
                else:
                    return self.reply({"error": "Not found"}, 404)
                self.reply({"ok": True})
            except (ValueError, TypeError, OverflowError) as exc:
                self.reply({"error": str(exc)[:250]}, 400)
            except Exception:
                logging.exception("Request failed")
                self.reply({"error": "The action could not be completed. See the companion log."}, 500)
    return Handler


def main():
    parser = argparse.ArgumentParser(description="Local Assetto Corsa Race Engineer")
    parser.add_argument("--port", type=int, default=8777)
    parser.add_argument("--data-dir", type=pathlib.Path, default=ROOT / "data")
    parser.add_argument("--demo", action="store_true")
    parser.add_argument("--no-browser", action="store_true")
    parser.add_argument("--mute", action="store_true")
    parser.add_argument("--engine", choices=("local", "pro_engineer"), default="pro_engineer")
    parser.add_argument("--start-engine", action="store_true", help="Launch the bundled native Pro Engineer process")
    args = parser.parse_args()
    args.data_dir.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(filename=args.data_dir / "companion.log", level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(message)s")
    companion = Companion(args.data_dir, demo=args.demo, voice=not args.mute, backend=args.engine)
    try:
        server = LocalServer(("127.0.0.1", args.port), handler_for(companion))
    except OSError:
        companion.close()
        if not args.no_browser:
            webbrowser.open("http://127.0.0.1:%s" % args.port)
        return 1
    server.timeout = 0.5
    companion.start()
    if args.start_engine and args.engine == "pro_engineer":
        try:
            companion.start_engine()
        except Exception as exc:
            logging.exception("Native engine did not start")
            companion.error = str(exc)[:250]
    if not args.no_browser:
        webbrowser.open("http://127.0.0.1:%s" % args.port)
    try:
        while not companion.stop_event.is_set():
            server.handle_request()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        companion.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
