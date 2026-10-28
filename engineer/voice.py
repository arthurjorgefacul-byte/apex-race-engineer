"""Offline speech through one owned, interruptible Windows speech worker."""
import collections
import json
import os
import pathlib
import subprocess
import threading
import time


class Voice:
    MAX_QUEUE = 3
    MAX_AGE_SECONDS = 12
    START_TIMEOUT_SECONDS = 5
    SPEECH_TIMEOUT_SECONDS = 35

    def __init__(self):
        self._enabled = True
        self.available = os.name == "nt"
        self.error = None
        self.last_text = ""
        self.last_spoken_at = None
        self._pending = collections.deque()
        self._condition = threading.Condition()
        self._closed = False
        self._process = None
        self._ready = False
        self._started_at = 0.0
        self._active = None
        self._next_id = 0
        self._needs_clear = False
        self._stderr = ""
        self._thread = threading.Thread(target=self._run, daemon=True, name="engineer-voice")
        self._thread.start()

    @property
    def enabled(self):
        return self._enabled

    @enabled.setter
    def enabled(self, value):
        self._enabled = bool(value)
        if not self._enabled:
            self.clear()

    def clear(self):
        """Cancel pending/current speech without blocking a telemetry callback."""
        with self._condition:
            self._pending.clear()
            self._active = None
            self._needs_clear = self._process is not None
            self._condition.notify_all()

    def say(self, text, force=False):
        text = str(text).strip()[:420]
        if not text or not self.available:
            return False
        with self._condition:
            if self._closed or (not force and not self.enabled):
                return False
            if force:
                # User requests and urgent calls take precedence over queued
                # coaching and interrupt an already speaking noncritical call.
                self._pending.clear()
                self._active = None
                self._needs_clear = self._process is not None
            elif len(self._pending) >= self.MAX_QUEUE:
                return False
            elif ((self._active and self._active["text"] == text)
                  or any(item["text"] == text for item in self._pending)):
                return False
            self._next_id += 1
            self._pending.append({"id": self._next_id, "text": text,
                                  "force": bool(force), "queued_at": time.monotonic()})
            self._condition.notify_all()
            return True

    def _read_events(self, process):
        try:
            for line in process.stdout:
                if len(line) > 4096:
                    continue
                try:
                    event = json.loads(line)
                except (ValueError, TypeError):
                    continue
                if not isinstance(event, dict):
                    continue
                with self._condition:
                    if process is not self._process:
                        return
                    kind = event.get("event")
                    if kind == "ready":
                        self._ready = True
                        self.error = None
                    elif self._active and event.get("id") == self._active["id"]:
                        if kind == "started":
                            self.last_text = self._active["text"]
                            self.last_spoken_at = time.time()
                        elif kind in ("finished", "cancelled", "error"):
                            if kind == "error":
                                self.error = "Windows speech could not complete the message."
                            self._active = None
                    self._condition.notify_all()
        except (OSError, ValueError):
            pass

    def _read_errors(self, process):
        try:
            for line in process.stderr:
                with self._condition:
                    if process is self._process:
                        self._stderr = (self._stderr + line)[-600:]
        except (OSError, ValueError):
            pass

    @staticmethod
    def _dispose_process(process, graceful=False):
        if process is None:
            return
        try:
            if graceful and process.poll() is None:
                try:
                    process.wait(timeout=0.08)
                except subprocess.TimeoutExpired:
                    pass
            if process.poll() is None:
                process.terminate()
            try:
                process.wait(timeout=0.1)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=0.05)
        except (OSError, ValueError, subprocess.TimeoutExpired):
            pass
        for stream in (process.stdin,):
            try:
                if stream:
                    stream.close()
            except (OSError, ValueError):
                pass

    def _start_worker(self):
        worker = pathlib.Path(__file__).with_name("voice_worker.ps1")
        process = subprocess.Popen(
            ["powershell.exe", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
             "-File", str(worker), "-Persistent"],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, encoding="utf-8", bufsize=1,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        with self._condition:
            if self._closed:
                keep = False
            else:
                keep = True
                self._process = process
                self._ready = False
                self._started_at = time.monotonic()
                self._stderr = ""
        if not keep:
            self._dispose_process(process)
            return
        threading.Thread(target=self._read_events, args=(process,), daemon=True,
                         name="engineer-speech-events").start()
        threading.Thread(target=self._read_errors, args=(process,), daemon=True,
                         name="engineer-speech-errors").start()

    def _detach(self, process, error=None):
        with self._condition:
            if self._process is process:
                self._process = None
                self._ready = False
                self._active = None
                self._needs_clear = False
                if error and not self._closed:
                    self.error = str(error)[:300]

    def _run(self):
        try:
            while True:
                start = False
                discard = None
                command = None
                with self._condition:
                    if self._closed:
                        break
                    now = time.monotonic()
                    while self._pending and (now - self._pending[0]["queued_at"] > self.MAX_AGE_SECONDS
                                             or (not self.enabled and not self._pending[0]["force"])):
                        self._pending.popleft()
                    process = self._process
                    if process is not None and (process.poll() is not None
                            or (not self._ready and now - self._started_at > self.START_TIMEOUT_SECONDS)):
                        discard = process
                        self._detach(process, self._stderr.strip() or "Windows speech worker stopped or timed out.")
                    elif self._active and now - self._active["sent_at"] > self.SPEECH_TIMEOUT_SECONDS:
                        self._active = None
                        self._needs_clear = True
                        self.error = "Windows speech timed out."
                    elif process is None and self._pending:
                        start = True
                    elif process is not None and self._ready:
                        if self._needs_clear:
                            self._needs_clear = False
                            command = {"action": "clear"}
                        elif not self._active and self._pending:
                            self._active = self._pending.popleft()
                            self._active["sent_at"] = now
                            command = {"action": "speak", "id": self._active["id"],
                                       "text": self._active["text"], "interrupt": self._active["force"]}
                    if not start and discard is None and command is None:
                        self._condition.wait(timeout=0.025)
                        continue
                if discard is not None:
                    self._dispose_process(discard)
                elif start:
                    try:
                        self._start_worker()
                    except (OSError, ValueError) as exc:
                        with self._condition:
                            self.error = str(exc)[:300]
                            self._pending.clear()
                elif command is not None:
                    try:
                        process.stdin.write(json.dumps(command, ensure_ascii=True) + "\n")
                        process.stdin.flush()
                    except (OSError, ValueError, AttributeError) as exc:
                        self._detach(process, exc)
                        self._dispose_process(process)
        finally:
            with self._condition:
                process = self._process
                ready = self._ready
                self._process = None
                self._ready = False
                self._active = None
            if process is not None and ready:
                try:
                    process.stdin.write('{"action":"close"}\n')
                    process.stdin.flush()
                except (OSError, ValueError, AttributeError):
                    pass
            self._dispose_process(process, graceful=ready)

    def snapshot(self):
        with self._condition:
            return {"enabled": self.enabled, "available": self.available,
                    "error": self.error, "last_text": self.last_text,
                    "last_spoken_at": self.last_spoken_at,
                    "speaking": self._active is not None,
                    "queued": len(self._pending), "worker_ready": self._ready}

    def close(self):
        with self._condition:
            if self._closed:
                return
            self._enabled = False
            self._closed = True
            self._pending.clear()
            self._active = None
            process = self._process
            self._condition.notify_all()
        # Allow the worker a brief graceful dispose, then interrupt only this
        # owned child. Never wait for a phrase or full PowerShell startup.
        if threading.current_thread() is not self._thread:
            self._thread.join(timeout=0.1)
        self._dispose_process(process)
