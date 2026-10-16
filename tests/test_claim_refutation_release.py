"""A reviewer-reported false claim cannot enter new training runs unchanged."""
from copy import deepcopy
import json
from pathlib import Path

import pytest

from lib.domain.workflow_node_prompts import snapshot_node_prompts, validate_node_prompt_snapshot
from lib.domain.workflow_quality import accepted, canonical
from lib.infrastructure import training_workflow as engine
from lib.prompts import get, render
from tests.test_blind_claim_workflow import BlindProbe, OfflineBlindClient, grade_response
from tests.test_claim_review_workflow import SOURCE, sample
from tests.test_workflow_production import model_request_data


@pytest.fixture(scope="session", autouse=True)
def mock_llm_server():
    yield


def minor_refutation(value):
    value = deepcopy(value)
    value["claim_checks"][0].update(status="refuted", severity="minor")
    value["verdict"].update(correctness=4, scores={name: 4 for name in value["verdict"]["scores"]})
    return value


class MinorProbe(BlindProbe):
    def ask(self, *args, **kwargs):
        value = super().ask(*args, **kwargs)
        return value if value.get("phase") == "blind_reference" else minor_refutation(value)


@pytest.mark.parametrize("enabled", [False, True])
def test_minor_refutation_gate_preserves_original_scores_and_derived_verdict(enabled):
    flow, row = MinorProbe(), sample()
    if enabled:
        flow.recipe["claim_refutation_release_version"] = 1
    check = flow._review_claim_score("sft", row, flow._review_evidence(row), ["sample"])
    audit = row["claim_review"]
    assert accepted(audit["derived_verdict"])
    assert audit["derived_verdict"]["keep"] is True
    assert audit["assessments"][0]["review"]["raw_response"]["verdict"]["keep"] is True
    assert check["scores"] == audit["derived_verdict"]["scores"]
    assert check["correctness"] == 4
    assert accepted(check) is not enabled
    if enabled:
        decision = row["claim_release_decision"]
        assert decision["version"] == 1 and decision["blocked"] is True
        assert decision["source_claim_review_sha256"] == engine.digest(audit)
        assert decision["refuted_claims"][0]["field"] == "content"
        assert decision["refuted_claims"][0]["quote"] == row["messages"][1]["content"]
        assert decision["refuted_claims"][0]["severity"] == "minor"
    else:
        assert "claim_release_decision" not in row


def test_minor_insufficient_is_not_reclassified_as_a_reported_refutation():
    flow, row = MinorProbe(), sample()
    flow.recipe["claim_refutation_release_version"] = 1
    original = flow.ask
    def insufficient(*args, **kwargs):
        value = original(*args, **kwargs)
        if "claim_checks" in value:
            value["claim_checks"][0]["status"] = "insufficient"
        return value
    flow.ask = insufficient
    assert accepted(flow._review_claim_score("sft", row, flow._review_evidence(row), ["sample"]))
    assert row["claim_release_decision"]["blocked"] is False
    assert row["claim_release_decision"]["refuted_claims"] == []


def test_refutation_veto_is_accepted_by_existing_candidate_audit_without_washing_upstream_grade():
    flow, row = MinorProbe(audit=True), sample()
    flow.recipe["claim_refutation_release_version"] = 1
    result, = flow._review_repair_item({"target": "sft", "record": row})
    assert result["status"] == "quarantined"
    assessment, = result["reasoning_distillation"]["assessments"]
    assert assessment["accepted"] is False and assessment["derived_verdict"]["keep"] is False
    assert assessment["claim_review"]["derived_verdict"]["keep"] is True
    assert assessment["claim_review"]["assessments"][0]["review"]["raw_response"]["verdict"]["keep"] is True
    assert assessment["derived_verdict"]["scores"] == assessment["claim_review"]["derived_verdict"]["scores"]


@pytest.mark.parametrize("value", [True, False, 0, 2, "1", []])
def test_bad_gate_version_is_rejected_before_a_model_call(value):
    flow, row = MinorProbe(), sample()
    flow.recipe["claim_refutation_release_version"] = value
    with pytest.raises(ValueError, match="invalid_claim_refutation_release_version"):
        flow._review_claim_score("sft", row, flow._review_evidence(row), ["sample"])
    assert not flow.calls
    with pytest.raises(ValueError, match="invalid_claim_refutation_release_version"):
        snapshot_node_prompts(None, recipe_version=22, claim_review_protocol_version=2,
                              claim_refutation_release_version=value)


