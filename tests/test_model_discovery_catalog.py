"""Endpoint model availability stays separate from upstream capability facts."""
from datetime import datetime, timezone
import json
import sys
from types import ModuleType, SimpleNamespace

import pytest

from lib.domain.model_capabilities import normalize_model_info
from lib.infrastructure import model_discovery as discovery


def _catalog():
    return {
        "openai": {"models": {"selected": {"limit": {"context": 200000, "output": 16000},
                     "modalities": {"input": ["text", "image"]}, "tool_call": True,
                     "attachment": True}, "not-served": {"tool_call": True}}},
        "other": {"api": "https://different.test/v1", "models": {"selected": {
                     "limit": {"context": 900000}, "tool_call": False}}},
    }


def _sdk(monkeypatch, records, protocol="chat", *, pages=None):
    calls = []
    responses = pages or [SimpleNamespace(data=records, has_more=False)]
    class Client:
        def __init__(self, **kwargs):
            calls.append(("init", kwargs))
            self.models = SimpleNamespace(list=self.list)
        def list(self, **kwargs):
            calls.append(("list", kwargs))
            return responses[min(len([item for item in calls if item[0] == "list"]) - 1, len(responses) - 1)]
        def close(self):
            calls.append(("close", {}))
    module = ModuleType("anthropic" if protocol == "anthropic" else "openai")
    setattr(module, "Anthropic" if protocol == "anthropic" else "OpenAI", Client)
    monkeypatch.setitem(sys.modules, module.__name__, module)
    return calls


def test_native_anthropic_and_openrouter_declarations_are_normalized():
    info = normalize_model_info("claude", {"max_input_tokens": 200000, "max_tokens": 64000,
        "capabilities": {"image_input": {"supported": True}, "pdf_input": {"supported": False},
                         "server_tools": {"supported": True}}}, "service")
    assert info["context_window_tokens"] == 200000
    assert info["max_output_tokens"] == 64000
    assert info["vision"] is True and info["pdf"] is False
    assert info["tools"] is None  # Server tools do not imply function calling.
    router = normalize_model_info("served", {"context_length": 128000,
        "top_provider": {"max_completion_tokens": 30000},
        "architecture": {"input_modalities": ["text", "image"]},
        "supported_parameters": ["tools", "temperature"]}, "service")
    assert router["vision"] is True and router["tools"] is True
    assert router["pdf"] is None
    assert router["sources"]["max_output_tokens"] == "service"


def test_invalid_or_generic_flags_remain_unknown():
    info = normalize_model_info("x", {"context_length": True, "max_tokens": "64000",
        "supports_vision": "true", "attachment": True, "tools": ["search"]}, "service")
    assert all(info[key] is None for key in ("context_window_tokens", "max_output_tokens", "vision", "pdf", "tools"))


@pytest.mark.parametrize("protocol", ["chat", "responses", "anthropic"])
def test_sdk_list_is_protocol_native_and_catalog_never_adds_models(tmp_path, monkeypatch, protocol):
    calls = _sdk(monkeypatch, [{"id": "selected", "context_length": 32000}], protocol)
    catalog = _catalog()
    if protocol == "anthropic":
        catalog["anthropic"] = catalog["openai"]
    monkeypatch.setattr(discovery, "load_catalog", lambda directory: (catalog, "cached"))
    endpoint = {"base_url": "https://api.anthropic.com" if protocol == "anthropic" else "https://api.openai.com/v1",
                "api_format": protocol, "api_key": "endpoint-secret"}
    result = discovery.discover_models(endpoint, tmp_path)
    assert result["models"] == ["selected"]
    info = result["model_info"]["selected"]
    assert info["context_window_tokens"] == 32000
    assert info["max_output_tokens"] == 16000
    assert info["vision"] is True and info["tools"] is True and info["pdf"] is None
    assert info["sources"] == {"context_window_tokens": "service", "max_output_tokens": "models.dev",
                               "vision": "models.dev", "tools": "models.dev"}
    assert "endpoint-secret" not in str(result)
    assert calls[1][1]["timeout"] <= 12
    if protocol == "anthropic":
        assert calls[1][1]["limit"] == 1000
    assert calls[-1][0] == "close"


