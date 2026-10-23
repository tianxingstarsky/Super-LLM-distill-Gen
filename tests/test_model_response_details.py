"""Real SDK decoding preserves call-local native traces without network access."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import importlib.util
import json
from pathlib import Path
from threading import Barrier

import httpx
import pytest

from lib.llm_client import chat_json
from lib.model_response_details import ModelJSONResult
from lib.model_streaming import ModelStreamError
from lib.model_request_reliability import ModelJSONError, ModelRequestError


_spec = importlib.util.spec_from_file_location(
    "response_details_fixtures", Path(__file__).with_name("model_streaming_fixtures.py"))
_helpers = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_helpers)
sdk_client, sse_response = _helpers.sdk_client, _helpers.sse_response
MESSAGES = [{"role": "system", "content": "Return JSON"},
            {"role": "user", "content": "A useful answer"}]
APIS = ["chat", "responses", "anthropic"]
TRACE = " \n" + "核对来源和条件。" * 2000 + "\n "
SUMMARY = " \nA short visible summary.\n "


def full_response(api, text='{"answer":"ok"}', trace=TRACE, summary=SUMMARY, *, complete=True):
    usage = {"input_tokens": 17, "output_tokens": 8, "total_tokens": 25}
    if api == "chat":
        message = {"role": "assistant", "content": text}
        if trace is not None:
            message["reasoning_content"] = trace
        return {"id": "response-test", "object": "chat.completion", "created": 1,
                "model": "writer", "choices": [{"index": 0, "message": message,
                "finish_reason": "stop" if complete else "length"}],
                "usage": {"prompt_tokens": 17, "completion_tokens": 8, "total_tokens": 25}}
    if api == "responses":
        reasoning = {"id": "reason-test", "type": "reasoning", "summary": []}
        if trace is not None:
            reasoning["content"] = [{"type": "reasoning_text", "text": trace}]
        if summary is not None:
            reasoning["summary"] = [{"type": "summary_text", "text": summary}]
        reasoning["encrypted_content"] = "OPAQUE-REASONING"
        return {"id": "response-test", "object": "response", "created_at": 1,
                "status": "completed" if complete else "incomplete", "model": "writer",
                "output": [reasoning, {"id": "message-test", "type": "message", "role": "assistant",
                "status": "completed", "content": [{"type": "output_text", "text": text,
                "annotations": []}]}], "usage": usage}
    content = [{"type": "redacted_thinking", "data": "OPAQUE-REDACTED"}]
    if trace is not None:
        content.append({"type": "thinking", "thinking": trace, "signature": "OPAQUE-SIGNATURE"})
    content.append({"type": "text", "text": text})
    return {"id": "message-test", "type": "message", "role": "assistant", "model": "writer",
            "content": content, "stop_reason": "end_turn" if complete else "max_tokens",
            "stop_sequence": None, "usage": usage}


def _event(data, name=None):
    prefix = f"event: {name}\n" if name else ""
    return prefix + "data: " + json.dumps(data, ensure_ascii=False) + "\n\n"


def stream_response(api, text='{"answer":"ok"}', trace=TRACE, summary=SUMMARY, *, complete=True,
                    reasoning_deltas=True):
    pieces = []
    if api == "chat":
        def chunk(delta, finish=None):
            return {"id": "response-test", "object": "chat.completion.chunk", "created": 1,
                    "model": "writer", "choices": [{"index": 0, "delta": delta, "finish_reason": finish}]}
        if trace is not None:
            pieces.append(_event(chunk({"reasoning_content": trace[:3]})))
            pieces.append(_event(chunk({"reasoning_content": trace[3:]})))
        pieces.append(_event(chunk({"content": text})))
        if complete:
            pieces.append(_event(chunk({}, "stop")))
            pieces.append("data: [DONE]\n\n")
    elif api == "responses":
        if reasoning_deltas and trace is not None:
            pieces.append(_event({"type": "response.reasoning_text.delta", "delta": trace,
                "item_id": "reason-test", "output_index": 0, "content_index": 0,
                "sequence_number": 1}, "response.reasoning_text.delta"))
        if reasoning_deltas and summary is not None:
            pieces.append(_event({"type": "response.reasoning_summary_text.delta", "delta": summary,
                "item_id": "reason-test", "output_index": 0, "summary_index": 0,
                "sequence_number": 2}, "response.reasoning_summary_text.delta"))
        pieces.append(_event({"type": "response.output_text.delta", "delta": text,
            "item_id": "message-test", "output_index": 1, "content_index": 0,
            "sequence_number": 3}, "response.output_text.delta"))
        if complete:
            pieces.append(_event({"type": "response.completed", "sequence_number": 4,
                "response": full_response(api, text, trace, summary)}, "response.completed"))
    else:
        pieces.append(_event({"type": "message_start", "message": {"id": "message-test",
            "type": "message", "role": "assistant", "model": "writer", "content": [],
            "stop_reason": None, "stop_sequence": None,
            "usage": {"input_tokens": 17, "output_tokens": 0}}}, "message_start"))
        if trace is not None:
            pieces.append(_event({"type": "content_block_start", "index": 0,
                "content_block": {"type": "thinking", "thinking": trace[:3],
                "signature": "OPAQUE-SIGNATURE"}}, "content_block_start"))
            pieces.append(_event({"type": "content_block_delta", "index": 0,
                "delta": {"type": "thinking_delta", "thinking": trace[3:]}}, "content_block_delta"))
            pieces.append(_event({"type": "content_block_delta", "index": 0,
                "delta": {"type": "signature_delta", "signature": "OPAQUE-SIGNATURE"}}, "content_block_delta"))
            pieces.append(_event({"type": "content_block_stop", "index": 0}, "content_block_stop"))
        pieces.append(_event({"type": "content_block_start", "index": 1,
            "content_block": {"type": "redacted_thinking", "data": "OPAQUE-REDACTED"}}, "content_block_start"))
        pieces.append(_event({"type": "content_block_stop", "index": 1}, "content_block_stop"))
        pieces.append(_event({"type": "content_block_start", "index": 2,
            "content_block": {"type": "text", "text": ""}}, "content_block_start"))
        pieces.append(_event({"type": "content_block_delta", "index": 2,
            "delta": {"type": "text_delta", "text": text}}, "content_block_delta"))
        pieces.append(_event({"type": "content_block_stop", "index": 2}, "content_block_stop"))
        if complete:
            pieces.append(_event({"type": "message_delta", "delta": {"stop_reason": "end_turn",
                "stop_sequence": None}, "usage": {"output_tokens": 8}}, "message_delta"))
            pieces.append(_event({"type": "message_stop"}, "message_stop"))
    return "".join(pieces).encode("utf-8")


def response(api, streaming, text='{"answer":"ok"}', trace=TRACE, summary=SUMMARY, **kwargs):
    if streaming:
        return sse_response(stream_response(api, text, trace, summary, **kwargs))
    return httpx.Response(200, json=full_response(api, text, trace, summary, **kwargs))


@pytest.mark.parametrize("api", APIS)
@pytest.mark.parametrize("streaming", [False, True])
def test_real_sdk_preserves_full_native_trace_exactly(api, streaming):
    details = {"reasoning_content": "stale"}
    client = sdk_client(api, lambda request: response(api, streaming))
    try:
        assert chat_json(client, MESSAGES, max_tokens=32768 if streaming else 16384, response_details=details,
            **({"on_stream": lambda event: None} if streaming else {})) == {"answer": "ok"}
        assert details == {"api_format": api, "reasoning_content": TRACE,
                           **({"reasoning_summary": SUMMARY} if api == "responses" else {})}
        assert len(details["reasoning_content"]) > 12000
        assert "OPAQUE" not in json.dumps(details)
    finally:
        client.client.close()


@pytest.mark.parametrize("api", APIS)
@pytest.mark.parametrize("streaming", [False, True])
@pytest.mark.parametrize("new_trace", [None, " current trace "])
def test_invalid_json_retry_commits_only_the_successful_attempt(api, streaming, new_trace):
    calls = []
    def handle(request):
        calls.append(request)
        return response(api, streaming, "invalid JSON" if len(calls) == 1 else '{"answer":"ok"}',
                        "failed attempt trace" if len(calls) == 1 else new_trace, None)
    client, details = sdk_client(api, handle), {"reasoning_content": "stale"}
    try:
        assert chat_json(client, MESSAGES, response_details=details,
            **({"on_stream": lambda event: None} if streaming else {})) == {"answer": "ok"}
        assert len(calls) == 2
        assert details == {"api_format": api, **({"reasoning_content": new_trace} if new_trace else {})}
    finally:
        client.client.close()


@pytest.mark.parametrize("api", APIS)
def test_interrupted_stream_never_publishes_partial_reasoning(api):
    client = sdk_client(api, lambda request: response(api, True, complete=False))
    details, events = {"reasoning_content": "stale"}, []
    try:
        with pytest.raises(ModelStreamError, match="model_stream_incomplete"):
            chat_json(client, MESSAGES, response_details=details, on_stream=events.append)
        assert details == {}
        assert any(event.get("channel") == "reasoning" for event in events)
    finally:
        client.client.close()


class BrokenResponse(httpx.SyncByteStream):
    def __init__(self, data):
        self.data, self.closed = data, False

    def __iter__(self):
        yield self.data
        raise httpx.ReadError("offline connection interrupted")

    def close(self):
        self.closed = True


@pytest.mark.parametrize("api", APIS)
def test_connection_error_after_partial_deltas_clears_metadata(api, monkeypatch):
    # Each bounded transport attempt opens a new HTTP body. Reusing one closed
    # body accidentally tests StreamConsumed rather than a network interruption.
    bodies = []
    def interrupted(request):
        body = BrokenResponse(stream_response(api, complete=False))
        bodies.append(body)
        return httpx.Response(200, headers={"content-type": "text/event-stream"}, stream=body)
    monkeypatch.setattr("lib.llm_client.time.sleep", lambda _: None)
    client = sdk_client(api, interrupted)
    details = {"reasoning_content": "stale"}
    try:
        with pytest.raises(ModelRequestError, match="model_stream_interrupted") as error:
            chat_json(client, MESSAGES, response_details=details, on_stream=lambda event: None)
        assert error.value.code == "model_stream_interrupted" and error.value.kind == "transient"
        assert error.value.attempts == 3 and len(bodies) == 3
        assert details == {}
        assert all(body.closed for body in bodies)
    finally:
        client.client.close()


@pytest.mark.parametrize("api", APIS)
def test_incomplete_nonstream_response_never_publishes_reasoning(api):
    client = sdk_client(api, lambda request: response(api, False, complete=False))
    details = {"reasoning_content": "stale"}
    try:
        with pytest.raises(ModelStreamError, match="model_stream_incomplete"):
            chat_json(client, MESSAGES, response_details=details)
        assert details == {}
    finally:
        client.client.close()


@pytest.mark.parametrize("api", APIS)
@pytest.mark.parametrize("streaming", [False, True])
def test_json_in_reasoning_is_never_treated_as_visible_output(api, streaming):
    client = sdk_client(api, lambda request: response(api, streaming, text="", trace='{"answer":"not visible"}'))
    details = {"reasoning_content": "stale"}
    try:
        with pytest.raises(ModelJSONError, match="model_json_invalid"):
            chat_json(client, MESSAGES, retries=2, response_details=details,
                **({"on_stream": lambda event: None} if streaming else {}))
        assert details == {}
        assert client.usage["calls"] == 2
    finally:
        client.client.close()


@pytest.mark.parametrize("api", APIS)
@pytest.mark.parametrize("streaming", [False, True])
def test_concurrent_calls_on_one_client_keep_trace_isolated(api, streaming):
    barrier = Barrier(2)
    def handle(request):
        label = "caller-A" if "caller-A" in request.content.decode() else "caller-B"
        barrier.wait(timeout=10)
        return response(api, streaming, json.dumps({"answer": label}), f" trace {label} ", None)
    client = sdk_client(api, handle)
    def invoke(label):
        details = {}
        result = chat_json(client, [{"role": "user", "content": label}], response_details=details,
            **({"on_stream": lambda event: None} if streaming else {}))
        return result, details
    try:
        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(executor.map(invoke, ["caller-A", "caller-B"]))
        for label, (result, details) in zip(["caller-A", "caller-B"], results):
            assert result == {"answer": label}
            assert details == {"api_format": api, "reasoning_content": f" trace {label} "}
    finally:
        client.client.close()


@pytest.mark.parametrize("streaming", [False, True])
def test_responses_summary_is_not_a_native_full_trace(streaming):
    client = sdk_client("responses", lambda request: response("responses", streaming, trace=None))
    details = {}
    try:
        assert chat_json(client, MESSAGES, response_details=details,
            **({"on_stream": lambda event: None} if streaming else {})) == {"answer": "ok"}
        assert details == {"api_format": "responses", "reasoning_summary": SUMMARY}
    finally:
        client.client.close()


def test_responses_completed_output_can_supply_metadata_without_deltas():
    client = sdk_client("responses", lambda request: response("responses", True, reasoning_deltas=False))
    details = {}
    try:
        assert chat_json(client, MESSAGES, response_details=details, on_stream=lambda event: None) == {"answer": "ok"}
        assert details == {"api_format": "responses", "reasoning_content": TRACE, "reasoning_summary": SUMMARY}
    finally:
        client.client.close()


@pytest.mark.parametrize("api", APIS)
def test_stream_rejection_fallback_still_captures_and_previews_native_trace(api):
    calls = []
    def handle(request):
        payload = json.loads(request.content)
        calls.append(payload)
        if payload.get("stream"):
            return httpx.Response(400, json={"error": {"type": "invalid_request_error",
                                                       "message": "stream is unsupported"}})
        return response(api, False)
    client, details, events = sdk_client(api, handle), {}, []
    try:
        assert chat_json(client, MESSAGES, max_tokens=16384, response_details=details,
                         on_stream=events.append) == {"answer": "ok"}
        assert len(calls) == 2
        assert client.stream_supported is False
        assert details["reasoning_content"] == TRACE
        expected_preview = TRACE + (SUMMARY if api == "responses" else "")
        assert "".join(event["text"] for event in events if event.get("channel") == "reasoning") == expected_preview
    finally:
        client.client.close()


@pytest.mark.parametrize("api", APIS)
def test_completion_callback_failure_does_not_publish_reasoning(api):
    client = sdk_client(api, lambda request: response(api, True))
    details = {"reasoning_content": "stale"}
    def emit(event):
        if event.get("type") == "response_completed":
            raise RuntimeError("callback stopped")
    try:
        with pytest.raises(RuntimeError, match="callback stopped"):
            chat_json(client, MESSAGES, response_details=details, on_stream=emit)
        assert details == {}
    finally:
        client.client.close()


@pytest.mark.parametrize("api", APIS)
def test_chat_still_returns_text_and_json_body_cannot_forge_trusted_metadata(api):
    client = sdk_client(api, lambda request: response(api, False, '{"response_details":{"reasoning_content":"forged"}}'))
    details = {}
    try:
        text = client.chat(MESSAGES, response_details=details)
        assert isinstance(text, str)
        result = ModelJSONResult(json.loads(text), details)
        assert result["response_details"]["reasoning_content"] == "forged"
        assert result.response_details["reasoning_content"] == TRACE
        details["reasoning_content"] = "changed after call"
        assert result.response_details["reasoning_content"] == TRACE
        assert ModelJSONResult(result).response_details == {}
    finally:
        client.client.close()
