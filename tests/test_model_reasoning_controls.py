"""Node reasoning selections reach real SDK payloads without provider calls."""
import json

import httpx
import pytest

from lib.domain.model_capabilities import merge_model_info, normalize_model_info
from lib.domain.workflow_scale import validate_node_models
from lib.llm_client import chat_json, load_backend
from lib.model_protocols import reasoning_request
from model_streaming_fixtures import sdk_client


DEEPSEEK = {"effort": {"supported_levels": ["low", "high", "max"], "default_level": "high"}}


def _response(protocol):
    if protocol == "chat":
        return {"id": "test", "object": "chat.completion", "created": 1, "model": "writer",
                "choices": [{"index": 0, "finish_reason": "stop", "message": {"role": "assistant", "content": '{"ok":true}'}}]}
    if protocol == "responses":
        return {"id": "test", "object": "response", "created_at": 1, "status": "completed",
                "output": [{"id": "msg", "type": "message", "role": "assistant", "status": "completed",
                            "content": [{"type": "output_text", "text": '{"ok":true}', "annotations": []}]}]}
    return {"id": "test", "type": "message", "role": "assistant", "model": "writer", "stop_reason": "end_turn",
            "content": [{"type": "text", "text": '{"ok":true}'}], "usage": {"input_tokens": 3, "output_tokens": 3}}


@pytest.mark.parametrize("protocol", ["chat", "responses", "anthropic"])
@pytest.mark.parametrize("mode", ["auto", "enabled", "disabled"])
def test_node_selection_reaches_sdk_wire_and_auto_preserves_provider_default(protocol, mode):
    captured = []
    def handler(request):
        captured.append(json.loads(request.content))
        return httpx.Response(200, json=_response(protocol))
    client = sdk_client(protocol, handler)
    client.model_info = normalize_model_info("writer", DEEPSEEK, "service")
    client.reasoning = {"mode": mode, **({"effort": "max"} if mode == "enabled" else {})}
    try:
        assert chat_json(client, [{"role": "system", "content": "Return JSON"},
                                  {"role": "user", "content": "Work"}], max_tokens=4096) == {"ok": True}
    finally:
        client.client.close()
    assert len(captured) == 1
    payload = captured[0]
    assert "extra_body" not in payload
    if mode == "auto":
        assert not {"thinking", "reasoning", "reasoning_effort", "output_config"} & payload.keys()
    elif protocol == "responses":
        assert payload["reasoning"] == {"effort": "max" if mode == "enabled" else "none"}
    else:
        assert payload["thinking"] == {"type": mode}
        if mode == "enabled":
            if protocol == "chat":
                assert payload["reasoning_effort"] == "max"
            else:
                assert payload["output_config"] == {"effort": "max"}
                assert "temperature" not in payload


def test_service_discovery_preserves_effort_order_default_and_source():
    info = normalize_model_info("arbitrary-model-name", DEEPSEEK, "service")
    assert info["reasoning_efforts"] == ["low", "high", "max"]
    assert info["default_reasoning_effort"] == "high"
    assert info["reasoning_format"] == "deepseek"
    assert info["reasoning_disabled"] is True
    assert info["sources"]["reasoning_efforts"] == "service"
    assert merge_model_info("arbitrary-model-name", info)["reasoning_efforts"] == ["low", "high", "max"]
    for record in ({"reasoning": True}, {"effort": {"supported_levels": "high"}},
                   {"effort": {"supported_levels": ["high", 5]}}):
        assert normalize_model_info("deepseek-v4-pro", record, "service")["reasoning_efforts"] is None


def test_anthropic_declared_adaptive_is_distinct_from_deepseek_and_budget_thinking():
    info = normalize_model_info("unrelated-name", {"capabilities": {
        "effort": {"supported": True, "low": {"supported": True}, "high": {"supported": True}, "max": {"supported": False}},
        "thinking": {"supported": True, "types": {"adaptive": {"supported": True},
                     "enabled": {"supported": False}, "disabled": {"supported": False}}}}}, "service")
    assert info["reasoning_efforts"] == ["low", "high"]
    assert reasoning_request({"mode": "enabled", "effort": "high"}, api_format="anthropic", model_info=info) == {
        "thinking": {"type": "adaptive"}, "extra_body": {"output_config": {"effort": "high"}}}
    with pytest.raises(ValueError, match="model_reasoning_disable_unsupported"):
        reasoning_request({"mode": "disabled"}, api_format="anthropic", model_info=info)
    with pytest.raises(ValueError, match="model_reasoning_budget_unsupported"):
        reasoning_request({"mode": "enabled", "budget_tokens": 1024}, api_format="anthropic", model_info=info, max_tokens=4096)
    with pytest.raises(ValueError, match="model_reasoning_format_unknown"):
        reasoning_request({"mode": "enabled", "effort": "high"}, api_format="anthropic", base_url="https://gateway.test")
    assert reasoning_request({"mode": "enabled", "budget_tokens": 1024}, api_format="anthropic", max_tokens=4096) == {
        "thinking": {"type": "enabled", "budget_tokens": 1024}}


