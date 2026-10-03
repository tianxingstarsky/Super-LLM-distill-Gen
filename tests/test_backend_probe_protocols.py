"""Backend probes select the matching SDK without exposing credentials."""
from __future__ import annotations

import sys
from types import ModuleType, SimpleNamespace

import pytest

from lib.infrastructure.backend_config_driver import FilesystemBackendConfigDriver


@pytest.fixture
def fake_provider_sdks(monkeypatch):
    calls = []

    def client_type(provider):
        class FakeClient:
            def __init__(self, *, base_url, api_key):
                calls.append((provider, base_url, api_key))
                self.models = SimpleNamespace(
                    list=lambda: SimpleNamespace(data=[
                        SimpleNamespace(id="model-z"),
                        SimpleNamespace(id="model-a"),
                    ])
                )

        return FakeClient

    openai = ModuleType("openai")
    openai.OpenAI = client_type("openai")
    anthropic = ModuleType("anthropic")
    anthropic.Anthropic = client_type("anthropic")
    monkeypatch.setitem(sys.modules, "openai", openai)
    monkeypatch.setitem(sys.modules, "anthropic", anthropic)
    return calls


@pytest.mark.parametrize(
    ("api_format", "provider", "default_env"),
    [
        ("chat", "openai", "OPENAI_API_KEY"),
        ("responses", "openai", "OPENAI_API_KEY"),
        ("anthropic", "anthropic", "ANTHROPIC_API_KEY"),
    ],
)
def test_probe_uses_format_sdk_and_default_key_without_exposing_it(
    tmp_path, monkeypatch, fake_provider_sdks, api_format, provider, default_env
):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    secret = "secret-for-" + api_format
    monkeypatch.setenv(default_env, secret)
    backend = {"base_url": "https://example.test/v1", "api_format": api_format}

    result = FilesystemBackendConfigDriver(tmp_path).probe("chosen", backend)

    assert fake_provider_sdks == [(provider, backend["base_url"], secret)]
    assert result == {
        "backend": "chosen",
        "base_url": backend["base_url"],
        "api_format": api_format,
        "models": ["model-a", "model-z"],
    }
    assert secret not in str(result)


def test_probe_defaults_older_backend_to_chat(tmp_path, monkeypatch, fake_provider_sdks):
    monkeypatch.setenv("OPENAI_API_KEY", "old-backend-secret")

    result = FilesystemBackendConfigDriver(tmp_path).probe(
        "older", {"base_url": "https://example.test/v1"}
    )

    assert fake_provider_sdks == [("openai", "https://example.test/v1", "old-backend-secret")]
    assert result["api_format"] == "chat"
    assert "old-backend-secret" not in str(result)


def test_explicit_key_environment_is_not_overridden_by_default(
    tmp_path, monkeypatch, fake_provider_sdks
):
    monkeypatch.delenv("TENANT_ANTHROPIC_KEY", raising=False)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "wrong-account-secret")
    backend = {
        "base_url": "https://example.test/v1",
        "api_format": "anthropic",
        "api_key_env": "TENANT_ANTHROPIC_KEY",
    }

    result = FilesystemBackendConfigDriver(tmp_path).probe("tenant", backend)

    assert fake_provider_sdks == [("anthropic", backend["base_url"], "sk-local")]
    assert "wrong-account-secret" not in str(result)


def test_explicit_empty_key_environment_keeps_local_endpoint_unauthenticated(
    tmp_path, monkeypatch, fake_provider_sdks
):
    monkeypatch.setenv("OPENAI_API_KEY", "wrong-account-secret")

    FilesystemBackendConfigDriver(tmp_path).probe(
        "local", {
            "base_url": "http://127.0.0.1:11434/v1",
            "api_format": "responses",
            "api_key_env": "",
        }
    )

    assert fake_provider_sdks == [("openai", "http://127.0.0.1:11434/v1", "sk-local")]


def test_probe_rejects_unknown_format_before_sdk_call(tmp_path, fake_provider_sdks):
    with pytest.raises(ValueError, match="invalid_api_format"):
        FilesystemBackendConfigDriver(tmp_path).probe(
            "wrong", {"base_url": "https://example.test/v1", "api_format": "other"}
        )
    assert fake_provider_sdks == []
