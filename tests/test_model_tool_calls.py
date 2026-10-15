"""Real SDK serialization for admitted tool turns, entirely in MockTransport."""
from copy import deepcopy
from contextlib import contextmanager
import importlib.util
import json
from pathlib import Path

import httpx
import pytest

from lib.llm_client import BudgetGuard
from lib.model_request_reliability import ModelRequestError

_spec = importlib.util.spec_from_file_location("model_tool_calls_fixtures", Path(__file__).with_name("model_streaming_fixtures.py"))
_helpers = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_helpers)
sdk_client = _helpers.sdk_client


@pytest.fixture(scope="session", autouse=True)
def mock_llm_server():
    # No local or remote model endpoint is opened by this test module.
    yield


TOOLS = [{"type": "function", "function": {"name": "source_read", "description": "Read exact source lines.",
    "parameters": {"type": "object", "properties": {"start": {"type": "integer"}}, "required": ["start"]}}}]
MESSAGES = [{"role": "system", "content": "Use read-only tools, then return visible JSON."},
            {"role": "user", "content": "Inspect the source."}]
THOUGHT = "  Check the original lines.\n"
ANSWER = ' {"answer":"verified"} '


def reply(api, *, calls=True, thought=THOUGHT, content=ANSWER):
    usage = {"prompt_tokens": 17, "completion_tokens": 8} if api == "chat" else {"input_tokens": 17, "output_tokens": 8}
    call = {"id": "call-1", "type": "function", "function": {"name": "source_read", "arguments": '{"start":1}'}}
    if api == "chat":
        return {"id": "chat-1", "object": "chat.completion", "created": 1, "model": "writer", "usage": usage,
            "choices": [{"index": 0, "finish_reason": "tool_calls" if calls else "stop", "message": {
                "role": "assistant", "content": None if calls else content, "reasoning_content": thought,
                "tool_calls": [call] if calls else None}}]}
    if api == "responses":
        output = [{"type": "reasoning", "id": "rs-1", "summary": [{"type": "summary_text", "text": "Source check"}],
                   "content": [{"type": "reasoning_text", "text": thought}], "encrypted_content": "opaque-ciphertext"}]
        output += ([{"type": "function_call", "id": "fc-1", "call_id": "call-1", "name": "source_read",
                    "arguments": '{"start":1}', "status": "completed"}] if calls else
                   [{"type": "message", "id": "msg-1", "role": "assistant", "status": "completed", "content": [
                       {"type": "output_text", "text": content, "annotations": []}]}])
        return {"id": "resp-1", "object": "response", "created_at": 1, "model": "writer", "status": "completed",
                "output": output, "usage": usage}
    blocks = [{"type": "thinking", "thinking": thought, "signature": "untouched-signature"},
              {"type": "redacted_thinking", "data": "opaque-redacted-data"}]
    blocks += ([{"type": "tool_use", "id": "call-1", "name": "source_read", "input": {"start": 1}}]
               if calls else [{"type": "text", "text": content}])
    return {"id": "msg-1", "type": "message", "role": "assistant", "model": "writer", "content": blocks,
            "stop_reason": "tool_use" if calls else "end_turn", "stop_sequence": None, "usage": usage}