@pytest.mark.parametrize("reasoning,code", [
    ({"mode": "auto", "effort": "high"}, "invalid_model_reasoning"),
    ({"mode": "enabled", "effort": "none"}, "invalid_model_reasoning_effort"),
    ({"mode": "enabled", "effort": "high", "budget_tokens": 2048}, "invalid_model_reasoning"),
    ({"mode": "enabled", "budget_tokens": 4096}, "model_reasoning_budget_exceeds_output"),
    ({"mode": "enabled", "budget_tokens": True}, "invalid_model_reasoning_budget"),
    ({"mode": {}}, "invalid_model_reasoning"),
])
def test_invalid_node_reasoning_is_rejected_before_persistence(reasoning, code):
    with pytest.raises(ValueError, match=code):
        validate_node_models({"sft": {"generation": {"backend": "x", "model": "x", "max_output_tokens": 4096,
                                                     "reasoning": reasoning}}})


def test_bindings_preserve_per_node_reasoning_without_changing_legacy_defaults():
    old = {"backend": "service", "model": "writer"}
    config = validate_node_models({"sft": {"generation": old}, "cot": {"generation": {
        **old, "reasoning": {"mode": "enabled", "effort": "max"}}}})
    assert "reasoning" not in config["sft"]["generation"]
    assert config["cot"]["generation"]["reasoning"] == {"mode": "enabled", "effort": "max"}


def test_explicit_unsupported_thinking_is_not_retried_without_the_setting():
    captured = []
    def handler(request):
        captured.append(json.loads(request.content))
        return httpx.Response(400, json={"error": {"message": "thinking is not supported", "type": "invalid_request_error"}})
    client = sdk_client("chat", handler)
    client.reasoning = {"mode": "enabled", "effort": "high"}
    client.model_info = normalize_model_info("writer", DEEPSEEK, "service")
    try:
        with pytest.raises(ValueError, match="model_reasoning_parameter_unsupported"):
            client.chat([{"role": "user", "content": "Work"}], retries=1)
    finally:
        client.client.close()
    assert len(captured) == 1
    assert captured[0]["thinking"] == {"type": "enabled"}


def test_load_backend_reads_bound_discovery_metadata_without_network(tmp_path, monkeypatch):
    from lib.bootstrap.backends import backend_application
    from lib.infrastructure.backend_config_driver import FilesystemBackendConfigDriver
    monkeypatch.delenv("LLM_BASE_URL", raising=False)
    app = backend_application(tmp_path)
    app.save_endpoint("chosen", "https://gateway.test/v1", ["writer"], api_key="offline", api_format="anthropic")
    endpoint = app.merged_backends()["chosen"]
    FilesystemBackendConfigDriver(tmp_path).write_model_cache("chosen", endpoint, {
        "models": ["writer"], "model_info": {"writer": normalize_model_info("writer", DEEPSEEK, "service")}})
    client, _ = load_backend(tmp_path, backend="chosen", model="writer", reasoning={"mode": "enabled", "effort": "max"})
    try:
        assert client.reasoning == {"mode": "enabled", "effort": "max"}
        assert client.model_info["reasoning_format"] == "deepseek"
    finally:
        client.client.close()


def test_document_cache_reuses_same_effort_but_reparses_after_effort_change(tmp_path):
    from lib.infrastructure import training_workflow as engine
    from tests.test_document_model_parser import PAGE_ONE, Reader, configure, model_parser, text_pdf
    configure(tmp_path)
    source = text_pdf(tmp_path, pages=(PAGE_ONE,))
    reader = Reader()
    output = tmp_path / "output"
    for reasoning in (None, {"mode": "auto"}, {"mode": "enabled", "effort": "low"},
                      {"mode": "enabled", "effort": "low"}, {"mode": "enabled", "effort": "max"}):
        parser = model_parser()
        if reasoning is not None:
            parser["binding"]["reasoning"] = reasoning
        run_id = engine.create_run(output, sources=[source], targets=["cpt"],
                                   settings_root=tmp_path, document_parser=parser)
        workflow = engine.Workflow(output, run_id, tmp_path, generator=reader)
        list(workflow.iter_source_units(workflow.recipe["sources"][0]))
    assert len(reader.calls) == 3
