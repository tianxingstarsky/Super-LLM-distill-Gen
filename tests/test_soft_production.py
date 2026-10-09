"""Soft production expectations never create an obligation to invent fillers."""
from __future__ import annotations

import json

import pytest

from lib.domain.workflow_production import validate_production, production_batch_size
from lib.domain.workflow_qa_director import validate_qa_director
from lib.infrastructure import training_workflow as engine
from lib.io_utils import atomic_json
from tests.test_workflow_production import Generator, Reviewer, TEXT, create


@pytest.fixture(scope="session", autouse=True)
def mock_llm_server():
    """Offline injected clients need no shared TCP server."""
    yield


def soft_run(tmp_path, *, policy="quality_first", **kwargs):
    return create(tmp_path, version=2, quantity_policy=policy, **kwargs)


def assert_final_report_agrees(path, state):
    report = engine.read_json(path / "artifacts/quality.json")["production"]
    manifest = engine.verify_artifacts(path)["production"]
    for actual in (report, manifest):
        for key in ("status", "stop_reason", "counts", "goals", "quantity_policy", "below_expectation"):
            assert actual[key] == state["production"][key]


def test_v2_defaults_are_soft_while_v1_recipe_configuration_is_unchanged():
    legacy = validate_production({"goals": {"sft": 8}}, ["sft"])
    soft = validate_production({"version": 2, "goals": {"sft": 8}}, ["sft"])
    assert legacy["version"] == 1 and "quantity_policy" not in legacy
    assert legacy["min_acceptance_rate"] == .01 and legacy["low_acceptance_rounds"] == 3
    assert soft["quantity_policy"] == "quality_first"
    assert soft["min_acceptance_rate"] == .2 and soft["low_acceptance_rounds"] == 2
    assert production_batch_size(legacy, {"sft": 1}, 8, 1) == 7
    assert production_batch_size(soft, {"sft": 1}, 8, 1) == 0


@pytest.mark.parametrize("value", [
    {"version": 1, "quantity_policy": "quality_first"},
    {"version": 2, "quantity_policy": "hard_requirement"},
    {"version": 2, "quantity_policy": []},
    {"version": 2, "quantity_policy": None},
    {"version": 3},
])
def test_invalid_policy_is_rejected_before_any_requests(value):
    with pytest.raises(ValueError, match="invalid_production"):
        validate_production(value, ["sft"])


def test_document_generation_uses_source_once_without_forced_variants(tmp_path):
    output, run_id, path = soft_run(tmp_path, goals={"sft": 100})
    generator = Generator()
    state = engine.Workflow(output, run_id, tmp_path, generator=generator, jev=Reviewer()).execute()
    assert state["status"] == "completed", state.get("error")
    assert generator.sft_calls == 1
    assert state["production"]["attempted"] == 1
    assert state["production"]["goals"]["sft"]["remaining"] == 99
    assert state["production"]["stop_reason"] == "source_coverage_complete"
    assert engine.read_json(path / "recipe.json")["version"] == engine.SOFT_PRODUCTION_RECIPE_VERSION
    assert_final_report_agrees(path, state)


def test_open_generation_dedup_shortfall_is_not_refilled(tmp_path):
    output, run_id, path = soft_run(tmp_path, goals={"sft": 5}, source=False)
    generator = Generator(duplicate_first=100)
    state = engine.Workflow(output, run_id, tmp_path, generator=generator, jev=Reviewer()).execute()
    assert state["status"] == "completed", state.get("error")
    assert generator.sft_calls == 5
    assert state["production"]["counts"] == {"sft": 1}
    assert state["production"]["below_expectation"] == {"sft": 4}
    assert state["production"]["stop_reason"] == "candidate_budget_reached"
    assert len(state["production"]["yield_history"]["sft"]) <= 2
    assert_final_report_agrees(path, state)


