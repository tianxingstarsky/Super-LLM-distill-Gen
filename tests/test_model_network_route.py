"""Explicit service routing must not weaken TLS or change endpoint identity."""
from copy import deepcopy
import os
import ssl
from types import SimpleNamespace

import pytest
import yaml

from lib.application.backend_service import BackendApplication
from lib.domain.model_network import validate_network_route
from lib.infrastructure.backend_config_driver import FilesystemBackendConfigDriver
from lib.llm_client import ChatClient, load_backend, snapshot_backend_endpoint
from lib.model_network import sdk_network_options


@pytest.mark.parametrize("value", [None, "", "auto", "proxy", "DIRECT", True, {}, []])
def test_only_explicit_supported_routes(value):
    with pytest.raises(ValueError, match="invalid_model_network_route"):
        validate_network_route(value)


@pytest.mark.parametrize("api_format", ["chat", "responses", "anthropic"])
def test_direct_uses_provider_default_transport_and_keeps_tls(api_format, monkeypatch):
    monkeypatch.setenv("HTTPS_PROXY", "http://proxy.invalid:8123")
    before = dict(os.environ)
    options = sdk_network_options("direct", api_format=api_format)
    transport = options["http_client"]
    try:
        assert type(transport).__module__.split(".")[0] == ("anthropic" if api_format == "anthropic" else "openai")
        assert transport._trust_env is False
        assert all(value is None for value in transport._mounts.values())
        assert transport._transport._pool._ssl_context.verify_mode == ssl.CERT_REQUIRED
        assert transport._transport._pool._ssl_context.check_hostname is True
        assert os.environ == before
    finally:
        transport.close()


def test_system_injects_no_http_client():
    assert sdk_network_options() == {}
    assert sdk_network_options("system", api_format="anthropic") == {}


@pytest.mark.parametrize("api_format", ["chat", "responses", "anthropic"])
def test_chat_client_direct_is_local_and_sdk_copies_keep_route(api_format, monkeypatch):
    monkeypatch.delenv("NO_PROXY", raising=False)
    monkeypatch.delenv("no_proxy", raising=False)
    monkeypatch.setenv("HTTPS_PROXY", "http://proxy.invalid:8123")
    before = dict(os.environ)
    client = ChatClient("https://example.invalid", "offline", "fixture", api_format=api_format,
                        network_route="direct")
    try:
        assert client.network_route == "direct"
        assert client.client._client._trust_env is False
        scoped = client.client.with_options(base_url="https://example.invalid/beta")
        assert scoped._client is client.client._client
        assert os.environ == before
    finally:
        client.client.close()


@pytest.fixture
def configured(tmp_path):
    folder = tmp_path / "configs"
    folder.mkdir()
    (folder / "backends.yaml").write_text(yaml.safe_dump({"default_backend": "fixture", "backends": {
        "fixture": {"base_url": "https://example.invalid/v1", "models": ["m"], "api_key_env": "FIXTURE_KEY"}}}), encoding="utf-8")
    return tmp_path, BackendApplication(FilesystemBackendConfigDriver(tmp_path))


def test_route_save_changes_only_route_and_keeps_pins(configured, monkeypatch):
    root, application = configured
    application.save_model_capabilities("fixture", "m", vision=True, max_output_tokens=32768)
    before = application.read_config("backends.local.yaml")
    pin = snapshot_backend_endpoint(root, "fixture", "m")
    assert application.save_network_route("fixture", "direct") == "direct"
    saved = application.read_config("backends.local.yaml")
    assert saved["backends"]["fixture"]["network_route"] == "direct"
    copy = deepcopy(saved)
    del copy["backends"]["fixture"]["network_route"]
    assert copy == before
    assert snapshot_backend_endpoint(root, "fixture", "m") == pin
    assert application.list_backends()["backends"][0]["network_route"] == "direct"
    calls = []
    def fake(**options):
        calls.append(options)
        return SimpleNamespace()
    monkeypatch.setattr("lib.llm_client.ChatClient", fake)
    load_backend(root, "fixture", "m", allow_global_endpoint_override=False, expected_endpoint_pin=pin)
    assert calls[0]["network_route"] == "direct"
    assert calls[0]["base_url"] == "https://example.invalid/v1"


def test_old_backend_defaults_system_and_validation_precedes_write(configured, monkeypatch):
    root, application = configured
    assert application.list_backends()["backends"][0]["network_route"] == "system"
    before = application.read_config("backends.local.yaml")
    with pytest.raises(ValueError, match="invalid_model_network_route"):
        application.save_network_route("fixture", "automatic")
    with pytest.raises(ValueError, match="缺少 base_url"):
        application.save_network_route("missing", "direct")
    assert application.read_config("backends.local.yaml") == before
    calls = []
    monkeypatch.setattr("lib.llm_client.ChatClient", lambda **options: calls.append(options) or SimpleNamespace())
    load_backend(root, "fixture", "m", allow_global_endpoint_override=False)
    assert calls[0]["network_route"] == "system"


def test_new_endpoint_can_explicitly_save_direct(configured):
    _, application = configured
    application.save_endpoint("other", "https://other.invalid/v1", ["model"], network_route="direct")
    assert application.read_config("backends.local.yaml")["backends"]["other"]["network_route"] == "direct"


def test_manager_route_api_preserves_saved_key_and_capabilities(configured, monkeypatch):
    from lib import backend_manager
    root, application = configured
    monkeypatch.setattr(backend_manager, "ROOT", root)
    application.save_endpoint("private", "https://private.invalid/v1", ["m"], api_key="offline-secret")
    application.save_model_capabilities("private", "m", tools=True)
    before = application.read_config("backends.local.yaml")
    assert backend_manager.save_network_route("private", "direct") == "direct"
    expected = deepcopy(before)
    expected["backends"]["private"]["network_route"] = "direct"
    assert application.read_config("backends.local.yaml") == expected
    shown = next(row for row in backend_manager.list_backends()["backends"] if row["name"] == "private")
    assert shown["network_route"] == "direct"
    assert "offline-secret" not in str(shown)


@pytest.mark.parametrize("protocol", ["chat", "responses", "anthropic"])
def test_discovery_and_feature_probe_use_saved_route(protocol, monkeypatch, tmp_path):
    from lib.infrastructure.model_discovery import discover_models
    from lib.infrastructure.model_capability_probe import _make_client
    sdk = "anthropic" if protocol == "anthropic" else "openai"
    constructions = []
    def factory(**options):
        constructions.append(options)
        return SimpleNamespace(models=SimpleNamespace(list=lambda **options: SimpleNamespace(data=[])), close=lambda: None)
    monkeypatch.setattr(sdk + (".Anthropic" if protocol == "anthropic" else ".OpenAI"), factory)
    monkeypatch.setattr("lib.infrastructure.model_discovery.load_catalog", lambda cache: ({}, "offline"))
    endpoint = {"base_url": "https://offline.invalid/v1", "api_format": protocol, "network_route": "direct"}
    discover_models(endpoint, tmp_path)
    _make_client(endpoint, protocol)
    FilesystemBackendConfigDriver(tmp_path).probe("fixture", endpoint)
    assert len(constructions) == 3
    for options in constructions:
        client = options["http_client"]
        assert client._trust_env is False
        client.close()