@pytest.mark.parametrize("api", ["chat", "responses", "anthropic"])
def test_sdk_round_trip_preserves_calls_ids_and_provider_reasoning_without_mutation(api):
    captured, events = [], []
    first_response = reply(api)
    def handle(request):
        captured.append(json.loads(request.content))
        return httpx.Response(200, json=first_response if len(captured) == 1 else reply(api, calls=False))
    client = sdk_client(api, handle)
    messages, tools = deepcopy(MESSAGES), deepcopy(TOOLS)
    before = deepcopy((messages, tools))
    details = {"stale": "discard"}
    try:
        first = client.tool_turn(messages, tools, max_tokens=128, response_details=details, on_stream=events.append)
        assert first["content"] == "" and first["reasoning_content"] == THOUGHT
        assert first["tool_calls"] == [{"id": "call-1", "type": "function",
            "function": {"name": "source_read", "arguments": '{"start":1}'}}]
        assert details["reasoning_content"] == THOUGHT
        history = [*messages, first, {"role": "tool", "tool_call_id": "call-1", "content": '{"lines":"real result"}'}]
        history_before = deepcopy(history)
        final = client.tool_turn(history, tools, max_tokens=128)
        assert final["content"] == ANSWER and not final["tool_calls"]
        assert history == history_before and (messages, tools) == before
        assert all("stream" not in request for request in captured)
        assert all("response_format" not in request and "text" not in request for request in captured)
        assert [e["type"] for e in events] == ["start", "delta", "response_completed"]
        assert events[1] == {"type": "delta", "channel": "reasoning",
            "text": THOUGHT + ("Source check" if api == "responses" else ""), "delivery": "complete_response"}
        assert events[-1]["tool_calls"] == 1
        assert client.usage == {"prompt_tokens": 34, "completion_tokens": 16, "calls": 2}
        second = captured[1]
        if api == "chat":
            assert second["tool_choice"] == "auto"
            assert second["messages"][2]["reasoning_content"] == THOUGHT
            assert second["messages"][3]["tool_call_id"] == "call-1"
            assert second["tools"] == TOOLS
        elif api == "responses":
            assert second["tool_choice"] == "auto" and second["store"] is False
            assert second["input"][2:4] == first["provider_state"]["output_items"]
            assert second["input"][-1] == {"type": "function_call_output", "call_id": "call-1", "output": '{"lines":"real result"}'}
            assert second["tools"][0]["name"] == "source_read"
        else:
            assert second["tool_choice"] == {"type": "auto"}
            assert second["messages"][1]["content"] == first["provider_state"]["content_blocks"]
            assert second["messages"][-1]["content"] == [{"type": "tool_result", "tool_use_id": "call-1", "content": '{"lines":"real result"}'}]
    finally:
        client.client.close()


def test_deepseek_preserves_reasoning_from_all_prior_assistants_including_final_turn():
    captured = []
    def handle(request):
        captured.append(json.loads(request.content))
        return httpx.Response(200, json=reply("chat", calls=False, thought="earlier" if len(captured) == 1 else "later"))
    client = sdk_client("chat", handle)
    client.base_url = "https://api.deepseek.com/v1"
    client.reasoning = {"mode": "enabled"}
    try:
        first = client.tool_turn(MESSAGES, TOOLS)
        client.tool_turn([*MESSAGES, first, {"role": "user", "content": "Next real request"}], TOOLS)
        assert captured[1]["messages"][2]["reasoning_content"] == "earlier"
        assert captured[1]["tool_choice"] == "auto"
        assert captured[1]["thinking"] == {"type": "enabled"}
    finally:
        client.client.close()


def test_persistent_final_assistant_omits_empty_calls_on_wire_but_keeps_native_history():
    """A no-tool final reply must remain replayable in the next tool-enabled turn."""
    captured = []
    def handle(request):
        body = json.loads(request.content)
        captured.append(body)
        if any(message.get("tool_calls") == [] for message in body["messages"]):
            return httpx.Response(400, json={"error": {"type": "invalid_request_error",
                "code": "invalid_request_error", "param": "messages.3.tool_calls",
                "message": "Assistant tool_calls must not be empty when present."}})
        return httpx.Response(200, json=reply("chat", calls=False, thought="exact native replay\n"))
    client = sdk_client("chat", handle)
    client.base_url = "https://api.deepseek.com/v1"
    client.reasoning = {"mode": "enabled"}
    try:
        initial = [*MESSAGES[:1], {"role": "system", "content": "Source memory."}, *MESSAGES[1:]]
        first = client.tool_turn(initial, TOOLS)
        assert first["tool_calls"] == []
        history = [*initial, first, {"role": "user", "content": "Continue the same real task."}]
        before = deepcopy(history)
        second = client.tool_turn(history, TOOLS)
        assert second["content"] == ANSWER
        sent = captured[1]["messages"][3]
        assert "tool_calls" not in sent
        assert sent["content"] == first["content"]
        assert sent["reasoning_content"] == "exact native replay\n"
        assert history == before and history[3]["tool_calls"] == []
        assert captured[1]["tools"] == TOOLS and captured[1]["tool_choice"] == "auto"
    finally:
        client.client.close()


@pytest.mark.parametrize("api", ["chat", "responses", "anthropic"])
def test_hidden_reasoning_is_never_a_final_plan(api):
    data = reply(api, calls=False, content="", thought='{"plan":"hidden"}')
    client = sdk_client(api, lambda request: httpx.Response(200, json=data))
    details = {"stale": True}
    try:
        with pytest.raises(ValueError, match="model_visible_output_missing"):
            client.tool_turn(MESSAGES, TOOLS, response_details=details)
        assert details == {}
    finally:
        client.client.close()


