"""Desktop lifecycle must reuse services and preserve long running tasks."""
from types import SimpleNamespace

import pytest

from scripts import launch_desktop as desktop
from scripts import launch_console as console


def test_healthy_service_is_reused_without_spawning(monkeypatch):
    monkeypatch.setattr(console, "_existing_console_ready", lambda: True)
    monkeypatch.setattr(desktop, "_start_service", lambda: pytest.fail("duplicate service"))
    desktop._ensure_console()


def test_new_service_waits_for_both_health_checks_and_is_left_running(monkeypatch):
    probes = iter([False, False, True])
    monkeypatch.setattr(console, "_existing_console_ready", lambda: next(probes))
    calls = []
    child = SimpleNamespace(poll=lambda: None, terminate=lambda: pytest.fail("stopped task service"))
    monkeypatch.setattr(desktop, "_start_service", lambda: calls.append("start") or child)
    monkeypatch.setattr(desktop.time, "sleep", lambda delay: calls.append("wait"))
    desktop._ensure_console()
    assert calls == ["start", "wait"]


def test_service_exit_is_reported_before_opening_window(monkeypatch):
    monkeypatch.setattr(console, "_existing_console_ready", lambda: False)
    monkeypatch.setattr(desktop, "_start_service", lambda: SimpleNamespace(poll=lambda: 1))
    with pytest.raises(RuntimeError, match="退出代码 1"):
        desktop._ensure_console()


def test_timeout_terminates_only_the_child_it_started(monkeypatch):
    monkeypatch.setattr(console, "_existing_console_ready", lambda: False)
    clock = iter([0, 56])
    monkeypatch.setattr(desktop.time, "monotonic", lambda: next(clock))
    calls = []
    child = SimpleNamespace(poll=lambda: None, terminate=lambda: calls.append("terminate"),
                            wait=lambda timeout: calls.append(("wait", timeout)))
    monkeypatch.setattr(desktop, "_start_service", lambda: child)
    with pytest.raises(RuntimeError, match="55 秒"):
        desktop._ensure_console()
    assert calls == ["terminate", ("wait", 5)]


def test_window_uses_local_console_without_python_bridge_or_browser(monkeypatch):
    calls = []
    monkeypatch.setattr(desktop, "_ensure_console", lambda: calls.append("ready"))
    monkeypatch.setattr(desktop.sys, "platform", "win32")
    monkeypatch.setenv("WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS", "--example-existing-flag")
    webview = SimpleNamespace(settings={}, create_window=lambda *args, **kwargs: calls.append((args, kwargs)),
                              start=lambda **kwargs: calls.append(kwargs))
    desktop._run_window(webview)
    assert calls[0] == "ready"
    assert calls[1][0] == ("数简立方 · ShuJian Cube", console.CONSOLE_URL)
    assert "js_api" not in calls[1][1]
    assert calls[1][1]["min_size"] == (1000, 700)
    assert calls[2] == {"gui": "edgechromium"}
    assert webview.settings == {"ALLOW_DOWNLOADS": True, "ALLOW_FILE_URLS": False}
    assert desktop.os.environ["WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS"] == (
        "--example-existing-flag --force-renderer-accessibility")


def test_missing_desktop_dependency_reports_error_without_starting_service(monkeypatch, tmp_path):
    monkeypatch.setattr(desktop, "ROOT", tmp_path)
    monkeypatch.setattr(desktop.importlib, "import_module", lambda name: (_ for _ in ()).throw(ImportError("webview missing")))
    monkeypatch.setattr(desktop, "_ensure_console", lambda: pytest.fail("unexpected service"))
    errors = []
    monkeypatch.setattr(console, "_show_error", errors.append)
    assert desktop.main() == 1
    assert "webview missing" in errors[0]
    assert "WebView2" in errors[0]
    assert (tmp_path / "data/output/desktop.log").is_file()


def test_service_only_launcher_never_opens_system_browser(monkeypatch):
    monkeypatch.setattr(console.sys, "argv", ["launch_console.py", "--service-only"])
    console._open_console()


def test_default_double_click_entry_uses_desktop():
    source = (desktop.ROOT / "scripts/start_all.vbs").read_text(encoding="utf-8")
    assert '\\scripts\\launch_desktop.py' in source
