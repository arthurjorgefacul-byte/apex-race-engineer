"""Driver workflows across lifecycle, requested speech and wheel settings.

Uses the real Companion/Engineer thread and local persistence, without starting
Assetto Corsa, a microphone or Windows speech.
"""
import copy
import json
import pathlib
import tempfile
import time
import unittest
from unittest import mock

import app
from tests.test_analysis import frame
from tests.test_companion import SilentVoice


class StableReader:
    def __init__(self):
        self.current = frame(0, .3, is_ai_controlled=False)
        self.last_error = ""
        self.closed = False

    def read(self):
        return copy.deepcopy(self.current)

    def close(self):
        self.closed = True


class FakePTT:
    def __init__(self, callback):
        self.callback = callback
        self.starting = self.listening = False
        self.closed = False

    def snapshot(self):
        return {"available": True, "starting": self.starting, "listening": self.listening}

    def stop(self):
        self.starting = self.listening = False

    def close(self):
        self.closed = True
        self.stop()


class V05FlowTests(unittest.TestCase):
    def test_natural_race_and_compound_questions_route_to_measured_answers(self):
        for question, topic in (("my fastest lap", "personal_best"), ("other drivers fastest laps", "others_best"),
                                ("who has the fastest lap", "race_best"), ("gap to the car ahead", "gap_ahead"),
                                ("put soft tyres on at the next stop", "change_compound"), ("medium", "change_compound")):
            with self.subTest(question=question):
                answer = self.companion.ask_engineer({"question": question})
                self.assertEqual(topic, answer["topic"])

    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.patches = [mock.patch.object(app, "Voice", SilentVoice),
                        mock.patch.object(app, "ACReader", StableReader),
                        mock.patch.object(app, "PushToTalk", FakePTT)]
        for patch in self.patches:
            patch.start()
        self.companion = app.Companion(self.folder.name, voice=False, backend="local")

    def tearDown(self):
        self.companion.close()
        for patch in reversed(self.patches):
            patch.stop()
        self.folder.cleanup()

    def wait_for(self, predicate, timeout=1.5):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if predicate():
                return
            time.sleep(.01)
        self.fail("Companion did not reach the expected state: " + str(self.companion.snapshot().get("error")))

    def speech_context(self, **overrides):
        c = self.companion
        c.telemetry = frame(2, .25, **overrides)
        c.analysis = {"session": {"id": "driver-session"}, "laps": [], "messages": [],
                      "coaching": {}, "fuel": {"consumption_l_per_lap": 2.5}}
        c.configure({"voice": True, "quiet": True, "verbosity": "less"})
        c.last_auto_voice = time.time()
        return c

    def test_homepage_connected_telemetry_does_not_record_before_start(self):
        c = self.companion
        c.start()
        self.wait_for(lambda: c.snapshot()["connected"])
        time.sleep(.08)
        state = c.snapshot()
        self.assertEqual("home", state["lifecycle"]["phase"])
        self.assertFalse(state["analysis"]["session"]["recording"])
        self.assertIsNone(state["analysis"]["session"]["id"])
        self.assertEqual([], c.engineer.list_sessions())

    def test_explicit_start_records_and_end_returns_home_with_persisted_debrief(self):
        c = self.companion
        c.start()
        self.wait_for(lambda: c.snapshot()["connected"])
        c.session_action({"action": "start"})
        self.wait_for(lambda: bool(c.snapshot()["lifecycle"]["active_session_id"]))
        self.assertEqual("session", c.snapshot()["lifecycle"]["phase"])
        with c.lock:
            # Feed one partial and one complete clean lap through the real
            # recorder while excluding the background reader for this block.
            for count, positions in ((0, range(70, 100)), (1, range(0, 100)), (2, range(0, 1))):
                for i in positions:
                    current = frame(count, i / 100, is_ai_controlled=False)
                    c.telemetry = current
                    c._update_analysis(current)
            c.reader.current = current
            result = c.session_action({"action": "end"})
        self.assertEqual("debrief", result["phase"])
        self.assertFalse(c.snapshot()["analysis"]["session"]["recording"])
        self.assertIsNone(result["active_session_id"])
        self.assertEqual(1, result["last_debrief"]["metrics"]["clean_laps"])
        self.assertEqual(60000, result["last_debrief"]["metrics"]["best_lap_ms"])
        saved = json.loads((pathlib.Path(self.folder.name) / "debriefs" / "latest.json").read_text(encoding="utf-8"))
        self.assertEqual(result["last_debrief"]["id"], saved["id"])
        time.sleep(.09)
        self.assertFalse(c.lifecycle.active)
        self.assertEqual("debrief", c.snapshot()["lifecycle"]["phase"])

    def test_start_can_arm_before_connected_and_cancel_does_not_record(self):
        c = self.companion
        armed = c.session_action({"action": "start"})
        self.assertEqual("home", armed["phase"])
        self.assertTrue(armed["armed"])
        cancelled = c.session_action({"action": "end"})
        self.assertFalse(cancelled["armed"])
        c.start()
        self.wait_for(lambda: c.snapshot()["connected"])
        self.assertFalse(c.lifecycle.active)
        self.assertEqual([], c.engineer.list_sessions())

    def test_deleted_lap_does_not_reappear_in_session_debrief(self):
        c = self.companion
        c.telemetry = frame(0, .7, is_ai_controlled=False)
        c.session_action({"action": "start"})
        for count, positions in ((0, range(71, 100)), (1, range(0, 100)), (2, range(0, 1))):
            for i in positions:
                c.telemetry = frame(count, i / 100, is_ai_controlled=False)
                c._update_analysis(c.telemetry)
        lap = c.analysis["laps"][0]
        session_id = c.analysis["session"]["id"]
        c.edit_lap({"lap_id": lap["id"], "session_id": session_id})
        self.assertEqual([], c.analysis["laps"])
        result = c.session_action({"action": "end"})
        self.assertEqual(0, result["last_debrief"]["metrics"]["clean_laps"])
        self.assertIsNone(result["last_debrief"]["metrics"]["best_lap_ms"])

    def test_thread_ends_after_disconnect_grace_and_does_not_auto_resume(self):
        c = self.companion
        clock = [100.0]
        c.lifecycle.clock = lambda: clock[0]
        c.session_action({"action": "start"})
        c.start()
        self.wait_for(lambda: c.lifecycle.active)
        c.reader.current = None
        self.wait_for(lambda: c.snapshot()["telemetry"] is None)
        self.wait_for(lambda: c.lifecycle._missing_since is not None)
        clock[0] += 2.9
        time.sleep(.09)
        self.assertTrue(c.lifecycle.active)
        clock[0] += .2
        self.wait_for(lambda: not c.lifecycle.active)
        self.assertEqual("debrief", c.snapshot()["lifecycle"]["phase"])
        c.reader.current = frame(0, .3, is_ai_controlled=False)
        self.wait_for(lambda: c.snapshot()["connected"])
        self.assertFalse(c.lifecycle.active)

    def test_requested_answers_speak_stopped_paused_or_unknown_ai_despite_quiet(self):
        for overrides in ({"speed_kmh": 0, "is_ai_controlled": False},
                          {"status": "paused", "is_ai_controlled": False},
                          {"is_ai_controlled": None, "steer": None, "acc_g": None}):
            with self.subTest(overrides=overrides):
                c = self.speech_context(**overrides)
                c.reply_started_at = 0
                c.voice.calls.clear()
                result = c.ask_engineer({"topic": "fuel"})
                self.assertEqual("started", result["speech_status"])
                self.assertEqual([result["text"]], c.voice.calls)
                c._schedule_voice(c.telemetry)
                self.assertEqual([result["text"]], c.voice.calls)

    def test_global_mute_respected_for_requested_answers(self):
        c = self.speech_context(status="paused")
        c.configure({"voice": False})
        result = c.ask_engineer({"topic": "fuel"})
        self.assertEqual("muted", result["speech_status"])
        c._dispatch_replies()
        self.assertEqual([], c.voice.calls)
        self.assertEqual([], list(c.reply_queue))

    def test_requested_answer_waits_for_microphone_release_then_speaks(self):
        c = self.speech_context(speed_kmh=0, is_ai_controlled=None)
        for flag in ("starting", "listening"):
            with self.subTest(flag=flag):
                c.reply_started_at = 0
                setattr(c.ptt, flag, True)
                result = c.ask_engineer({"topic": "fuel"})
                self.assertEqual("queued", result["speech_status"])
                self.assertEqual([], c.voice.calls)
                c.ptt.stop()
                c._dispatch_replies()
                self.assertEqual("started", c.last_reply["speech_status"])
                self.assertEqual([result["text"]], c.voice.calls)
                c.voice.calls.clear()

    def test_compound_voice_topic_routes_to_race_director_with_explicit_compound(self):
        c = self.speech_context()
        with mock.patch.object(c.race, "ask", return_value={"topic": "change_compound", "text": "Medium selected.", "available": True}) as ask:
            result = c.ask_engineer({"topic": "compound_medium"})
        self.assertEqual("change_compound", result["topic"])
        self.assertEqual("change_compound", ask.call_args.args[0])
        self.assertEqual("Medium", ask.call_args.kwargs["compound"])
        self.assertEqual(["Medium selected."], c.voice.calls)

    def test_wheel_binding_persists_identity_and_keyboard_changes_replace_it(self):
        c = self.companion
        binding = {"kind": "joystick", "device_id": 0, "button": 5,
                   "device_name": "G29 Driving Force Racing Wheel", "vendor": 1133, "product": 49743}
        c.configure({"ptt_enabled": True, "ptt_binding": binding})
        self.assertEqual(binding, c.snapshot()["settings"]["ptt_binding"])
        saved = json.loads(c.settings_path.read_text(encoding="utf-8"))
        self.assertEqual(binding, saved["ptt_binding"])
        c.configure({"ptt_key": "Space"})
        self.assertEqual({"kind": "keyboard", "key": "Space"}, c.settings["ptt_binding"])
        c.configure({"ptt_binding": {"kind": "keyboard", "key": "F24"}})
        self.assertEqual("F24", c.settings["ptt_key"])

    def test_invalid_wheel_bindings_are_rejected_without_overwriting_valid_setting(self):
        c = self.companion
        valid = {"kind": "joystick", "device_id": 0, "button": 2, "vendor": 1133, "product": 49743}
        c.configure({"ptt_binding": valid})
        expected = copy.deepcopy(c.settings["ptt_binding"])
        for change in ({"device_id": True}, {"button": 32}, {"device_id": -1}, {"vendor": 65536}, {"product": 4.5}, {"kind": "mouse"}):
            with self.subTest(change=change):
                with self.assertRaises(ValueError):
                    c.configure({"ptt_binding": {**valid, **change}})
                self.assertEqual(expected, c.settings["ptt_binding"])


if __name__ == "__main__":
    unittest.main()
