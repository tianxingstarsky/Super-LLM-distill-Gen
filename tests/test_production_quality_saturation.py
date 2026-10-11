"""Quality exhaustion is a durable verdict, never a transport/parse failure."""
from __future__ import annotations

import json

import pytest

from lib.domain.workflow_production import evaluated_quality_stop, validate_production
from lib.infrastructure import training_workflow as engine
from lib.infrastructure.workflow_production import _quality_assessment_valid
from lib.io_utils import atomic_json
from lib.model_request_reliability import ModelRequestError
from tests.test_soft_production import assert_final_report_agrees, enable_adaptive_director
from tests.test_workflow_production import Generator, Reviewer, TEXT, create, model_request_data, verdict


@pytest.fixture(scope="session", autouse=True)
def mock_llm_server():
    yield


def evaluated_run(tmp_path, **kwargs):
    return create(tmp_path, version=2, quantity_policy="quality_first", quality_stop_version=1,
        goals=kwargs.pop("goals", {"sft": 1000}), source=kwargs.pop("source", False),
        round_size=kwargs.pop("round_size", 1), min_acceptance_rate=kwargs.pop("min_acceptance_rate", .2),
        low_acceptance_rounds=kwargs.pop("low_acceptance_rounds", 2), **kwargs)


def test_quality_stop_is_versioned_without_reinterpreting_saved_recipes():
    old = validate_production({"version": 2, "goals": {"sft": 1000}}, ["sft"])
    new = validate_production({**old, "quality_stop_version": 1}, ["sft"])
    assert not evaluated_quality_stop(old) and "quality_stop_version" not in old
    assert evaluated_quality_stop(new)


def test_new_defaults_leave_assessment_windows_before_the_thousand_limit():
    config = validate_production({"version": 2, "quality_stop_version": 1,
                                  "goals": {"sft": 1000}}, ["sft"])
    assert config["round_size"] == 100
    assert config["round_size"] * 2 < config["goals"]["sft"]
    million = validate_production({"version": 2, "quality_stop_version": 1,
                                   "goals": {"sft": 1_000_000}}, ["sft"])
    assert million["max_rounds"] * million["round_size"] >= 1_000_000
    explicit = validate_production({"version": 2, "quality_stop_version": 1,
                                    "goals": {"sft": 1000}, "round_size": 500}, ["sft"])
    assert explicit["round_size"] == 500


@pytest.mark.parametrize("value", [True, 0, 2, "1", None])
def test_invalid_quality_stop_marker_fails_before_execution(value):
    with pytest.raises(ValueError, match="invalid_production"):
        validate_production({"version": 2, "quality_stop_version": value}, ["sft"])


@pytest.mark.parametrize("row, expected", [
    ({"status": "eligible"}, True),
    ({"status": "duplicate"}, True),
    ({"status": "quarantined", "reason": "no_new_grounded_scenario"}, True),
    ({"status": "quarantined", "judge": verdict(False)}, True),
    ({"status": "quarantined", "generation_failure_kind": "quality"}, True),
    ({"status": "quarantined", "reason": "sft_quality_failed_after_repair", "feedback": "shape invalid"}, False),
    ({"status": "quarantined", "generation_failure_kind": "invalid_output", "judge": verdict(False)}, False),
    ({"status": "quarantined", "request_error": "model_json_invalid", "judge": verdict(False)}, False),
    ({"status": "quarantined", "reason": "qa_director_plan_invalid_after_repair"}, False),
    ({"status": "quarantined", "review_repair": {"error": "invalid_judge_schema"}, "judge": verdict(False)}, False),
    ({"status": "quarantined", "package_review": {"error": "model_output_invalid"}, "judge": verdict(False)}, False),
])
def test_quality_evidence_requires_a_real_assessment(row, expected):
    assert _quality_assessment_valid(row) is expected


