"""Live radio integration: actual event engine, local HTTP and silent output."""
import time
import unittest
from unittest import mock
from tests import test_companion as companion_tests
from tests.test_analysis import frame


class RadioTests(unittest.TestCase):
    setUp = companion_tests.CompanionTests.setUp
    tearDown = companion_tests.CompanionTests.tearDown
    request = companion_tests.CompanionTests.request
    json_request = companion_tests.CompanionTests.json_request

    def context(self):
        c = self.companion
        c.telemetry = frame(2, .1, is_ai_controlled=False)
        c.analysis = {"session": {"id": "one", "current_lap_eligible": True, "current_lap_flags": []},
                      "fuel": {"consumption_l_per_lap": 2.5}, "coaching": {}, "messages": []}
        c.settings["voice"] = True; c.voice.enabled = True
        return c

    def test_first_requested_answer_speaks_without_waiting_for_a_safe_gap(self):
        c = self.context()
        status, result = self.json_request("/api/radio", body={"question": "how much fuel do I have?"})
        self.assertEqual(status, 200); self.assertEqual(result["topic"], "fuel")
        self.assertIn("litres", result["text"])
        c.radio_state = c.live_engineer.update(c.telemetry, c.analysis)
        c.straight_since = time.time()-1; c.last_auto_voice = 0
        c._schedule_voice(c.telemetry)
        self.assertIn(result["text"], c.voice.calls)

    def test_more_updates_answer_survives_configure_generation(self):
        c = self.context()
        status, result = self.json_request("/api/radio", body={"topic": "more_updates"})
        self.assertEqual(status, 200); self.assertEqual(c.settings["verbosity"], "more")
        c.radio_state = c.live_engineer.update(c.telemetry, c.analysis)
        c._schedule_voice(c.telemetry)
        self.assertIn(result["text"], c.voice.calls)
        self.assertEqual("started", c.last_reply["speech_status"])

    def test_track_limit_call_interrupts_while_turning_but_respects_mute(self):
        c = self.context(); clock = [100.]
        c.live_engineer._clock = lambda: clock[0]
        f = frame(2, .2, wheels_out=4, steer=.5)
        with mock.patch("app.time.time", side_effect=lambda: clock[0]):
            c.radio_state = c.live_engineer.update(f, c.analysis); c._schedule_voice(f)
            clock[0] += .3
            c.radio_state = c.live_engineer.update(f, c.analysis); c._schedule_voice(f)
        self.assertTrue(any("track limits" in text.lower() for text in c.voice.calls))
        self.assertFalse(any("lap is invalid" in text.lower() for text in c.voice.calls))
        c.voice.calls.clear(); c.settings["voice"] = False
        c.radio_state["new_events"] = [{"id": "urgent-muted", "kind": "track_limits", "text": "Off track", "immediate": True, "timestamp": 101., "expires_at": 120.}]
        with mock.patch("app.time.time", return_value=101.): c._schedule_voice(f)
        self.assertEqual(c.voice.calls, [])

    def test_quiet_also_suppresses_upstream_routine_calls(self):
        c = self.context(); c.settings.update(backend="pro_engineer", quiet=True)
        c.analysis["messages"] = [{"id": "upstream", "kind": "fuel", "text": "Routine native advice"}]
        c._schedule_voice(c.telemetry)
        self.assertFalse(any("Routine native" in e.get("text", "") for e in c.radio_queue))

    def test_reset_clears_visible_reply_and_old_queue_immediately(self):
        c = self.context(); c.ask_engineer({"topic": "fuel"})
        self.assertIsNotNone(c.last_reply)
        c.action("reset")
        self.assertIsNone(c.last_reply); self.assertEqual(list(c.radio_history), []); self.assertEqual(c.radio_queue, [])

    def test_reference_catalog_and_sourced_corner_principles(self):
        status, result = self.json_request("/api/references")
        self.assertEqual(status, 200); self.assertTrue(result["references"])
        self.assertTrue(result["knowledge"]["sources"])
        self.assertNotIn("trace", result["references"][0])
        self.assertTrue(result["references"][0]["eligibility"]["context_only"])
        status, full = self.json_request("/api/references/"+result["references"][0]["id"])
        self.assertEqual(status, 200); self.assertGreater(len(full["trace"]), 100)

    def test_unknown_question_returns_supported_commands(self):
        status, result = self.json_request("/api/radio", body={"question": "tell me tomorrow's weather"})
        self.assertEqual(status, 200); self.assertFalse(result["available"])
        self.assertTrue(result["suggested_commands"])

    def test_requested_corner_is_specific_and_source_guidance_is_attached(self):
        c = self.context()
        c.analysis["coaching"]["comparison_available"] = True
        c.analysis["corners"] = [{"number": 5, "time_loss_s": .3, "exit_speed_kmh": 120,
                                  "reference_exit_speed_kmh": 125, "full_throttle_delta_m": 15}]
        status, result = self.json_request("/api/radio", body={"question": "tell me about corner 5"})
        self.assertEqual(status, 200); self.assertEqual(result["topic"], "corner_5")
        self.assertIn("0.30 seconds lost", result["text"]); self.assertIn("120", result["text"])
        self.assertTrue(result["guidance"])
        status, result = self.json_request("/api/radio", body={"topic": "corner_9"})
        self.assertFalse(result["available"])


if __name__ == "__main__": unittest.main()
