"""Real review-only workspaces preserve editable review prompts across rework."""
from copy import deepcopy
from pathlib import Path
import shutil

import pytest

from lib.bootstrap.workflows import human_augmentation_application
from lib.domain.workflow_node_prompts import snapshot_node_prompts
from lib.infrastructure import training_workflow as engine
from lib.prompts import get, render
from tests.test_blind_claim_workflow import OfflineBlindClient, freeze_pre_adversarial
from tests.test_claim_review_workflow import SOURCE, grade, sample


@pytest.fixture(scope="session", autouse=True)
def mock_llm_server():
    yield


def parent_run(tmp_path, monkeypatch, *, custom=False, legacy=False, previous_anchor=False):
    shutil.copytree(Path(__file__).resolve().parents[1] / "configs", tmp_path / "configs", dirs_exist_ok=True)
    source = tmp_path / "source.txt"
    source.write_text(SOURCE, encoding="utf-8")
    output = tmp_path / "output"
    overrides = {"review": {"workflow.review_reference": "PARENT_REFERENCE_CUSTOM\n" + render(get("workflow.review_reference", "1.0.1")),
        "workflow.review_score": "PARENT_SCORE_CUSTOM\n" + render(get("workflow.review_score", "1.15.0"))}} if custom else None
    run_id = engine.create_run(output, sources=[source], targets=["sft"], settings_root=tmp_path,
        reasoning_required=True, sft_output_style="separated", node_prompts=overrides,
        node_models={"review": {"generation": {"backend": "ollama", "model": "offline-review"}}})
    path = engine.run_path(output, run_id)
    if previous_anchor:
        freeze_pre_adversarial(output, run_id)
    if legacy:
        recipe = engine.read_json(path / "recipe.json")
        for marker in ("claim_review_protocol_version", "claim_review_format_feedback_version", "claim_refutation_release_version",
                       "claim_review_quote_matching_version", "claim_reference_policy_version", "claim_review_experience_scope_version",
                       "claim_review_anchor_protocol_version", "claim_review_adversarial_protocol_version"):
            recipe.pop(marker)
        recipe["node_prompt_templates"] = snapshot_node_prompts(None, recipe_version=recipe["version"],
            learner_context_review_version=1)
        recipe["prompts"].pop("workflow.review_reference", None)
        spec = get("workflow.review_score", "1.11.0")
        recipe["prompts"]["workflow.review_score"] = {"version": spec.version, "sha256": engine.digest(spec.template)}
        engine.atomic_json(path / "recipe.json", recipe)
        state = engine.read_json(path / "state.json")
        state["recipe_hash"] = engine.digest(recipe)
        engine.atomic_json(path / "state.json", state)
    def generate(self, unit):
        return [{**sample(), "id": unit["id"], "source_id": unit["source_id"],
            "source_context": deepcopy(unit), "quotes": [SOURCE], "judge": grade()}]
    monkeypatch.setattr(engine.Workflow, "sft", generate)
    client = OfflineBlindClient()
    result = engine.Workflow(output, run_id, tmp_path, generator=client).execute()
    assert result["status"] == "completed", result.get("error")
    assert not client.calls
    return output, run_id, overrides


@pytest.mark.parametrize("custom,legacy,previous_anchor,explicit", [(True, False, False, False),
    (False, False, False, False), (False, True, False, False), (False, False, True, False),
    (False, True, False, True)])
def test_application_review_only_issue_rework_keeps_parent_text_and_adds_current_missing_defaults(tmp_path, monkeypatch, custom, legacy, previous_anchor, explicit):
    output, parent_id, overrides = parent_run(tmp_path, monkeypatch, custom=custom, legacy=legacy, previous_anchor=previous_anchor)
    parent_path = engine.run_path(output, parent_id)
    parent_recipe_bytes = (parent_path / "recipe.json").read_bytes()
    parent_records_bytes = (parent_path / "artifacts/sft.records.json").read_bytes()
    parent_recipe = engine.read_json(parent_path / "recipe.json")
    app = human_augmentation_application(tmp_path, output)
    current_custom = {"review": {"workflow.review_reference": "USER_CURRENT_CHALLENGER_OVERRIDE_991",
        "workflow.review_score": "USER_CURRENT_ARBITER_OVERRIDE_991"}}
    session_id = app.create_session(from_run_id=parent_id, review_only=True,
        **({"node_prompts": current_custom} if explicit else {}))
    session = app.session(session_id)
    templates = session["blueprint"]["node_prompts"]["review"]
    if explicit:
        assert all(templates[prompt_id] == text for prompt_id, text in current_custom["review"].items())
    elif custom:
        for prompt_id, text in overrides["review"].items():
            assert templates[prompt_id] == text
    elif legacy or previous_anchor:
        if legacy:
            assert "workflow.review_reference" not in parent_recipe["node_prompt_templates"]["review"]
        else:
            assert "claim_review_adversarial_protocol_version" not in parent_recipe
            assert parent_recipe["prompts"]["workflow.review_score"]["version"] == "1.17.0"
        assert templates["workflow.review_reference"] == render(get("workflow.review_reference", "2.0.0"))
        assert templates["workflow.review_score"] == render(get("workflow.review_score", "1.18.0"))
    else:
        assert templates == parent_recipe["node_prompt_templates"]["review"]
    candidate, = app.results(session_id)["rows"]
    parent_round, = session["rounds"]
    saved = app.save_issues(session_id, [{"round_id": parent_round["id"], "target": "sft",
        "candidate_id": candidate["candidate_id"]}], instruction="Preserve the inspection prerequisite.",
        expected_version=session["version"], request_id="save-review-issue")
    child = app.revise_issues(session_id, selected_issue_ids=saved["issue_ids"], request_id="rework-review-issue",
        expected_version=saved["version"])
    child_recipe = engine.read_json(engine.run_path(output, child["run_id"]) / "recipe.json")
    assert child_recipe["claim_review_protocol_version"] == 2
    assert child_recipe["claim_review_format_feedback_version"] == 2
    assert child_recipe["claim_refutation_release_version"] == 1
    assert child_recipe["claim_reference_policy_version"] == 1
    assert child_recipe["claim_review_anchor_protocol_version"] == 1
    assert child_recipe["claim_review_adversarial_protocol_version"] == 1
    assert child_recipe["prompts"]["workflow.review_score"]["version"] == "1.18.0"
    assert child_recipe["prompts"]["workflow.review_reference"]["version"] == "2.0.0"
    assert child_recipe["node_prompt_templates"]["review"] == templates
    assert child_recipe["repair_inputs"][0]["record"] == candidate["record"]
    client = OfflineBlindClient()
    state = engine.Workflow(output, child["run_id"], tmp_path, generator=client).execute()
    assert state["status"] == "completed", state.get("error")
    assert state["stages"]["sft"]["status"] == "skipped"
    assert len(client.calls) == 3
    challenge_call = next(call for call in client.calls if call["data"].get("phase") == "claim_challenge")
    assert templates["workflow.review_reference"] in challenge_call["wire"][0]["content"]
    grade_call = next(call for call in client.calls if call["data"].get("phase") == "claim_adjudication")
    assert templates["workflow.review_score"] in grade_call["wire"][0]["content"]
    result, = app.results(session_id, round_id=child["id"])["rows"]
    assert result["record"]["claim_review"]["version"] == 4
    assert result["record"]["messages"][1]["reasoning_content"] == "Inspection requires power-off."
    assert (parent_path / "recipe.json").read_bytes() == parent_recipe_bytes
    assert (parent_path / "artifacts/sft.records.json").read_bytes() == parent_records_bytes
