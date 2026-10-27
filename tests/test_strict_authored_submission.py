"""Strict submission uses real SDK SSE framing; all requests stay in MockTransport."""
from copy import deepcopy
import json

import httpx
import pytest
from jsonschema import Draft202012Validator

from lib.contexts.workflows.domain.authored_submission import (
    SUBMIT_AUTHORED, authored_submission_tool, assemble_authored_submission,
)
from lib.contexts.workflows.domain.authored_trace_transport import prepare_authored_response
from lib.contexts.models.domain.request_errors import ModelStreamError, ModelOutputLimitError, ModelJSONError
from model_streaming_fixtures import sdk_client, sse_response
from test_authored_reasoning import derived_candidate


@pytest.fixture(scope="session", autouse=True)
def mock_llm_server():
    yield


def submission(payload=None):
    return {"role": "assistant", "content": "", "tool_calls": [{"id": "call-submit", "type": "function",
        "function": {"name": SUBMIT_AUTHORED, "arguments": json.dumps(payload or derived_candidate(), ensure_ascii=False)}}]}


def stream_bytes(assistant, *, finish="tool_calls", native="原生思考\nNative reasoning.", usage=True):
    chunks = []
    def add(delta, stop=None):
        chunks.append({"id": "chat-strict", "object": "chat.completion.chunk", "created": 1,
            "model": "deepseek-flash", "choices": [{"index": 0, "delta": delta, "finish_reason": stop}]})
    add({"role": "assistant", "content": ""})
    for fragment in (native[:5], native[5:]):
        add({"reasoning_content": fragment})
    for index, call in enumerate(assistant.get("tool_calls", [])):
        add({"tool_calls": [{"index": index, "id": call["id"], "type": "function",
            "function": {"name": call["function"]["name"], "arguments": ""}}]})
        arguments = call["function"]["arguments"]
        for start in range(0, len(arguments), 31):
            add({"tool_calls": [{"index": index, "function": {"arguments": arguments[start:start+31]}}]})
    if assistant.get("content"):
        add({"content": assistant["content"]})
    add({}, finish)
    if usage:
        chunks[-1]["usage"] = {"prompt_tokens": 123, "completion_tokens": 456,
            "prompt_cache_hit_tokens": 100, "completion_tokens_details": {"reasoning_tokens": 321}}
    return ("".join("data: " + json.dumps(c, ensure_ascii=False) + "\n\n" for c in chunks) + "data: [DONE]\n\n").encode()


def test_wire_strict_stream_is_scoped_and_preserves_full_native_and_authored_output():
    captured, events = [], []
    payload = derived_candidate()
    payload["reasoning"] += '\n完整行："引号"，\\frac{x}{2}；\t中文与 English。' * 120
    raw = submission(payload)
    def handle(request):
        captured.append((str(request.url), json.loads(request.content)))
        return sse_response(stream_bytes(raw))
    client = sdk_client("chat", handle)
    client.base_url = "https://api.deepseek.com/v1"
    client.reasoning = {"mode": "enabled", "effort": "high"}
    sdk_url = str(client.client.base_url)
    details = {}
    history = [{"role": "system", "content": "Exact source."}, {"role": "user", "content": "我的问题。"}]
    before = deepcopy(history)
    try:
        response = client.tool_turn(history, [authored_submission_tool(answer_required=True)],
            strict=True, response_details=details, max_tokens=32768, retries=1, on_stream=events.append)
        assert assemble_authored_submission(response, answer_required=True) == payload
        assert response["reasoning_content"] == "原生思考\nNative reasoning."
        assert details["reasoning_content"] == response["reasoning_content"]
        assert details["strict_tool_response"]["choices"][0]["message"]["tool_calls"][0]["function"]["arguments"] == raw["tool_calls"][0]["function"]["arguments"]
        assert len(captured) == 1 and client.usage == {"calls": 1, "prompt_tokens": 123, "completion_tokens": 456}
        endpoint, sent = captured[0]
        assert endpoint == "https://api.deepseek.com/beta/chat/completions"
        assert sent["tool_choice"] == "auto" and sent["thinking"]["type"] == "enabled"
        assert sent["reasoning_effort"] == "high" and "response_format" not in sent
        assert sent["tools"][0]["function"]["strict"] is True
        assert '"minItems"' not in json.dumps(sent["tools"]) and '"minLength"' not in json.dumps(sent["tools"])
        assert str(client.client.base_url) == sdk_url and client.base_url == "https://api.deepseek.com/v1"
        assert history == before
        assert "".join(e["text"] for e in events if e.get("tool_arguments")) == raw["tool_calls"][0]["function"]["arguments"]
        assert "".join(e["text"] for e in events if e.get("channel") == "reasoning") == response["reasoning_content"]
        assert events[-1]["type"] == "response_completed" and all(e.get("delivery") != "complete_response" for e in events)
    finally:
        client.client.close()


