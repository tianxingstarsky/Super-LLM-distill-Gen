"""Capacity, ordered concurrency, per-node bindings, and recovery without a provider."""
import json
import threading
import time
from types import SimpleNamespace

import pytest

from lib.domain.workflow_scale import generation_units, validate_node_models
from lib.infrastructure import training_workflow as engine
from lib.infrastructure.workflow_rows import WorkflowRows


def workflow(tmp_path, **options):
    output = tmp_path / "output"
    rid = engine.create_run(output, brief="Generate equipment maintenance examples", targets=["sft"], **options)
    return engine.Workflow(output, rid, tmp_path)


@pytest.mark.parametrize("option,value", [("sample_count", 100001), ("sample_count", True),
    ("concurrency", 17), ("concurrency", 0), ("batch_size", 501), ("batch_size", 0), ("tasks", 100001)])
def test_capacity_is_validated(tmp_path, option, value):
    with pytest.raises(ValueError):
        workflow(tmp_path, **{option: value})


def test_capacity_and_node_snapshot_are_persisted(tmp_path):
    bindings = {"sft": {"generation": {"backend": "service-a", "model": "writer"},
                        "jev": {"backend": "service-b", "model": "reviewer"}}}
    run = workflow(tmp_path, sample_count=50000, max_units=100000, concurrency=8, batch_size=200, node_models=bindings)
    bindings["sft"]["generation"]["model"] = "changed"
    assert run.recipe["sample_count"] == 50000
    assert run.recipe["node_models"]["sft"]["generation"]["model"] == "writer"
    with pytest.raises(ValueError):
        validate_node_models({"sft": {"generation": {"backend": "a", "model": "b", "api_key": "secret"}}})


def test_large_planning_is_batched_and_resumes_completed_calls(tmp_path):
    run = workflow(tmp_path, sample_count=125, max_units=1000)
    calls = []

    class Planner:
        model = "offline-planner"
        usage = {}

        def chat(self, messages, **kwargs):
            data = json.loads(messages[1]["content"])
            calls.append((data["offset"], data["count"]))
            if len(calls) == 2:
                raise RuntimeError("temporary unavailable")
            return json.dumps({"tasks": [f"Maintenance scenario {data['offset'] + index}" for index in range(data["count"])]})

    run.generator = Planner()
    run.stage = "ingest"
    with pytest.raises(RuntimeError):
        run.plan()
    units = run.plan()
    assert calls == [(0, 50), (50, 50), (50, 50), (100, 25)]
    assert len(units) == len({unit["id"] for unit in units}) == 125
    assert run.state["stages"]["ingest"]["done"] == 125


def test_concurrency_is_bounded_results_ordered_and_replay_skips_work(tmp_path):
    run = workflow(tmp_path, concurrency=4, batch_size=7)
    lock = threading.Lock()
    active = peak = calls = 0

    def action(item):
        nonlocal active, peak, calls
        with lock:
            active += 1
            calls += 1
            peak = max(peak, active)
        time.sleep((6 - item["index"] % 7) / 1000)
        with lock:
            active -= 1
        return [{"status": "eligible", "index": item["index"]},
                {"status": "quarantined", "index": item["index"], "reason": "offline check"}]

    items = [{"index": index} for index in range(40)]
    rows = run.stage_items("sft", items, action)
    assert isinstance(rows, WorkflowRows) and len(rows) == 80
    assert [row["index"] for row in rows.eligible(40)] == list(range(40))
    assert 1 < peak <= 4 and calls == 40
    metrics = run.state["stages"]["sft"]
    assert metrics["done"] == metrics["eligible"] == metrics["quarantined"] == 40
    assert metrics["batches_done"] == metrics["batches_total"] == 6
    assert len(run.stage_items("sft", items, action)) == 80
    assert calls == 40 and metrics["cached"] == 40


def test_failure_preserves_completed_items_for_resume(tmp_path):
    run = workflow(tmp_path, concurrency=1, batch_size=10)
    calls = []
    fail = True

    def action(item):
        calls.append(item)
        if item == 3 and fail:
            raise RuntimeError("retry me")
        return [{"status": "eligible", "index": item}]

    with pytest.raises(RuntimeError):
        run.stage_items("sft", list(range(8)), action)
    fail = False
    rows = run.stage_items("sft", list(range(8)), action)
    assert [row["index"] for row in rows] == list(range(8))
    assert all(calls.count(item) == 1 for item in range(3))
    assert calls.count(3) == 2


