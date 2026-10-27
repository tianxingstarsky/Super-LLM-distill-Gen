"""Exercise real OSS executors offline, including cancellation and telemetry faults."""
from contextlib import contextmanager
from types import SimpleNamespace

import pytest

from lib.contexts.models.application.retry_port import RetryDirective
from lib.contexts.models.infrastructure.retry import TenacityRetry
from lib.contexts.models.infrastructure import telemetry


def test_tenacity_exhaustion_has_no_final_sleep_and_preserves_terminal_error():
    calls, sleeps = [], []
    failure = RuntimeError("transient")
    def request(attempt):
        calls.append(attempt)
        return RetryDirective(failure, delay=1)
    with pytest.raises(RuntimeError) as caught:
        TenacityRetry().run(request, attempts=3, wait=sleeps.append)
    assert caught.value is failure and calls == [0, 1, 2] and sleeps == [1, 1]


def test_tenacity_only_retries_explicit_results_and_bounds_free_protocol_fallback():
    calls = []
    def faulty(attempt):
        calls.append(attempt)
        raise ValueError("storage_commit_failed")
    with pytest.raises(ValueError):
        TenacityRetry().run(faulty, attempts=5, wait=lambda seconds: pytest.fail("unexpected wait"))
    assert calls == [0]
    calls.clear()
    def fallback(attempt):
        calls.append(attempt)
        return RetryDirective(ValueError("protocol_rejected"), counted=False)
    with pytest.raises(ValueError):
        TenacityRetry().run(fallback, attempts=1, wait=lambda seconds: None)
    assert calls == [0, 0]  # a bad adapter cannot create an infinite free retry


def test_langfuse_sdk_generation_lifecycle_usage_and_failure_are_observable(monkeypatch):
    events = []
    class Observation:
        def update(self, **fields):
            events.append(fields)
    class Client:
        @contextmanager
        def start_as_current_observation(self, **fields):
            events.append(fields)
            try:
                yield Observation()
            finally:
                events.append("closed")
    monkeypatch.setattr(telemetry, "configured_observation_client", lambda config: Client())
    client = SimpleNamespace(observation_config={}, model="fixed-model", api_format="chat")
    failure = ValueError("partial_tools_never_execute")
    with pytest.raises(ValueError) as caught:
        with telemetry.observe_request(client) as observation:
            telemetry.observation_update(observation, usage_details={"input": 123, "output": 456})
            raise failure
    assert caught.value is failure
    assert events[0]["as_type"] == "generation"
    assert events[1] == {"usage_details": {"input": 123, "output": 456}}
    assert events[2]["level"] == "ERROR" and events[-1] == "closed"


def test_langfuse_fault_does_not_repeat_or_hide_business_work(monkeypatch, caplog):
    def unavailable(config):
        raise ConnectionError("private upstream details")
    monkeypatch.setattr(telemetry, "configured_observation_client", unavailable)
    executions = []
    with telemetry.observe_request(SimpleNamespace(observation_config={})) as observation:
        assert observation is None
        executions.append("completed")
    assert executions == ["completed"]
    assert "ConnectionError" in caplog.text and "private upstream details" not in caplog.text


def test_cli_monitor_uses_current_sdk_instead_of_legacy_trace(monkeypatch, tmp_path):
    from lib import monitor
    events = []
    class CurrentSDK:
        @contextmanager
        def start_as_current_observation(self, **fields):
            events.append(fields)
            yield SimpleNamespace(update=lambda **fields: events.append(fields))
        def flush(self):
            events.append("flush")
    monkeypatch.setattr(monitor, "_langfuse_config", lambda root: {"enabled": True})
    monkeypatch.setattr(telemetry, "configured_observation_client", lambda config: CurrentSDK())
    monitor.trace_run(tmp_path, "test", {"usage": {"prompt_tokens": 20, "completion_tokens": 30}})
    assert events[0]["as_type"] == "span"
    assert events[2]["usage_details"] == {"input": 20, "output": 30}
    assert events[-1] == "flush"


def test_official_knowledge_clients_preserve_response_ceiling_without_retries(monkeypatch):
    from tests.test_knowledge import qdrant_server, config
    from lib.contexts.documents.infrastructure import knowledge_transport
    monkeypatch.setattr(knowledge_transport, "MAX_RESPONSE_BYTES", 64)
    with qdrant_server() as (url, calls):
        connection = {**config(url), "api_key": "", "embedding_key": ""}
        with pytest.raises(ValueError, match="^knowledge_response_too_large$"):
            knowledge_transport.collection_info(connection)
        assert len(calls) == 1
        with pytest.raises(ValueError, match="^knowledge_response_too_large$"):
            # The complete float embedding may fit 64 bytes, so force a smaller cap.
            monkeypatch.setattr(knowledge_transport, "MAX_RESPONSE_BYTES", 8)
            knowledge_transport.embed_query(connection, "query")
        assert len(calls) == 2
        assert "authorization" not in {key.lower() for key in calls[-1][2]}
