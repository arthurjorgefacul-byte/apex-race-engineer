"""Exercise speech scheduling and owned-worker cleanup without audio playback."""
import io
import json
import queue
import subprocess
import threading
import time
import unittest
from unittest import mock

from engineer.voice import Voice


class EventLines:
    def __init__(self):
        self.queue = queue.Queue()

    def emit(self, event):
        self.queue.put(json.dumps(event) + "\n")

    def finish(self):
        self.queue.put(None)

    def __iter__(self):
        return self

    def __next__(self):
        line = self.queue.get()
        if line is None:
            raise StopIteration
        return line


class FakeInput:
    def __init__(self, process):
        self.process = process
        self.closed = False
        self.pending = ""

    def write(self, text):
        if self.closed:
            raise BrokenPipeError("closed worker")
        self.pending += text
        while "\n" in self.pending:
            line, self.pending = self.pending.split("\n", 1)
            self.process.command(json.loads(line))

    def flush(self):
        pass

    def close(self):
        self.closed = True


class FakeProcess:
    def __init__(self):
        self.stdout = EventLines()
        self.stderr = io.StringIO("")
        self.stdin = FakeInput(self)
        self.commands = []
        self.returncode = None
        self.active = None
        self.terminated = 0
        self.killed = 0
        self.stdout.emit({"event": "ready"})

    def command(self, command):
        self.commands.append(command)
        if command["action"] == "clear":
            if self.active is not None:
                self.stdout.emit({"event": "cancelled", "id": self.active})
                self.active = None
        elif command["action"] == "speak":
            self.active = command["id"]
            self.stdout.emit({"event": "started", "id": self.active})
        elif command["action"] == "close":
            self.returncode = 0
            self.stdout.finish()

    def complete(self):
        self.stdout.emit({"event": "finished", "id": self.active})
        self.active = None

    def poll(self):
        return self.returncode

    def terminate(self):
        self.terminated += 1
        self.returncode = -15
        self.stdout.finish()

    def kill(self):
        self.killed += 1
        self.returncode = -9
        self.stdout.finish()

    def wait(self, timeout=None):
        if self.returncode is None:
            raise subprocess.TimeoutExpired("fake worker", timeout)
        return self.returncode


