"""Windowless Windows launcher for the local ShuJian Cube console.

The FileLock owns one console process; health polling has a fixed deadline and
never starts a second service. ShellExecuteW opens the system browser without
the blocking webbrowser.open call that caused orphaned launcher processes.
"""
from __future__ import annotations

import ctypes
from contextlib import redirect_stderr, redirect_stdout
from datetime import datetime
import json
import os
from pathlib import Path
import socket
import sys
import threading
import time
import traceback
import urllib.error
import urllib.request

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

CONSOLE_URL = "http://127.0.0.1:8501/"
HEALTH_URL = "http://127.0.0.1:8501/_stcore/health"
REVIEW_HEALTH_URL = "http://127.0.0.1:6900/health"
_DIRECT_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def _log(message: str) -> None:
    print(f"[{datetime.now().isoformat(timespec='seconds')}] [launch] {message}", flush=True)


def _show_error(message: str) -> None:
    """A pythonw process has no terminal, so failures must be visible."""
    if sys.platform == "win32":
        ctypes.windll.user32.MessageBoxW(None, message, "数简立方 启动失败", 0x10)
    else:
        print(message, file=sys.stderr)


def _open_console() -> None:
    if sys.platform != "win32":
        return
    launch = ctypes.windll.shell32.ShellExecuteW
    launch.argtypes = [ctypes.c_void_p, ctypes.c_wchar_p, ctypes.c_wchar_p,
                       ctypes.c_wchar_p, ctypes.c_wchar_p, ctypes.c_int]
    launch.restype = ctypes.c_void_p
    result = launch(None, "open", CONSOLE_URL, None, None, 1)
    if not result or result <= 32:
        raise OSError(f"无法打开系统浏览器（ShellExecuteW 返回 {result}）")


def _request(url: str) -> bytes | None:
    try:
        with _DIRECT_OPENER.open(url, timeout=0.6) as response:
            return response.read(256) if response.status == 200 else None
    except (OSError, urllib.error.URLError):
        return None


def _console_ready() -> bool:
    return _request(HEALTH_URL) == b"ok"


def _existing_console_ready() -> bool:
    """Require both the Streamlit UI and review API to be healthy."""
    if not _console_ready():
        return False
    body = _request(REVIEW_HEALTH_URL)
    if body is None:
        return False
    try:
        return json.loads(body).get("service") == "df-review-center"
    except (ValueError, AttributeError):
        return False


def _console_port_in_use() -> bool:
    with socket.socket() as sock:
        sock.settimeout(0.5)
        return sock.connect_ex(("127.0.0.1", 8501)) == 0


def _wait_then_open(*, timeout: float, stop: threading.Event | None = None) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if stop is not None and stop.is_set():
            return False
        if _existing_console_ready():
            _open_console()
            _log("控制台已就绪，已打开浏览器。")
            return True
        if stop is None:
            time.sleep(0.3)
        else:
            stop.wait(0.3)
    return False


def main() -> int:
    output = ROOT / "data" / "output"
    output.mkdir(parents=True, exist_ok=True)
    with (output / "console.log").open("a", encoding="utf-8", buffering=1) as log, \
            redirect_stdout(log), redirect_stderr(log):
        os.environ.setdefault("NO_PROXY", "127.0.0.1,localhost")
        os.environ.setdefault("PYTHONIOENCODING", "utf-8")
        for key, name in (("PIP_CACHE_DIR", "pip-cache"), ("HF_HOME", "hf-home"), ("DSH_HOME", "dsh-home")):
            os.environ.setdefault(key, str(ROOT.parent / "tools" / name))
        os.chdir(ROOT)

        try:
            from filelock import FileLock, Timeout
            from streamlit.web import bootstrap  # noqa: F401 - fail before opening a service
        except Exception as exc:  # noqa: BLE001 - pythonw has no visible traceback
            _log(f"运行依赖加载失败：{type(exc).__name__}: {exc}")
            traceback.print_exc()
            _show_error("控制台运行依赖加载失败。请在项目目录运行：\n"
                        ".venv\\Scripts\\python.exe -m pip install -r requirements.txt\n\n"
                        f"详细错误：{exc}\n日志：{output / 'console.log'}")
            return 1

        lock = FileLock(str(output / "console.lock"), timeout=0)
        try:
            lock.acquire()
        except Timeout:
            _log("已有控制台进程；等待它就绪并打开页面。")
            try:
                if _wait_then_open(timeout=20):
                    return 0
            except OSError as exc:
                _log(f"打开浏览器失败：{exc}")
                _show_error(f"控制台已就绪，但浏览器未能打开：{exc}\n请手动访问 {CONSOLE_URL}")
                return 1
            _show_error("控制台进程仍在启动，或启动失败。请稍后重试；若持续失败，查看日志：\n"
                        f"{output / 'console.log'}")
            return 1
        except OSError as exc:
            _log(f"无法获取单实例锁：{exc}")
            _show_error(f"无法使用控制台数据目录：{exc}\n请检查目录权限：\n{output}")
            return 1

        try:
            if _existing_console_ready():
                _open_console()
                _log("已打开现有控制台。")
                return 0
            if _console_port_in_use():
                raise RuntimeError("端口 8501 已被其他服务占用，无法启动控制台。")

            stop = threading.Event()
            opened = threading.Event()

            def open_when_ready() -> None:
                try:
                    if _wait_then_open(timeout=45, stop=stop):
                        opened.set()
                    elif not stop.is_set():
                        _log("启动超过 45 秒，控制台尚未就绪。")
                        _show_error("控制台启动超过 45 秒仍未就绪。请查看启动日志：\n"
                                    f"{output / 'console.log'}")
                except OSError as exc:
                    _log(f"打开浏览器失败：{exc}")
                    _show_error(f"控制台已启动，但浏览器未能打开：{exc}\n请手动访问 {CONSOLE_URL}")

            opener = threading.Thread(target=open_when_ready, name="df-open-console", daemon=True)
            opener.start()
            try:
                from lib.cli import _launch_console

                result = _launch_console()
            finally:
                stop.set()
                opener.join(timeout=2)
            if not opened.is_set():
                raise RuntimeError("控制台在页面就绪前退出，请检查启动日志。")
            return result
        except Exception as exc:  # noqa: BLE001 - pythonw must make failures visible
            _log(f"启动失败：{type(exc).__name__}: {exc}")
            traceback.print_exc()
            _show_error(f"控制台启动失败：{exc}\n\n详细日志：{output / 'console.log'}")
            return 1
        finally:
            lock.release()


if __name__ == "__main__":
    raise SystemExit(main())
