"""Real SDK SSE decoding and interrupted-output safety, with no network."""
from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace as Obj

import httpx
import pytest

from lib.llm_client import BudgetGuard, ChatClient, chat_json
from lib.model_streaming import ModelStreamError, consume_stream

import importlib.util

_spec = importlib.util.spec_from_file_location("model_streaming_fixtures", Path(__file__).with_name("model_streaming_fixtures.py"))
_helpers = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_helpers)
fixture, sdk_client, sse_response, unfinished = (_helpers.fixture, _helpers.sdk_client,
                                               _helpers.sse_response, _helpers.unfinished)
MESSAGES = [{"role": "user", "content": "Return JSON"}]


@pytest.mark.parametrize("api", ["chat", "responses", "anthropic"])
def test_actual_sdk_streaming_deltas_usage_and_request_format(api):
    calls, events = [], []
    def handle(request):
        calls.append(json.loads(request.content))
        return sse_response(fixture(api))
    client = sdk_client(api, handle)
    try:
        assert chat_json(client, MESSAGES, max_tokens=32768, on_stream=events.append) == {"answer": "ok"}
        assert calls[0]["stream"] is True
        assert calls[0]["max_output_tokens" if api == "responses" else "max_tokens"] == 32768
        assert client.usage == {"prompt_tokens": 17, "completion_tokens": 8, "calls": 1}
        assert events[0] == {"type": "start", "api_format": api}
        assert "".join(event["text"] for event in events if event.get("channel") == "text") == '{"answer":"ok"}'
        assert "".join(event["text"] for event in events if event.get("channel") == "reasoning") == "Check the source."
        assert events[-1]["type"] == "response_completed"
        assert "SUPER-SECRET" not in json.dumps(events)
    finally:
        client.client.close()


@pytest.mark.parametrize("api", ["chat", "responses", "anthropic"])
def test_valid_json_prefix_without_protocol_completion_is_rejected(api):
    calls, events = [], []
    def handle(request):
        calls.append(request)
        return sse_response(unfinished(api))
    client = sdk_client(api, handle)
    try:
        with pytest.raises(ModelStreamError, match="^model_stream_incomplete$"):
            chat_json(client, MESSAGES, on_stream=events.append)
        assert len(calls) == 1  # Never silently replay a partially received request.
        assert '{"answer":"ok"}' == "".join(event["text"] for event in events if event.get("channel") == "text")
        assert not any(event["type"] == "response_completed" for event in events)
    finally:
        client.client.close()


class BrokenSSE(httpx.SyncByteStream):
    def __init__(self, data):
        self.data, self.closed = data, False
    def __iter__(self):
        yield self.data
        raise httpx.ReadError("connection dropped SUPER-SECRET-KEY")
    def close(self):
        self.closed = True


@pytest.mark.parametrize("api", ["chat", "responses", "anthropic"])
def test_disconnect_keeps_visible_prefix_closes_transport_and_books_budget(api, tmp_path):
    body, events = BrokenSSE(unfinished(api)), []
    client = sdk_client(api, lambda request: httpx.Response(200, headers={"content-type": "text/event-stream"}, stream=body))
    client.price_input = client.price_output = 1.0
    client.budget = BudgetGuard(tmp_path, 1, hard_stop=True)
    try:
        with pytest.raises(ModelStreamError, match="^model_stream_interrupted$"):
            chat_json(client, MESSAGES, max_tokens=32768, on_stream=events.append)
        assert body.closed
        booked = json.loads((tmp_path / "data/output/budget.json").read_text(encoding="utf-8"))
        assert booked["spent_usd"] == pytest.approx((131072 + 32768) / 1e6)
        assert not booked["reservations"]
        assert any(event.get("channel") == "text" for event in events)
        assert "SUPER-SECRET" not in json.dumps(events)
    finally:
        client.client.close()


