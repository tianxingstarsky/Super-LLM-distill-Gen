"""Useful partial open plans survive exhaustion and interruption without fillers."""
from __future__ import annotations

import json
import sqlite3

import pytest

from lib.domain.open_task_plan import task_plan_issue, validate_short_task_plan
from lib.infrastructure import training_workflow as engine
from lib.io_utils import atomic_json
from tests.test_soft_production import soft_run, assert_final_report_agrees
from tests.test_workflow_production import Generator, Reviewer


@pytest.fixture(scope="session", autouse=True)
def mock_llm_server():
    yield


class SmallPlanner(Generator):
    def __init__(self, *, useful=3, exhausted=True, invalid_after=None):
        super().__init__()
        self.useful, self.exhausted, self.invalid_after = useful, exhausted, invalid_after
        self.planning_calls = 0

    def chat(self, messages, **kwargs):
        data = json.loads(messages[1]["content"])
        if "count" not in data:
            return super().chat(messages, **kwargs)
        self.calls.append(data)
        self.usage["calls"] += 1
        self.planning_calls += 1
        if self.invalid_after is not None and self.planning_calls >= self.invalid_after:
            return json.dumps({"tasks": [""]})
        tasks = [f"维护场景 {index}：共同分析设备故障，再给出断电检查步骤。"
                 for index in range(data["offset"], data["offset"] + min(self.useful, data["count"]))]
        return json.dumps({"tasks": tasks, "exhausted": self.exhausted,
            "stop_reason": "no_new_grounded_scenario" if self.exhausted else None,
            "reason": "只存在这些不同且有实际用途的场景。" if self.exhausted else ""})


def test_short_plan_validation_preserves_legacy_exact_count_rule():
    assert task_plan_issue(["Work together on the problem."], 50, set()) == "wrong_task_count"
    assert task_plan_issue(["Work together on the problem."], 50, set(), allow_short=True) is None
    assert task_plan_issue([], 50, set(), allow_short=True) is None
    assert task_plan_issue(["A", "B"], 1, set(), allow_short=True) == "wrong_task_count"


@pytest.mark.parametrize("value", [
    {"tasks": [], "exhausted": "yes"},
    {"tasks": [], "exhausted": True, "stop_reason": "any_reason"},
    {"tasks": ["Scenario"], "exhausted": False, "stop_reason": "source_exhausted"},
    {"tasks": ["Repeat", "Repeat"], "exhausted": True},
    {"tasks": [""], "exhausted": True},
])
def test_bad_short_plan_metadata_is_rejected(value):
    with pytest.raises(ValueError, match="invalid_task_plan"):
        validate_short_task_plan(value, 50, set())


@pytest.mark.parametrize("policy", ["quality_first", "bounded_replenishment"])
def test_small_valid_plan_is_kept_when_planner_declares_exhaustion(tmp_path, policy):
    output, run_id, path = soft_run(tmp_path, policy=policy, source=False, goals={"sft": 1000}, round_size=100)
    generator = SmallPlanner(useful=3)
    state = engine.Workflow(output, run_id, tmp_path, generator=generator, jev=Reviewer()).execute()
    assert state["status"] == "completed", state.get("error")
    assert generator.planning_calls == 1 and generator.sft_calls == 3
    assert state["production"]["attempted"] == 3
    assert state["production"]["planning_requested"] == 50
    assert state["production"]["stop_reason"] == "no_new_grounded_scenario"
    assert state["input_summary"]["units"] == state["input_summary"]["ready"] == 3
    assert state["input_summary"]["quarantined"] == 0
    metadata = engine.read_json(path / "production/round-000001/planning.json")
    assert metadata["requested_window"] == 100 and metadata["requested"] == 50 and metadata["planned"] == 3
    receipt = engine.read_json(path / "production/round-000001/receipt.json")
    assert receipt["attempted"] == receipt["planned_ready"] == 3
    assert "planning.json" in receipt["sha256"] and "planned.jsonl" in receipt["sha256"]
    assert len(engine.read_json(path / "artifacts/sft.records.json")) == 3
    assert_final_report_agrees(path, state)