@pytest.mark.parametrize("api", ["chat", "responses", "anthropic"])
@pytest.mark.parametrize("broken", ["incomplete", "bad_arguments", "unlisted_tool"])
def test_incomplete_or_unexecutable_calls_never_escape_to_executor(api, broken):
    data = reply(api)
    if api == "chat":
        call = data["choices"][0]["message"]["tool_calls"][0]["function"]
        if broken == "incomplete": data["choices"][0]["finish_reason"] = "length"
        elif broken == "bad_arguments": call["arguments"] = "{"
        else: call["name"] = "forbidden"
    elif api == "responses":
        call = data["output"][-1]
        if broken == "incomplete": data["status"] = "incomplete"
        elif broken == "bad_arguments": call["arguments"] = "[]"
        else: call["name"] = "forbidden"
    else:
        call = data["content"][-1]
        if broken == "incomplete": data["stop_reason"] = "max_tokens"
        elif broken == "bad_arguments": call["input"] = []
        else: call["name"] = "forbidden"
    calls = []
    def handle(request):
        calls.append(request)
        return httpx.Response(200, json=data)
    client = sdk_client(api, handle)
    try:
        with pytest.raises(ValueError): client.tool_turn(MESSAGES, TOOLS)
        assert len(calls) == 1
        assert client.usage == {"calls": 1, "prompt_tokens": 17, "completion_tokens": 8}
    finally:
        client.client.close()


@pytest.mark.parametrize("api", ["responses", "anthropic"])
def test_tampered_or_cross_protocol_opaque_history_rejected_before_admission(api):
    client = sdk_client(api, lambda request: httpx.Response(200, json=reply(api)))
    try:
        first = client.tool_turn(MESSAGES, TOOLS)
        first["content"] = "invented edited body"
        history = [*MESSAGES, first, {"role": "tool", "tool_call_id": "call-1", "content": "result"}]
        client.client.close()
        with pytest.raises(ValueError, match="model_tool_history_state_mismatch"):
            client.tool_turn(history, TOOLS)
        first["provider_state"]["api_format"] = "chat"
        with pytest.raises(ValueError, match="invalid_model_tool_provider_state"):
            client.tool_turn(history, TOOLS)
    finally:
        client.client.close()


@pytest.mark.parametrize("api", ["chat", "responses", "anthropic"])
def test_schema_and_historical_thinking_are_counted_before_admission(api):
    client = sdk_client(api, lambda request: pytest.fail("context must fail before request"))
    client.context_window_tokens = 700
    try:
        tools = deepcopy(TOOLS)
        tools[0]["function"]["description"] = "x" * 1000
        with pytest.raises(ValueError, match="model_context_window_exceeded"):
            client.tool_turn(MESSAGES, tools, max_tokens=100)
        if api == "chat":
            message = {"role": "assistant", "content": "prior", "reasoning_content": "x" * 1000}
        else:
            from lib.model_tool_calls import _assistant
            message = _assistant(reply(api, calls=False, thought="x" * 1000), api, {"source_read"})
        with pytest.raises(ValueError, match="model_context_window_exceeded"):
            client.tool_turn([*MESSAGES, message, {"role": "user", "content": "continue"}], TOOLS, max_tokens=100)
    finally:
        client.client.close()


def test_invalid_tools_unpaired_history_and_beta_prefix_fail_without_queue_or_network():
    client = sdk_client("chat", lambda request: pytest.fail("must not be requested"))
    class Never:
        def acquire(self, **kwargs): pytest.fail("must not acquire")
    client.scheduler = Never()
    try:
        with pytest.raises(ValueError, match="invalid_model_tools"):
            client.tool_turn(MESSAGES, [*TOOLS, *TOOLS])
        with pytest.raises(ValueError, match="orphan_model_tool_result"):
            client.tool_turn([*MESSAGES, {"role": "tool", "tool_call_id": "absent", "content": "fake"}], TOOLS)
        with pytest.raises(ValueError, match="model_tool_turn_native_prefill_unsupported"):
            client.tool_turn([*MESSAGES, {"role": "assistant", "prefix": True, "content": "", "reasoning_content": "prefix"}], TOOLS)
        client.model_info = {"tools": False}
        with pytest.raises(ValueError, match="model_tools_unsupported"):
            client.tool_turn(MESSAGES, TOOLS)
    finally:
        client.client.close()


