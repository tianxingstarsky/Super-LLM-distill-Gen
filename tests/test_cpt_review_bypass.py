"""CPT keeps its cleaning verdict without repeating downstream sample review."""
from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path

import pytest

from lib.infrastructure import training_workflow as engine
from lib.io_utils import atomic_json
from tests.test_cpt_model_processing import Cleaner, Reviewer, ORIGINAL, CLEAN, MODEL_CONFIG
from tests.test_review_repair_engine import create_child
from tests.test_workflow_production import model_request_data


SOURCE = "Before equipment inspection, disconnect the power supply. Check the cable connections and record the maintenance results."


@pytest.fixture(scope="session", autouse=True)
def mock_llm_server():
    """Every model below is injected; this suite neither uses TCP nor paid APIs."""
    yield


class NoCalls:
    model = "cpt-bypass-unused"
    usage = {}

    def chat(self, *_args, **_kwargs):
        raise AssertionError("CPT source text must not enter post-generation review.")


class Approve:
    model = "cpt-bypass-reviewer"

    def __init__(self):
        self.calls = []
        self.usage = {"calls": 0}

    def chat(self, messages, **_kwargs):
        data = model_request_data(messages)
        self.calls.append(data)
        self.usage["calls"] += 1
        assessment = {"keep": True, "grounded": True, "reasoning_valid": True, "correctness": 5,
            "scores": {name: 5 for name in ("correctness", "reasoning", "grounding", "instruction", "safety")},
            "reason": "The answer preserves the source conditions."}
        if "supervised_assistant_turns" in data:
            assessment["turn_verdicts"] = [{**deepcopy(assessment),
                "message_index": turn["message_index"], "content_sha256": turn["content_sha256"],
                "leakage_free": True, "critical_error": False} for turn in data["supervised_assistant_turns"]]
        return json.dumps(assessment)


def make_run(tmp_path, *, targets=("cpt",), text=SOURCE, batch=False, **options):
    source = tmp_path / "manual.txt"
    source.write_text(text, encoding="utf-8")
    output = tmp_path / "output"
    production = ({"version": 2, "goals": {target: 1 for target in targets},
                   "max_attempts": 1, "max_rounds": 1, "round_size": 1} if batch else None)
    run_id = engine.create_run(output, sources=[source], targets=list(targets),
                               production=production, **options)
    path = engine.run_path(output, run_id)
    assert engine.read_json(path / "recipe.json")["cpt_review_policy_version"] == 1
    return output, run_id, path


def records(path, target="cpt"):
    return engine.read_json(path / f"artifacts/{target}.records.json")


def generated_sft(self, unit):
    return [{"id": unit["id"], "source_id": unit["source_id"], "status": "eligible",
        "messages": [{"role": "user", "content": "What should I do before inspecting the equipment?"},
                     {"role": "assistant", "content": "Disconnect the power supply before inspection."}],
        "quotes": [SOURCE], "source_context": deepcopy(unit),
        "evidence_level": "source_and_model_assessed"}]


@pytest.mark.parametrize("mode", ["auto", "human"])
@pytest.mark.parametrize("batch", [False, True])
def test_native_cpt_does_not_wait_for_or_call_sample_review(tmp_path, mode, batch):
    output, run_id, path = make_run(tmp_path, batch=batch, review_repair={"mode": mode})
    state = engine.Workflow(output, run_id, tmp_path, generator=NoCalls(), jev=NoCalls()).execute()
    assert state["status"] == "completed", state.get("error")
    row, = records(path)
    assert row["status"] == "eligible" and row["text"] == SOURCE
    assert "review_repair" not in row and "package_review" not in row
    assert row["retention_reason"] == "source_text_passed_deterministic_checks"
    assert row["quality"]["keep"]
    assert state["stages"].get("review", {}).get("status", "skipped") == "skipped"
    assert state["quality"].get("human_review") != "pending"
    assert engine.verify_artifacts(path)["counts"] == {"cpt": 1}