class ScriptedQuality(engine.Workflow):
    """A deterministic final generator isolates the production state machine."""
    outcomes = ("good", "quality", "quality")
    attempted_ids = []

    def sft(self, unit):
        index = len(self.attempted_ids)
        self.attempted_ids.append(unit["id"])
        outcome = self.outcomes[min(index, len(self.outcomes) - 1)]
        if outcome == "good":
            return [{"id": unit["id"], "source_id": unit["source_id"], "source_context": unit,
                "status": "eligible", "messages": [
                    {"role": "user", "content": "维护场景 " + unit["id"]},
                    {"role": "assistant", "content": "断电后检查线路。"}],
                "judge": verdict(), "evidence_level": "model_assessed_synthetic"}]
        return [{**self.rejected(unit, "invalid_model_result" if outcome == "invalid_output" else
            "sft_quality_failed_after_repair"), "generation_failure_kind": outcome,
            **({"request_error": "model_json_invalid"} if outcome == "invalid_output" else
               {"judge": verdict(False)})}]


def execute_scripted(tmp_path, *, cls=ScriptedQuality, outcomes=None, **kwargs):
    output, run_id, path = evaluated_run(tmp_path, **kwargs)
    workflow = cls(output, run_id, tmp_path, generator=Generator(), jev=Reviewer())
    workflow.attempted_ids = []
    if outcomes:
        workflow.outcomes = outcomes
    state = workflow.execute()
    return state, workflow, output, run_id, path


def test_consecutive_valid_low_yield_stops_far_below_a_thousand(tmp_path):
    state, workflow, _, _, path = execute_scripted(tmp_path, low_acceptance_rounds=1)
    assert state["status"] == "completed", state.get("error")
    assert len(workflow.attempted_ids) == state["production"]["attempted"] == 3
    assert state["production"]["counts"]["sft"] == 1
    assert state["production"]["below_expectation"]["sft"] == 999
    assert state["production"]["stop_reason"] == "diminishing_returns"
    assert_final_report_agrees(path, state)


def test_malformed_results_are_attention_not_saturation(tmp_path):
    state, workflow, _, _, path = execute_scripted(tmp_path,
        outcomes=("good", "invalid_output"), max_attempts=4)
    assert state["status"] == "needs_attention", state.get("error")
    assert len(workflow.attempted_ids) == 4
    assert state["production"]["counts"]["sft"] == 1
    assert state["production"]["stop_reason"] == "max_attempts"
    assert state["production"]["invalid_assessment_count"] == 3
    assert state["production"]["quality_assessment_history"][-1]["targets"]["sft"] == {
        "assessed": 0, "unassessed": 1, "valid_for_saturation": False}
    assert_final_report_agrees(path, state)


def test_a_parse_failure_breaks_the_consecutive_quality_streak(tmp_path):
    state, workflow, _, _, path = execute_scripted(tmp_path,
        outcomes=("good", "quality", "invalid_output", "quality", "quality"))
    assert state["status"] == "needs_attention", state.get("error")
    assert len(workflow.attempted_ids) == 5
    assert state["production"]["stop_reason"] == "diminishing_returns"
    assert state["production"]["invalid_assessment_count"] == 1
    assert_final_report_agrees(path, state)


@pytest.mark.parametrize("target", ["cot", "dpo"])
def test_downstream_targets_keep_upstream_quality_verdicts_after_filtering(tmp_path, target):
    state, workflow, _, _, path = execute_scripted(tmp_path, outcomes=("quality",),
        goals={target: 1000}, targets=(target,))
    assert state["status"] == "needs_attention", state.get("error")
    assert len(workflow.attempted_ids) == state["production"]["attempted"] == 2
    assert state["production"]["stop_reason"] == "diminishing_returns"
    assert state["production"]["counts"][target] == 0
    assert len(workflow._production_receipts(path / "production")) == 2
    assessment = state["production"]["quality_assessment_history"][-1]["targets"][target]
    assert assessment == {"assessed": 1, "unassessed": 0, "valid_for_saturation": True,
                          "upstream_stage": "sft", "upstream_assessed": 1, "upstream_unassessed": 0}
    assert_final_report_agrees(path, state)


def test_downstream_cannot_relabel_upstream_parse_failures_as_quality_limits(tmp_path):
    state, workflow, _, _, path = execute_scripted(tmp_path, outcomes=("invalid_output",),
        goals={"cot": 1000}, targets=("cot",), max_attempts=3)
    assert state["status"] == "needs_attention", state.get("error")
    assert len(workflow.attempted_ids) == 3
    assert state["production"]["stop_reason"] == "max_attempts"
    assert state["production"]["invalid_assessment_count"] == 3
    assert len(workflow._production_receipts(path / "production")) == 3
    assert_final_report_agrees(path, state)


