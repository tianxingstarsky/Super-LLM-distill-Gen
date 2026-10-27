"""In-memory request policies: bounded retries, admission and safe publication."""
from __future__ import annotations

from contextlib import contextmanager
import importlib
import json
import subprocess
import sys
from pathlib import Path

import pytest

from lib.contexts.models.api import ExecuteModelRequest, ExecuteToolTurn, RequestJSONResponse
from lib.contexts.models.contracts import ModelQueueTimeout, ModelRequestError, ModelStreamError
from lib.contexts.models.domain.request_errors import ModelJSONError
from lib.contexts.models.domain.json_response import parse_json_robust, JSONResponseDecodeError
from copy import deepcopy


def TenacityRetry():
    from lib.contexts.models.infrastructure.retry import TenacityRetry as Runtime
    return Runtime()


class FakeFailure(RuntimeError):
    def __init__(self, code, *, kind="transient", rejected=False, format_rejected=False):
        super().__init__("provider text is not a public diagnostic")
        self.code = code
        self.kind = kind
        self.rejected = rejected
        self.format_rejected = format_rejected


class MemoryBudget:
    def __init__(self, events):
        self.events = events

    def check(self):
        self.events.append("check-budget")

    def reserve(self, amount):
        self.events.append(("reserve", amount))
        return "reservation"


class MemoryAdmission:
    api_format = "chat"
    cancel_check = None
    json_supported = None

    def __init__(self, outcomes, reservation=0.5):
        self.outcomes = iter(outcomes)
        self.events = []
        self.guard = MemoryBudget(self.events)
        self.reservation = reservation
        self.calls = []
        self.slot_open = False

    @contextmanager
    def admit(self, check_cancel, on_wait):
        self.events.append("admit")
        self.slot_open = True
        try:
            yield
        finally:
            self.slot_open = False
            self.events.append("release")

    def budget_guards(self):
        return [self.guard]

    def settle_all(self, reservations, amount):
        assert self.slot_open
        self.events.append(("settle", amount))
        reservations.clear()

    def account_usage(self, prompt_tokens, completion_tokens, reservations, reservation_usd):
        self.events.append(("usage", prompt_tokens, completion_tokens))
        self.settle_all(reservations, (prompt_tokens + completion_tokens) / 1000)

    def commit_details(self, target, details):
        if target is not None:
            target.clear()
            target.update(details)

    def classify_error(self, error):
        return {"code": getattr(error, "code", str(error)), "kind": getattr(error, "kind", "transient")}

    def rejected_before_generation(self, error):
        return getattr(error, "rejected", False)

    def retry_delay(self, error, attempt):
        return 0.25 * (attempt + 1)

    def defer(self, delay):
        assert self.slot_open
        self.events.append(("defer", delay))

    def wait_retry(self, delay, check_cancel):
        assert not self.slot_open
        self.events.append(("wait", delay))
        check_cancel()


class MemoryTextRequest(MemoryAdmission):
    def prepare_request(self, messages, max_tokens, **options):
        return max_tokens, self.reservation

    def request(self, messages, **options):
        self.calls.append((messages, options))
        outcome = next(self.outcomes)
        if isinstance(outcome, BaseException):
            raise outcome
        if options.get("on_stream"):
            options["on_stream"]({"type": "delta", "channel": "text", "text": outcome})
        if "response_details" in options:
            options["response_details"].update(reasoning_content="完整思考", completed=True)
        return outcome, 100, 50

    def is_format_unsupported(self, error):
        return getattr(error, "format_rejected", False)


def test_rate_limit_defers_before_releasing_slot_and_retries_with_clean_reservation():
    port = MemoryTextRequest([FakeFailure("service_rate_limited", rejected=True), "answer"])
    details = {"old": "stale"}
    assert ExecuteModelRequest(port, retry=TenacityRetry()).execute([{"role": "user", "content": "q"}], response_details=details) == "answer"
    assert port.events == ["admit", ("reserve", .5), ("defer", .25), ("settle", 0), "release",
                           "admit", ("reserve", .5), ("usage", 100, 50), ("settle", .15), "release"]
    assert details == {"reasoning_content": "完整思考", "completed": True}
    assert all(call[1]["allow_reasoning_fallback"] is False for call in port.calls)


