"""Native shell for the same local workbench; services outlive the window."""
from __future__ import annotations

from contextlib import redirect_stderr, redirect_stdout
import importlib
import os
from pathlib import Path
import subprocess
import sys
import time
import traceback

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from scripts import launch_console as console


def _start_service():
    python = Path(sys.executable)
    windowless = python.with_name("pythonw.exe")
    if sys.platform == "win32" and windowless.is_file():
        python = windowless
    return subprocess.Popen(
        [str(python), str(ROOT / "scripts" / "launch_console.py"), "--service-only"],
        cwd=ROOT, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
    )


def _ensure_console(timeout=55):
    if console._existing_console_ready():
        return
    child = _start_service()
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if console._existing_console_ready():
            return
        result = child.poll()
        if result is not None:
            raise RuntimeError(f"本地服务未能启动（退出代码 {result}），请查看 console.log。")
        time.sleep(0.3)
    # Only this launcher's child is stopped. Never touch an existing service.
    if child.poll() is None:
        child.terminate()
        try:
            child.wait(timeout=5)
        except subprocess.TimeoutExpired:
            child.kill()
            child.wait(timeout=5)
    raise RuntimeError("本地服务启动超过 55 秒，请查看 console.log。")


def _run_window(webview):
    _ensure_console()
    if sys.platform == "win32":
        arguments = os.environ.get("WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS", "")
        if "--force-renderer-accessibility" not in arguments:
            os.environ["WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS"] = (
                arguments + " --force-renderer-accessibility").strip()
    webview.settings["ALLOW_DOWNLOADS"] = True
    webview.settings["ALLOW_FILE_URLS"] = False
    webview.create_window(
        "数简立方 · ShuJian Cube", console.CONSOLE_URL,
        width=1440, height=960, min_size=(1000, 700),
        text_select=True,
        background_color="#F3F7FD",
    )
    # No Python bridge is exposed to the page. The UI uses the same local API.
    webview.start(gui="edgechromium" if sys.platform == "win32" else None)


def main():
    output = ROOT / "data" / "output"
    try:
        output.mkdir(parents=True, exist_ok=True)
        with (output / "desktop.log").open("a", encoding="utf-8", buffering=1) as log:
            with redirect_stdout(log), redirect_stderr(log):
                try:
                    webview = importlib.import_module("webview")
                    _run_window(webview)
                    return 0
                except Exception as error:
                    traceback.print_exc()
                    console._show_error(
                        f"桌面窗口启动失败：{error}\n\n"
                        "请确认已安装项目依赖和 Microsoft Edge WebView2 Runtime。\n"
                        f"详细日志：{output / 'desktop.log'}")
                    return 1
    except OSError as error:
        console._show_error(f"无法写入桌面启动日志：{error}\n请检查目录：{output}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