def test_shared_admission_and_budget_are_used_and_429_retries_are_bounded(tmp_path, monkeypatch):
    calls, trace = [], []
    def handle(request):
        calls.append(json.loads(request.content))
        if len(calls) == 1:
            return httpx.Response(429, json={"error": {"message": "rate limited SECRET", "type": "rate_limit"}})
        return httpx.Response(200, json=reply("chat"))
    class Scheduler:
        @contextmanager
        def acquire(self, **kwargs):
            trace.append("acquire")
            kwargs["cancel_check"]()
            yield
            trace.append("release")
        def defer(self, delay): trace.append("defer")
    client = sdk_client("chat", handle)
    client.scheduler = Scheduler()
    client.budget = BudgetGuard(tmp_path, 1, hard_stop=True)
    client.price_input = client.price_output = 1
    client.context_window_tokens = 4000
    monkeypatch.setattr("lib.model_tool_calls.retry_delay", lambda *args: 0)
    try:
        assert client.tool_turn(MESSAGES, TOOLS, max_tokens=100)["tool_calls"]
        assert len(calls) == 2 and trace == ["acquire", "defer", "release", "acquire", "release"]
        assert client.budget._state()["spent_usd"] == pytest.approx(25 / 1e6)
        assert not client.budget._state()["reservations"]
    finally:
        client.client.close()


def test_permanent_errors_do_not_retry_or_expose_provider_payload():
    calls = []
    def handle(request):
        calls.append(request)
        return httpx.Response(402, json={"error": {"message": "quota exhausted SECRET"}})
    client = sdk_client("chat", handle)
    try:
        with pytest.raises(ModelRequestError, match="service_quota_exhausted") as error:
            client.tool_turn(MESSAGES, TOOLS)
        assert len(calls) == 1 and "SECRET" not in str(error.value)
    finally:
        client.client.close()


@pytest.mark.parametrize("failure_event", ["delta", "response_completed"])
def test_completion_callback_failure_does_not_publish_details_or_retry(failure_event):
    calls = []
    def handle(request):
        calls.append(request)
        return httpx.Response(200, json=reply("chat"))
    client = sdk_client("chat", handle)
    details = {"stale": True}
    class Cancelled(Exception): pass
    def observe(event):
        if event["type"] == failure_event: raise Cancelled()
    try:
        with pytest.raises(Cancelled):
            client.tool_turn(MESSAGES, TOOLS, response_details=details, on_stream=observe)
        assert len(calls) == 1 and details == {} and client.usage["calls"] == 1
    finally:
        client.client.close()


@pytest.mark.parametrize("api", ["chat", "responses", "anthropic"])
def test_nonstream_tool_turn_journal_preserves_complete_real_text_and_reasoning(api, tmp_path):
    from lib.infrastructure.workflow_stream_journal import StreamJournal, read_stream_delta, read_streams
    data = reply(api, calls=False)
    journal = StreamJournal(tmp_path, stage="director", unit="knowledge-plan", role="generation", run_attempt=1)
    events = []
    def observe(event):
        events.append(deepcopy(event))
        journal(event)
    client = sdk_client(api, lambda request: httpx.Response(200, json=data))
    try:
        assistant = client.tool_turn(MESSAGES, TOOLS, on_stream=observe)
        expected_reasoning = THOUGHT + ("Source check" if api == "responses" else "")
        assert [event["type"] for event in events] == ["start", "delta", "delta", "response_completed"]
        assert events[1]["text"] == expected_reasoning and events[2]["text"] == ANSWER
        assert all(event["delivery"] == "complete_response" for event in events[1:])
        journal.completed()
        row = read_streams(tmp_path, active=False, run_attempt=1)[0]
        assert row["text"] == assistant["content"] == ANSWER
        assert row["reasoning"] == expected_reasoning
        assert row["text_chars"] == len(ANSWER) and row["reasoning_chars"] == len(expected_reasoning)
        saved = read_stream_delta(tmp_path, row["id"], active=False, run_attempt=1, stage="director")
        assert saved["events"] == [{"channel": "reasoning", "text": expected_reasoning},
                                   {"channel": "text", "text": ANSWER}]
        assert "opaque-ciphertext" not in row["reasoning"] and "opaque-redacted-data" not in row["reasoning"]
    finally:
        journal.interrupted()
        client.client.close()


