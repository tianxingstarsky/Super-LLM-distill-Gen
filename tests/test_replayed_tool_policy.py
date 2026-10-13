"""Completed source reads cannot cause new tool calls or hidden-JSON fallback."""
from pathlib import Path
from types import SimpleNamespace as Obj
import importlib.util
import json

import httpx
import pytest

from lib.llm_client import chat_json
from lib.model_streaming import ModelStreamError
from lib.model_request_reliability import ModelJSONError, ModelRequestError


@pytest.fixture(scope="session", autouse=True)
def mock_llm_server():
    yield


_spec = importlib.util.spec_from_file_location("replay_policy_fixtures", Path(__file__).with_name("model_streaming_fixtures.py"))
_helpers = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_helpers)

HISTORY = [{"role": "user", "content": "Load source memory."},
           {"role": "assistant", "content": "", "tool_calls": [{"id": "source-1", "type": "function",
                "function": {"name": "source_memory", "arguments": "{}"}}]},
           {"role": "tool", "name": "source_memory", "tool_call_id": "source-1", "content": "期限30天。"},
           {"role": "user", "content": "Return JSON for the actual question."}]


@pytest.mark.parametrize("api", ["chat", "responses", "anthropic"])
def test_actual_sdk_request_disables_new_tools_only_for_completed_history(api):
    calls = []
    def handle(request):
        calls.append(json.loads(request.content))
        return _helpers.sse_response(_helpers.fixture(api))
    client = _helpers.sdk_client(api, handle)
    try:
        assert chat_json(client, HISTORY, on_stream=lambda event: None) == {"answer": "ok"}
        assert calls[0]["tool_choice"] == ({"type": "none"} if api == "anthropic" else "none")
        assert chat_json(client, [{"role": "user", "content": "JSON"}], on_stream=lambda event: None) == {"answer": "ok"}
        assert "tool_choice" not in calls[1]
    finally:
        client.client.close()


class Body(httpx.SyncByteStream):
    closed = False
    def __init__(self, data):
        self.data = data
    def __iter__(self):
        yield self.data
    def close(self):
        self.closed = True


@pytest.mark.parametrize("api", ["chat", "responses"])
def test_unintended_stream_tool_call_is_rejected_even_when_service_later_claims_completion(api):
    if api == "chat":
        event = {"id": "call", "object": "chat.completion.chunk", "created": 1, "model": "writer",
                 "choices": [{"index": 0, "delta": {"tool_calls": [{"index": 0, "id": "new-tool", "type": "function",
                    "function": {"name": "source_memory", "arguments": "{}"}}]}, "finish_reason": None}]}
        prefix = "data: " + json.dumps(event) + "\n\n"
    else:
        event = {"type": "response.output_item.added", "output_index": 0,
                 "item": {"type": "function_call", "id": "new-tool", "call_id": "new-call",
                          "name": "source_memory", "arguments": "{}", "status": "in_progress"}}
        prefix = "event: response.output_item.added\ndata: " + json.dumps(event) + "\n\n"
    body = Body(prefix.encode() + _helpers.fixture(api))
    calls, emitted = [], []
    def handle(request):
        calls.append(json.loads(request.content))
        return httpx.Response(200, headers={"content-type": "text/event-stream"}, stream=body)
    client = _helpers.sdk_client(api, handle)
    try:
        details = {"stale": "must clear"}
        with pytest.raises(ModelStreamError, match="^model_unexpected_tool_call$"):
            chat_json(client, HISTORY, retries=3, on_stream=emitted.append, response_details=details)
        assert len(calls) == 1 and calls[0]["tool_choice"] == "none"
        assert body.closed and details == {}
        assert not any(event["type"] == "response_completed" for event in emitted)
    finally:
        client.client.close()


@pytest.mark.parametrize("api", ["chat", "responses"])
def test_rejection_of_tool_none_never_retries_without_the_policy(api):
    calls = []
    def handle(request):
        calls.append(json.loads(request.content))
        return httpx.Response(400, json={"error": {"message": "tool_choice none unsupported", "type": "invalid_request_error"}})
    client = _helpers.sdk_client(api, handle)
    try:
        with pytest.raises(ModelRequestError):
            chat_json(client, HISTORY, retries=3)
        assert len(calls) == 1 and calls[0]["tool_choice"] == "none"
    finally:
        client.client.close()


