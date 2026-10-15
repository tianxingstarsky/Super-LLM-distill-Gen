"""Meaningful-output idle/cancellation, with blocked readers and offline SDKs."""
from __future__ import annotations

import importlib.util
from pathlib import Path
import socket
import threading
from time import monotonic, sleep
from types import SimpleNamespace as Obj

import httpx
import pytest

from lib.llm_client import BudgetGuard
from lib.model_request_reliability import ModelRequestError, classify_request_error
from lib.model_streaming import ModelStreamError, consume_stream

_spec = importlib.util.spec_from_file_location("model_stream_idle_fixtures", Path(__file__).with_name("model_streaming_fixtures.py"))
_helpers = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_helpers)


@pytest.fixture(scope="session", autouse=True)
def mock_llm_server():
    yield


class BlockedStream:
    def __init__(self, prefix=()):
        self.prefix = prefix
        self.closed = threading.Event()
        self.reading = threading.Event()

    def __iter__(self):
        yield from self.prefix
        self.reading.set()
        if not self.closed.wait(3):
            raise AssertionError("watchdog did not close blocked stream")

    def close(self):
        self.closed.set()


@pytest.mark.parametrize("api", ["chat", "responses", "anthropic"])
def test_decoded_heartbeats_do_not_extend_meaningful_output_idle(api):
    class Heartbeats(BlockedStream):
        count = 0
        def __iter__(self):
            while not self.closed.wait(0.005):
                self.count += 1
                yield {"type": "ping"}
    stream, events = Heartbeats(), []
    with pytest.raises(ModelStreamError, match="^model_stream_idle_timeout$") as failure:
        consume_stream(stream, api, events.append, idle_timeout_seconds=0.06)
    assert stream.closed.is_set() and stream.count > 1 and not events
    assert classify_request_error(failure.value) == {"kind": "transient", "code": "model_stream_idle_timeout"}


def test_silent_blocked_next_is_closed_and_partial_json_is_never_completed():
    prefix = [{"choices": [{"index": 0, "delta": {"content": '{"answer":"partial"}'}, "finish_reason": None}]}]
    stream, events, details = BlockedStream(prefix), [], {"stale": True}
    with pytest.raises(ModelStreamError, match="^model_stream_idle_timeout$"):
        consume_stream(stream, "chat", events.append, response_details=details, idle_timeout_seconds=0.04)
    assert stream.closed.is_set() and details == {}
    assert events == [{"type": "delta", "channel": "text", "text": '{"answer":"partial"}'}]


def test_continuous_reasoning_can_run_longer_than_idle_limit():
    class Reasoning(BlockedStream):
        def __iter__(self):
            for _ in range(5):
                sleep(0.06)
                yield {"type": "response.reasoning_text.delta", "delta": "thought "}
            yield {"type": "response.output_text.delta", "delta": '{"answer":"ok"}'}
            yield {"type": "response.completed", "response": {"status": "completed"}}
    stream, events = Reasoning(), []
    started = monotonic()
    assert consume_stream(stream, "responses", events.append, idle_timeout_seconds=0.2)[0] == '{"answer":"ok"}'
    assert monotonic() - started > 0.2 and stream.closed.is_set()
    assert "".join(event["text"] for event in events if event.get("channel") == "reasoning") == "thought " * 5
    assert events[-1]["type"] == "response_completed"


@pytest.mark.parametrize("version,shutdown", [("HTTP/1.1", True), ("HTTP/2", False)])
def test_only_current_http1_socket_is_shutdown_before_sdk_close(version, shutdown):
    stream = BlockedStream()
    actions = []
    class Socket:
        def shutdown(self, how):
            assert how == socket.SHUT_RDWR
            actions.append("shutdown")
            stream.closed.set()
    stream.response = Obj(http_version=version, extensions={"network_stream": Obj(get_extra_info=lambda name: Socket())})
    original = stream.close
    def close():
        actions.append("close")
        original()
    stream.close = close
    with pytest.raises(ModelStreamError, match="^model_stream_idle_timeout$"):
        consume_stream(stream, "chat", lambda event: None, idle_timeout_seconds=0.03)
    assert actions == (["shutdown", "close"] if shutdown else ["close"])