@pytest.mark.parametrize("exhausted", [True, False])
def test_empty_successful_plan_stops_without_fabricated_failure_rows(tmp_path, exhausted):
    output, run_id, path = soft_run(tmp_path, source=False, goals={"sft": 100}, round_size=100)
    generator = SmallPlanner(useful=0, exhausted=exhausted)
    state = engine.Workflow(output, run_id, tmp_path, generator=generator, jev=Reviewer()).execute()
    assert state["status"] == "needs_attention", state.get("error")
    assert generator.planning_calls == 1 and generator.sft_calls == 0
    assert state["production"]["attempted"] == 0
    assert state["production"]["stop_reason"] == "no_new_grounded_scenario"
    assert state["production"]["planning_exhaustion"]["exhaustion_source"] == ("planner_declared" if exhausted else "empty_window")
    assert engine.read_json(path / "input_records.json") == []
    assert engine.read_json(path / "artifacts/sft.records.json") == []
    assert_final_report_agrees(path, state)


def test_short_non_exhausted_plan_consumes_window_without_backfilling_rejections(tmp_path):
    output, run_id, path = soft_run(tmp_path, source=False, goals={"sft": 10}, round_size=10)
    generator = SmallPlanner(useful=3, exhausted=False)
    state = engine.Workflow(output, run_id, tmp_path, generator=generator, jev=Reviewer()).execute()
    assert state["status"] == "completed", state.get("error")
    assert generator.planning_calls == 1 and generator.sft_calls == 3
    assert state["production"]["attempted"] == 3
    assert state["production"]["planning_requested"] == 10
    assert state["production"]["stop_reason"] == "candidate_budget_reached"
    assert_final_report_agrees(path, state)


def test_multiple_short_batches_use_actual_offsets_and_bounded_planning_window(tmp_path):
    output, run_id, path = soft_run(tmp_path, source=False, goals={"sft": 100}, round_size=100)
    generator = SmallPlanner(useful=3, exhausted=False)
    state = engine.Workflow(output, run_id, tmp_path, generator=generator, jev=Reviewer()).execute()
    assert state["status"] == "completed", state.get("error")
    assert [call["offset"] for call in generator.calls if "count" in call] == [0, 3]
    assert generator.planning_calls == 2 and generator.sft_calls == 6
    assert state["production"]["attempted"] == 6 and state["production"]["planning_requested"] == 100
    assert state["production"]["stop_reason"] == "candidate_budget_reached"
    assert_final_report_agrees(path, state)


def test_bounded_extension_has_distinct_checkpoint_keys_for_short_rounds(tmp_path):
    output, run_id, path = soft_run(tmp_path, policy="bounded_replenishment", source=False,
                                  goals={"sft": 4}, round_size=2)
    generator = SmallPlanner(useful=1, exhausted=False)
    state = engine.Workflow(output, run_id, tmp_path, generator=generator, jev=Reviewer()).execute()
    assert state["status"] == "completed", state.get("error")
    assert generator.planning_calls == 4 and generator.sft_calls == 4
    assert [call["offset"] for call in generator.calls if "count" in call] == [0, 1, 2, 3]
    assert [call["count"] for call in generator.calls if "count" in call] == [2, 2, 2, 1]
    assert state["production"]["attempted"] == 4 and state["production"]["planning_requested"] == 7
    assert engine.verify_artifacts(path)["counts"]["sft"] == 4
    assert_final_report_agrees(path, state)


def test_invalid_later_batch_does_not_discard_earlier_valid_scenarios(tmp_path):
    output, run_id, path = soft_run(tmp_path, source=False, goals={"sft": 100}, round_size=100)
    generator = SmallPlanner(useful=2, exhausted=False, invalid_after=2)
    state = engine.Workflow(output, run_id, tmp_path, generator=generator, jev=Reviewer()).execute()
    assert state["status"] == "needs_attention", state.get("error")
    assert generator.planning_calls == 4 and generator.sft_calls == 2
    assert state["production"]["attempted"] == 2
    assert state["production"]["stop_reason"] == "planning_failed_after_repair"
    assert state["input_summary"]["quarantined"] == 0
    assert len(engine.read_json(path / "artifacts/sft.records.json")) == 2
    assert_final_report_agrees(path, state)


class ProcessDeath(BaseException):
    pass


class StopsAfterPlan(engine.Workflow):
    def event(self, kind, **fields):
        super().event(kind, **fields)
        if kind == "production_plan_committed":
            raise ProcessDeath()


