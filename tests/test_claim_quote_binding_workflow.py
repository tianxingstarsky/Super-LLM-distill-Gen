"""Presentation-only quotes keep upstream rejection and real checkpoint proof."""
from copy import deepcopy
import json
from pathlib import Path

import pytest

from lib.domain.reasoning_candidates import assessment_from_verdict
from lib.domain.workflow_quality import accepted, canonical
from lib.infrastructure import training_workflow as engine
from tests.test_blind_claim_workflow import OfflineBlindClient, grade_response, worker, freeze_pre_anchor
from tests.test_claim_review_workflow import SOURCE, sample
from tests.test_workflow_production import model_request_data


@pytest.fixture(scope="session", autouse=True)
def mock_llm_server():
    yield


class RenderedQuoteClient(OfflineBlindClient):
    def __init__(self, *, refuted=False):
        super().__init__()
        self.refuted = refuted

    def chat(self, wire, **kwargs):
        value = super().chat(wire, **kwargs)
        data = model_request_data(wire)
        if "target_fields" not in data:
            return value
        raw = grade_response(data)
        for claim in raw["claim_checks"]:
            claim["quote"] = claim["quote"].replace("**", "")
            if self.refuted and "WRONG_DRAFT" in canonical(data["target_fields"]):
                claim.update(status="refuted", severity="material")
        for check in raw["reference_checks"]:
            check["quote"] = check["quote"].replace("**", "")
        return json.dumps(raw)


def row_with_emphasis():
    row = sample()
    row["messages"][1]["content"] = "**Confirm the power is off** before inspection."
    row["messages"][1]["reasoning_content"] = "**Power-off** is the prerequisite for inspection."
    return row


def test_new_factory_real_request_keeps_raw_quote_and_continuous_span_receipt_in_audit(tmp_path):
    client = RenderedQuoteClient()
    flow, row = worker(tmp_path, client), row_with_emphasis()
    assert flow.recipe["claim_review_quote_matching_version"] == 1
    evidence = {"teacher_evidence": SOURCE, "source_id": "paper", "source_kind": "document"}
    check = flow._review_claim_score("sft", row, evidence, ["sample"])
    assert accepted(check) and len(client.calls) == 2
    audit = row["claim_review"]
    assert audit["schema_failures"] == []
    receipt = audit["assessments"][0]["review"]
    assert receipt["quote_matching_version"] == 1
    assert len(receipt["quote_bindings"]) == 3
    for item in receipt["quote_bindings"]:
        original = receipt["request"]["target_fields"][item["field"]]
        assert item["match"] == "markdown_emphasis"
        assert original[item["source_start"]:item["source_end"]] == item["source_text"]
        assert "**" in item["source_text"] and "**" not in item["quote"]
    checkpoint = audit["assessments"][0]["checkpoint"]
    stored = engine.read_json(flow.path / "checkpoints/review" / (checkpoint["checkpoint_sha256"] + ".json"))
    assert stored["data"] == receipt["raw_response"]
    assert stored["sha256"] == checkpoint["response_sha256"] == engine.digest(receipt["raw_response"])
    assessment = assessment_from_verdict(flow._review_payload("sft", row), check,
        evidence_ref="actual-model-checkpoint", method="llm_claim_review", claim_review=audit)
    assert assessment["accepted"] is True
    row["messages"][1]["content"] = "Changed current draft."
    with pytest.raises(ValueError):
        assessment_from_verdict(flow._review_payload("sft", row), check,
            evidence_ref="actual-model-checkpoint", method="llm_claim_review", claim_review=audit)


def test_frozen_recipe_without_flag_retains_both_raw_failures_and_never_rewrites_quote(tmp_path):
    client = RenderedQuoteClient()
    flow, row = worker(tmp_path, client), row_with_emphasis()
    flow.recipe.pop("claim_review_quote_matching_version")
    evidence = {"teacher_evidence": SOURCE, "source_id": "paper", "source_kind": "document"}
    with pytest.raises(ValueError, match="fabricated_claim_review_quote"):
        flow._review_claim_score("sft", row, evidence, ["sample"])
    assert len(client.calls) == 3  # one blind reference and the existing bounded schema retry
    assert len(row["claim_review"]["schema_failures"]) == 2
    for failure in row["claim_review"]["schema_failures"]:
        ref = failure["checkpoint"]
        stored = engine.read_json(flow.path / "checkpoints/review" / (ref["checkpoint_sha256"] + ".json"))
        assert stored["data"]["claim_checks"][0]["quote"] == "Confirm the power is off before inspection."
        assert stored["data"]["verdict"]["keep"] is True


@pytest.mark.parametrize("value", [True, False, 0, 2, "1", []])
def test_invalid_factory_quote_flag_fails_before_model_request(tmp_path, value):
    client = RenderedQuoteClient()
    flow, row = worker(tmp_path, client), row_with_emphasis()
    flow.recipe["claim_review_quote_matching_version"] = value
    with pytest.raises(ValueError, match="invalid_claim_review_quote_matching_version"):
        flow._review_claim_score("sft", row, {"teacher_evidence": SOURCE}, ["sample"])
    assert not client.calls


def test_actual_refuted_emphasis_quote_routes_to_repair_not_schema_quarantine_and_retains_reasoning(tmp_path, monkeypatch):
    source = tmp_path / "source.txt"
    source.write_text(SOURCE, encoding="utf-8")
    output = tmp_path / "output"
    run_id = engine.create_run(output, sources=[source], targets=["sft"], reasoning_required=True,
        review_repair={"mode": "auto", "max_rounds": 1}, sft_output_style="separated")
    freeze_pre_anchor(output, run_id)
    def generate(self, unit):
        row = row_with_emphasis()
        row["messages"][1]["content"] = "**WRONG_DRAFT: Inspect** with power on."
        return [{**row, "id": unit["id"], "source_id": unit["source_id"],
            "source_context": deepcopy(unit), "quotes": [SOURCE]}]
    monkeypatch.setattr(engine.Workflow, "sft", generate)
    client = RenderedQuoteClient(refuted=True)
    flow = engine.Workflow(output, run_id, Path(__file__).resolve().parents[1], generator=client)
    state = flow.execute()
    assert state["status"] == "completed", state.get("error")
    assert len(client.calls) == 4
    record, = engine.read_json(flow.path / "artifacts/sft.records.json")
    assert record["review_repair"]["attempts"] == 1
    assert record["review_repair"]["history"][0]["status"] == "rejected"
    assert record["review_repair"]["history"][0]["claim_release_decision"]["refuted_claims"][0]["quote"] == "WRONG_DRAFT: Inspect with power on."
    assert record["claim_review"]["schema_failures"] == []
    exported = json.loads((flow.path / "artifacts/sft.jsonl").read_text(encoding="utf-8"))
    assert exported["messages"][1]["reasoning_content"] == "Inspection requires power-off."
    assert "WRONG_DRAFT" not in canonical(exported)
    resumed_client = RenderedQuoteClient()
    assert engine.Workflow(output, run_id, flow.root, generator=resumed_client).execute(resume_run=True)["status"] == "completed"
    assert not resumed_client.calls
