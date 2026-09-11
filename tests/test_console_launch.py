"""The trusted local console must apply loopback flags before starting Streamlit."""
from lib import cli, review_center
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