@pytest.mark.parametrize("api", ["chat", "responses"])
def test_complete_nonstream_response_with_a_new_tool_call_is_rejected(api):
    if api == "chat":
        payload = {"id": "reply", "object": "chat.completion", "created": 1, "model": "writer",
                   "choices": [{"index": 0, "finish_reason": "stop", "message": {"role": "assistant",
                    "content": '{"answer":"ok"}', "tool_calls": [{"id": "new", "type": "function",
                    "function": {"name": "source_memory", "arguments": "{}"}}]}}]}
    else:
        payload = {"id": "reply", "object": "response", "created_at": 1, "model": "writer", "status": "completed",
                   "output": [{"type": "message", "id": "answer", "role": "assistant", "status": "completed",
                               "content": [{"type": "output_text", "text": '{"answer":"ok"}', "annotations": []}]},
                              {"type": "function_call", "id": "new", "call_id": "new-call", "name": "source_memory",
                               "arguments": "{}", "status": "completed"}]}
    calls = []
    def handle(request):
        calls.append(json.loads(request.content))
        return httpx.Response(200, json=payload)
    client = _helpers.sdk_client(api, handle)
    try:
        with pytest.raises(ModelStreamError, match="^model_unexpected_tool_call$"):
            chat_json(client, HISTORY, retries=3)
        assert len(calls) == 1 and calls[0]["tool_choice"] == "none"
    finally:
        client.client.close()


@pytest.mark.parametrize("failure", [ValueError("model_visible_output_missing"),
                                    ModelRequestError("model_visible_output_missing", "invalid", 1)])
def test_reasoning_only_completed_json_call_retries_with_clean_metadata_and_exact_bound(failure):
    calls = []
    class Client:
        def chat(self, messages, **kwargs):
            details = kwargs.get("response_details")
            assert details == {}
            details["reasoning_content"] = "PRIVATE_NATIVE_OLD"
            calls.append((messages, kwargs))
            if len(calls) < 3:
                raise failure
            details.clear()
            details["reasoning_content"] = "final native"
            return '{"answer":"ok"}'
    details = {"stale": "discard"}
    assert chat_json(Client(), HISTORY, retries=3, response_details=details) == {"answer": "ok"}
    assert details == {"reasoning_content": "final native"} and len(calls) == 3
    assert all(messages is HISTORY and kwargs["allow_reasoning_fallback"] is False for messages, kwargs in calls)


def test_reasoning_only_failure_without_metadata_still_has_a_bounded_json_retry():
    calls = []
    class Client:
        def chat(self, messages, **kwargs):
            calls.append(kwargs)
            raise ValueError("model_visible_output_missing")
    with pytest.raises(ModelJSONError, match="2"):
        chat_json(Client(), HISTORY, retries=2, allow_reasoning_fallback=False)
    assert len(calls) == 2 and all(kwargs["allow_reasoning_fallback"] is False for kwargs in calls)


@pytest.mark.parametrize("failure", [ValueError("invalid_model_configuration"), RuntimeError("unknown error"),
    ModelRequestError("model_visible_output_missing", "transient", 1),
    ModelRequestError("service_authentication_failed", "authentication", 1), ModelStreamError("model_stream_incomplete")])
def test_json_retry_does_not_capture_other_execution_errors(failure):
    calls = []
    class Client:
        def chat(self, messages, **kwargs):
            calls.append(kwargs)
            raise failure
    details = {"stale": "discard"}
    with pytest.raises(type(failure)) as caught:
        chat_json(Client(), HISTORY, retries=3, response_details=details)
    assert caught.value is failure and len(calls) == 1 and details == {}


def test_stream_callback_cancellation_with_the_same_message_is_never_retried():
    failure = ValueError("model_visible_output_missing")
    calls = []
    class Client:
        def chat(self, messages, **kwargs):
            calls.append(kwargs)
            kwargs["on_stream"]({"type": "delta"})
    def cancel(event):
        raise failure
    with pytest.raises(ValueError) as caught:
        chat_json(Client(), HISTORY, retries=3, on_stream=cancel)
    assert caught.value is failure and len(calls) == 1
