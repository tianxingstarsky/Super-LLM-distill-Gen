"""Provider-specific prefix contracts verified through a real offline SDK."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from copy import deepcopy
import hashlib
import json
from threading import Barrier

import httpx
import pytest

from lib.llm_client import chat_json
from lib.model_reasoning_prefill import prepare_reasoning_prefill, validate_prefill_client
from lib.model_request_reliability import ModelRequestError
from lib.model_streaming import ModelStreamError
from model_streaming_fixtures import sdk_client, sse_response


MESSAGES = [{"role": "system", "content": "Return JSON."},
            {"role": "user", "content": "现在呢？"}]
PREFIX = " \n先核对不确定条件，再决定怎样回答。\n "


def full_reply(text='{"answer":"ok"}', native="Additional provider reasoning.", finish="stop"):
    return {"id": "prefill-test", "object": "chat.completion", "created": 1,
        "model": "deepseek-flash", "choices": [{"index": 0, "finish_reason": finish,
        "message": {"role": "assistant", "content": text, "reasoning_content": native}}],
        "usage": {"prompt_tokens": 17, "completion_tokens": 8, "total_tokens": 25}}


def client_for(handler, base_url="https://api.deepseek.com/v1"):
    client = sdk_client("chat", handler)
    client.base_url = base_url
    client.client.base_url = base_url
    client.model = "deepseek-flash"
    return client


def metadata(prefix=PREFIX):
    return {"version": 1, "transport": "deepseek_chat_beta",
        "prefix_sha256": hashlib.sha256(prefix.encode("utf-8")).hexdigest(),
        "prefix_utf8_bytes": len(prefix.encode("utf-8")),
        "response_reasoning": "provider_continuation"}


@pytest.mark.parametrize("path", ["", "/", "/v1", "/v1/", "/beta", "/beta/"])
def test_explicit_official_route_appends_exact_prefix_without_mutation(path):
    messages = deepcopy(MESSAGES)
    prepared, details = prepare_reasoning_prefill(messages, PREFIX, api_format="chat",
        base_url="https://api.deepseek.com" + path)
    assert messages == MESSAGES
    assert prepared[:-1] == messages
    assert prepared[-1] == {"role": "assistant", "content": "", "prefix": True,
                            "reasoning_content": PREFIX}
    assert details == {"reasoning_prefill": metadata()}


@pytest.mark.parametrize("url,api", [
    ("https://api.deepseek.com/v1", "responses"),
    ("https://api.deepseek.com/anthropic", "anthropic"),
    ("https://api.deepseek.com.evil.invalid/v1", "chat"),
    ("http://api.deepseek.com/v1", "chat"),
    ("https://api.deepseek.com:444/v1", "chat"),
    ("https://USER:PASS@api.deepseek.com/v1", "chat"),
    ("https://api.deepseek.com/v1?secret=NO", "chat"),
    ("https://api.deepseek.com/v1#NO", "chat"),
    ("https://api.deepseek.com/gateway", "chat"),
])
def test_unsupported_protocol_or_service_is_rejected_before_admission(url, api):
    calls = []
    client = client_for(lambda request: calls.append(request), url)
    client.api_format = api
    try:
        with pytest.raises(ValueError, match="^model_reasoning_prefill_unsupported$"):
            chat_json(client, MESSAGES, reasoning_prefix=PREFIX)
        assert not calls
        assert not client.usage["calls"]
    finally:
        client.client.close()


@pytest.mark.parametrize("prefix", ["", " \n\t", 17, False, [], "\ud800"])
def test_invalid_prefix_rejected_without_network(prefix):
    calls = []
    client = client_for(lambda request: calls.append(request))
    try:
        with pytest.raises(ValueError, match="^invalid_model_reasoning_prefix$"):
            client.chat(MESSAGES, reasoning_prefix=prefix)
        assert not calls
    finally:
        client.client.close()


@pytest.mark.parametrize("mode,thinking", [("disabled", None), ("auto", False)])
def test_thinking_disabled_never_ignores_requested_prefix(mode, thinking):
    client = client_for(lambda request: pytest.fail("must not send a request"))
    client.reasoning = {"mode": mode}
    try:
        with pytest.raises(ValueError, match="^model_reasoning_prefill_thinking_disabled$"):
            client.chat(MESSAGES, reasoning_prefix=PREFIX, thinking=thinking)
    finally:
        client.client.close()


@pytest.mark.parametrize("explicit", [None, PREFIX])
def test_prefix_flags_in_ordinary_messages_cannot_bypass_explicit_path(explicit):
    client = client_for(lambda request: pytest.fail("must not send a request"))
    messages = [*MESSAGES, {"role": "assistant", "content": "", "prefix": True,
                            "reasoning_content": PREFIX}]
    try:
        with pytest.raises(ValueError, match="^model_reasoning_prefill_explicit_required$"):
            client.chat(messages, reasoning_prefix=explicit)
    finally:
        client.client.close()


@pytest.mark.parametrize("message", [
    {"role": "tool", "tool_call_id": "read-source", "content": "Private source material."},
    {"role": "assistant", "content": "", "tool_calls": [{"id": "read-source",
        "type": "function", "function": {"name": "source_memory", "arguments": "{}"}}]},
    {"role": "assistant", "content": "", "tool_calls": []},
    {"role": "assistant", "content": "", "function_call": {
        "name": "source_memory", "arguments": "{}"}},
])
def test_native_prefill_tool_history_is_rejected_before_queue_or_network_without_rewrite(message):
    calls = []
    client = client_for(lambda request: calls.append(request))
    class Scheduler:
        def acquire(self, **kwargs):
            pytest.fail("unsupported tool history must not acquire a slot")
    client.scheduler = Scheduler()
    messages = [MESSAGES[0], deepcopy(message), MESSAGES[1]]
    original = deepcopy(messages)
    try:
        with pytest.raises(ValueError, match="^model_reasoning_prefill_tools_unsupported$"):
            client.chat(messages, reasoning_prefix=PREFIX)
        assert messages == original
        assert not calls
        assert client.usage["calls"] == 0
    finally:
        client.client.close()


def test_real_sdk_uses_beta_same_model_credentials_pool_and_separate_capture():
    calls = []
    def handle(request):
        calls.append(request)
        return httpx.Response(200, json=full_reply())
    client = client_for(handle)
    original = client.client
    original_http = original._client
    details = {"stale": True}
    class Scheduler:
        admitted = 0
        @contextmanager
        def acquire(self, **kwargs):
            self.admitted += 1
            yield
    scheduler = Scheduler()
    client.scheduler = scheduler
    try:
        assert chat_json(client, MESSAGES, reasoning_prefix=PREFIX,
                         response_details=details) == {"answer": "ok"}
        assert len(calls) == scheduler.admitted == 1
        request = calls[0]
        payload = json.loads(request.content)
        assert str(request.url) == "https://api.deepseek.com/beta/chat/completions"
        assert request.headers["authorization"] == "Bearer SUPER-SECRET-KEY"
        assert payload["model"] == client.model == "deepseek-flash"
        assert payload["messages"][-1]["reasoning_content"] == PREFIX
        assert payload["thinking"] == {"type": "enabled"}
        assert "response_format" not in payload
        assert client.json_supported is None
        assert details == {"api_format": "chat", "reasoning_content": "Additional provider reasoning.",
                           "reasoning_prefill": metadata()}
        assert PREFIX not in details["reasoning_content"]
        assert client.client is original and original._client is original_http
        assert str(original.base_url) == "https://api.deepseek.com/v1/"
        assert client.base_url == "https://api.deepseek.com/v1"
        assert client.scheduler is scheduler
        assert MESSAGES[-1]["role"] == "user"
    finally:
        client.client.close()


def test_prefix_context_cost_includes_exact_utf8_before_queue_or_provider():
    client = client_for(lambda request: pytest.fail("must not send a request"))
    client.context_window_tokens = 1024
    class Scheduler:
        def acquire(self, **kwargs):
            pytest.fail("must not acquire a slot")
    client.scheduler = Scheduler()
    try:
        with pytest.raises(ValueError, match="^model_context_window_exceeded$"):
            client.chat(MESSAGES, max_tokens=100, reasoning_prefix="推" * 350)
    finally:
        client.client.close()


def test_client_validation_is_offline_and_rejects_disabled_or_unsupported():
    client = client_for(lambda request: pytest.fail("validation must be offline"))
    try:
        validate_prefill_client(client)
        client.reasoning = {"mode": "disabled"}
        with pytest.raises(ValueError, match="^model_reasoning_prefill_thinking_disabled$"):
            validate_prefill_client(client)
        client.reasoning = {"mode": "auto"}
        client.api_format = "responses"
        with pytest.raises(ValueError, match="^model_reasoning_prefill_unsupported$"):
            validate_prefill_client(client)
        assert client.usage["calls"] == 0
    finally:
        client.client.close()


@pytest.mark.parametrize("status", [400, 404, 405, 422])
def test_beta_rejection_never_drops_prefix_or_exposes_provider_payload(status):
    calls = []
    def handle(request):
        calls.append(request)
        return httpx.Response(status, json={"error": {"message":
            "BadRequest unsupported response_format/prefix SUPER-SECRET-KEY " + PREFIX}})
    client = client_for(handle)
    details = {"stale": True}
    try:
        with pytest.raises(ValueError, match="^model_reasoning_prefill_rejected$") as error:
            chat_json(client, MESSAGES, reasoning_prefix=PREFIX, response_details=details)
        assert len(calls) == 1
        assert details == {}
        assert client.json_supported is None
        assert "SECRET" not in str(error.value) and PREFIX not in str(error.value)
    finally:
        client.client.close()


def test_invalid_json_retry_retains_prefix_and_only_successful_native_capture():
    calls = []
    def handle(request):
        calls.append(request)
        return httpx.Response(200, json=full_reply(
            text="invalid" if len(calls) == 1 else '{"answer":"ok"}',
            native="discarded first reasoning" if len(calls) == 1 else "Final continuation."))
    client = client_for(handle)
    details = {}
    try:
        assert chat_json(client, MESSAGES, reasoning_prefix=PREFIX,
                         response_details=details) == {"answer": "ok"}
        assert len(calls) == 2
        assert all(json.loads(request.content)["messages"][-1]["reasoning_content"] == PREFIX
                   for request in calls)
        assert details["reasoning_content"] == "Final continuation."
        assert details["reasoning_prefill"] == metadata()
    finally:
        client.client.close()


@pytest.mark.parametrize("ordinary_json_capability", [None, True, False])
def test_beta_prefix_excludes_json_format_and_preserves_ordinary_capability(ordinary_json_capability):
    calls = []
    def handle(request):
        payload = json.loads(request.content)
        calls.append(payload)
        if "response_format" in payload:
            return httpx.Response(400, json={"error": {"message":
                "response_format json_object should not be used with prefix"}})
        return httpx.Response(200, json=full_reply())
    client = client_for(handle)
    client.json_supported = ordinary_json_capability
    try:
        assert chat_json(client, MESSAGES, reasoning_prefix=PREFIX) == {"answer": "ok"}
        assert len(calls) == 1
        assert "response_format" not in calls[0]
        assert calls[0]["messages"][-1] == {"role": "assistant", "content": "",
            "prefix": True, "reasoning_content": PREFIX}
        assert client.json_supported is ordinary_json_capability
    finally:
        client.client.close()


def stream_reply(complete=True):
    def event(delta, finish=None):
        return "data: " + json.dumps({"id": "prefill-test", "object": "chat.completion.chunk",
            "created": 1, "model": "deepseek-flash", "choices": [{"index": 0,
            "delta": delta, "finish_reason": finish}]}) + "\n\n"
    raw = event({"reasoning_content": "Actual continuation."}) + event({"content": '{"answer":"ok"}'})
    if complete:
        raw += event({}, "stop") + "data: [DONE]\n\n"
    return raw.encode("utf-8")


@pytest.mark.parametrize("complete", [False, True])
def test_stream_capture_separates_prefix_and_only_commits_completed_response(complete):
    client = client_for(lambda request: sse_response(stream_reply(complete)))
    details, events = {"stale": True}, []
    try:
        if not complete:
            with pytest.raises(ModelStreamError, match="model_stream_incomplete"):
                chat_json(client, MESSAGES, reasoning_prefix=PREFIX,
                          response_details=details, on_stream=events.append)
            assert details == {}
        else:
            assert chat_json(client, MESSAGES, reasoning_prefix=PREFIX,
                response_details=details, on_stream=events.append) == {"answer": "ok"}
            assert details == {"api_format": "chat", "reasoning_content": "Actual continuation.",
                               "reasoning_prefill": metadata()}
        assert any(event.get("channel") == "reasoning" for event in events)
        assert PREFIX not in json.dumps(events, ensure_ascii=False)
    finally:
        client.client.close()


def test_native_reasoning_is_never_returned_as_visible_prefilled_answer():
    client = client_for(lambda request: httpx.Response(200, json=full_reply(text="")))
    try:
        with pytest.raises(ValueError, match="^model_visible_output_missing$"):
            client.chat(MESSAGES, reasoning_prefix=PREFIX, allow_reasoning_fallback=True)
    finally:
        client.client.close()


def test_plain_prefilled_answer_with_no_native_continuation_remains_plain_text():
    answer = "还不能确定，得先看当前事务是否结束。"
    client = client_for(lambda request: httpx.Response(200,
        json=full_reply(text=answer, native="")))
    details = {}
    try:
        assert client.chat(MESSAGES, reasoning_prefix=PREFIX,
                           response_details=details) == answer
        assert details == {"api_format": "chat", "reasoning_prefill": metadata()}
        assert "reasoning_content" not in details
    finally:
        client.client.close()


def test_prefill_enforces_complete_output_without_metadata_request():
    client = client_for(lambda request: httpx.Response(200, json=full_reply(finish="length")))
    try:
        with pytest.raises(ModelStreamError, match="model_stream_incomplete"):
            client.chat(MESSAGES, reasoning_prefix=PREFIX)
    finally:
        client.client.close()


def test_authentication_failure_keeps_existing_safe_nonretryable_code():
    calls = []
    def handle(request):
        calls.append(request)
        return httpx.Response(401, json={"error": {"message": "SECRET INVALID"}})
    client = client_for(handle)
    try:
        with pytest.raises(ModelRequestError) as error:
            chat_json(client, MESSAGES, reasoning_prefix=PREFIX)
        assert error.value.code == "service_authentication_failed"
        assert len(calls) == 1
    finally:
        client.client.close()


def test_shared_client_concurrent_prefixes_do_not_mutate_url_or_cross_calls():
    rendezvous = Barrier(2)
    captured = []
    def handle(request):
        captured.append(json.loads(request.content)["messages"][-1]["reasoning_content"])
        rendezvous.wait(timeout=10)
        return httpx.Response(200, json=full_reply())
    client = client_for(handle)
    def run(prefix):
        details = {}
        result = chat_json(client, MESSAGES, reasoning_prefix=prefix, response_details=details)
        return result, details["reasoning_prefill"]["prefix_sha256"]
    try:
        prefixes = [PREFIX, "A different authored prefix."]
        with ThreadPoolExecutor(max_workers=2) as pool:
            rows = list(pool.map(run, prefixes))
        assert sorted(captured) == sorted(prefixes)
        assert [row[1] for row in rows] == [metadata(prefix)["prefix_sha256"] for prefix in prefixes]
        assert str(client.client.base_url) == "https://api.deepseek.com/v1/"
    finally:
        client.client.close()