def test_catalog_pins_new_gate_and_retains_all_historical_versions():
    for protocol, feedback, release, score, reference in [
        (None, None, None, "1.11.0", None), (1, None, None, "1.12.0", None),
        (2, None, None, "1.13.0", "1.0.0"), (2, 1, None, "1.14.0", "1.0.1"),
        (2, 1, 1, "1.15.0", "1.0.1")]:
        settings = {"recipe_version": 22, "claim_review_protocol_version": protocol,
            "claim_review_format_feedback_version": feedback, "claim_refutation_release_version": release}
        templates = snapshot_node_prompts(None, **settings)
        assert templates["review"]["workflow.review_score"] == render(get("workflow.review_score", version=score))
        if reference:
            assert templates["review"]["workflow.review_reference"] == render(get("workflow.review_reference", version=reference))
        assert validate_node_prompt_snapshot(templates, "system", **settings) == templates


class MinorRefutationClient(OfflineBlindClient):
    def chat(self, wire, **kwargs):
        value = super().chat(wire, **kwargs)
        data = model_request_data(wire)
        if "target_fields" in data and "WRONG_DRAFT" in canonical(data["target_fields"]):
            return json.dumps(minor_refutation(grade_response(data)))
        return value


def test_real_new_factory_minor_refutation_repairs_regrades_and_exports_full_reasoning(tmp_path, monkeypatch):
    source = tmp_path / "source.txt"
    source.write_text(SOURCE, encoding="utf-8")
    output = tmp_path / "output"
    run_id = engine.create_run(output, sources=[source], targets=["sft"], reasoning_required=True,
        review_repair={"mode": "auto", "max_rounds": 1}, sft_output_style="separated")
    def generate(self, unit):
        row = sample()
        row["messages"][1]["content"] = "WRONG_DRAFT: Inspect with power on."
        return [{**row, "id": unit["id"], "source_id": unit["source_id"],
                 "source_context": deepcopy(unit), "quotes": [SOURCE]}]
    monkeypatch.setattr(engine.Workflow, "sft", generate)
    client = MinorRefutationClient()
    flow = engine.Workflow(output, run_id, Path(__file__).resolve().parents[1], generator=client)
    assert flow.recipe["claim_refutation_release_version"] == 1
    assert flow.recipe["prompts"]["workflow.review_score"]["version"] == "1.16.0"
    assert flow.execute()["status"] == "completed"
    assert len(client.calls) == 4
    assert sum(call["data"].get("phase") == "blind_reference" for call in client.calls) == 1
    record, = engine.read_json(flow.path / "artifacts/sft.records.json")
    first, final = record["review_repair"]["history"]
    assert first["status"] == "rejected" and first["verdict"]["scores"]["correctness"] == 4
    assert first["claim_release_decision"]["refuted_claims"][0]["severity"] == "minor"
    assert final["status"] == "accepted" and final["claim_release_decision"]["blocked"] is False
    assert record["claim_release_decision"]["source_claim_review_sha256"] == engine.digest(record["claim_review"])
    failed = [engine.read_json(path)["data"] for path in (flow.path / "checkpoints/review").glob("*.json")]
    raw = next(value for value in failed if isinstance(value, dict) and "claim_checks" in value
               and value["claim_checks"][0]["status"] == "refuted")
    assert raw["verdict"]["keep"] is True and raw["verdict"]["correctness"] == 4
    assert raw["claim_checks"][0]["severity"] == "minor"
    exported = json.loads((flow.path / "artifacts/sft.jsonl").read_text(encoding="utf-8"))
    assert exported["messages"][1]["reasoning_content"] == "Inspection requires power-off."
    assert "WRONG_DRAFT" not in canonical(exported)
    resumed_client = MinorRefutationClient()
    resumed = engine.Workflow(output, run_id, flow.root, generator=resumed_client)
    assert resumed.execute(resume_run=True)["status"] == "completed" and not resumed_client.calls
    assert engine.read_json(flow.path / "recipe.json")["claim_refutation_release_version"] == 1
