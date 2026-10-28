import hashlib
import json
import pathlib
import tempfile
import unittest
from unittest import mock

from engineer.pro_engineer_runtime import ProEngineerRuntime


class FakeProcess:
    pid = 4242

    def __init__(self):
        self.code = None
        self.terminated = False

    def poll(self):
        return self.code

    def terminate(self):
        self.terminated = True
        self.code = 0

    def wait(self, timeout=None):
        return self.code


class RuntimeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = pathlib.Path(self.temp.name)
        native = self.root / "third_party/pro_engineer"
        (native / "bin").mkdir(parents=True)
        self.binary = native / "bin/ac_pro_engineer.exe"
        self.binary.write_bytes(b"mocked native executable - never run")
        (native / "PROVENANCE.json").write_text(json.dumps({"binary_sha256": hashlib.sha256(self.binary.read_bytes()).hexdigest()}))
        self.config = self.root / "native-config/config.json"
        self.runtime = ProEngineerRuntime(self.root, self.root / "data", config_path=self.config)

    def tearDown(self):
        self.runtime.stop()
        self.temp.cleanup()

    def test_config_keeps_existing_values_and_backups_and_uses_loopback_only(self):
        self.config.parent.mkdir()
        old = {"language": "Russian", "target_hot_pressure_front": 21.2, "overlay": {"custom": 4}, "lan": {"custom": 6}}
        self.config.write_text(json.dumps(old))
        config = self.runtime.configure()
        self.assertEqual(config["language"], "Russian")
        self.assertEqual(config["target_hot_pressure_front"], 21.2)
        self.assertEqual(config["overlay"]["custom"], 4)
        self.assertEqual(config["overlay"]["broadcast_to"], "127.0.0.1:8778")
        self.assertFalse(config["lan"]["announce"])
        self.assertEqual(json.loads(self.runtime.backup_path.read_text()), old)

    def test_invalid_config_and_modified_binary_are_left_untouched(self):
        self.config.parent.mkdir()
        self.config.write_text("{invalid")
        with self.assertRaises(ValueError):
            self.runtime.configure()
        self.assertEqual(self.config.read_text(), "{invalid")
        self.binary.write_bytes(b"modified")
        with self.assertRaisesRegex(ValueError, "verified"):
            self.runtime.verify()

    def test_process_launch_is_pinned_demo_explicit_and_stop_is_owned_only(self):
        process = FakeProcess()
        with mock.patch("engineer.pro_engineer_runtime.another_engine_running", return_value=False), mock.patch("engineer.pro_engineer_runtime.subprocess.Popen", return_value=process) as spawn:
            result = self.runtime.start(demo=True, hidden=True)
            self.assertTrue(result["running"])
            self.assertIn("--demo", spawn.call_args.args[0])
            self.assertEqual(spawn.call_args.args[0][0], str(self.binary))
            self.runtime.start(demo=True)
            self.assertEqual(spawn.call_count, 1)
            self.runtime.stop()
            self.assertTrue(process.terminated)
            self.assertFalse(self.runtime.snapshot()["running"])

    def test_unrelated_native_instance_is_neither_reconfigured_nor_stopped(self):
        with mock.patch("engineer.pro_engineer_runtime.another_engine_running", return_value=True), mock.patch("engineer.pro_engineer_runtime.subprocess.Popen") as spawn:
            with self.assertRaisesRegex(ValueError, "already running"):
                self.runtime.start()
            self.assertFalse(self.config.exists())
            self.assertFalse(spawn.called)
            self.runtime.stop()


if __name__ == "__main__":
    unittest.main()