@pytest.mark.parametrize("finish,error", [("length", ModelOutputLimitError), (None, ModelStreamError)])
def test_incomplete_arguments_never_admitted_or_retried(finish, error):
    requests, events, details = [], [], {}
    raw = submission()
    raw["tool_calls"][0]["function"]["arguments"] = '{"reasoning":"unfinished'
    def handle(request):
        requests.append(request)
        return sse_response(stream_bytes(raw, finish=finish))
    client = sdk_client("chat", handle)
    try:
        with pytest.raises(error):
            client.tool_turn([{"role": "user", "content": "test"}], [authored_submission_tool(answer_required=True)],
                strict=True, retries=3, response_details=details, on_stream=events.append)
        assert len(requests) == 1 and client.usage["calls"] == 1
        assert details["incomplete_response"]["details"]["strict_tool_response"]
        assert not any(e["type"] == "response_completed" for e in events)
    finally:
        client.client.close()


@pytest.mark.parametrize("bad", ["empty_reasoning", "empty_steps", "missing_check", "extra_field", "duplicate_field", "prose", "multiple", "unassigned"])
def test_bad_submission_has_no_guessing_or_semantic_waiver(bad):
    payload = derived_candidate()
    if bad == "empty_reasoning": payload["reasoning"] = " "
    if bad == "empty_steps": payload["reasoning_trace"]["steps"] = []
    if bad == "missing_check": del payload["reasoning_trace"]["checks"]
    if bad == "extra_field": payload["keep"] = True
    response = submission(payload)
    if bad == "duplicate_field":
        response["tool_calls"][0]["function"]["arguments"] = '{"reasoning":"old",' + response["tool_calls"][0]["function"]["arguments"][1:]
    if bad == "prose": response.update(content=json.dumps(payload), tool_calls=[])
    if bad == "multiple": response["tool_calls"].append(deepcopy(response["tool_calls"][0]))
    if bad == "unassigned": response["content"] = "An additional argument outside the submitted rationale."
    before = deepcopy(response)
    with pytest.raises(ValueError):
        assemble_authored_submission(response, answer_required=True)
    assert response == before


def test_strict_schema_keeps_local_minima_and_content_checks():
    tool = authored_submission_tool(answer_required=False)
    Draft202012Validator.check_schema(tool["function"]["parameters"])
    payload = derived_candidate(); payload.pop("answer")
    step = payload["reasoning_trace"]["steps"][0]
    step["basis"] = step["result"]
    candidate = assemble_authored_submission(submission(payload), answer_required=False)
    _, receipt = prepare_authored_response(candidate, answer_required=False)
    assert receipt["content_issue"] == "authored_reasoning_circular_support"
    assert receipt["semantic_status"] == "not_assessed"


def test_unsupported_strict_endpoint_does_not_silently_retry_without_constraint():
    requests = []
    def handle(request):
        requests.append(request)
        return httpx.Response(400, json={"error": {"message": "unsupported tools", "param": "tools", "type": "invalid_request_error"}})
    client = sdk_client("chat", handle)
    try:
        with pytest.raises(Exception):
            client.tool_turn([{"role": "user", "content": "test"}], [authored_submission_tool(answer_required=True)], strict=True, retries=1)
        assert len(requests) == 1
    finally:
        client.client.close()


def test_retry_error_reports_attempts_separately():
    error = ModelJSONError(1)
    assert error.attempts == 1 and error.retries == 0 and "0 次重试" in str(error)


def test_workflow_request_checkpoints_program_assembly_without_rebuying(tmp_path):
    from test_source_memory_requests import workflow
    captured = []
    def handle(request):
        captured.append(json.loads(request.content))
        return sse_response(stream_bytes(submission()))
    client = sdk_client("chat", handle)
    worker = workflow(tmp_path, client)
    # Explicit test fixture selects the new frozen protocol, not a provider-name heuristic.
    worker.recipe.update(version=36, authored_submission_version=1, authored_stage_contract_version=1)
    worker.stage = "cot"
    key = ["exact-case", "strict"]
    data = {"learner_messages": [{"role": "user", "content": "我的问题。"}]}
    try:
        result = worker.ask(key, "generation", "workflow.cot_generate", data)
        assert result == derived_candidate()
        assert worker.ask(key, "generation", "workflow.cot_generate", data) == result
        assert len(captured) == 1
        assert captured[0]["messages"][-1]["role"] == "system"
        assert "submit_authored_candidate" in captured[0]["messages"][-1]["content"]
    finally:
        client.client.close()


