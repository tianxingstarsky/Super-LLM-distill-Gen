"""Real SDK transport interruptions stay operational failures, not bad samples."""
import importlib.util
import json
from pathlib import Path

import httpx
import pytest

from lib.infrastructure import training_workflow as engine
from lib.model_request_reliability import ModelRequestError, classify_request_error
from lib.model_streaming import ModelStreamError


_spec = importlib.util.spec_from_file_location("transport_recovery_fixtures",
    Path(__file__).with_name("model_streaming_fixtures.py"))
_helpers = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_helpers)


class DisconnectedBody(httpx.SyncByteStream):
    def __init__(self, prefix):
        self.prefix, self.closed = prefix, False
    def __iter__(self):
        yield self.prefix
        raise httpx.ReadError("PRIVATE credential/source details")
    def close(self):
        self.closed = True


@pytest.mark.parametrize("code,kind", [("model_stream_interrupted", "transient"),
    ("model_stream_idle_timeout", "transient"), ("model_stream_incomplete", "invalid"),
    ("model_stream_invalid_event", "invalid")])
def test_stream_classification_separates_transport_from_content(code, kind):
    result = classify_request_error(ModelStreamError(code))
    assert result["kind"] == kind
    if kind == "transient":
        assert result["code"] == code


@pytest.mark.parametrize("api", ["chat", "responses", "anthropic"])
@pytest.mark.parametrize("status,hint,code", [
    (503, "false", "service_unavailable"),
    (402, "true", "service_quota_exhausted"),
])
def test_sdk_retry_hint_cannot_cause_repeated_vetoed_or_quota_requests(api, status, hint, code):
    calls = []
    def handle(request):
        calls.append(request)
        return httpx.Response(status, headers={"x-should-retry": hint},
            json={"error": {"type": "server_error", "message": "offline fixture"}})
    client = _helpers.sdk_client(api, handle)
    try:
        with pytest.raises(ModelRequestError) as caught:
            client.chat([{"role": "user", "content": "Return JSON"}], retries=3)
        assert caught.value.kind == "fatal" and caught.value.code == code
        assert caught.value.attempts == len(calls) == 1
    finally:
        client.client.close()


@pytest.mark.parametrize("api", ["chat", "responses", "anthropic"])
def test_sdk_disconnect_retries_within_bound_then_returns_only_complete_attempt(api, monkeypatch):
    import lib.llm_client as llm
    monkeypatch.setattr(llm, "retry_delay", lambda *_: 0)
    bodies, calls, events = [], [], []
    def handle(request):
        calls.append(request)
        if len(calls) == 1:
            body = DisconnectedBody(_helpers.unfinished(api))
            bodies.append(body)
            return httpx.Response(200, headers={"content-type": "text/event-stream"}, stream=body)
        return _helpers.sse_response(_helpers.fixture(api))
    client = _helpers.sdk_client(api, handle)
    try:
        result = client.chat([{"role": "user", "content": "Return JSON"}], retries=2, on_stream=events.append)
        assert result == '{"answer":"ok"}'
        assert len(calls) == 2 and bodies[0].closed
        assert sum(row["type"] == "start" for row in events) == 2
        assert sum(row["type"] == "response_completed" for row in events) == 1
        assert client.usage["calls"] == 1
        assert "PRIVATE" not in json.dumps(events)
    finally:
        client.client.close()


@pytest.mark.parametrize("api", ["chat", "responses", "anthropic"])
def test_exhausted_disconnect_is_safe_retryable_request_error(api, monkeypatch):
    import lib.llm_client as llm
    monkeypatch.setattr(llm, "retry_delay", lambda *_: 0)
    bodies, events = [], []
    def handle(request):
        body = DisconnectedBody(_helpers.unfinished(api))
        bodies.append(body)
        return httpx.Response(200, headers={"content-type": "text/event-stream"}, stream=body)
    client = _helpers.sdk_client(api, handle)
    try:
        with pytest.raises(ModelRequestError) as caught:
            client.chat([{"role": "user", "content": "Return JSON"}], retries=2, on_stream=events.append)
        assert caught.value.code == "model_stream_interrupted"
        assert caught.value.retryable and caught.value.attempts == 2
        assert caught.value.diagnostics["phase"] == "stream"
        assert len(bodies) == 2 and all(body.closed for body in bodies)
        assert not any(row["type"] == "response_completed" for row in events)
        assert client.usage["calls"] == 0
    finally:
        client.client.close()


