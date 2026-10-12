"""Persistent uniqueness through complete offline workflows and batch resumes."""
from concurrent.futures import ThreadPoolExecutor

import pytest

from lib.domain.math_tasks import verified_arithmetic_fingerprint
from lib.infrastructure import arithmetic_registry as registry_module
from lib.infrastructure import training_workflow as engine
from lib.infrastructure.arithmetic_registry import ArithmeticRegistry
from tests.test_arithmetic_workflow_independence import NoModels, CountingWriter, production
from tests.test_workflow_production import Reviewer


@pytest.fixture(scope="session", autouse=True)
def mock_llm_server():
    """All clients are injected: no network or shared test server is needed."""
    yield


def make_run(output, *, batch=False, count=5, theme="Arithmetic practice"):
    run_id = engine.create_run(output, targets=["gsm8k"], tasks=count, brief=theme,
        production=production("gsm8k", goal=count) if batch else None)
    return engine.Workflow(output, run_id, output, generator=NoModels(), jev=NoModels())


def rows(workflow):
    return engine.read_json(workflow.path / "artifacts/gsm8k.records.json")


def fingerprints(records):
    return {verified_arithmetic_fingerprint(row) for row in records}


def consume_space(output, monkeypatch, count):
    monkeypatch.setattr(registry_module, "GSM8K_V2_SEED_CAPACITY", 8)
    registry = ArithmeticRegistry(output / "arithmetic-registry.sqlite3")
    for ordinal in range(count):
        assert registry.reserve("earlier-archived-work", ordinal, "Earlier theme") is not None
    return registry


@pytest.mark.parametrize("batch", [False, True])
@pytest.mark.parametrize("second_theme", ["Arithmetic practice", "A renamed project"])
def test_repeated_runs_and_changed_themes_do_not_repeat_problems(tmp_path, batch, second_theme):
    first = make_run(tmp_path, batch=batch)
    assert first.execute()["status"] == "completed"
    second = make_run(tmp_path, batch=batch, theme=second_theme)
    assert second.execute()["status"] == "completed"
    first_rows, second_rows = rows(first), rows(second)
    assert len(first_rows) == len(second_rows) == 5
    assert len(fingerprints(first_rows + second_rows)) == 10
    assert first.recipe["arithmetic_registry_version"] == second.recipe["arithmetic_registry_version"] == 1
    assert ArithmeticRegistry(tmp_path / "arithmetic-registry.sqlite3").snapshot()["allocated"] == 10
    assert not first.state["usage"] and not second.state["usage"]


def test_concurrent_complete_runs_share_one_ledger(tmp_path):
    workflows = [make_run(tmp_path, theme=f"Concurrent theme {index}") for index in range(2)]
    with ThreadPoolExecutor(max_workers=2) as workers:
        states = list(workers.map(lambda workflow: workflow.execute(), workflows))
    assert [state["status"] for state in states] == ["completed", "completed"], states
    assert len(fingerprints(rows(workflows[0]) + rows(workflows[1]))) == 10


def test_cancelled_batch_resumes_original_questions_after_another_run(tmp_path):
    first = make_run(tmp_path, batch=True)
    original_event = first.event

    def interrupt(kind, **fields):
        original_event(kind, **fields)
        if kind == "production_round_completed" and fields["round"] == 1:
            engine.cancel(tmp_path, first.state["id"])

    first.event = interrupt
    assert first.execute()["status"] == "cancelled"
    checkpoint = first.path / "production/round-000001/gsm8k.jsonl"
    saved = checkpoint.read_bytes()
    second = make_run(tmp_path)
    assert second.execute()["status"] == "completed"
    restored = engine.Workflow(tmp_path, first.state["id"], tmp_path, generator=NoModels(), jev=NoModels())
    state = restored.execute(resume_run=True)
    assert state["status"] == "completed", state.get("error")
    assert checkpoint.read_bytes() == saved
    assert len(rows(restored)) == 5
    assert len(fingerprints(rows(restored) + rows(second))) == 10
    assert state["production"]["attempted"] == 5
    assert ArithmeticRegistry(tmp_path / "arithmetic-registry.sqlite3").snapshot()["allocated"] == 10


