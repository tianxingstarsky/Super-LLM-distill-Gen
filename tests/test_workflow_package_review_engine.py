"""Final-record AI review is bounded, explicit about coverage, and resumable."""
from __future__ import annotations

from copy import deepcopy
import json
import tracemalloc

import pytest

from lib.domain.workflow_node_prompts import LEGACY_NODE_PROMPT_IDS
from lib.infrastructure import training_workflow as engine
from lib.io_utils import atomic_json
from lib.llm_client import BudgetExceeded


def good_verdict(keep=True):
    score = 5 if keep else 1
    return {"keep": keep, "grounded": keep, "reasoning_valid": keep, "correctness": score,
            "scores": {key: score for key in ("correctness", "reasoning", "grounding", "instruction", "safety")},
            "reason": "Source and final record are consistent." if keep else "Unsafe maintenance advice."}


class Reviewer:
    model = "offline-package-reviewer"

    def __init__(self, *, reject=None, invalid_at=None, fail_at=None, failure=RuntimeError, hook=None):
        self.calls, self.package_calls = [], []
        self.usage = {"calls": 0}
        self.reject, self.invalid_at, self.fail_at = reject, invalid_at, fail_at
        self.failure, self.hook = failure, hook

    def chat(self, messages, **kwargs):
        data = json.loads(messages[1]["content"])
        call = {"messages": deepcopy(messages), "data": data, "kwargs": kwargs}
        self.calls.append(call)
        self.usage["calls"] += 1
        if "target" in data.get("context", {}):
            self.package_calls.append(call)
            position = len(self.package_calls)
            if position == self.fail_at:
                raise self.failure("provider message must not enter saved reports")
            if position == self.invalid_at:
                return json.dumps({"keep": "true"})
            if self.hook:
                self.hook()
            keep = not (self.reject and self.reject(data["answer"]))
            return json.dumps(good_verdict(keep))
        return json.dumps(good_verdict())


def make_run(tmp_path, count=6, *, targets=None, **options):
    source = tmp_path / "source.jsonl"
    source.write_text("\n".join(json.dumps({
        "text": f"Maintenance procedure {index}: disconnect power before inspecting the cable and record the result."
    }) for index in range(count)), encoding="utf-8")
    output = tmp_path / "output"
    rid = engine.create_run(output, sources=[source], targets=targets or ["cpt"], **options)
    return output, rid, engine.run_path(output, rid), source


def report(path):
    return engine.read_json(path / "artifacts/quality.json")


def test_disabled_package_review_keeps_document_cpt_offline(tmp_path):
    output, rid, path, _ = make_run(tmp_path)
    reviewer = Reviewer()
    state = engine.Workflow(output, rid, tmp_path, jev=reviewer).execute()
    assert state["status"] == "completed" and state["usage"] == {}
    assert reviewer.calls == []
    assert report(path)["package_review"] == {"enabled": False, "status": "disabled"}
    assert all("package_review" not in row for row in engine.read_json(path / "artifacts/cpt.records.json"))


def test_all_review_isolates_rejected_rows_and_keeps_source_unchanged(tmp_path, monkeypatch):
    output, rid, path, source = make_run(tmp_path, package_review={"enabled": True, "mode": "all"})
    source_text = source.read_text(encoding="utf-8")
    original_write = engine._write_quality_report
    def check_writing_stage(*args, **kwargs):
        pending_state = engine.read_json(path / "state.json")
        assert pending_state["stages"]["package"]["status"] == "running"
        assert pending_state["stages"]["package"]["phase"] == "writing_artifacts"
        assert not any(event["stage"] == "package" and event["kind"] == "stage_completed"
                       for event in pending_state["events"])
        return original_write(*args, **kwargs)
    monkeypatch.setattr(engine, "_write_quality_report", check_writing_stage)
    reviewer = Reviewer(reject=lambda payload: "procedure 2:" in payload["text"])
    state = engine.Workflow(output, rid, tmp_path, jev=reviewer).execute()
    assert state["status"] == "needs_attention"
    assert len(reviewer.package_calls) == 6
    rows = engine.read_json(path / "artifacts/cpt.records.json")
    rejected = [row for row in rows if row["status"] == "quarantined"]
    assert len(rejected) == 1 and rejected[0]["reason"] == "package_ai_review_rejected"
    assert rejected[0]["package_review"]["verdict"]["reason"] == "Unsafe maintenance advice."
    training = (path / "artifacts/cpt.jsonl").read_text(encoding="utf-8")
    assert "procedure 2:" not in training
    assert all(row["status"] == "eligible" for row in engine.read_json(path / "checkpoints/cpt" /
               next((path / "checkpoints/cpt").glob("*.json")).name)["data"])
    assert source.read_text(encoding="utf-8") == source_text
    summary = report(path)["package_review"]["targets"]["cpt"]
    assert summary == {"candidates": 6, "planned": 6, "reviewed": 6, "accepted": 5, "rejected": 1,
                       "unreviewed": 0, "coverage_percent": 100.0, "status": "all_reviewed"}
    assert engine.verify_artifacts(path)["package_review"]["targets"]["cpt"] == summary
    assert state["stages"]["package"]["done"] == state["stages"]["package"]["total"] == 6