@pytest.mark.parametrize("batch", [False, True])
@pytest.mark.parametrize("keep", [False, True])
def test_cleaning_preservation_runs_once_and_rejection_still_blocks_export(tmp_path, batch, keep):
    output, run_id, path = make_run(tmp_path, text=ORIGINAL, batch=batch,
        cpt_processing=MODEL_CONFIG, review_repair={"mode": "auto", "max_rounds": 2})
    cleaner = Cleaner()
    reviewer = Reviewer(fail_dimension=None if keep else "numbers_and_units_preserved")
    state = engine.Workflow(output, run_id, tmp_path, generator=cleaner, jev=reviewer).execute()
    assert state["status"] == ("completed" if keep else "needs_attention"), state.get("error")
    assert len(cleaner.calls) == len(reviewer.calls) == 1
    row, = records(path)
    assert row["status"] == ("eligible" if keep else "quarantined")
    assert "review_repair" not in row and "package_review" not in row
    assert row["cpt_processing"]["source_text_sha256"] == engine.digest(ORIGINAL)
    assert row["cpt_processing"]["cleaned_text_sha256"] == engine.digest(CLEAN)
    assert row["judge"]["preservation"]["numbers_and_units_preserved"] is keep
    assert engine.verify_artifacts(path)["counts"] == {"cpt": int(keep)}
    if not keep:
        assert row["reason"] == "cpt_source_review_rejected"


@pytest.mark.parametrize("mode", ["auto", "human"])
@pytest.mark.parametrize("batch", [False, True])
def test_mixed_workflow_preserves_cpt_rows_and_reviews_only_sft(tmp_path, monkeypatch, mode, batch):
    output, run_id, path = make_run(tmp_path, targets=("cpt", "sft"), batch=batch, review_repair={"mode": mode})
    monkeypatch.setattr(engine.Workflow, "sft", generated_sft)
    reviewer = Approve() if mode == "auto" else NoCalls()
    state = engine.Workflow(output, run_id, tmp_path, generator=reviewer, jev=NoCalls()).execute()
    assert state["status"] == ("completed" if mode == "auto" else "needs_attention"), state.get("error")
    cpt, = records(path)
    sft, = records(path, "sft")
    assert cpt["status"] == "eligible" and cpt["text"] == SOURCE
    assert "review_repair" not in cpt and "package_review" not in cpt
    assert cpt["source_cleaning"]["status"] == "accepted"
    assert sft["review_repair"]["status"] == ("accepted" if mode == "auto" else "waiting_manual_review")
    assert state["stages"]["review"]["total"] == 1
    assert "cpt" not in state["quality"]["review_repair"]["targets"]
    assert engine.verify_artifacts(path)["counts"] == {"cpt": 1, "sft": int(mode == "auto")}
    if mode == "auto":
        assert len(reviewer.calls) == 1


@pytest.mark.parametrize("mode", ["all", "sample"])
@pytest.mark.parametrize("batch", [False, True])
def test_legacy_package_review_style_skips_new_cpt_but_still_scores_sft(tmp_path, monkeypatch, mode, batch):
    output, run_id, path = make_run(tmp_path, targets=("cpt", "sft"), batch=batch,
        package_review={"enabled": True, "node": "jev", "mode": mode, "sample_percent": 100})
    monkeypatch.setattr(engine.Workflow, "sft", generated_sft)
    reviewer = Approve()
    state = engine.Workflow(output, run_id, tmp_path, generator=NoCalls(), jev=reviewer).execute()
    assert state["status"] == "completed", state.get("error")
    cpt, = records(path)
    sft, = records(path, "sft")
    assert "package_review" not in cpt and cpt["status"] == "eligible"
    assert sft["package_review"]["status"] == "accepted"
    assert len(reviewer.calls) == 1
    assert "cpt" not in state["quality"]["package_review"]["targets"]