@pytest.mark.parametrize("batch", [False, True])
@pytest.mark.parametrize("consumed,expected", [(6, 2), (8, 0)])
def test_finite_exhaustion_is_a_normal_stop_without_replenishment(tmp_path, monkeypatch, batch, consumed, expected):
    registry = consume_space(tmp_path, monkeypatch, consumed)
    workflow = make_run(tmp_path, batch=batch)
    state = workflow.execute()
    assert state["status"] == "completed", state.get("error")
    assert state["stages"]["gsm8k"]["stop_reason"] == "arithmetic_space_exhausted"
    assert state["quality"]["targets"]["gsm8k"]["eligible"] == expected
    assert len(rows(workflow)) == expected
    assert registry.snapshot()["allocated"] == 8
    quality_report = engine.read_json(workflow.path / "artifacts/quality.json")
    assert quality_report["arithmetic_registry"]["exhausted"] is True
    if batch:
        progress = state["production"]["goals"]["gsm8k"]
        assert progress["attempted"] == progress["eligible"] == expected
        assert progress["remaining"] == 5 - expected
        assert progress["stop_reason"] == "arithmetic_space_exhausted"
        assert state["production"]["stop_reason"] == "arithmetic_space_exhausted"
    # Reopening a completed result neither allocates nor rewrites its questions.
    old_bytes = (workflow.path / "artifacts/gsm8k.records.json").read_bytes()
    reopened = engine.Workflow(tmp_path, state["id"], tmp_path, generator=NoModels(), jev=NoModels())
    assert reopened.execute(resume_run=True)["status"] == "completed"
    assert (workflow.path / "artifacts/gsm8k.records.json").read_bytes() == old_bytes
    assert registry.snapshot()["allocated"] == 8


def test_resuming_after_last_available_batch_cannot_refill_exhausted_space(tmp_path, monkeypatch):
    registry = consume_space(tmp_path, monkeypatch, 6)
    workflow = make_run(tmp_path, batch=True)
    original_event = workflow.event

    def interrupt(kind, **fields):
        original_event(kind, **fields)
        if kind == "production_round_completed":
            engine.cancel(tmp_path, workflow.state["id"])

    workflow.event = interrupt
    assert workflow.execute()["status"] == "cancelled"
    first_path = workflow.path / "production/round-000001/gsm8k.jsonl"
    saved = first_path.read_bytes()
    restored = engine.Workflow(tmp_path, workflow.state["id"], tmp_path, generator=NoModels(), jev=NoModels())
    state = restored.execute(resume_run=True)
    assert state["status"] == "completed", state.get("error")
    assert first_path.read_bytes() == saved
    assert len(rows(restored)) == 2
    assert state["production"]["attempted"] == 2
    assert state["production"]["stop_reason"] == "arithmetic_space_exhausted"
    assert registry.snapshot()["allocated"] == 8


def test_exhausted_arithmetic_does_not_block_other_generation_targets(tmp_path, monkeypatch):
    registry = consume_space(tmp_path, monkeypatch, 8)
    run_id = engine.create_run(tmp_path, targets=["sft", "gsm8k"], brief="Maintenance practice.",
        production={"version": 2, "quantity_policy": "quality_first", "goals": {"sft": 2, "gsm8k": 5},
                    "round_size": 2, "max_attempts": 10, "max_rounds": 5})
    writer = CountingWriter()
    workflow = engine.Workflow(tmp_path, run_id, tmp_path, generator=writer, jev=Reviewer())
    state = workflow.execute()
    assert state["status"] == "completed", state.get("error")
    assert len(writer.answers) == 2
    assert state["quality"]["targets"]["sft"]["eligible"] == 2
    assert state["quality"]["targets"]["gsm8k"]["eligible"] == 0
    assert state["production"]["goals"]["gsm8k"]["attempted"] == 0
    assert state["production"]["goals"]["gsm8k"]["stop_reason"] == "arithmetic_space_exhausted"
    assert registry.snapshot()["allocated"] == 8


@pytest.mark.parametrize("batch", [False, True])
def test_exhaustion_does_not_hide_an_unavailable_sft_source(tmp_path, monkeypatch, batch):
    consume_space(tmp_path, monkeypatch, 8)
    source = tmp_path / "invalid.jsonl"
    source.write_text("not valid json\n", encoding="utf-8")
    run_id = engine.create_run(tmp_path, sources=[source], targets=["sft", "gsm8k"], tasks=5,
        production=production("sft", "gsm8k") if batch else None)
    workflow = engine.Workflow(tmp_path, run_id, tmp_path, generator=NoModels(), jev=NoModels())
    state = workflow.execute()
    assert state["status"] == "needs_attention", state.get("error")
    assert state["input_summary"]["quarantined"] == 1
    assert state["quality"]["targets"]["sft"]["eligible"] == 0
    assert state["quality"]["targets"]["gsm8k"]["eligible"] == 0


@pytest.mark.parametrize("batch", [False, True])
def test_exhaustion_does_not_hide_pending_human_review(tmp_path, monkeypatch, batch):
    consume_space(tmp_path, monkeypatch, 6)
    run_id = engine.create_run(tmp_path, targets=["gsm8k"], tasks=5,
        review_repair={"mode": "human"}, production=production("gsm8k") if batch else None)
    workflow = engine.Workflow(tmp_path, run_id, tmp_path, generator=NoModels(), jev=NoModels())
    state = workflow.execute()
    assert state["status"] == "needs_attention", state.get("error")
    assert state["quality"]["targets"]["gsm8k"]["eligible"] == 0
    assert state["quality"]["targets"]["gsm8k"]["reasons"]
    assert len(rows(workflow)) == 2
