"""Session-owned jobs; worker threads never access Streamlit session state."""
from __future__ import annotations
from collections import deque
import os
from pathlib import Path
import re
import subprocess
import sys
import threading
from filelock import FileLock
from lib.io_utils import quiet_process


def workflow_run_id(command) -> str | None:
    """Identify the one console command that resumes an existing isolated run.

    Other workflow invocations stay under the workspace lock, including new
    runs started from the advanced command form.  Keeping this narrow avoids
    granting a shared-output command a per-run lock by accident.
    """
    args = tuple(command)
    if (len(args) == 5 and args[:4] == ("workflow", "--action", "resume", "--run-id")
            and isinstance(args[4], str) and re.fullmatch(r"[a-f0-9]{32}", args[4])):
        return args[4]
    return None


class Job:
    def __init__(self, command, workspace, cwd):
        self.workspace = workspace
        self.command = tuple(command)
        self.run_id = workflow_run_id(self.command)
        self.cwd = Path(cwd)
        self.lines = deque(maxlen=500)
        self.code = None
        self._lock = threading.Lock()
        self._thread = None
        self._ws_lock = None

    def start(self):
        if self._thread:
            raise ValueError("Job already started")
        # Legacy commands may write shared workspace output, while a resumed
        # workflow writes only its own run directory.  Never hold .run.lock
        # here: the child workflow process acquires that execution lock itself.
        # thread_local=False lets the worker release a lock acquired here.
        from lib import workspace
        output = Path(workspace.out(self.workspace, self.cwd))
        if self.run_id is None:
            output.mkdir(parents=True, exist_ok=True)
            lock_path = output / ".jobs.lock"
        else:
            from lib.infrastructure.training_workflow import run_path
            run_dir = run_path(output, self.run_id)
            if not (run_dir / "state.json").is_file():
                raise ValueError("运行不存在")
            lock_path = run_dir / ".console-job.lock"
        self._ws_lock = FileLock(str(lock_path), timeout=0.1, thread_local=False)
        self._ws_lock.acquire()  # Already running in another session/process.
        try:
            self._thread = threading.Thread(target=self._run, daemon=True)
            self._thread.start()
        except Exception:
            self._thread = None
            self._ws_lock.release()
            self._ws_lock = None
            raise

    def _run(self):
        env = {**os.environ, "DF_WORKSPACE": self.workspace, "PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1", "PYTHONUNBUFFERED": "1"}
        try:
            with subprocess.Popen([sys.executable, "-m", "lib.cli", *self.command, "--ws", self.workspace],
                cwd=self.cwd, env=env, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT, text=True, encoding="utf-8", errors="replace", **quiet_process()) as process:
                for line in process.stdout:
                    with self._lock:
                        self.lines.append(line.rstrip())
                code = process.wait()
        except Exception as error:  # noqa: BLE001 - 失败也要落日志并释放工作区锁
            with self._lock:
                self.lines.append(f"{type(error).__name__}: {error}")
            code = 1
        finally:
            with self._lock:
                self.code = code
            if self._ws_lock is not None:
                self._ws_lock.release()

    def snapshot(self):
        with self._lock:
            return self.code, list(self.lines)