def test_explicit_expansion_remains_available_but_not_required_for_completion(tmp_path):
    output, run_id, path = soft_run(tmp_path, policy="bounded_replenishment", goals={"sft": 5}, max_attempts=2)
    generator = Generator()
    state = engine.Workflow(output, run_id, tmp_path, generator=generator, jev=Reviewer()).execute()
    assert state["status"] == "completed", state.get("error")
    assert generator.sft_calls == 2
    assert state["production"]["counts"]["sft"] == 2
    assert state["production"]["stop_reason"] == "max_attempts"
    assert state["production"]["below_expectation"] == {"sft": 3}
    assert_final_report_agrees(path, state)


def test_explicit_expansion_can_generate_source_variants_when_productive(tmp_path):
    output, run_id, path = soft_run(tmp_path, policy="bounded_replenishment", goals={"sft": 3})
    generator = Generator()
    state = engine.Workflow(output, run_id, tmp_path, generator=generator, jev=Reviewer()).execute()
    assert state["status"] == "completed", state.get("error")
    assert generator.sft_calls == 3
    assert state["production"]["stop_reason"] == "expectation_reached"
    assert_final_report_agrees(path, state)


@pytest.mark.parametrize("policy", ["quality_first", "bounded_replenishment"])
def test_yield_drops_naturally_stop_a_task_with_useful_samples(tmp_path, policy):
    class YieldDrops(Generator):
        def chat(self, messages, **kwargs):
            if self.sft_calls:
                self.bad_first = 100
            return super().chat(messages, **kwargs)
    output, run_id, path = soft_run(tmp_path, policy=policy, goals={"sft": 8}, source=False,
                                  round_size=1, min_acceptance_rate=.2, low_acceptance_rounds=2)
    generator = YieldDrops()
    state = engine.Workflow(output, run_id, tmp_path, generator=generator, jev=Reviewer()).execute()
    assert state["status"] == "completed", state.get("error")
    assert state["production"]["attempted"] == 3
    assert generator.sft_calls == 5  # Two rejected candidates each get one bounded repair.
    assert state["production"]["stop_reason"] == "diminishing_returns"
    assert state["production"]["counts"]["sft"] == 1
    assert state["production"]["below_expectation"] == {"sft": 7}
    assert_final_report_agrees(path, state)


def test_zero_valid_samples_still_require_attention(tmp_path):
    output, run_id, path = soft_run(tmp_path, goals={"sft": 2}, source=False)
    generator = Generator(bad_first=100)
    state = engine.Workflow(output, run_id, tmp_path, generator=generator, jev=Reviewer()).execute()
    assert state["status"] == "needs_attention", state.get("error")
    assert state["production"]["attempted"] == 2
    assert generator.sft_calls == 4  # Repairs do not allocate new candidate identities.
    assert engine.verify_artifacts(path)["counts"]["sft"] == 0
    assert_final_report_agrees(path, state)


def test_source_preserving_cpt_keeps_all_selected_originals_even_above_expectation(tmp_path):
    source = tmp_path / "source.jsonl"
    source.write_text(json.dumps({"text": TEXT}, ensure_ascii=False) + "\n" +
                      json.dumps({"text": "保养结束后关闭设备，并把维修时间记录在日志中。"}, ensure_ascii=False), encoding="utf-8")
    output = tmp_path / "output"
    run_id = engine.create_run(output, sources=[source], targets=["cpt"], max_units=100,
                              production={"version": 2, "goals": {"cpt": 1}})
    path = engine.run_path(output, run_id)
    state = engine.Workflow(output, run_id, tmp_path).execute()
    assert state["status"] == "completed", state.get("error")
    assert state["production"]["counts"]["cpt"] == 2
    assert state["usage"] == {}
    assert_final_report_agrees(path, state)


def test_source_processing_limit_is_reported_as_unprocessed_work(tmp_path):
    source = tmp_path / "source.jsonl"
    source.write_text(json.dumps({"text": TEXT}, ensure_ascii=False) + "\n" +
                      json.dumps({"text": "保养结束后关闭设备，并把维修时间记录在日志中。"}, ensure_ascii=False), encoding="utf-8")
    output = tmp_path / "output"
    run_id = engine.create_run(output, sources=[source], targets=["sft"], max_units=1,
                              production={"version": 2, "goals": {"sft": 3}})
    path = engine.run_path(output, run_id)
    state = engine.Workflow(output, run_id, tmp_path, generator=Generator(), jev=Reviewer()).execute()
    assert state["status"] == "needs_attention", state.get("error")
    assert state["input_summary"]["deferred"] == 1
    assert state["production"]["stop_reason"] == "source_limit_reached"
    assert_final_report_agrees(path, state)


