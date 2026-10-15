"""No-network transport diagnosis without provider text or credentials."""
import json
from types import SimpleNamespace

import httpx
import pytest
from openai import APIConnectionError, BadRequestError

from lib.llm_client import ChatClient
from lib.model_request_reliability import ModelRequestError, safe_request_diagnostic
from lib.model_streaming import ModelStreamError


def _client():
    return ChatClient("https://offline.invalid/v1?secret=PRIVATE", "PRIVATE-KEY", "offline")


def _request():
    return httpx.Request("POST", "https://offline.invalid/v1?secret=PRIVATE",
                         headers={"Authorization": "Bearer PRIVATE-KEY"})


def test_diagnostic_preserves_only_structural_cause_chain():
    try:
        try:
            raise httpx.RemoteProtocolError("PRIVATE body source and authorization")
        except Exception as cause:
            raise APIConnectionError(request=_request()) from cause
    except Exception as error:
        result = safe_request_diagnostic(error, phase="headers", elapsed_seconds=602.68)
    assert result == {"phase": "headers", "exception_types": ["APIConnectionError", "RemoteProtocolError"],
        "elapsed_seconds": 602.68, "headers_received": False, "headers_elapsed_seconds": None,
        "first_output_elapsed_seconds": None}
    assert "PRIVATE" not in json.dumps(result)


def test_only_allowlisted_response_fields_are_returned():
    response = httpx.Response(503, request=_request(), headers={
        "x-request-id": "req_123-abc", "authorization": "PRIVATE-KEY", "set-cookie": "PRIVATE"},
        json={"error": {"message": "PRIVATE"}})
    error = BadRequestError("PRIVATE", response=response, body={"private": "PRIVATE"})
    result = safe_request_diagnostic(error, phase="headers", elapsed_seconds=1.1)
    assert result["headers_received"] is True
    assert result["http_status"] == 503
    assert result["provider_request_id"] == "req_123-abc"
    assert "PRIVATE" not in json.dumps(result)


@pytest.mark.parametrize("request_id", ["PRIVATE/with/slashes", "bad value", "a" * 129, "https://private.invalid?q=key"])
def test_invalid_provider_request_id_is_omitted(request_id):
    result = safe_request_diagnostic(RuntimeError("PRIVATE"), phase="stream", elapsed_seconds=1,
        response=SimpleNamespace(status_code=200, headers={"x-request-id": request_id}))
    assert "provider_request_id" not in result


def test_unknown_exception_names_and_invalid_values_cannot_become_output():
    error = type("PRIVATE_SECRET", (Exception,), {})("PRIVATE")
    error.__cause__ = error
    result = safe_request_diagnostic(error, phase="PRIVATE", elapsed_seconds=float("nan"),
        response=SimpleNamespace(status_code=True, headers={}),
        headers_elapsed_seconds=-1, first_output_elapsed_seconds=float("inf"))
    assert result["exception_types"] == ["OtherError"]
    assert result["phase"] == "unknown"
    assert result["elapsed_seconds"] is None
    assert result["headers_elapsed_seconds"] is None
    assert result["first_output_elapsed_seconds"] is None
    assert "http_status" not in result
    assert "PRIVATE" not in json.dumps(result)


def test_header_failure_survives_safe_outer_error_without_changing_request():
    client, observed = _client(), []
    def create(**kwargs):
        observed.append(kwargs)
        try:
            raise httpx.ReadError("PRIVATE network details")
        except Exception as cause:
            raise APIConnectionError(request=_request()) from cause
    client.client.chat.completions.create = create
    messages = [{"role": "system", "content": "Use JSON"}, {"role": "user", "content": "test"}]
    with pytest.raises(ModelRequestError) as caught:
        client.chat(messages, max_tokens=32768, retries=1, json_mode=True, on_stream=lambda _: None)
    error = caught.value
    assert error.code == "service_connection_failed"
    assert error.diagnostics["phase"] == "headers"
    assert error.diagnostics["headers_received"] is False
    assert error.diagnostics["exception_types"] == ["APIConnectionError", "ReadError"]
    assert error.diagnostics["elapsed_seconds"] >= 0
    assert "PRIVATE" not in json.dumps(error.diagnostics)
    assert len(observed) == 1
    assert observed[0]["messages"] == messages
    assert observed[0]["response_format"] == {"type": "json_object"}
    assert observed[0]["stream"] is True
    assert observed[0]["max_tokens"] == 32768
    assert "extra_body" not in observed[0]


@pytest.mark.parametrize("produce_delta", [False, True])
def test_stream_failure_is_distinct_and_preserves_timing(produce_delta):
    client, events = _client(), []
    class Stream:
        response = SimpleNamespace(status_code=200, headers={"x-request-id": "req_stream"})
        def __iter__(self):
            if produce_delta:
                yield {"choices": [{"index": 0, "delta": {"content": "partial"}}]}
            raise httpx.RemoteProtocolError("PRIVATE socket details")
        def close(self):
            pass
    client.client.chat.completions.create = lambda **_: Stream()
    with pytest.raises(ModelRequestError, match="model_stream_interrupted") as caught:
        client.chat([{"role": "user", "content": "test"}], retries=1, on_stream=events.append)
    diagnostic = caught.value.diagnostics
    assert diagnostic["phase"] == "stream"
    assert diagnostic["headers_received"] is True
    assert diagnostic["headers_elapsed_seconds"] >= 0
    assert (diagnostic["first_output_elapsed_seconds"] is not None) is produce_delta
    assert diagnostic["http_status"] == 200
    assert diagnostic["provider_request_id"] == "req_stream"
    assert diagnostic["exception_types"] == ["ModelStreamError", "RemoteProtocolError"]
    assert "PRIVATE" not in json.dumps(diagnostic)
    assert not any(event.get("type") == "response_completed" for event in events)


def test_success_adds_no_events_or_transport_fields():
    client, events = _client(), []
    class Stream:
        response = SimpleNamespace(status_code=200, headers={"x-request-id": "req_ok"})
        def __iter__(self):
            yield {"choices": [{"index": 0, "delta": {"content": "ok"}, "finish_reason": "stop"}]}
        def close(self):
            pass
    client.client.chat.completions.create = lambda **_: Stream()
    assert client.chat([{"role": "user", "content": "test"}], retries=1, on_stream=events.append) == "ok"
    assert events == [{"type": "start", "api_format": "chat"},
        {"type": "delta", "channel": "text", "text": "ok"},
        {"type": "response_completed", "prompt_tokens": 0, "completion_tokens": 0}]