def test_sample_percent_cap_and_unreviewed_status_are_explicit(tmp_path):
    output, rid, path, _ = make_run(tmp_path, count=20, package_review={
        "enabled": True, "mode": "sample", "sample_percent": 50, "max_samples_per_target": 3})
    reviewer = Reviewer()
    state = engine.Workflow(output, rid, tmp_path, jev=reviewer).execute()
    assert state["status"] == "completed" and len(reviewer.package_calls) == 3
    rows = engine.read_json(path / "artifacts/cpt.records.json")
    assert sum(row["package_review"]["status"] == "accepted" for row in rows) == 3
    assert sum(row["package_review"]["status"] == "not_selected" for row in rows) == 17
    summary = report(path)["targets"]["cpt"]["package_review"]
    assert summary["status"] == "sampled" and summary["unreviewed"] == 17
    assert summary["coverage_percent"] == 15.0
    assert engine.verify_artifacts(path)["counts"]["cpt"] == 20


def test_percentage_rounds_up_and_all_mode_does_not_apply_sample_cap(tmp_path):
    output, rid, path, _ = make_run(tmp_path, count=6, package_review={
        "enabled": True, "mode": "sample", "sample_percent": 1, "max_samples_per_target": 2})
    reviewer = Reviewer()
    assert engine.Workflow(output, rid, tmp_path, jev=reviewer).execute()["status"] == "completed"
    assert report(path)["package_review"]["targets"]["cpt"]["reviewed"] == 1
    other = tmp_path / "other"
    other.mkdir()
    output, rid, path, _ = make_run(other, count=6, package_review={
        "enabled": True, "mode": "all", "max_samples_per_target": 2})
    reviewer = Reviewer()
    assert engine.Workflow(output, rid, other, jev=reviewer).execute()["status"] == "completed"
    assert len(reviewer.package_calls) == 6


def test_deterministic_duplicate_filter_runs_before_ai_review(tmp_path):
    output, rid, path, _ = make_run(tmp_path, count=1, package_review={"enabled": True, "mode": "all"})
    reviewer = Reviewer()
    run = engine.Workflow(output, rid, tmp_path, jev=reviewer)
    (path / "input_records.json").write_text("[]", encoding="utf-8")
    row = {"id": "first", "source_id": "source", "status": "eligible", "text": "Disconnect power before cable inspection."}
    run.package({"cpt": [row, {**row, "id": "second"}, {"id": "bad", "source_id": "source",
                         "status": "quarantined", "reason": "potential_secret"}]})
    assert len(reviewer.package_calls) == 1
    assert report(path)["package_review"]["targets"]["cpt"]["candidates"] == 1
    assert row.get("package_review") is None
    assert not list((path / "stage-results").glob("package-*.sqlite3"))


@pytest.mark.parametrize("failure", [RuntimeError, BudgetExceeded])
def test_provider_or_budget_failure_resumes_completed_review_only_once(tmp_path, failure):
    output, rid, path, _ = make_run(tmp_path, package_review={"enabled": True, "mode": "all"}, batch_size=1)
    reviewer = Reviewer(fail_at=2, failure=failure)
    state = engine.Workflow(output, rid, tmp_path, jev=reviewer).execute()
    assert state["status"] == "failed" and state["stages"]["package"]["done"] == 1
    assert "provider message" not in json.dumps(state)
    assert not (path / "artifacts/manifest.json").exists()
    first = reviewer.package_calls[0]["data"]["answer"]
    reviewer.fail_at = None
    state = engine.resume(output, rid, tmp_path, jev=reviewer)
    assert state["status"] == "completed"
    assert sum(call["data"]["answer"] == first for call in reviewer.package_calls) == 1
    assert len(reviewer.package_calls) == 7
    assert state["stages"]["package"]["cached"] == 1
    assert engine.verify_artifacts(path)["counts"]["cpt"] == 6


def test_invalid_verdict_never_exports_and_can_be_retried(tmp_path):
    output, rid, path, _ = make_run(tmp_path, count=2, package_review={"enabled": True, "mode": "all"}, batch_size=1)
    reviewer = Reviewer(invalid_at=1)
    state = engine.Workflow(output, rid, tmp_path, jev=reviewer).execute()
    assert state["status"] == "failed" and state["error"] == "invalid_judge_boolean"
    assert not (path / "artifacts/cpt.jsonl").exists()
    reviewer.invalid_at = None
    assert engine.resume(output, rid, tmp_path, jev=reviewer)["status"] == "completed"
    assert len(reviewer.package_calls) == 3