@pytest.mark.parametrize("api,events", [
    ("chat", [{"choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}]},
              {"choices": [{"index": 0, "delta": {"content": "late"}, "finish_reason": None}]}]),
    ("responses", [{"type": "response.completed", "response": {"status": "completed"}},
                   {"type": "response.output_text.delta", "delta": "late"}]),
    ("anthropic", [{"type": "message_delta", "delta": {"stop_reason": "end_turn"}},
                   {"type": "message_stop"},
                   {"type": "content_block_delta", "delta": {"type": "text_delta", "text": "late"}}]),
])
def test_late_content_after_terminal_is_rejected(api, events):
    with pytest.raises(ModelStreamError, match="model_stream_invalid_event"):
        consume_stream(iter(events), api, lambda event: None)


@pytest.mark.parametrize("api", ["chat", "responses", "anthropic"])
def test_output_limit_is_not_a_success_even_with_valid_json(api):
    data = fixture(api)
    if api == "chat":
        data = data.replace(b'"finish_reason":"stop"', b'"finish_reason":"length"')
    elif api == "responses":
        data = data.replace(b"response.completed", b"response.incomplete").replace(b'"status":"completed"', b'"status":"incomplete"')
    else:
        data = data.replace(b'"stop_reason":"end_turn"', b'"stop_reason":"max_tokens"')
    client = sdk_client(api, lambda request: sse_response(data))
    try:
        with pytest.raises(ModelStreamError, match="model_stream_incomplete"):
            chat_json(client, MESSAGES, on_stream=lambda event: None)
    finally:
        client.client.close()


@pytest.mark.parametrize("api", ["chat", "responses", "anthropic"])
def test_explicit_pre_stream_rejection_falls_back_and_remembers_capability(api):
    calls, events = [], []
    full = {"chat": {"id": "chat-test", "object": "chat.completion", "created": 1, "model": "writer",
                     "choices": [{"index": 0, "message": {"role": "assistant", "content": '{"answer":"ok"}'}, "finish_reason": "stop"}]},
            "responses": {"id": "resp-test", "object": "response", "created_at": 1, "status": "completed", "model": "writer",
                          "output": [{"type": "message", "id": "msg-test", "role": "assistant", "status": "completed",
                                      "content": [{"type": "output_text", "text": '{"answer":"ok"}', "annotations": []}]}]},
            "anthropic": {"id": "msg-test", "type": "message", "role": "assistant", "model": "writer",
                          "content": [{"type": "text", "text": '{"answer":"ok"}'}], "stop_reason": "end_turn",
                          "stop_sequence": None, "usage": {"input_tokens": 1, "output_tokens": 1}}}[api]
    def handle(request):
        body = json.loads(request.content)
        calls.append(body)
        if "stream" in body:
            return httpx.Response(400, json={"error": {"message": "stream is not supported SUPER-SECRET-KEY", "type": "invalid_request_error"}})
        return httpx.Response(200, json=full)
    client = sdk_client(api, handle)
    try:
        for _ in range(2):
            assert chat_json(client, MESSAGES, on_stream=events.append) == {"answer": "ok"}
        assert len(calls) == 3 and calls[0]["stream"] is True
        assert "stream" not in calls[1] and "stream" not in calls[2]
        assert events[-1]["type"] == "response_completed"
        assert "SUPER-SECRET" not in json.dumps(events)
    finally:
        client.client.close()


def test_callback_cancellation_propagates_without_retry_and_closes_stream():
    class Cancelled(Exception):
        pass
    body, calls = BrokenSSE(unfinished("chat")), []
    def handle(request):
        calls.append(request)
        return httpx.Response(200, headers={"content-type": "text/event-stream"}, stream=body)
    client = sdk_client("chat", handle)
    def cancel(event):
        if event["type"] == "delta":
            raise Cancelled()
    try:
        with pytest.raises(Cancelled):
            chat_json(client, MESSAGES, on_stream=cancel)
        assert len(calls) == 1 and body.closed
    finally:
        client.client.close()