@pytest.mark.parametrize("provider_code,kind,code,attempts", [
    ("insufficient_balance", "fatal", "service_quota_exhausted", 1),
    ("context_length_exceeded", "fatal", "service_request_rejected", 1),
    ("invalid_api_key", "fatal", "service_authentication_failed", 1),
    ("overloaded_error", "transient", "service_unavailable", 3),
    ("PRIVATE-UNKNOWN-CODE", "fatal", "service_stream_error", 1),
])
def test_http_200_provider_sse_error_uses_structured_code_not_socket_retry(
        provider_code, kind, code, attempts, monkeypatch):
    import lib.llm_client as llm
    monkeypatch.setattr(llm, "retry_delay", lambda *_: 0)
    calls, events = [], []
    def handle(request):
        calls.append(request)
        error = {"error": {"message": "PRIVATE credential and source details",
                           "code": provider_code, "type": "PRIVATE-UNKNOWN-TYPE"}}
        return _helpers.sse_response(("data: " + json.dumps(error) + "\n\n").encode())
    client = _helpers.sdk_client("chat", handle)
    try:
        with pytest.raises(ModelRequestError) as caught:
            client.chat([{"role": "user", "content": "Return JSON"}], retries=3, on_stream=events.append)
        error = caught.value
        assert error.kind == kind and error.code == code
        assert len(calls) == attempts == error.attempts
        assert error.diagnostics["exception_types"] == ["ModelStreamError", "APIError"]
        assert error.diagnostics["http_status"] == 200
        if provider_code.startswith("PRIVATE"):
            assert "provider_error_code" not in error.diagnostics
        else:
            assert error.diagnostics["provider_error_code"] == provider_code
        assert "PRIVATE" not in json.dumps(error.diagnostics)
        assert not any(row["type"] == "response_completed" for row in events)
    finally:
        client.client.close()


@pytest.mark.parametrize("failure", [httpx.StreamClosed(), httpx.StreamConsumed(),
    httpx.ResponseNotRead(), httpx.RequestNotRead(), RuntimeError("PRIVATE local stream bug")])
def test_local_stream_state_error_stops_without_paid_retries(failure):
    calls = []
    class InvalidBody(httpx.SyncByteStream):
        def __iter__(self):
            raise failure
            yield b""  # The generator reaches a deterministic local failure.
        def close(self):
            pass
    def handle(request):
        calls.append(request)
        return httpx.Response(200, headers={"content-type": "text/event-stream"}, stream=InvalidBody())
    client = _helpers.sdk_client("chat", handle)
    try:
        with pytest.raises(ModelRequestError) as caught:
            client.chat([{"role": "user", "content": "Return JSON"}], retries=3, on_stream=lambda _: None)
        assert caught.value.code == "model_stream_state_error"
        assert caught.value.kind == "fatal" and caught.value.attempts == len(calls) == 1
        assert caught.value.diagnostics["exception_types"] == ["ModelStreamError", type(failure).__name__]
        assert "PRIVATE" not in json.dumps(caught.value.diagnostics)
    finally:
        client.client.close()


@pytest.mark.parametrize("failure", [ModelStreamError("model_stream_interrupted"),
    ModelStreamError("model_stream_idle_timeout"), RuntimeError("local callback failure")])
def test_callback_exception_is_not_a_transport_retry(failure):
    calls = []
    def handle(request):
        calls.append(request)
        return _helpers.sse_response(_helpers.fixture("chat"))
    client = _helpers.sdk_client("chat", handle)
    def callback(event):
        if event["type"] == "delta":
            raise failure
    try:
        with pytest.raises(type(failure)) as caught:
            client.chat([{"role": "user", "content": "Return JSON"}], retries=3, on_stream=callback)
        assert caught.value is failure
        assert len(calls) == 1
    finally:
        client.client.close()


def test_real_sdk_disconnect_pauses_workflow_without_bad_sample_or_extra_item_retry(tmp_path, monkeypatch):
    import lib.llm_client as llm
    monkeypatch.setattr(llm, "retry_delay", lambda *_: 0)
    source = tmp_path / "source.txt"
    source.write_text("设备出现故障时，应先断开电源，再检查线路。", encoding="utf-8")
    output = tmp_path / "output"
    run_id = engine.create_run(output, sources=[source], targets=["sft"], tasks=1, sample_count=1,
        production={"goals": {"sft": 1}, "round_size": 1, "item_retries": 2,
                    "min_acceptance_rate": 0})
    bodies = []
    def handle(request):
        body = DisconnectedBody(_helpers.unfinished("chat"))
        bodies.append(body)
        return httpx.Response(200, headers={"content-type": "text/event-stream"}, stream=body)
    client = _helpers.sdk_client("chat", handle)
    class NoReviewer:
        model = "offline-reviewer"
        usage = {}
        def chat(self, *args, **kwargs):
            pytest.fail("Transport failure must not produce a candidate for grading")
    try:
        state = engine.Workflow(output, run_id, tmp_path, generator=client, jev=NoReviewer()).execute()
        assert state["status"] == "failed"
        assert state["error"] == "model_stream_interrupted"
        assert state["production"]["status"] == "paused"
        assert state["production"]["stop_reason"] == "model_stream_interrupted"
        assert state["request_failure"]["phase"] == "stream"
        assert "ReadError" in state["request_failure"]["exception_types"]
        assert len(bodies) == 3  # ChatClient's existing default, no outer item retries.
        path = engine.run_path(output, run_id)
        assert not list((path / "production").glob("round-*/receipt.json"))
        for file in list((path / "stage-results").glob("*.jsonl")) + list((path / "production").glob("round-*/*.jsonl")):
            assert '"reason":"invalid_model_result"' not in file.read_text(encoding="utf-8")
        events = (path / "events.jsonl").read_text(encoding="utf-8")
        failure_event = next(json.loads(line) for line in events.splitlines()
                             if json.loads(line).get("kind") == "run_failed")
        assert failure_event["request_failure"] == state["request_failure"]
        assert '"kind":"item_retry"' not in events
        assert "PRIVATE" not in (path / "state.json").read_text(encoding="utf-8")
    finally:
        client.client.close()
