import json
import pathlib
import tempfile
import time
import unittest
from unittest import mock

import app
from engineer.pro_engineer import ProEngineerReader
from tests.test_companion import SilentVoice, MissingReader
from tests.test_pro_engineer import golden


class ManagedDemo:
    def __init__(self, running=True, demo=True):
        self.running, self.demo = running, demo

    def snapshot(self):
        return {"running": self.running, "demo": self.demo}

    def stop(self):
        self.running = False


class NativeIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.voice = mock.patch.object(app, "Voice", SilentVoice)
        self.native = mock.patch.object(app, "ACReader", MissingReader)
        self.voice.start()
        self.native.start()
        self.companion = app.Companion(pathlib.Path(self.temp.name), demo=True, voice=False, backend="pro_engineer")
        reader = ProEngineerReader(demo=True, port=0, process_check=lambda: False)
        for kind in ("reading", "summary"):
            reader.accept(json.dumps(golden(kind)).encode())
        self.companion.upstream_reader = reader
        self.companion.runtime = ManagedDemo()
        # Recording now requires an explicit driver session start. Arm before
        # the managed native telemetry arrives on the companion thread.
        self.companion.session_action({"action": "start"})

    def tearDown(self):
        self.companion.close()
        self.voice.stop()
        self.native.stop()
        self.temp.cleanup()

    def test_running_native_demo_messages_are_reused_and_attributed(self):
        self.companion.start()
        time.sleep(0.13)
        state = self.companion.snapshot()
        self.assertTrue(state["connected"])
        self.assertEqual(state["telemetry"]["backend"], "pro_engineer")
        self.assertEqual(state["engine"]["upstream"]["version"], "0.5.1")
        self.assertTrue(state["analysis"]["messages"])
        self.assertTrue(all(m["source"] == "AC Pro Engineer" for m in state["analysis"]["messages"]))
        self.assertTrue(all(m["id"].startswith("pro:") for m in state["analysis"]["messages"]))
        self.assertIn("rolling", state["analysis"]["traces"])

    def test_unmanaged_feed_is_not_silently_accepted(self):
        self.companion.runtime.running = False
        self.companion.start()
        time.sleep(0.12)
        state = self.companion.snapshot()
        self.assertFalse(state["connected"])
        self.assertEqual(state["engine"]["backend"], "pro_engineer")
        self.assertIsNone(state["telemetry"])

    def test_managed_demo_cannot_be_accepted_as_live(self):
        self.companion.mode = "live"
        self.companion.start()
        time.sleep(0.12)
        self.assertFalse(self.companion.snapshot()["connected"])

    def test_explicit_local_fallback_stops_owned_engine_and_clears_reference(self):
        self.companion.action("engine_local")
        state = self.companion.snapshot()
        self.assertEqual(state["engine"]["backend"], "local")
        self.assertFalse(state["engine"]["runtime"]["running"])
        self.assertIsNone(state["analysis"]["reference"])
        self.assertIsNone(state["telemetry"])


if __name__ == "__main__":
    unittest.main()