def test_low_yield_cannot_hide_unread_original_sources(tmp_path):
    sources = []
    for index in range(4):
        path = tmp_path / f"source-{index}.txt"
        path.write_text(TEXT + f"记录编号 {index}。", encoding="utf-8")
        sources.append(path)
    output = tmp_path / "output"
    run_id = engine.create_run(output, sources=sources, targets=["sft"], max_units=100,
        production={"version": 2, "quality_stop_version": 1, "goals": {"sft": 1000},
                    "round_size": 1, "low_acceptance_rounds": 2})
    workflow = ScriptedQuality(output, run_id, tmp_path, generator=Generator(), jev=Reviewer())
    workflow.attempted_ids, workflow.outcomes = [], ("quality", "quality", "quality", "good")
    state = workflow.execute()
    assert state["status"] == "completed", state.get("error")
    assert len(workflow.attempted_ids) == 4
    assert state["production"]["counts"]["sft"] == 1
    assert state["production"]["stop_reason"] == "source_coverage_complete"


class StopAfterReceipt(BaseException):
    pass


class InterruptedQuality(ScriptedQuality):
    def event(self, kind, **fields):
        super().event(kind, **fields)
        if kind == "production_round_completed" and fields["round"] == 2:
            raise StopAfterReceipt()


def test_resume_rebuilds_streak_from_committed_rounds_without_repeating_work(tmp_path):
    output, run_id, path = evaluated_run(tmp_path)
    original = InterruptedQuality(output, run_id, tmp_path, generator=Generator(), jev=Reviewer())
    original.attempted_ids = []
    with pytest.raises(StopAfterReceipt):
        original.execute()
    assert len(original.attempted_ids) == 2
    resumed = ScriptedQuality(output, run_id, tmp_path, generator=Generator(), jev=Reviewer())
    resumed.attempted_ids, resumed.outcomes = [], ("quality",)
    state = resumed.execute(resume_run=True)
    assert state["status"] == "completed", state.get("error")
    assert len(resumed.attempted_ids) == 1
    assert not set(resumed.attempted_ids) & set(original.attempted_ids)
    assert state["production"]["attempted"] == 3
    assert state["production"]["stop_reason"] == "diminishing_returns"
    assert_final_report_agrees(path, state)


def test_quality_assessment_receipt_cannot_be_changed_independently_of_saved_rows(tmp_path):
    _, workflow, _, _, path = execute_scripted(tmp_path)
    receipt_path = path / "production/round-000001/receipt.json"
    receipt = engine.read_json(receipt_path)
    assert len(workflow._production_receipts(path / "production")) == 3
    receipt["quality_assessments"]["sft"] = {
        "assessed": 0, "unassessed": 1, "valid_for_saturation": False}
    atomic_json(receipt_path, receipt)
    with pytest.raises(ValueError, match="production_round_integrity_error"):
        workflow._production_receipts(path / "production")


def test_unprocessed_source_limit_is_not_reported_as_quality_exhaustion(tmp_path):
    source = tmp_path / "source.jsonl"
    source.write_text("\n".join(json.dumps({"text": TEXT + f"记录编号 {index}。"}, ensure_ascii=False)
                                for index in range(3)), encoding="utf-8")
    output = tmp_path / "output"
    run_id = engine.create_run(output, sources=[source], targets=["sft"], max_units=2,
        production={"version": 2, "quality_stop_version": 1, "goals": {"sft": 1000},
                    "round_size": 1, "low_acceptance_rounds": 2})
    workflow = ScriptedQuality(output, run_id, tmp_path, generator=Generator(), jev=Reviewer())
    workflow.attempted_ids, workflow.outcomes = [], ("quality",)
    state = workflow.execute()
    assert state["status"] == "needs_attention", state.get("error")
    assert len(workflow.attempted_ids) == 2
    assert state["input_summary"]["deferred"] == 1
    assert state["production"]["stop_reason"] == "source_limit_reached"


