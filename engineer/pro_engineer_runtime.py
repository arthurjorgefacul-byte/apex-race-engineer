"""Launch the pinned, unmodified AC Pro Engineer backend; never kill another instance."""
import ctypes
import datetime
import hashlib
import json
import os
import pathlib
import subprocess
import threading
import time


def native_config_path():
    if os.name != "nt":
        raise ValueError("The bundled Pro Engineer backend requires Windows.")
    buffer = ctypes.create_unicode_buffer(32768)
    result = ctypes.windll.shell32.SHGetFolderPathW(None, 0x1A, None, 0, buffer)
    if result or not buffer.value:
        raise ValueError("Windows could not locate Pro Engineer's configuration folder.")
    return pathlib.Path(buffer.value) / "RaceEngineer" / "RaceEngineer" / "config" / "config.json"


def another_engine_running():
    if os.name != "nt":
        return False
    result = subprocess.run(["tasklist.exe", "/FI", "IMAGENAME eq ac_pro_engineer.exe", "/FO", "CSV", "/NH"],
                            capture_output=True, timeout=5, creationflags=subprocess.CREATE_NO_WINDOW)
    return b'"ac_pro_engineer.exe"' in result.stdout.lower()


class ProEngineerRuntime:
    def __init__(self, package_root, data_dir, config_path=None):
        self.root = pathlib.Path(package_root)
        self.data_dir = pathlib.Path(data_dir)
        self.binary = self.root / "third_party" / "pro_engineer" / "bin" / "ac_pro_engineer.exe"
        self.config_path = pathlib.Path(config_path) if config_path else None
        self.process = None
        self.demo = False
        self.error = None
        self.backup_path = None
        self._lock = threading.RLock()

    def verify(self):
        metadata = self.root / "third_party" / "pro_engineer" / "PROVENANCE.json"
        if not self.binary.is_file() or not metadata.is_file():
            raise ValueError("The pinned Pro Engineer package is missing. Extract the complete APEX package.")
        manifest = json.loads(metadata.read_text(encoding="utf-8"))
        expected = manifest.get("binary_sha256")
        if not expected or hashlib.sha256(self.binary.read_bytes()).hexdigest() != expected:
            raise ValueError("The native engine does not match the verified bundled release. It was not started.")
        return manifest

    def configure(self):
        path = self.config_path or native_config_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        config = {}
        if path.exists():
            config = json.loads(path.read_text(encoding="utf-8-sig"))
            if not isinstance(config, dict):
                raise ValueError("The existing Pro Engineer configuration is not an object; it was left untouched.")
            stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S-%f")
            backup = path.with_name("config.apex-backup-" + stamp + ".json")
            backup.write_bytes(path.read_bytes())
            self.backup_path = backup
        overlay = config.get("overlay", {})
        lan = config.get("lan", {})
        if not isinstance(overlay, dict) or not isinstance(lan, dict):
            raise ValueError("Pro Engineer's existing network settings are invalid; the configuration was left untouched.")
        overlay = dict(overlay, broadcast_to="127.0.0.1:8778", broadcast_enabled=True,
                       broadcast_hz=10.0, broadcast_name="APEX local", receive_from="", receive_enabled=False)
        lan = dict(lan, mode="Share", share_hz=30.0, only_on_track=False, quiet_after_s=3.0, announce=False)
        config.update(overlay=overlay, lan=lan, game="assetto_corsa")
        config.setdefault("language", "English")
        config.setdefault("config_version", 2)
        config.setdefault("last_run_version", "0.5.1")
        native_data = self.data_dir / "pro_engineer"
        native_data.mkdir(parents=True, exist_ok=True)
        config.setdefault("data_path", str(native_data.resolve()))
        temp = path.with_suffix(".apex-tmp")
        temp.write_text(json.dumps(config, indent=2, ensure_ascii=False), encoding="utf-8")
        temp.replace(path)
        self.config_path = path
        return config

    def start(self, demo=False, hidden=False):
        with self._lock:
            if self.process and self.process.poll() is None:
                if self.demo == bool(demo):
                    return self.snapshot()
                self.stop()
            try:
                self.verify()
                if another_engine_running():
                    raise ValueError("Another Pro Engineer instance is already running. Close it, or configure its sharing target to 127.0.0.1:8778 manually. It has been left untouched.")
                self.configure()
                args = [str(self.binary), "--log", str((self.data_dir / "pro-engineer.log").resolve())]
                if demo:
                    args.append("--demo")
                kwargs = {"cwd": str(self.binary.parent)}
                if os.name == "nt":
                    startup = subprocess.STARTUPINFO()
                    startup.dwFlags |= subprocess.STARTF_USESHOWWINDOW
                    startup.wShowWindow = 0 if hidden else 1
                    kwargs.update(startupinfo=startup, creationflags=subprocess.CREATE_NEW_CONSOLE)
                self.process = subprocess.Popen(args, **kwargs)
                self.demo = bool(demo)
                self.error = None
            except Exception as exc:
                self.error = str(exc)
                raise ValueError(str(exc)) from exc
            return self.snapshot()

    def snapshot(self):
        with self._lock:
            running = bool(self.process and self.process.poll() is None)
            return {"running": running, "pid": self.process.pid if running else None,
                    "demo": self.demo, "error": self.error,
                    "config_path": str(self.config_path) if self.config_path else None,
                    "backup_path": str(self.backup_path) if self.backup_path else None,
                    "instruction": "Start AC, then press Enter on Start in the Pro Engineer window." if running and not self.demo else "Native upstream demo" if running else "Start engine to run the bundled Pro Engineer backend."}

    def stop(self):
        with self._lock:
            if self.process and self.process.poll() is None:
                deadline = time.monotonic() + 0.6
                try:
                    self.process.terminate()
                    try:
                        self.process.wait(timeout=min(0.35, max(0, deadline - time.monotonic())))
                    except subprocess.TimeoutExpired:
                        self.process.kill()
                        self.process.wait(timeout=max(0, deadline - time.monotonic()))
                except subprocess.TimeoutExpired:
                    # Retain the owned handle for a retry/diagnostic rather than
                    # pretending a child that ignored termination has exited.
                    self.error = "The owned native engine did not exit within the shutdown budget."
                    return
                except OSError as exc:
                    if self.process.poll() is None:
                        self.error = str(exc)[:300]
                        return
            self.process = None
