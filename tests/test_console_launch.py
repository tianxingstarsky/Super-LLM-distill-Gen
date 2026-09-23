"""The trusted local console and Windows entry must be single-instance."""
import io
import threading
import urllib.request

from filelock import FileLock
import pytest
from lib import cli, review_center
from scripts import launch_console as launcher
from streamlit.web import bootstrap


def test_console_applies_loopback_config_before_server_start(monkeypatch):
    calls = []
    monkeypatch.setattr(review_center, 'start_in_thread', lambda: calls.append('api-start'))
    monkeypatch.setattr(review_center, 'stop_thread', lambda: calls.append('api-stop'))
    def load(*, flag_options):
        assert flag_options['server.address'] == '127.0.0.1'
        assert flag_options['server.fileWatcherType'] == 'none'
        calls.append('config')
    def run(script, hello, args, flags):
        assert calls == ['api-start', 'config']
        assert flags['server.address'] == '127.0.0.1'
        calls.append('server')
    monkeypatch.setattr(bootstrap, 'load_config_options', load)
    monkeypatch.setattr(bootstrap, 'run', run)
    assert cli._launch_console() == 0
    assert calls == ['api-start', 'config', 'server', 'api-stop']


def test_console_reuses_standalone_review_api_without_stopping_it(monkeypatch):
    calls = []
    monkeypatch.setattr(review_center, 'start_in_thread', lambda: (_ for _ in ()).throw(OSError('busy')))
    monkeypatch.setattr(review_center, 'stop_thread', lambda: calls.append('api-stop'))

    class Opener:
        def open(self, url, timeout):
            assert url == 'http://127.0.0.1:6900/health'
            return io.BytesIO(b'{"ok":true,"service":"df-review-center"}')

    monkeypatch.setattr(urllib.request, 'build_opener', lambda *args: Opener())
    monkeypatch.setattr(bootstrap, 'load_config_options', lambda **kwargs: calls.append('config'))
    monkeypatch.setattr(bootstrap, 'run', lambda *args: calls.append('server'))
    assert cli._launch_console() == 0
    assert calls == ['config', 'server']


def test_console_releases_owned_review_api_if_streamlit_fails(monkeypatch):
    calls = []
    monkeypatch.setattr(review_center, 'start_in_thread', lambda: True)
    monkeypatch.setattr(review_center, 'stop_thread', lambda: calls.append('api-stop'))
    monkeypatch.setattr(bootstrap, 'load_config_options',
                        lambda **kwargs: (_ for _ in ()).throw(RuntimeError('bad config')))
    with pytest.raises(RuntimeError, match='bad config'):
        cli._launch_console()
    assert calls == ['api-stop']


def _prepare_launcher(monkeypatch, tmp_path):
    monkeypatch.setattr(launcher, 'ROOT', tmp_path)
    monkeypatch.chdir(tmp_path)
    for key in ('NO_PROXY', 'PYTHONIOENCODING', 'PIP_CACHE_DIR', 'HF_HOME', 'DSH_HOME'):
        monkeypatch.setenv(key, 'test-value')


def test_double_click_reuses_locked_console(monkeypatch, tmp_path):
    _prepare_launcher(monkeypatch, tmp_path)
    output = tmp_path / 'data' / 'output'
    output.mkdir(parents=True)
    observed = []
    monkeypatch.setattr(launcher, '_wait_then_open', lambda **kwargs: observed.append(kwargs['timeout']) or True)
    monkeypatch.setattr(cli, '_launch_console', lambda: (_ for _ in ()).throw(AssertionError('duplicate')))
    with FileLock(str(output / 'console.lock'), timeout=0):
        assert launcher.main() == 0
    assert observed == [20]


def test_fresh_double_click_opens_after_console_health(monkeypatch, tmp_path):
    _prepare_launcher(monkeypatch, tmp_path)
    opened = threading.Event()
    monkeypatch.setattr(launcher, '_show_error', lambda message: (_ for _ in ()).throw(AssertionError(message)))
    probes = [0]

    def ready():
        probes[0] += 1
        return probes[0] > 1

    monkeypatch.setattr(launcher, '_existing_console_ready', ready)
    monkeypatch.setattr(launcher, '_console_port_in_use', lambda: False)
    monkeypatch.setattr(launcher, '_open_console', opened.set)

    def serve():
        assert opened.wait(3), 'browser was not opened after health passed'
        return 0

    monkeypatch.setattr(cli, '_launch_console', serve)
    assert launcher.main() == 0
    assert opened.is_set()


def test_double_click_reuses_console_started_outside_launcher(monkeypatch, tmp_path):
    _prepare_launcher(monkeypatch, tmp_path)
    opened = []
    monkeypatch.setattr(launcher, '_existing_console_ready', lambda: True)
    monkeypatch.setattr(launcher, '_open_console', lambda: opened.append(True))
    monkeypatch.setattr(cli, '_launch_console', lambda: (_ for _ in ()).throw(AssertionError('duplicate')))
    assert launcher.main() == 0
    assert opened == [True]


def test_early_console_exit_is_reported(monkeypatch, tmp_path):
    _prepare_launcher(monkeypatch, tmp_path)
    errors = []
    monkeypatch.setattr(launcher, '_existing_console_ready', lambda: False)
    monkeypatch.setattr(launcher, '_console_port_in_use', lambda: False)
    monkeypatch.setattr(launcher, '_show_error', errors.append)
    monkeypatch.setattr(cli, '_launch_console', lambda: 0)
    assert launcher.main() == 1
    assert '页面就绪前退出' in errors[0]


def test_occupied_unrelated_port_never_starts_second_console(monkeypatch, tmp_path):
    _prepare_launcher(monkeypatch, tmp_path)
    errors = []
    monkeypatch.setattr(launcher, '_existing_console_ready', lambda: False)
    monkeypatch.setattr(launcher, '_console_port_in_use', lambda: True)
    monkeypatch.setattr(launcher, '_show_error', errors.append)
    monkeypatch.setattr(cli, '_launch_console', lambda: (_ for _ in ()).throw(AssertionError('duplicate')))
    assert launcher.main() == 1
    assert '8501' in errors[0]


def test_vbs_reports_missing_python_before_hidden_launch():
    source = (launcher.ROOT / 'scripts' / 'start_all.vbs').read_text(encoding='utf-8')
    assert 'fs.FileExists(python)' in source
    assert 'MsgBox' in source