class ExhaustedDirector(Generator):
    def chat(self, messages, **kwargs):
        data = model_request_data(messages)
        if "candidates" in data:
            self.calls.append(data)
            return json.dumps({"tasks": [{"id": row["id"], "skip_reason": "no_new_grounded_scenario",
                "guidance": "无新的有效情境。"} for row in data["candidates"]]})
        return super().chat(messages, **kwargs)


def test_director_saturation_requires_two_valid_rounds_and_no_answer_calls(tmp_path):
    output, run_id, path = evaluated_run(tmp_path, round_size=2, min_acceptance_rate=0)
    enable_adaptive_director(path)
    generator = ExhaustedDirector()
    state = engine.Workflow(output, run_id, tmp_path, generator=generator, jev=Reviewer()).execute()
    assert state["status"] == "needs_attention", state.get("error")
    assert state["production"]["attempted"] == 4 and generator.sft_calls == 0
    assert state["production"]["stop_reason"] == "director_saturation"
    assert [row["stop_expansion"] for row in state["production"]["director_saturation_evidence"]] == [False, True]
    assert_final_report_agrees(path, state)


def test_director_can_revisit_one_document_then_stop_on_evaluated_saturation(tmp_path):
    output, run_id, path = evaluated_run(tmp_path, source=True, min_acceptance_rate=0)
    enable_adaptive_director(path)
    generator = ExhaustedDirector()
    state = engine.Workflow(output, run_id, tmp_path, generator=generator, jev=Reviewer()).execute()
    assert state["status"] == "needs_attention", state.get("error")
    assert state["production"]["attempted"] == 2 and generator.sft_calls == 0
    assert state["production"]["stop_reason"] == "director_saturation"
    calls = [call for call in generator.calls if "candidates" in call]
    assert len(calls) == 2
    assert calls[0]["candidates"][0]["id"] != calls[1]["candidates"][0]["id"]
    assert calls[0]["candidates"][0]["source_context_pack"] == calls[1]["candidates"][0]["source_context_pack"]
    assert_final_report_agrees(path, state)


def test_director_expansion_stays_inside_candidate_cap_without_refilling_rejections(tmp_path):
    class GuidedQuality(ScriptedQuality):
        def directed_generation(self, units, *, needs_sft, needs_multiturn):
            self.state["qa_director"] = {"feedback": {}, "coverage": {}, "batches_planned": 1}
            self.state["stages"]["director"]["total"] = len(units)
            directory = self.path / "stage-results"
            directory.mkdir(exist_ok=True)
            (directory / "director.jsonl").write_text("".join(json.dumps(unit, ensure_ascii=False) + "\n"
                                                           for unit in units), encoding="utf-8")
            return {"sft": self.stage_items("sft", units, self.sft), "multiturn": []}
    output, run_id, path = evaluated_run(tmp_path, source=True, goals={"sft": 3}, min_acceptance_rate=0)
    enable_adaptive_director(path)
    workflow = GuidedQuality(output, run_id, tmp_path, generator=Generator(), jev=Reviewer())
    workflow.attempted_ids = []
    state = workflow.execute()
    assert state["status"] == "completed", state.get("error")
    assert len(workflow.attempted_ids) == state["production"]["attempted"] == 3
    assert state["production"]["counts"]["sft"] == 1
    assert state["production"]["stop_reason"] == "candidate_budget_reached"
    assert_final_report_agrees(path, state)


def test_provider_failure_is_never_a_planner_exhaustion_verdict(tmp_path):
    class UnavailablePlanner(Generator):
        def chat(self, messages, **kwargs):
            self.usage["calls"] += 1
            raise ModelRequestError("service_unavailable", "transient", 3)
    output, run_id, path = evaluated_run(tmp_path)
    generator = UnavailablePlanner()
    state = engine.Workflow(output, run_id, tmp_path, generator=generator, jev=Reviewer()).execute()
    assert state["status"] == "failed"
    assert state["error"] == "service_unavailable"
    assert generator.usage["calls"] == 1
    assert "planning_exhaustion" not in state.get("production", {})
    assert not list((path / "production").glob("round-*/receipt.json"))