@pytest.mark.parametrize("api", ["responses", "anthropic"])
def test_opaque_only_continuity_is_replayed_and_counted(api):
    captured = []
    data = reply(api, calls=False, thought="")
    if api == "responses":
        data["output"][0]["encrypted_content"] = "encrypted" * 200
    else:
        data["content"][0]["signature"] = "signature" * 200
    def handle(request):
        captured.append(json.loads(request.content))
        return httpx.Response(200, json=data)
    client = sdk_client(api, handle)
    try:
        first = client.tool_turn(MESSAGES, TOOLS)
        history = [*MESSAGES, first, {"role": "user", "content": "continue"}]
        client.tool_turn(history, TOOLS)
        if api == "responses":
            assert captured[1]["input"][2]["encrypted_content"] == "encrypted" * 200
        else:
            assert captured[1]["messages"][1]["content"][0]["signature"] == "signature" * 200
        client.context_window_tokens = 1000
        with pytest.raises(ValueError, match="model_context_window_exceeded"):
            client.tool_turn(history, TOOLS, max_tokens=100)
        assert len(captured) == 2
    finally:
        client.client.close()


def test_chat_provider_reasoning_details_are_round_tripped():
    captured = []
    data = reply("chat", calls=False)
    opaque = [{"type": "reasoning.encrypted", "data": "opaque", "signature": "unchanged"}]
    data["choices"][0]["message"]["reasoning_details"] = opaque
    def handle(request):
        captured.append(json.loads(request.content))
        return httpx.Response(200, json=data)
    client = sdk_client("chat", handle)
    try:
        first = client.tool_turn(MESSAGES, TOOLS)
        client.tool_turn([*MESSAGES, first, {"role": "user", "content": "continue"}], TOOLS)
        assert captured[1]["messages"][2]["reasoning_details"] == opaque
        assert "provider_state" not in captured[1]["messages"][2]
    finally:
        client.client.close()


@pytest.mark.parametrize("api", ["chat", "responses", "anthropic"])
def test_tools_capability_rejection_never_retries_as_text_or_charges_rejected_request(api, tmp_path):
    calls = []
    def handle(request):
        calls.append(json.loads(request.content))
        return httpx.Response(400, json={"error": {"message": "tools is not supported SECRET", "type": "invalid_request_error"}})
    client = sdk_client(api, handle)
    client.budget = BudgetGuard(tmp_path, 1, hard_stop=True)
    client.context_window_tokens = 4000
    client.price_input = client.price_output = 1
    try:
        with pytest.raises(ValueError, match="^model_tools_unsupported$"):
            client.tool_turn(MESSAGES, TOOLS, max_tokens=100)
        assert len(calls) == 1 and "tools" in calls[0]
        assert client.budget._state()["spent_usd"] == 0
        assert not client.budget._state()["reservations"]
    finally:
        client.client.close()


def test_parameter_negotiation_uses_same_tool_request_and_existing_sdk():
    calls = []
    def handle(request):
        body = json.loads(request.content)
        calls.append(body)
        if "max_tokens" in body:
            return httpx.Response(400, json={"error": {"message": "max_tokens is not supported; use max_completion_tokens"}})
        if "temperature" in body:
            return httpx.Response(400, json={"error": {"message": "temperature is not supported"}})
        return httpx.Response(200, json=reply("chat"))
    client = sdk_client("chat", handle)
    try:
        assert client.tool_turn(MESSAGES, TOOLS, max_tokens=100)["tool_calls"]
        assert len(calls) == 3
        assert all(r["tools"] == TOOLS and r["tool_choice"] == "auto" for r in calls)
        assert calls[-1]["max_completion_tokens"] == 100
    finally:
        client.client.close()


def test_cancellation_before_admission_is_propagated_without_network():
    client = sdk_client("chat", lambda request: pytest.fail("cancelled request must not run"))
    class Cancelled(Exception): pass
    def cancel(): raise Cancelled()
    client.cancel_check = cancel
    try:
        with pytest.raises(Cancelled): client.tool_turn(MESSAGES, TOOLS)
        assert client.usage["calls"] == 0
    finally:
        client.client.close()