def test_sample_selection_and_finished_verdicts_survive_cancellation(tmp_path):
    output, rid, path, _ = make_run(tmp_path, count=20, batch_size=1, package_review={
        "enabled": True, "mode": "sample", "sample_percent": 50, "max_samples_per_target": 4})
    reviewer = Reviewer(hook=lambda: engine.cancel(output, rid))
    assert engine.Workflow(output, rid, tmp_path, jev=reviewer).execute()["status"] == "cancelled"
    first = reviewer.package_calls[0]["data"]["answer"]
    reviewer.hook = None
    state = engine.resume(output, rid, tmp_path, jev=reviewer)
    assert state["status"] == "completed" and len(reviewer.package_calls) == 4
    assert sum(call["data"]["answer"] == first for call in reviewer.package_calls) == 1
    selected = [row["text"] for row in engine.read_json(path / "artifacts/cpt.records.json")
                if row["package_review"]["status"] == "accepted"]
    assert selected == [call["data"]["answer"]["text"] for call in reviewer.package_calls]
    assert report(path)["package_review"]["targets"]["cpt"]["unreviewed"] == 16


def test_review_uses_exact_export_payload_node_prompt_and_output_limit(tmp_path):
    custom = 'PACKAGE_LOCAL_RULE: keep {literal} and {"json":"braces"} unchanged.'
    output, rid, path, _ = make_run(tmp_path, count=1, targets=["sft"], sft_output_style="drop",
        package_review={"enabled": True, "mode": "all"},
        node_prompts={"package": {"workflow.package_review": custom}},
        node_models={"package": {"jev": {"backend": "service", "model": "review",
                    "context_window_tokens": 131072, "max_output_tokens": 32768}}})
    reviewer = Reviewer()
    run = engine.Workflow(output, rid, tmp_path, jev=reviewer)
    (path / "input_records.json").write_text("[]", encoding="utf-8")
    row = {"id": "sft", "source_id": "source", "status": "eligible", "messages": [
        {"role": "user", "content": "When can I inspect the cable?"},
        {"role": "assistant", "content": "After disconnecting power.", "reasoning_content": "SOURCE_REASONING"}],
        "source_context": {"text": "Disconnect power before inspection."}}
    run.package({"sft": [row]})
    call = reviewer.package_calls[0]
    training = json.loads((path / "artifacts/sft.jsonl").read_text(encoding="utf-8"))
    assert call["data"]["answer"] == training
    assert "reasoning_content" not in call["data"]["answer"]["messages"][-1]
    assert row["messages"][-1]["reasoning_content"] == "SOURCE_REASONING"
    assert call["messages"][0]["content"].endswith(custom)
    assert call["kwargs"]["max_tokens"] == 32768
    assert call["kwargs"]["allow_reasoning_fallback"] is False


def test_non_cpt_candidate_population_and_sampling_stay_on_disk(tmp_path):
    output, rid, path, _ = make_run(tmp_path, targets=["sft"], package_review={
        "enabled": True, "mode": "sample", "sample_percent": 10, "max_samples_per_target": 10})
    run = engine.Workflow(output, rid, tmp_path)
    class Population:
        def __iter__(self):
            for index in range(20_000):
                yield {"id": str(index), "source_id": "source", "status": "eligible", "messages": [
                    {"role": "user", "content": f"Scenario {index}"}, {"role": "assistant", "content": "Disconnect power."}]}
    tracemalloc.start()
    try:
        candidates, plans, selections = run._prepare_package_candidates(
            {"sft": Population()}, {}, None, None, None, None, "drop", run.recipe["package_review"])
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    assert len(candidates["sft"]) == plans["sft"]["candidates"] == 20_000
    assert len(selections["sft"]) == plans["sft"]["planned"] == 10
    assert peak < 6 * 1024 * 1024
    assert not list((path / "stage-results").glob("package-*.sqlite3"))


def test_version_nine_prompt_snapshot_runs_without_new_ai_review(tmp_path):
    output, rid, path, _ = make_run(tmp_path, count=1)
    recipe = engine.read_json(path / "recipe.json")
    recipe["version"] = 9
    recipe.pop("package_review")
    recipe["node_prompt_templates"] = {
        stage: {prompt_id: recipe["node_prompt_templates"][stage][prompt_id] for prompt_id in prompt_ids}
        for stage, prompt_ids in LEGACY_NODE_PROMPT_IDS.items()
    }
    atomic_json(path / "recipe.json", recipe)
    state = engine.read_json(path / "state.json")
    state["recipe_hash"] = engine.digest(recipe)
    atomic_json(path / "state.json", state)
    reviewer = Reviewer()
    assert engine.Workflow(output, rid, tmp_path, jev=reviewer).execute()["status"] == "completed"
    assert reviewer.calls == []