def test_json_capability_rejection_does_not_consume_retry_or_reuse_live_reservation():
    port = MemoryTextRequest([FakeFailure("rejected", kind="fatal", rejected=True, format_rejected=True), "{}"])
    assert ExecuteModelRequest(port, retry=TenacityRetry()).execute([], json_mode=True, retries=1) == "{}"
    assert port.json_supported is False
    assert len(port.calls) == 2
    assert port.events.count("admit") == port.events.count("release") == 2
    assert ("settle", 0) in port.events


def test_interrupted_stream_books_unknown_usage_and_waits_after_slot_release():
    port = MemoryTextRequest([ModelStreamError("model_stream_interrupted"), "complete"])
    assert ExecuteModelRequest(port, retry=TenacityRetry()).execute([]) == "complete"
    assert port.events[:6] == ["admit", ("reserve", .5), ("settle", .5), "release", ("wait", .25), "admit"]


def test_cancellation_and_callback_failures_never_retry_or_publish_details():
    port = MemoryTextRequest(["answer"])
    cancelled = RuntimeError("stop now")
    port.cancel_check = lambda: (_ for _ in ()).throw(cancelled)
    with pytest.raises(RuntimeError) as observed:
        ExecuteModelRequest(port, retry=TenacityRetry()).execute([])
    assert observed.value is cancelled
    assert port.events == [] and port.calls == []

    port = MemoryTextRequest(["answer"])
    details = {"stale": True}
    callback_failure = ValueError("observer failed")
    def callback(event):
        if event["type"] == "delta":
            raise callback_failure
    with pytest.raises(ValueError) as observed:
        ExecuteModelRequest(port, retry=TenacityRetry()).execute([], response_details=details, on_stream=callback)
    assert observed.value is callback_failure
    assert len(port.calls) == 1
    assert details == {}
    assert port.events[-2:] == [("settle", .5), "release"]


def test_fatal_provider_error_is_sanitized_and_queue_timeout_has_zero_sent_attempts():
    port = MemoryTextRequest([FakeFailure("service_authentication_failed", kind="fatal", rejected=True)])
    with pytest.raises(ModelRequestError) as observed:
        ExecuteModelRequest(port, retry=TenacityRetry()).execute([])
    assert observed.value.code == "service_authentication_failed"
    assert observed.value.attempts == 1
    assert "provider text" not in str(observed.value)
    assert len(port.calls) == 1

    @contextmanager
    def timeout(*args):
        raise ModelQueueTimeout()
        yield  # pragma: no cover
    port = MemoryTextRequest([])
    port.admit = timeout
    with pytest.raises(ModelRequestError) as observed:
        ExecuteModelRequest(port, retry=TenacityRetry()).execute([])
    assert observed.value.attempts == 0
    assert port.events == [] and port.calls == []


class MemoryJSONRequest:
    def __init__(self, outcomes):
        self.outcomes = iter(outcomes)
        self.calls = []
        self.messages = []

    def chat(self, messages, **options):
        self.calls.append(options)
        self.messages.append(deepcopy(messages))
        outcome = next(self.outcomes)
        if "response_details" in options:
            options["response_details"]["attempt"] = len(self.calls)
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome

    def parse_json(self, output):
        return json.loads(output)

    def commit_details(self, target, details):
        if target is not None:
            target.clear()
            target.update(details)


def test_json_retries_only_invalid_body_and_publishes_only_successful_attempt_details():
    port = MemoryJSONRequest(["bad json", '{"answer":"ok"}'])
    details = {"stale": True}
    assert RequestJSONResponse(port, retry=TenacityRetry()).execute([], temperature=.8, response_details=details) == {"answer": "ok"}
    assert details == {"attempt": 2}
    assert [call["temperature"] for call in port.calls] == [.8, .3]
    port = MemoryJSONRequest([ModelRequestError("service_authentication_failed", "fatal", 1)])
    with pytest.raises(ModelRequestError):
        RequestJSONResponse(port, retry=TenacityRetry()).execute([])
    assert len(port.calls) == 1