def test_final_dedup_loss_does_not_restart_quality_first_generation(tmp_path):
    class ConcurrentPublication(engine.Workflow):
        lost = False
        def final_qa_duplicate(self, row, target, history):
            if getattr(self, "_production_finalizing", False) and not self.lost and row.get("status") == "eligible":
                self.lost = True
                return {"id": "other-run:published-same-contract"}
            return None
    output, run_id, path = soft_run(tmp_path, goals={"sft": 3}, source=False)
    generator = Generator()
    state = ConcurrentPublication(output, run_id, tmp_path, generator=generator, jev=Reviewer()).execute()
    assert state["status"] == "completed", state.get("error")
    assert generator.sft_calls == 3
    assert state["production"]["counts"]["sft"] == 2
    assert state["production"]["stop_reason"] == "final_validation_loss"
    assert_final_report_agrees(path, state)


def test_soft_resume_reuses_paid_response_and_preserves_candidate_window(tmp_path):
    output, run_id, path = soft_run(tmp_path, goals={"sft": 4}, source=False)
    generator, reviewer = Generator(budget_at=3), Reviewer()
    state = engine.Workflow(output, run_id, tmp_path, generator=generator, jev=reviewer).execute()
    assert state["status"] == "failed" and state["error"] == "budget_exhausted"
    assert engine.verify_artifacts(path)["counts"]["sft"] == 2
    state = engine.Workflow(output, run_id, tmp_path, generator=generator, jev=reviewer).execute(resume_run=True)
    assert state["status"] == "completed", state.get("error")
    assert state["production"]["attempted"] == 4
    assert generator.sft_calls == 5  # Four useful responses plus the interrupted request.
    assert_final_report_agrees(path, state)


def test_partial_soft_export_keeps_final_dedup_shortfall_in_state_and_report(tmp_path):
    class ConcurrentPublication(engine.Workflow):
        lost = False
        def final_qa_duplicate(self, row, target, history):
            if getattr(self, "_production_finalizing", False) and not self.lost and row.get("status") == "eligible":
                self.lost = True
                return {"id": "other-run:published-same-contract"}
            return None
    output, run_id, path = soft_run(tmp_path, goals={"sft": 4}, source=False)
    generator = Generator(budget_at=3)
    state = ConcurrentPublication(output, run_id, tmp_path, generator=generator, jev=Reviewer()).execute()
    assert state["status"] == "failed" and state["error"] == "budget_exhausted"
    assert state["production"]["status"] == "paused"
    assert state["production"]["counts"]["sft"] == 1
    assert state["production"]["below_expectation"] == {"sft": 3}
    assert_final_report_agrees(path, state)


class ExhaustedDirector(Generator):
    def chat(self, messages, **kwargs):
        data = json.loads(messages[1]["content"])
        if "candidates" in data:
            self.calls.append(data)
            return json.dumps({"tasks": [{"id": candidate["id"], "skip_reason": "no_new_grounded_scenario",
                "guidance": "现有资料不再支持新的有效互动。"} for candidate in data["candidates"]]})
        return super().chat(messages, **kwargs)


def enable_adaptive_director(path):
    recipe = engine.read_json(path / "recipe.json")
    recipe["qa_director"] = validate_qa_director({"enabled": True, "planning_mode": "adaptive"})
    atomic_json(path / "recipe.json", recipe)
    state = engine.read_json(path / "state.json")
    state["recipe_hash"] = engine.digest(recipe)
    atomic_json(path / "state.json", state)