def test_pure_cpt_does_not_resolve_inactive_review_services(tmp_path, monkeypatch):
    def unavailable(*_args, **_kwargs):
        raise AssertionError("An inactive review service must not be resolved.")
    monkeypatch.setattr(engine, "snapshot_backend_endpoint", unavailable)
    binding = {"backend": "deleted-service", "model": "removed-model"}
    output, run_id, path = make_run(tmp_path, settings_root=Path(engine.__file__).resolve().parents[2],
        review_repair={"mode": "auto"}, package_review={"enabled": True, "node": "jev"},
        node_models={"review": {"generation": binding, "jev": binding},
                     "jev": {"jev": binding}, "package": {"jev": binding}},
        node_prompts={"review": {"workflow.review_score": "Inactive scoring prompt."}})
    recipe = engine.read_json(path / "recipe.json")
    assert not recipe.get("review_repair") and not recipe["package_review"]["enabled"]
    assert not recipe["node_models"] and "review" not in recipe["node_prompts"]
    state = engine.Workflow(output, run_id, tmp_path, generator=NoCalls(), jev=NoCalls()).execute()
    assert state["status"] == "completed", state.get("error")


@pytest.mark.parametrize("mode", ["auto", "human"])
def test_unversioned_recipe_retains_saved_cpt_review_contract(tmp_path, mode):
    # Keep both targets so new pure-CPT config filtering does not erase the
    # historical saved review configuration before we construct the old recipe.
    output, run_id, path = make_run(tmp_path, targets=("cpt", "sft"), review_repair={"mode": mode})
    recipe = engine.read_json(path / "recipe.json")
    recipe.pop("cpt_review_policy_version")
    recipe["targets"] = ["cpt"]
    atomic_json(path / "recipe.json", recipe)
    state = engine.read_json(path / "state.json")
    state["recipe_hash"] = engine.digest(recipe)
    atomic_json(path / "state.json", state)
    reviewer = Approve() if mode == "auto" else NoCalls()
    state = engine.Workflow(output, run_id, tmp_path, generator=reviewer, jev=NoCalls()).execute()
    assert state["status"] == ("completed" if mode == "auto" else "needs_attention"), state.get("error")
    row, = records(path)
    assert row["review_repair"]["status"] == ("accepted" if mode == "auto" else "waiting_manual_review")
    if mode == "auto":
        assert len(reviewer.calls) == 1


@pytest.mark.parametrize("model_cleaned", [False, True])
def test_explicit_cpt_repair_keeps_manual_score_and_correction(tmp_path, model_cleaned):
    output, parent_id, path = make_run(tmp_path, text=ORIGINAL if model_cleaned else SOURCE,
        **({"cpt_processing": MODEL_CONFIG} if model_cleaned else {}))
    state = engine.Workflow(output, parent_id, tmp_path,
        generator=Cleaner() if model_cleaned else NoCalls(),
        jev=Reviewer() if model_cleaned else NoCalls()).execute()
    assert state["status"] == "completed", state.get("error")
    parent, = records(path)
    corrected = (CLEAN if model_cleaned else
        "Disconnect the power supply before equipment inspection. Check cable connections, then record the maintenance results.")
    assert parent["source_context"]["text"] == (ORIGINAL if model_cleaned else SOURCE)
    child_id = create_child(output, parent_id, parent, target="cpt", mode="human", score=.95,
        approved=True, corrected_record={"text": corrected})
    child_path = engine.run_path(output, child_id)
    assert "cpt_review_policy_version" not in engine.read_json(child_path / "recipe.json")
    workflow = engine.Workflow(output, child_id, tmp_path, generator=NoCalls())
    assert not workflow.skips_post_generation_review("cpt")
    state = workflow.execute()
    assert state["status"] == "completed", state.get("error")
    child, = records(child_path)
    assert child["text"] == corrected and child["review_repair"]["status"] == "accepted"
    assert child["review_repair"]["score"] == .95
    assert child["revision_context"]["parent_run_id"] == parent_id
    assert records(path) == [parent]
