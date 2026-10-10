"""An exhausted model request stops a run without condemning its sources."""
from collections import Counter
from contextlib import contextmanager
import json

import pytest

from lib.infrastructure import training_workflow as engine
from lib.llm_client import BudgetGuard, ChatClient
from lib.model_request_reliability import ModelJSONError, ModelRequestError
from lib.model_request_scheduler import ModelQueueTimeout


@pytest.fixture(scope="session", autouse=True)
def mock_llm_server():
    """Inject local functions; no shared TCP port or external model is used."""
    yield


TEXT = "检查设备前必须断电。维护结束后记录结果。"


class Writer:
    model = "offline-scheduler-writer"

    def __init__(self, failure):
        self.failure = failure
        self.fail = True
        self.calls = []
        self.usage = {"calls": 0}

    def chat(self, messages, **kwargs):
        data = json.loads(messages[1]["content"])
        identity = data["source"]["id"]
        self.calls.append(identity)
        self.usage["calls"] += 1
        if self.fail and len(self.calls) == 2:
            raise self.failure
        return json.dumps({"question": "怎样安全检查设备？" + identity[:12],
                           "answer": "检查前先断电。", "reasoning": "断电是安全检查的必要前提。",
                           "quotes": [TEXT]}, ensure_ascii=False)


class Reviewer:
    model = "offline-scheduler-reviewer"

    def __init__(self):
        self.usage = {"calls": 0}

    def chat(self, messages, **kwargs):
        self.usage["calls"] += 1
        return json.dumps({"keep": True, "grounded": True, "reasoning_valid": True, "correctness": 5,
            "scores": {key: 5 for key in ("correctness", "reasoning", "grounding", "instruction", "safety")},
            "reason": "已按本次来源核对回答。"})


def make_workflow(tmp_path, *, generator=None, jev=None, size=3):
    source = tmp_path / "source.txt"
    source.write_text(TEXT, encoding="utf-8")
    output = tmp_path / "out"
    run_id = engine.create_run(output, sources=[source], targets=["sft"], concurrency=1,
        production={"version": 2, "quantity_policy": "bounded_replenishment", "goals": {"sft": size},
                    "round_size": size, "max_attempts": size * 3, "max_rounds": 3, "item_retries": 5})
    workflow = engine.Workflow(output, run_id, tmp_path, generator=generator, jev=jev or Reviewer())
    return workflow, engine.run_path(output, run_id)


@pytest.mark.parametrize("code,kind,attempts", [
    ("service_rate_limited", "transient", 3),
    ("service_timeout", "transient", 3),
    ("model_request_queue_timeout", "transient", 0),
    ("service_authentication_failed", "fatal", 1),
])
def test_exhausted_model_request_stops_the_run_and_resume_reuses_paid_checkpoints(tmp_path, code, kind, attempts):
    writer = Writer(ModelRequestError(code, kind, attempts))
    workflow, path = make_workflow(tmp_path, generator=writer)
    state = workflow.execute()
    assert state["status"] == "failed" and state["error"] == code
    assert state["production"]["status"] == "paused"
    assert state["production"]["stop_reason"] == code
    assert len(writer.calls) == 2
    assert not any(event["kind"] == "item_retry" for event in state["events"])
    assert not list((path / "production").glob("round-*/receipt.json"))
    # No completed item/response can be replaced by a transport-failure row.
    assert state["stages"]["sft"]["quarantined"] == 0
    first = writer.calls[0]
    writer.fail = False
    restored = engine.Workflow(path.parent.parent, state["id"], tmp_path, generator=writer, jev=Reviewer())
    state = restored.execute(resume_run=True)
    assert state["status"] == "completed", state.get("error")
    counts = Counter(writer.calls)
    assert counts[first] == 1 and sorted(counts.values()) == [1, 1, 2]
    rows = engine.read_json(path / "artifacts/sft.records.json")
    assert len(rows) == 3 and all(row["status"] == "eligible" for row in rows)
    assert state["production"]["attempted"] == 3


class NoPermit:
    def __init__(self):
        self.waits = 0

    @contextmanager
    def acquire(self, **kwargs):
        self.waits += 1
        raise ModelQueueTimeout("private queue payload must never be stored")
        yield  # pragma: no cover


def test_chatclient_queue_timeout_is_wrapped_once_and_workflow_does_not_requeue(tmp_path):
    scheduler = NoPermit()
    client = ChatClient("https://example.invalid/v1", "offline-private-key", "offline-model",
                        scheduler=scheduler, budget=BudgetGuard(tmp_path / "budget", 1),
                        price_input_per_1m=1, price_output_per_1m=1,
                        context_window_tokens=131072)
    client._request = lambda *a, **kw: pytest.fail("A queued request cannot reach the provider")
    workflow, path = make_workflow(tmp_path, generator=client, size=1)
    state = workflow.execute()
    assert state["status"] == "failed" and state["error"] == "model_request_queue_timeout"
    assert scheduler.waits == 1 and client.usage["calls"] == 0
    assert not client.budget.path.exists()
    assert state["stages"]["sft"]["quarantined"] == 0
    assert not any(event["kind"] == "item_retry" for event in state["events"])
    assert "private queue payload" not in (path / "state.json").read_text(encoding="utf-8")


@pytest.mark.parametrize("stage", ["sft", "trim", "package", "jev"])
def test_wrapped_model_failure_never_becomes_a_quality_or_package_rejection(tmp_path, stage):
    workflow, _ = make_workflow(tmp_path)
    calls = 0
    failure = ModelRequestError("service_connection_failed", "transient", 3)
    def action(unit):
        nonlocal calls
        calls += 1
        raise failure
    with pytest.raises(ModelRequestError) as caught:
        workflow.run_item_safely(stage, {"id": "source", "source_id": "fixed-source"}, action)
    assert caught.value is failure and calls == 1


def test_invalid_model_json_remains_an_isolated_quality_failure(tmp_path):
    workflow, _ = make_workflow(tmp_path)
    calls = 0
    def action(unit):
        nonlocal calls
        calls += 1
        raise ModelJSONError(3)
    rows = workflow.run_item_safely("sft", {"id": "source", "source_id": "fixed-source"}, action)
    assert calls == 1 and rows[0]["status"] == "quarantined"
    assert rows[0]["reason"] == "invalid_model_result"
    assert rows[0]["request_error"] == "model_json_invalid"


def test_other_transient_actions_keep_a_finite_retry_budget(tmp_path):
    workflow, _ = make_workflow(tmp_path)
    workflow.recipe["production"]["item_retries"] = 2
    calls = 0
    def action(unit):
        nonlocal calls
        calls += 1
        if calls < 3:
            raise TimeoutError("offline action timeout")
        return [{"id": unit["id"], "status": "eligible"}]
    rows = workflow.run_item_safely("sft", {"id": "source", "source_id": "fixed-source"}, action)
    assert calls == 3 and rows[0]["status"] == "eligible"
    assert len([event for event in workflow.state["events"] if event["kind"] == "item_retry"]) == 2


def test_other_transient_actions_stop_after_their_budget_and_remain_isolated(tmp_path):
    workflow, _ = make_workflow(tmp_path)
    workflow.recipe["production"]["item_retries"] = 2
    calls = 0
    def action(unit):
        nonlocal calls
        calls += 1
        raise TimeoutError("offline action timeout")
    rows = workflow.run_item_safely("sft", {"id": "source", "source_id": "fixed-source"}, action)
    assert calls == 3 and rows[0]["status"] == "quarantined"
    assert rows[0]["reason"] == "request_retries_exhausted" and rows[0]["request_attempts"] == 3