class VoiceTests(unittest.TestCase):
    def setUp(self):
        self.processes = []
        self.factory_entered = threading.Event()
        self.factory_release = None

        def factory(*args, **kwargs):
            self.factory_entered.set()
            if self.factory_release is not None:
                self.factory_release.wait(timeout=2)
            process = FakeProcess()
            self.processes.append(process)
            return process

        self.patch = mock.patch("engineer.voice.subprocess.Popen", side_effect=factory)
        self.patch.start()
        self.voice = Voice()
        self.voice.available = True

    def tearDown(self):
        if self.factory_release is not None:
            self.factory_release.set()
        self.voice.close()
        self.patch.stop()

    def wait_for(self, predicate, timeout=1):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if predicate():
                return
            time.sleep(0.005)
        self.fail("Speech worker did not reach the expected state")

    def speaking_texts(self):
        return [item["text"] for process in self.processes for item in process.commands
                if item["action"] == "speak"]

    def first_phrase(self):
        self.assertTrue(self.voice.say("First corner advice"))
        self.wait_for(lambda: self.speaking_texts() == ["First corner advice"])
        return self.processes[0]

    def test_multiple_messages_reuse_one_persistent_worker(self):
        process = self.first_phrase()
        self.assertTrue(self.voice.say("Second corner advice"))
        self.assertEqual(self.speaking_texts(), ["First corner advice"])
        process.complete()
        self.wait_for(lambda: len(self.speaking_texts()) == 2)
        self.assertEqual(len(self.processes), 1)
        self.assertTrue(self.voice.snapshot()["worker_ready"])

    def test_forced_radio_interrupts_and_discards_queued_coaching(self):
        process = self.first_phrase()
        self.voice.say("Old queued corner advice")
        self.assertTrue(self.voice.say("Radio check", force=True))
        self.wait_for(lambda: "Radio check" in self.speaking_texts())
        self.assertNotIn("Old queued corner advice", self.speaking_texts())
        actions = [item["action"] for item in process.commands]
        self.assertIn("clear", actions)
        self.assertTrue(process.commands[-1]["interrupt"])
        process.complete()
        self.wait_for(lambda: not self.voice.snapshot()["speaking"])

    def test_mute_cancels_audio_but_explicit_radio_is_available(self):
        process = self.first_phrase()
        self.voice.enabled = False
        self.wait_for(lambda: any(item["action"] == "clear" for item in process.commands))
        self.assertFalse(self.voice.say("Unrequested coaching"))
        self.assertTrue(self.voice.say("Explicit radio", force=True))
        self.wait_for(lambda: "Explicit radio" in self.speaking_texts())

    def test_clear_removes_current_and_pending_calls(self):
        process = self.first_phrase()
        self.voice.say("Pending advice")
        self.voice.clear()
        self.wait_for(lambda: any(item["action"] == "clear" for item in process.commands))
        self.assertEqual(self.voice.snapshot()["queued"], 0)
        self.assertFalse(self.voice.snapshot()["speaking"])
        self.assertEqual(self.speaking_texts(), ["First corner advice"])

    def test_stale_queued_advice_is_not_spoken(self):
        process = self.first_phrase()
        with self.voice._condition:
            self.voice.say("Old advice")
            self.voice._pending[-1]["queued_at"] -= self.voice.MAX_AGE_SECONDS + 1
        process.complete()
        self.wait_for(lambda: self.voice.snapshot()["queued"] == 0)
        self.assertNotIn("Old advice", self.speaking_texts())

    def test_queue_is_bounded_and_force_always_has_priority(self):
        self.first_phrase()
        for index in range(self.voice.MAX_QUEUE):
            self.assertTrue(self.voice.say("Queued " + str(index)))
        self.assertFalse(self.voice.say("Overflow"))
        self.assertTrue(self.voice.say("Fuel warning", force=True))
        self.wait_for(lambda: "Fuel warning" in self.speaking_texts())
        self.assertNotIn("Overflow", self.speaking_texts())

    def test_close_is_prompt_and_reaps_only_its_owned_worker(self):
        process = self.first_phrase()
        other = FakeProcess()
        started = time.monotonic()
        self.voice.close()
        self.assertLess(time.monotonic() - started, 0.35)
        self.assertIsNotNone(process.poll())
        self.assertTrue(any(item["action"] == "close" for item in process.commands))
        self.assertEqual(process.terminated, 0)
        self.assertIsNone(other.poll())
        self.assertFalse(self.voice._thread.is_alive())
        self.assertFalse(self.voice.say("After close", force=True))
        self.voice.close()
        self.assertIsNone(other.poll())

    def test_close_during_worker_start_does_not_leave_orphan(self):
        self.factory_release = threading.Event()
        self.voice.say("Startup race")
        self.assertTrue(self.factory_entered.wait(timeout=1))
        started = time.monotonic()
        self.voice.close()
        self.assertLess(time.monotonic() - started, 0.35)
        self.factory_release.set()
        self.wait_for(lambda: self.processes and self.processes[0].poll() is not None)
        self.wait_for(lambda: not self.voice._thread.is_alive())

    def test_dead_worker_is_replaced_for_a_new_request(self):
        process = self.first_phrase()
        process.terminate()
        self.wait_for(lambda: self.voice._process is None)
        self.voice.say("A new request")
        self.wait_for(lambda: "A new request" in self.speaking_texts())
        self.assertEqual(len(self.processes), 2)
        self.assertIsNotNone(process.poll())


if __name__ == "__main__":
    unittest.main()
