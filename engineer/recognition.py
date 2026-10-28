"""On-demand Windows push-to-talk. No microphone is opened before Start."""
import json
import os
import pathlib
import subprocess
import threading
import time


class PushToTalk:
    TOPICS = {"fuel", "tyres", "pace", "next_corner", "corners", "last_lap", "strategy", "energy",
              "track_limits", "status", "quiet", "more_updates", "less_updates",
              "personal_best", "race_best", "others_best", "gap_ahead", "gap_behind", "gap_leader",
              "change_compound", "feedback"} | {"corner_"+str(i) for i in range(1, 11)} | {
                  "compound_" + name for name in ("soft", "medium", "hard", "intermediate", "wet")}
    def __init__(self, on_topic):
        self.on_topic = on_topic
        self.process = None
        self.lock = threading.RLock()
        self.closed = False
        self.listening = False
        self.requested = False
        self.error = None
        self.last_text = ""
        self.language = "English (Windows en-US)"
        self.deadline = None

    def _send(self, action):
        if self.process and self.process.poll() is None:
            self.process.stdin.write(json.dumps({"action": action}) + "\n")
            self.process.stdin.flush()

    def start(self):
        with self.lock:
            if self.closed:
                raise ValueError("Push-to-talk has stopped.")
            if os.name != "nt":
                raise ValueError("Push-to-talk requires Windows speech recognition.")
            if self.process is None or self.process.poll() is not None:
                self.process = subprocess.Popen(
                    ["powershell.exe", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
                     "-File", str(pathlib.Path(__file__).with_name("recognition_worker.ps1"))],
                    stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                    text=True, encoding="utf-8", bufsize=1,
                    creationflags=subprocess.CREATE_NO_WINDOW)
                threading.Thread(target=self._read, args=(self.process,), daemon=True, name="engineer-ptt").start()
            self.requested = True
            self.error = None
            self._send("start")
            if self.deadline:
                self.deadline.cancel()
            current = self.process
            def expire():
                with self.lock:
                    same = current is self.process
                if same:
                    self.stop()
            self.deadline = threading.Timer(12, expire)
            self.deadline.daemon = True
            self.deadline.start()

    def stop(self):
        with self.lock:
            self.requested = False
            self.listening = False
            if self.deadline:
                self.deadline.cancel()
            process, self.process = self.process, None
        # Some Windows audio drivers delay SAPI cancellation indefinitely on
        # background noise. Releasing PTT ends only our input worker process,
        # which gives the microphone a deterministic lifetime.
        if process:
            try:
                if process.poll() is None:
                    process.terminate()
                process.wait(timeout=.15)
            except (OSError, ValueError, subprocess.TimeoutExpired):
                try:
                    process.kill()
                except OSError:
                    pass

    def _read(self, process):
        try:
            for line in process.stdout:
                if len(line) > 4096:
                    continue
                try:
                    event = json.loads(line)
                except ValueError:
                    continue
                if not isinstance(event, dict):
                    continue
                topic = None
                with self.lock:
                    if process is not self.process or self.closed:
                        break
                    kind = event.get("event")
                    if kind == "listening":
                        self.listening = self.requested
                    elif kind == "stopped":
                        self.listening = False
                        self.requested = False
                    elif kind == "recognized":
                        self.last_text = str(event.get("text", ""))[:300]
                        topic = str(event.get("topic", ""))[:40]
                    elif kind == "error":
                        self.error = str(event.get("message", "Voice recognition unavailable."))[:250]
                        self.listening = False
                        self.requested = False
                if topic in self.TOPICS:
                    self.stop()
                    self.on_topic(topic)
        except (OSError, ValueError):
            pass
        finally:
            with self.lock:
                if process is self.process:
                    self.listening = False
                    self.requested = False

    def snapshot(self):
        with self.lock:
            return {"available": os.name == "nt", "listening": self.listening, "starting": self.requested and not self.listening,
                    "error": self.error, "last_text": self.last_text, "language": self.language,
                    "note": "Hold the configured key, speak a short engineer command, then release. Microphone is used only during push-to-talk."}

    def close(self):
        with self.lock:
            if self.closed:
                return
            self.closed = True
            if self.deadline:
                self.deadline.cancel()
            self.requested = self.listening = False
            process, self.process = self.process, None
        if process:
            try:
                if process.poll() is None:
                    process.stdin.write('{"action":"close"}\n')
                    process.stdin.flush()
                    try:
                        process.wait(timeout=.12)
                    except subprocess.TimeoutExpired:
                        process.terminate()
                process.wait(timeout=.12)
            except (OSError, ValueError, subprocess.TimeoutExpired):
                try:
                    process.kill()
                except OSError:
                    pass