def test_observer_failure_during_strict_stream_is_not_a_model_retry():
    requests = []
    def handle(request):
        requests.append(request)
        return sse_response(stream_bytes(submission()))
    client = sdk_client("chat", handle)
    sentinel = RuntimeError("observer storage failure")
    def observe(event):
        if event["type"] == "delta":
            raise sentinel
    try:
        with pytest.raises(RuntimeError) as caught:
            client.tool_turn([{"role": "user", "content": "test"}], [authored_submission_tool(answer_required=True)],
                strict=True, retries=3, on_stream=observe)
        assert caught.value is sentinel and len(requests) == 1
    finally:
        client.client.close()


def test_invalid_completed_submission_checkpoint_does_not_purchase_again(tmp_path):
    from test_source_memory_requests import workflow
    payload = derived_candidate(); payload["reasoning_trace"]["checks"] = []
    requests = []
    def handle(request):
        requests.append(request)
        return sse_response(stream_bytes(submission(payload)))
    client = sdk_client("chat", handle)
    worker = workflow(tmp_path, client)
    worker.recipe.update(version=36, authored_submission_version=1, authored_stage_contract_version=1)
    worker.stage = "cot"
    try:
        for _ in range(2):
            with pytest.raises(ValueError, match="authored_submission_schema_invalid"):
                worker.ask(["bad-checks"], "generation", "workflow.cot_generate", {})
        assert len(requests) == 1
    finally:
        client.client.close()


def test_source_lookup_can_finish_with_strict_submission_and_preserve_tool_history():
    from test_generation_source_lookup import SourcePort, tool, paper, LOOKUP
    from lib.contexts.workflows.application.generate_with_sources import GenerateWithSources
    port = SourcePort([tool(), submission()])
    candidate, receipt = GenerateWithSources(port, LOOKUP, strict_submission=True).execute(
        ["strict-source"], "workflow.cot_generate", {"source": paper()}, "Use original facts.")
    assert candidate == derived_candidate() and receipt["status"] == "completed"
    assert len(port.sent) == 2 and len(port.searches) == 1
    assert port.sent[1][0][-1]["role"] == "tool"
    assert port.sent[1][0][-2]["reasoning_content"] == tool()["reasoning_content"]


def test_sdk_accumulator_bookkeeping_never_leaks_into_tool_history_wire():
    requests = []
    def handle(request):
        requests.append(json.loads(request.content))
        return sse_response(stream_bytes(submission()))
    client = sdk_client("chat", handle)
    messages = [{"role": "user", "content": "Inspect."}]
    tools = [authored_submission_tool(answer_required=True)]
    try:
        first = client.tool_turn(messages, tools, strict=True)
        client.tool_turn([*messages, first, {"role": "tool", "tool_call_id": "call-submit", "content": "Recorded."}], tools, strict=True)
        sent = requests[1]["messages"][1]
        assert sent["reasoning_content"] == first["reasoning_content"]
        call, = sent["tool_calls"]
        assert set(call) == {"id", "type", "function"}
        assert set(call["function"]) == {"name", "arguments"}
        assert call["function"]["arguments"] == submission()["tool_calls"][0]["function"]["arguments"]
    finally:
        client.client.close()


def test_new_official_deepseek_runs_pin_submission_without_migrating_other_endpoints(tmp_path, monkeypatch):
    from pathlib import Path
    from test_workflow_styled_generation import run
    from test_integrated_reflection import recipe
    from lib.infrastructure import training_workflow as engine
    (tmp_path / "configs").mkdir()
    (tmp_path / "configs/preferences.yaml").write_bytes((Path(__file__).parents[1] / "configs/preferences.yaml").read_bytes())
    monkeypatch.setattr(engine, "snapshot_backend_endpoint", lambda *a: {
        "endpoint_origin": "https://api.deepseek.com", "api_format": "chat"})
    output, run_id, path = run(tmp_path, targets=["cot"], conversation=True,
        node_generation=recipe()["node_generation"], settings_root=tmp_path,
        node_models={"cot": {"generation": {"backend": "test", "model": "writer"}}})
    frozen = engine.read_json(path / "recipe.json")
    assert frozen["version"] == 39 and frozen["authored_submission_version"] == 1
    assert frozen["authored_task_contract_version"] == 3
    assert frozen["cot_style_transfer_version"] == 2
