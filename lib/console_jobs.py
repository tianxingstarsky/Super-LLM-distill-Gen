"""Session-owned jobs; worker threads never access Streamlit session state."""
from __future__ import annotations
from collections import deque
import os
from pathlib import Path
import subprocess
import sys
import threading
from filelock import FileLock
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
        self._ws_lock = None

    def start(self):
        if self._thread:
            raise ValueError("Job already started")
        # 工作区级文件锁：任务只登记在浏览器会话里，换窗口/刷新后仍要防止
        # 两个子进程并发写同一输出目录。thread_local=False 允许工作线程释放
        # 主线程持有的锁（filelock 3.11+ 默认线程本地）。
        from lib import workspace
        lock_dir = Path(workspace.out(self.workspace, self.cwd))
        lock_dir.mkdir(parents=True, exist_ok=True)
        self._ws_lock = FileLock(str(lock_dir / ".jobs.lock"), timeout=0.1, thread_local=False)
        self._ws_lock.acquire()  # 被占用 → filelock.Timeout
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
        except Exception as error:  # noqa: BLE001 - 失败也要落日志并释放工作区锁
            with self._lock:
                self.lines.append(f"{type(error).__name__}: {error}")
            code = 1
        finally:
            if self._ws_lock is not None:
                self._ws_lock.release()
        with self._lock:
            self.code = code

    def snapshot(self):
        with self._lock:
            return self.code, list(self.lines)
