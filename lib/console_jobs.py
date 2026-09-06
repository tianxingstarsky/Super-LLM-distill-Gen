"""Session-owned jobs; worker threads never access Streamlit session state."""
from __future__ import annotations
from collections import deque
import os
from pathlib import Path
import subprocess
import sys
import threading
from lib.io_utils import quiet_process


class Job:
    def __init__(self, command, workspace, cwd):
        self.workspace = workspace
        self.command = tuple(command)
        self.cwd = Path(cwd)
        self.lines = deque(maxlen=500)
        self.code = None
        self._lock = threading.Lock()
        self._thread = None

    def start(self):
        if self._thread:
            raise ValueError("Job already started")
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def _run(self):
        env = {**os.environ, "DF_WORKSPACE": self.workspace, "PYTHONIOENCODING": "utf-8", "PYTHONUNBUFFERED": "1"}
        try:
            with subprocess.Popen([sys.executable, "-m", "lib.cli", *self.command, "--ws", self.workspace],
                cwd=self.cwd, env=env, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT, text=True, encoding="utf-8", errors="replace", **quiet_process()) as process:
                for line in process.stdout:
                    with self._lock:
                        self.lines.append(line.rstrip())
                code = process.wait()
        except Exception as error:
            with self._lock:
                self.lines.append(f"{type(error).__name__}: {error}")
            code = 1
        with self._lock:
            self.code = code

    def snapshot(self):
        with self._lock:
            return self.code, list(self.lines)