def test_blocking_sdk_close_has_one_daemon_and_bounded_join():
    release = threading.Event()
    class SlowClose(BlockedStream):
        def __iter__(self):
            return iter(self.prefix)
        def close(self):
            self.closed.set()
            release.wait(3)
    stream = SlowClose([{"choices": [{"index": 0, "delta": {"content": "ok"}, "finish_reason": "stop"}]}])
    # End the reader normally; only its SDK close is deliberately blocked.
    before = set(threading.enumerate())
    started = monotonic()
    try:
        assert consume_stream(stream, "chat", lambda event: None, idle_timeout_seconds=1)[0] == "ok"
        assert monotonic() - started < 1.5 and stream.closed.is_set()
        guards = [thread for thread in threading.enumerate() if thread not in before and thread.name == "model-stream-watchdog"]
        assert len(guards) == 1 and guards[0].daemon
    finally:
        release.set()
        for thread in set(threading.enumerate()) - before:
            if thread.name == "model-stream-watchdog":
                thread.join(1)
                assert not thread.is_alive()


class HeartbeatBody(httpx.SyncByteStream):
    def __init__(self, *, blocked=False):
        self.blocked = blocked
        self.closed = threading.Event()
        self.reading = threading.Event()
        self.heartbeats = 0

    def __iter__(self):
        self.reading.set()
        if self.blocked:
            if not self.closed.wait(3):
                raise AssertionError("cancelled SDK stream was not closed")
        else:
            while not self.closed.wait(0.005):
                self.heartbeats += 1
                yield b": heartbeat\n\n"

    def close(self):
        self.closed.set()


@pytest.mark.parametrize("api", ["chat", "responses", "anthropic"])
def test_sdk_sse_comments_timeout_without_completion_or_unbounded_retry(api, monkeypatch, tmp_path):
    import lib.llm_client as llm
    monkeypatch.setattr(llm, "MODEL_READ_IDLE_TIMEOUT_SECONDS", 0.06)
    body, calls, events = HeartbeatBody(), [], []
    def handle(request):
        calls.append(request)
        return httpx.Response(200, headers={"content-type": "text/event-stream"}, stream=body)
    client = _helpers.sdk_client(api, handle)
    client.price_input = client.price_output = 1
    client.budget = BudgetGuard(tmp_path, 1, hard_stop=True)
    try:
        with pytest.raises(ModelRequestError, match="model_stream_idle_timeout") as error:
            client.chat([{"role": "user", "content": "Return JSON"}], max_tokens=128, retries=1, on_stream=events.append)
        assert error.value.retryable and error.value.attempts == 1
        assert len(calls) == 1 and body.closed.is_set() and body.heartbeats > 1
        assert not any(event["type"] == "response_completed" for event in events)
        assert not client.budget._state()["reservations"]
        assert client.budget._state()["spent_usd"] > 0
    finally:
        client.client.close()


def test_idle_stream_retries_only_within_configured_attempts_then_accepts_real_completion(monkeypatch):
    import lib.llm_client as llm
    monkeypatch.setattr(llm, "MODEL_READ_IDLE_TIMEOUT_SECONDS", 0.04)
    monkeypatch.setattr(llm, "retry_delay", lambda *args: 0)
    body, calls, events = HeartbeatBody(), [], []
    def handle(request):
        calls.append(request)
        if len(calls) == 1:
            return httpx.Response(200, headers={"content-type": "text/event-stream"}, stream=body)
        return _helpers.sse_response(_helpers.fixture("chat"))
    client = _helpers.sdk_client("chat", handle)
    try:
        assert client.chat([{"role": "user", "content": "Return JSON"}], retries=2, on_stream=events.append) == '{"answer":"ok"}'
        assert len(calls) == 2 and body.closed.is_set()
        assert sum(event["type"] == "start" for event in events) == 2
        assert sum(event["type"] == "response_completed" for event in events) == 1
    finally:
        client.client.close()


@pytest.mark.parametrize("api", ["chat", "responses", "anthropic"])
def test_sdk_cancel_while_next_blocks_preserves_original_exception_and_does_not_retry(api, monkeypatch):
    import lib.llm_client as llm
    monkeypatch.setattr(llm, "MODEL_READ_IDLE_TIMEOUT_SECONDS", 1)
    body, calls, events = HeartbeatBody(blocked=True), [], []
    class Cancelled(Exception): pass
    original = Cancelled("cancelled")
    def check_cancel():
        if body.reading.is_set():
            raise original
    def handle(request):
        calls.append(request)
        return httpx.Response(200, headers={"content-type": "text/event-stream"}, stream=body)
    client = _helpers.sdk_client(api, handle)
    client.cancel_check = check_cancel
    try:
        with pytest.raises(Cancelled) as error:
            client.chat([{"role": "user", "content": "Return JSON"}], retries=3, on_stream=events.append)
        assert error.value is original and len(calls) == 1 and body.closed.is_set()
        assert not any(event["type"] == "response_completed" for event in events)
        assert not [thread for thread in threading.enumerate() if thread.name == "model-stream-watchdog"]
    finally:
        client.client.close()