def test_unknown_gateway_does_not_match_model_name_or_provider_label(tmp_path, monkeypatch):
    _sdk(monkeypatch, [{"id": "selected"}])
    monkeypatch.setattr(discovery, "load_catalog", lambda directory: (_catalog(), "cached"))
    result = discovery.discover_models({"base_url": "https://gateway.test/v1", "api_key": "x",
                                       "provider": "openai"}, tmp_path)
    info = result["model_info"]["selected"]
    assert result["provider"] is None
    assert info["context_window_tokens"] is None and info["vision"] is None
    assert discovery.match_provider({"base_url": "https://api.openai.com.evil.test/v1"}, _catalog()) is None
    assert discovery.match_provider({"base_url": "https://different.test/v1"}, _catalog()) == "other"


def test_complete_service_declaration_needs_no_public_request(tmp_path, monkeypatch):
    _sdk(monkeypatch, [{"id": "selected", "context_length": 100000, "max_output_tokens": 20000,
                       "vision": False, "pdf": False, "tools": True}])
    monkeypatch.setattr(discovery, "load_catalog", lambda directory: pytest.fail("unnecessary catalog access"))
    result = discovery.discover_models({"base_url": "https://local.test/v1", "api_key": "x"}, tmp_path)
    assert result["catalog_status"] == "not_needed"
    assert set(result["model_info"]["selected"]["sources"].values()) == {"service"}


def test_repeated_cursor_and_page_limit_are_explicitly_truncated(tmp_path, monkeypatch):
    pages = [SimpleNamespace(data=[{"id": "one"}], has_more=True, last_id="one")]
    calls = _sdk(monkeypatch, [], "anthropic", pages=pages)
    monkeypatch.setattr(discovery, "load_catalog", lambda directory: ({}, "unavailable"))
    result = discovery.discover_models({"base_url": "https://gateway.test", "api_format": "anthropic",
                                       "api_key": "x"}, tmp_path)
    assert result["truncated"] is True
    assert len([item for item in calls if item[0] == "list"]) == 2
    pages = [SimpleNamespace(data=[{"id": str(index)}], has_more=True, last_id=str(index)) for index in range(8)]
    calls = _sdk(monkeypatch, [], "anthropic", pages=pages)
    result = discovery.discover_models({"base_url": "https://gateway.test", "api_format": "anthropic"}, tmp_path)
    assert result["truncated"] is True
    assert len([item for item in calls if item[0] == "list"]) == 5


def test_catalog_weekly_cache_and_failure_preserve_existing(tmp_path, monkeypatch):
    cache = tmp_path / "models-dev.json"
    monkeypatch.setattr(discovery.time, "time", lambda: 1_000_000)
    cache.write_text(json.dumps({"fetched_at": 1_000_000, "providers": _catalog()}))
    monkeypatch.setattr(discovery, "urlopen", lambda *args, **kwargs: pytest.fail("fresh cache must not fetch"))
    assert discovery.load_catalog(tmp_path)[1] == "cached"
    cache.write_text(json.dumps({"fetched_at": 1, "providers": _catalog()}))
    def failure(*args, **kwargs):
        raise OSError("offline")
    monkeypatch.setattr(discovery, "urlopen", failure)
    providers, status = discovery.load_catalog(tmp_path)
    assert providers == _catalog() and status == "stale"
    assert json.loads(cache.read_text())["fetched_at"] == 1


def test_catalog_uses_public_request_without_endpoint_credentials_and_bounds_size(tmp_path, monkeypatch):
    seen = []
    class Reply:
        headers = {}
        def read(self, maximum):
            seen.append(maximum)
            return json.dumps(_catalog()).encode()
        def close(self):
            seen.append("closed")
    def open_public(request, timeout):
        seen.append((request.full_url, request.headers, timeout))
        return Reply()
    monkeypatch.setattr(discovery, "urlopen", open_public)
    providers, status = discovery.load_catalog(tmp_path)
    assert providers == _catalog() and status == "refreshed"
    assert seen[0][0] == "https://models.dev/api.json"
    assert "Authorization" not in seen[0][1] and "X-api-key" not in seen[0][1]
    assert seen[0][2] == 5
    assert seen[1] == discovery.MAX_CATALOG_BYTES + 1
    assert seen[-1] == "closed"
    malformed = tmp_path / "models-dev.json"
    malformed.write_text(json.dumps({"fetched_at": "tomorrow", "providers": _catalog()}))
    assert discovery.load_catalog(tmp_path)[1] == "refreshed"


def test_invalid_route_is_rejected_before_sdk_construction(tmp_path, monkeypatch):
    calls = _sdk(monkeypatch, [])
    with pytest.raises(ValueError):
        discovery.discover_models({"base_url": "https://secret:password@host.test/v1"}, tmp_path)
    assert calls == []