def test_json_retry_includes_parser_feedback_without_changing_context_or_accumulating_history():
    first, second = '{"answer":["first"}}', '{"answer":["second"}}'
    port = MemoryJSONRequest([first, second, '{"answer":["third"]}'])
    port.parse_json = parse_json_robust
    messages = [{"role": "system", "content": "fixed rules"}, {"role": "user", "content": "real question"}]
    original = deepcopy(messages)
    RequestJSONResponse(port, retry=TenacityRetry()).execute(messages)
    assert messages == original and port.messages[0] == original
    assert port.messages[1][:-2] == original and port.messages[2][:-2] == original
    assert port.messages[1][-2] == {"role": "assistant", "content": first}
    assert port.messages[2][-2] == {"role": "assistant", "content": second}
    assert port.messages[1][-1]["role"] == "system"
    assert '"column"' in port.messages[1][-1]["content"]


def test_identical_invalid_json_after_feedback_stops_without_third_purchase():
    port = MemoryJSONRequest(['{"answer":', '{"answer":'])
    with pytest.raises(ModelJSONError):
        RequestJSONResponse(port, retry=TenacityRetry()).execute([], retries=3)
    assert len(port.calls) == 2


@pytest.mark.parametrize("phase", ["parse", "commit"])
@pytest.mark.parametrize("failure", [ValueError("storage failure"), RuntimeError("adapter bug")])
def test_non_syntax_program_errors_never_buy_another_model_response(phase, failure):
    port = MemoryJSONRequest(['{"ok":true}'])
    def fail(*args):
        raise failure
    setattr(port, "parse_json" if phase == "parse" else "commit_details", fail)
    with pytest.raises(type(failure)) as observed:
        RequestJSONResponse(port, retry=TenacityRetry()).execute([])
    assert observed.value is failure and len(port.calls) == 1


def test_json_decode_error_has_machine_readable_location_without_raw_content():
    with pytest.raises(JSONResponseDecodeError) as observed:
        parse_json_robust('{\n"answer": ["sensitive content"}}')
    assert observed.value.line == 2 and observed.value.column > 0
    assert "sensitive content" not in str(observed.value)


class MemoryToolRequest(MemoryAdmission):
    def prepare_request(self, messages, tools, **options):
        return self.reservation

    def request(self):
        self.calls.append("tool")
        return next(self.outcomes)

    def response_usage(self, response):
        return 50, 100

    def complete_response(self, response, **options):
        if isinstance(response, BaseException):
            raise response
        return response

    def response_details(self, response):
        return {"reasoning_content": "保留推理"}


def test_tool_decision_bills_invalid_response_and_never_executes_returned_tools():
    port = MemoryToolRequest([ValueError("model_unexpected_tool_call")])
    details = {"stale": True}
    with pytest.raises(ValueError, match="model_unexpected_tool_call"):
        ExecuteToolTurn(port, retry=TenacityRetry()).execute([], [], response_details=details)
    assert port.events[-3:] == [("usage", 50, 100), ("settle", .15), "release"]
    assert details == {} and len(port.calls) == 1
    decision = {"role": "assistant", "content": "", "tool_calls": [{"id": "pending-call"}]}
    port = MemoryToolRequest([decision])
    events = []
    assert ExecuteToolTurn(port, retry=TenacityRetry()).execute([], [], on_stream=events.append) is decision
    assert events[-1]["delivery"] == "complete_response"
    assert events[-1]["tool_calls"] == 1


def test_request_use_cases_import_without_runtime_or_sdk_dependencies():
    script = '''
import importlib.abc, sys
class BlockRuntime(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if (fullname.startswith(("lib.contexts.models.infrastructure", "lib.bootstrap", "openai", "anthropic", "httpx"))
                or fullname in {"lib.llm_client", "lib.model_tool_calls", "lib.model_request_scheduler", "lib.model_streaming"}):
            raise AssertionError(fullname)
sys.meta_path.insert(0, BlockRuntime())
from lib.contexts.models.api import ExecuteModelRequest, ExecuteToolTurn, RequestJSONResponse
from lib.contexts.models.contracts import ModelRequestError, parse_json_robust
assert parse_json_robust('{"ok":true}') == {"ok": True}
'''
    subprocess.run([sys.executable, "-c", script], cwd=Path(__file__).resolve().parents[1], check=True)


@pytest.mark.parametrize("legacy,canonical", [
    ("lib.llm_client", "lib.contexts.models.infrastructure.request_runtime"),
    ("lib.model_tool_calls", "lib.contexts.models.infrastructure.tool_transport"),
])
def test_legacy_request_imports_share_one_module_namespace(legacy, canonical):
    assert importlib.import_module(legacy) is importlib.import_module(canonical)