def interrupted_plan(tmp_path):
    output, run_id, path = soft_run(tmp_path, source=False, goals={"sft": 100}, round_size=100)
    generator = SmallPlanner(useful=3)
    with pytest.raises(ProcessDeath):
        StopsAfterPlan(output, run_id, tmp_path, generator=generator, jev=Reviewer()).execute()
    assert generator.planning_calls == 1 and generator.sft_calls == 0
    return output, run_id, path, generator


def test_successful_partial_plan_resume_does_not_repeat_paid_planning(tmp_path):
    output, run_id, path, generator = interrupted_plan(tmp_path)
    state = engine.Workflow(output, run_id, tmp_path, generator=generator, jev=Reviewer()).execute(resume_run=True)
    assert state["status"] == "completed", state.get("error")
    assert generator.planning_calls == 1 and generator.sft_calls == 3
    assert state["production"]["attempted"] == 3
    assert state["production"]["stop_reason"] == "no_new_grounded_scenario"
    assert_final_report_agrees(path, state)


def test_plan_checkpoint_survives_death_before_metadata_file_publication(tmp_path, monkeypatch):
    from lib.infrastructure import workflow_production as production_engine
    output, run_id, path = soft_run(tmp_path, source=False, goals={"sft": 100}, round_size=100)
    generator = SmallPlanner(useful=3)
    original_write = production_engine.atomic_json
    interrupted = False
    def die_before_metadata(destination, value):
        nonlocal interrupted
        if destination.name == "planning.json" and not interrupted:
            interrupted = True
            raise ProcessDeath()
        original_write(destination, value)
    monkeypatch.setattr(production_engine, "atomic_json", die_before_metadata)
    with pytest.raises(ProcessDeath):
        engine.Workflow(output, run_id, tmp_path, generator=generator, jev=Reviewer()).execute()
    assert interrupted and generator.planning_calls == 1 and generator.sft_calls == 0
    assert not (path / "production/round-000001/planning.json").exists()
    state = engine.Workflow(output, run_id, tmp_path, generator=generator, jev=Reviewer()).execute(resume_run=True)
    assert state["status"] == "completed", state.get("error")
    assert generator.planning_calls == 1 and generator.sft_calls == 3
    assert_final_report_agrees(path, state)


@pytest.mark.parametrize("target", ["metadata", "rows"])
def test_uncommitted_plan_integrity_is_checked_before_any_new_model_calls(tmp_path, target):
    output, run_id, path, generator = interrupted_plan(tmp_path)
    round_dir = path / "production/round-000001"
    if target == "metadata":
        metadata = engine.read_json(round_dir / "planning.json")
        metadata["exhausted"] = False
        atomic_json(round_dir / "planning.json", metadata)
    else:
        with (round_dir / "planned.jsonl").open("a", encoding="utf-8") as handle:
            handle.write("{}\n")
    state = engine.Workflow(output, run_id, tmp_path, generator=generator, jev=Reviewer()).execute(resume_run=True)
    assert state["status"] == "failed" and state["error"] == "production_round_integrity_error"
    assert generator.planning_calls == 1 and generator.sft_calls == 0


def test_committed_receipt_cannot_change_the_pinned_planning_stop_decision(tmp_path):
    output, run_id, path = soft_run(tmp_path, source=False, goals={"sft": 100})
    workflow = engine.Workflow(output, run_id, tmp_path, generator=SmallPlanner(useful=1), jev=Reviewer())
    assert workflow.execute()["status"] == "completed"
    receipt_path = path / "production/round-000001/receipt.json"
    receipt = engine.read_json(receipt_path)
    receipt["planning"]["exhausted"] = False
    atomic_json(receipt_path, receipt)
    with pytest.raises(ValueError, match="production_round_integrity_error"):
        workflow._production_receipts(path / "production")


def test_planning_checkpoint_payload_digest_is_verified_before_resume(tmp_path):
    output, run_id, path, generator = interrupted_plan(tmp_path)
    with sqlite3.connect(path / "production-checkpoints.sqlite3") as connection:
        cursor = connection.execute("UPDATE checkpoints SET payload=? WHERE stage='ingest' AND payload LIKE ?",
            ('{"tampered":true}', '%"requested_window"%'))
        assert cursor.rowcount == 1
    state = engine.Workflow(output, run_id, tmp_path, generator=generator, jev=Reviewer()).execute(resume_run=True)
    assert state["status"] == "failed" and state["error"] == "checkpoint_integrity_error"
    assert generator.planning_calls == 1 and generator.sft_calls == 0