def test_node_clients_are_separate_and_usage_is_exact_under_concurrency(tmp_path, monkeypatch):
    bindings = {node: {"generation": {"backend": node, "model": node + "-model"}}
                for node in ("sft", "preference")}
    run = workflow(tmp_path, concurrency=4, batch_size=8, node_models=bindings)
    created = []

    class Client:
        def __init__(self, backend, model):
            self.model = model
            self.client = SimpleNamespace(base_url=backend)
            self.usage = {"calls": 0, "prompt_tokens": 0, "completion_tokens": 0}

        def chat(self, messages, **kwargs):
            time.sleep(.005)
            self.usage["calls"] += 1
            self.usage["prompt_tokens"] += 10
            self.usage["completion_tokens"] += 5
            return json.dumps({"status": "eligible"})

    def load(root, *, backend, model, role, allow_global_endpoint_override):
        assert allow_global_endpoint_override is False
        created.append((backend, model))
        return Client(backend, model), model

    monkeypatch.setattr(engine, "load_backend", load)
    for stage in bindings:
        run.stage_items(stage, list(range(12)), lambda item: [run.ask(item, "generation", "workflow.sft", {"index": item})])
    assert run.state["usage"]["generation"] == {"calls": 24, "prompt_tokens": 240, "completion_tokens": 120}
    assert {name for name, model in created} == {"sft", "preference"}
    assert run.state["models"]["sft.generation"]["model"] == "sft-model"
    assert run.state["models"]["preference.generation"]["model"] == "preference-model"


def test_documents_expand_with_provenance_but_recorded_context_is_not_repeated():
    document = {"id": "doc", "source_id": "source", "source_location": {"chunk": 1}, "kind": "document", "text": "facts"}
    conversation = {"id": "record", "kind": "conversation", "messages": [{"role": "user", "content": "hello"}]}
    units = generation_units([document, conversation], 2000)
    assert len(units) == len({unit["id"] for unit in units}) == 2000
    assert sum(unit["kind"] == "conversation" for unit in units) == 1
    assert all(unit["source_id"] == "source" and unit["source_location"] == {"chunk": 1}
               for unit in units if unit["kind"] == "document")
    assert generation_units([conversation], 2000) == [conversation]


def test_explicit_node_service_ignores_global_endpoint_override(tmp_path, monkeypatch):
    from lib import llm_client
    folder = tmp_path / "configs"
    folder.mkdir()
    (folder / "backends.yaml").write_text("backends:\n  chosen:\n    base_url: https://selected.invalid/v1\n    models: [writer]\n", encoding="utf-8")
    monkeypatch.setenv("LLM_BASE_URL", "https://temporary.invalid/v1")
    monkeypatch.setattr(llm_client, "ChatClient", lambda **kwargs: SimpleNamespace(**kwargs))
    selected, _ = llm_client.load_backend(tmp_path, backend="chosen", model="writer", allow_global_endpoint_override=False)
    legacy, _ = llm_client.load_backend(tmp_path, backend="chosen", model="writer")
    assert selected.base_url == "https://selected.invalid/v1"
    assert legacy.base_url == "https://temporary.invalid/v1"
    with pytest.raises(ValueError, match="workflow_node_service_not_configured"):
        llm_client.load_backend(tmp_path, backend="missing", model="writer", allow_global_endpoint_override=False)


def test_preference_rescores_chosen_answer_when_its_node_uses_another_reviewer(tmp_path, monkeypatch):
    bindings = {node: {"jev": {"backend": "reviewers", "model": node}} for node in ("sft", "preference")}
    run = workflow(tmp_path, node_models=bindings)
    grades = []

    def grade(key, context, answer):
        grades.append(key[-1])
        score = 4 if key[-1] == "chosen_judge" else 1
        return {"keep": score >= 4, "grounded": score >= 4, "reasoning_valid": score >= 4,
                "correctness": score, "scores": dict.fromkeys(("correctness", "reasoning", "grounding", "instruction", "safety"), score)}

    monkeypatch.setattr(run, "ask", lambda *args: {"answer": "Incorrect alternative", "reasoning": "Unfounded argument"})
    monkeypatch.setattr(run, "judge_answer", grade)
    sample = {"id": "one", "source_id": "source", "source_context": {}, "evidence_level": "source_grounded",
              "messages": [{"role": "user", "content": "Question"}, {"role": "assistant", "content": "Source answer"}],
              "judge": {"correctness": 5}}
    row = run.dpo(sample)[0]
    assert grades == ["alternative_judge", "chosen_judge"]
    assert row["status"] == "eligible" and row["preference"]["chosen"]["correctness"] == 4
    assert sample["judge"]["correctness"] == 5