@pytest.mark.parametrize("policy", ["quality_first", "bounded_replenishment"])
def test_director_exhaustion_stops_both_new_policies_without_filler_calls(tmp_path, policy):
    output, run_id, path = soft_run(tmp_path, policy=policy, source=False, goals={"sft": 100})
    enable_adaptive_director(path)
    generator = ExhaustedDirector()
    state = engine.Workflow(output, run_id, tmp_path, generator=generator, jev=Reviewer()).execute()
    assert state["status"] == "needs_attention", state.get("error")
    assert state["production"]["attempted"] == 2
    assert generator.sft_calls == 0
    assert state["production"]["stop_reason"] == "director_saturation"
    assert state["production"]["director_saturation_evidence"] == [{"round": 1, "guided": 2,
        "source_exhausted": 0, "no_new_grounded_scenario": 2, "explicit_skip_count": 2,
        "fraction": 1., "stop_expansion": True}]
    assert len([call for call in generator.calls if "candidates" in call]) == 1
    assert_final_report_agrees(path, state)


def test_director_local_exhaustion_does_not_hide_unseen_sources(tmp_path):
    paths = []
    for number in range(3):
        path = tmp_path / f"source-{number}.txt"
        path.write_text(TEXT + f"文档编号为 {number}，检修时应核对编号。", encoding="utf-8")
        paths.append(path)
    output = tmp_path / "output"
    run_id = engine.create_run(output, sources=paths, targets=["sft"], max_units=100,
        production={"version": 2, "goals": {"sft": 100}, "round_size": 1, "min_acceptance_rate": 0},
        qa_director={"enabled": True, "planning_mode": "adaptive"})
    path = engine.run_path(output, run_id)
    generator = ExhaustedDirector()
    state = engine.Workflow(output, run_id, tmp_path, generator=generator, jev=Reviewer()).execute()
    assert state["status"] == "needs_attention", state.get("error")
    assert state["production"]["attempted"] == 3
    assert len([call for call in generator.calls if "candidates" in call]) == 3
    evidence = state["production"]["director_saturation_evidence"]
    assert [item["stop_expansion"] for item in evidence] == [False, False, True]
    assert generator.sft_calls == 0
    assert_final_report_agrees(path, state)


def test_low_initial_yield_does_not_skip_later_useful_source_units(tmp_path):
    paths = []
    for number in range(3):
        path = tmp_path / f"source-{number}.txt"
        path.write_text(TEXT + f"文档编号为 {number}，检修时应核对编号。", encoding="utf-8")
        paths.append(path)
    output = tmp_path / "output"
    run_id = engine.create_run(output, sources=paths, targets=["sft"], max_units=100,
        production={"version": 2, "goals": {"sft": 100}, "round_size": 1,
                    "min_acceptance_rate": .2, "low_acceptance_rounds": 1})
    path = engine.run_path(output, run_id)
    generator = Generator(bad_first=4)  # Two candidates and their bounded repair attempts.
    state = engine.Workflow(output, run_id, tmp_path, generator=generator, jev=Reviewer()).execute()
    assert state["status"] == "completed", state.get("error")
    assert state["production"]["attempted"] == 3
    assert generator.sft_calls == 5
    assert state["production"]["counts"]["sft"] == 1
    assert state["production"]["stop_reason"] == "source_coverage_complete"
    assert_final_report_agrees(path, state)


def test_cpt_without_expectation_still_reports_unprocessed_source_cap(tmp_path):
    source = tmp_path / "source.jsonl"
    source.write_text(json.dumps({"text": TEXT}, ensure_ascii=False) + "\n" +
                      json.dumps({"text": "保养结束后关闭设备，并把维修时间记录在日志中。"}, ensure_ascii=False), encoding="utf-8")
    output = tmp_path / "output"
    run_id = engine.create_run(output, sources=[source], targets=["cpt"], max_units=1,
                              production={"version": 2, "goals": {}})
    path = engine.run_path(output, run_id)
    state = engine.Workflow(output, run_id, tmp_path).execute()
    assert state["status"] == "needs_attention", state.get("error")
    assert state["production"]["stop_reason"] == "source_limit_reached"
    assert engine.verify_artifacts(path)["counts"]["cpt"] == 1
    assert_final_report_agrees(path, state)
