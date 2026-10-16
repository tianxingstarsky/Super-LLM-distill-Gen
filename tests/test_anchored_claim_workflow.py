"""New grade calls identify current program-bound units instead of copying text."""
from copy import deepcopy
import json
from pathlib import Path

import pytest

from lib.domain.reasoning_candidates import assessment_from_verdict
from lib.domain.workflow_node_prompts import snapshot_node_prompts, validate_node_prompt_snapshot
from lib.domain.workflow_quality import accepted, canonical
from lib.infrastructure import training_workflow as engine
from lib.prompts import get, render
from tests.test_blind_claim_workflow import (BlindProbe, OfflineBlindClient, anchored_grade_response,
                                           blind_response)
from tests.test_claim_review_workflow import SOURCE, sample
from tests.test_workflow_production import model_request_data


@pytest.fixture(scope="session", autouse=True)
def mock_llm_server():
    yield


def workflow(tmp_path, client):
    output = tmp_path / "output"
    run_id = engine.create_run(output, brief="Arrange safe inspection.", targets=["sft"],
        reasoning_required=True, review_repair={"mode": "auto", "max_rounds": 0})
    result = engine.Workflow(output, run_id, Path(__file__).resolve().parents[1], generator=client)
    result.stage = "review"
    return result


def assert_anchored_wire_boundaries(call):
    tables = [json.loads(line) for line in call["wire"][0]["content"].splitlines()
              if line.startswith("{") and '"response_field_names"' in line]
    assert len(tables) == 1
    table = tables[0]
    assert table["response_field_names"] == ["claim_review_protocol_version", "scope_sha256", "coverage",
        "checked_unit_ids", "claim_checks", "reference_checks", "verdict"]
    assert "scope" not in table["response_field_names"] and "checked_fields" not in table["response_field_names"]
    assert table["scope_field_names"] == []
    assert table["current_target_unit_ids"] == [unit["id"] for unit in call["data"]["target_units"]]
    assert "不返回整个scope" in call["wire"][0]["content"]
    return table


class AnchoredProbe(BlindProbe):
    def __init__(self, *, bad=False, severity="material", partial=False, audit=False):
        super().__init__(partial=partial, audit=audit)
        self.bad, self.severity = bad, severity
        self.recipe.update(claim_review_anchor_protocol_version=1, claim_reference_policy_version=1,
                           claim_refutation_release_version=1, claim_review_format_feedback_version=2)

    def ask(self, key, role, prompt_id, data, **kwargs):
        self.calls.append({"key": deepcopy(key), "role": role, "prompt_id": prompt_id,
            "data": deepcopy(data), "kwargs": deepcopy(kwargs)})
        return (blind_response(data, partial=self.partial) if data.get("phase") == "blind_reference"
                else anchored_grade_response(data, bad=self.bad, severity=self.severity))


def test_true_new_factory_real_grade_transports_only_current_unit_ids_and_raw_scope_hash(tmp_path):
    client, row = OfflineBlindClient(), sample(True)
    row["messages"][1]["reasoning_content"] = "FIRST_REASONING_ONLY_772"
    row["messages"][3]["reasoning_content"] = "SECOND_REASONING_ONLY_884"
    flow = workflow(tmp_path, client)
    assert flow.recipe["claim_review_protocol_version"] == 2  # The blind contract remains sealed v2.
    assert flow.recipe["claim_review_anchor_protocol_version"] == 1
    assert flow.recipe["prompts"]["workflow.review_score"]["version"] == "1.17.0"
    evidence = {"teacher_evidence": SOURCE, "source_id": "paper", "source_kind": "document"}
    check = flow._review_claim_score("multiturn", row, evidence, ["sample"])
    assert accepted(check) and len(client.calls) == 4
    blind = [call for call in client.calls if call["data"].get("phase") == "blind_reference"]
    grades = [call for call in client.calls if call["data"].get("claim_review_protocol_version") == 3]
    assert len(blind) == len(grades) == 2
    assert all(call["data"]["claim_review_protocol_version"] == 2 for call in blind)
    assert "FIRST_REASONING_ONLY_772" not in canonical(blind[0]["data"])
    assert "SECOND_REASONING_ONLY_884" not in canonical(grades[0]["data"])
    first_units = grades[0]["data"]["target_units"]
    second_units = grades[1]["data"]["target_units"]
    assert "FIRST_REASONING_ONLY_772" in canonical(first_units)
    assert "FIRST_REASONING_ONLY_772" not in canonical(second_units)
    assert {unit["id"] for unit in first_units}.isdisjoint({unit["id"] for unit in second_units})
    audit = row["claim_review"]
    assert audit["version"] == 3 and audit["method"] == "anchored_reference_model_claim_review"
    for wrapped in audit["assessments"]:
        receipt, checkpoint = wrapped["review"], wrapped["checkpoint"]
        raw = receipt["raw_response"]
        assert raw["claim_review_protocol_version"] == 3 and "scope" not in raw
        assert set(raw["claim_checks"][0]) == {"unit_id", "status", "severity", "basis"}
        assert receipt["normalized_response"]["claim_review_protocol_version"] == 2
        assert receipt["normalized_receipt"]["raw_response"] == receipt["normalized_response"]
        for normalized in receipt["normalized_response"]["claim_checks"]:
            assert normalized["quote"] in receipt["request"]["target_fields"][normalized["field"]]
        stored = engine.read_json(flow.path / "checkpoints/review" / (checkpoint["checkpoint_sha256"] + ".json"))
        assert stored["data"] == raw and stored["sha256"] == checkpoint["response_sha256"] == engine.digest(raw)
        assert stored["data"] != receipt["normalized_response"]
    assessment = assessment_from_verdict(flow._review_payload("multiturn", row), check,
        evidence_ref="actual-anchored-checkpoints", method="llm_claim_review", claim_review=audit)
    assert assessment["accepted"] is True
    for call in grades:
        assert_anchored_wire_boundaries(call)
        source = json.loads(call["wire"][1]["content"].removeprefix("<untrusted_source_memory>\n").removesuffix("\n</untrusted_source_memory>"))
        assert source["source_evidence"] == evidence
        assert "source_evidence" not in call["data"] and "source_evidence_ref" in call["data"]
    assert row["messages"][1]["reasoning_content"] == "FIRST_REASONING_ONLY_772"


@pytest.mark.parametrize("severity", ["material", "minor"])
def test_actual_reported_refutation_vetoes_raw_all_five_scores_after_program_mapping(severity):
    flow, row = AnchoredProbe(bad=True, severity=severity, audit=True), sample()
    result, = flow._review_repair_item({"target": "sft", "record": row})
    assert result["status"] == "quarantined"
    audit = result["claim_review"]
    assert audit["assessments"][0]["review"]["raw_response"]["verdict"]["keep"] is True
    assert all(value == 5 for value in audit["assessments"][0]["review"]["raw_response"]["verdict"]["scores"].values())
    decision = result["claim_release_decision"]
    assert decision["blocked"] is True and decision["source_claim_review_sha256"] == engine.digest(audit)
    assert decision["refuted_claims"][0]["quote"] in row["messages"][1][decision["refuted_claims"][0]["field"]]
    assert result["reasoning_distillation"]["assessments"][0]["accepted"] is False


def test_partial_blind_reference_remains_visible_without_relabeling_complete_candidate_as_incorrect():
    flow, row = AnchoredProbe(partial=True, audit=True), sample()
    result, = flow._review_repair_item({"target": "sft", "record": row})
    assert result["status"] == "eligible"
    assessment, = result["reasoning_distillation"]["assessments"]
    receipt = result["claim_review"]["assessments"][0]["review"]
    assert receipt["verdict"]["keep"] is True and receipt["normalized_receipt"]["verdict"]["keep"] is False
    assert result["claim_review"]["blind_reviews"][0]["review"]["raw_response"]["coverage"] == "partial"
    assert receipt["raw_response"]["coverage"] == "complete"
    assert assessment["accepted"] is True


@pytest.mark.parametrize("mutate", [
    lambda audit: audit["assessments"][0]["review"]["raw_response"]["claim_checks"][0].__setitem__("unit_id", "m9u999"),
    lambda audit: audit["assessments"][0]["review"]["normalized_response"]["claim_checks"][0].__setitem__("quote", "Copied prior reasoning"),
    lambda audit: audit["assessments"][0]["review"]["normalized_receipt"]["verdict"].__setitem__("keep", False),
    lambda audit: audit["assessments"][0]["checkpoint"].__setitem__("response_sha256", "0" * 64),
    lambda audit: audit["assessments"][0]["review"]["request"]["target_units"][0].__setitem__("text", "A changed unit"),
    lambda audit: audit["blind_reviews"][0]["checkpoint"].__setitem__("checkpoint_sha256", "0" * 64),
])
def test_candidate_audit_rejects_raw_normalized_unit_and_real_checkpoint_tampering(mutate):
    flow, row = AnchoredProbe(), sample()
    check = flow._review_claim_score("sft", row, flow._review_evidence(row), ["sample"])
    audit = deepcopy(row["claim_review"])
    mutate(audit)
    with pytest.raises(ValueError):
        assessment_from_verdict(flow._review_payload("sft", row), check, evidence_ref="real-checkpoint",
            method="llm_claim_review", claim_review=audit)


class StaleUnitClient(OfflineBlindClient):
    def chat(self, wire, **kwargs):
        value = super().chat(wire, **kwargs)
        data = model_request_data(wire)
        if data.get("claim_review_protocol_version") == 3 and data["scope"]["message_index"] == 1:
            self.prior_unit_id = data["target_units"][0]["id"]
        if data.get("claim_review_protocol_version") == 3 and data["scope"]["message_index"] == 3 and "format_repair" not in data:
            raw = anchored_grade_response(data)
            raw["claim_checks"][0]["unit_id"] = self.prior_unit_id
            return json.dumps(raw)
        return value


def test_stale_unit_id_gets_one_bounded_retry_without_ever_remapping_it_to_current_turn(tmp_path):
    client, row = StaleUnitClient(), sample(True)
    flow = workflow(tmp_path, client)
    assert accepted(flow._review_claim_score("multiturn", row,
        {"teacher_evidence": SOURCE, "source_id": "paper", "source_kind": "document"}, ["sample"]))
    assert len(client.calls) == 5
    retry, = [call for call in client.calls if "format_repair" in call["data"]]
    assert retry["data"]["format_repair"]["code"] == "invalid_claim_review_v3_response"
    table = assert_anchored_wire_boundaries(retry)
    diagnostic = json.loads(retry["data"]["format_repair"]["instruction"].split("Schema-only diagnostic: ", 1)[1])
    assert diagnostic["response_field_names"] == table["response_field_names"]
    assert diagnostic["current_target_unit_ids"] == table["current_target_unit_ids"]
    assert "scope" not in diagnostic["response_field_names"] and "checked_fields" not in diagnostic["response_field_names"]
    assert client.prior_unit_id not in retry["data"]["format_repair"]["instruction"]
    failure, = row["claim_review"]["schema_failures"]
    stored = engine.read_json(flow.path / "checkpoints/review" / (failure["checkpoint"]["checkpoint_sha256"] + ".json"))
    assert stored["data"]["claim_checks"][0]["unit_id"] == client.prior_unit_id
    final = row["claim_review"]["assessments"][1]
    assert final["checkpoint"]["format_repair"] == retry["data"]["format_repair"]
    assert final["checkpoint"]["request_sha256"] == engine.digest({
        **final["review"]["request"], "format_repair": retry["data"]["format_repair"]})
    assessment_from_verdict(flow._review_payload("multiturn", row), row["claim_review"]["derived_verdict"],
        evidence_ref="real-retry-checkpoints", method="llm_claim_review", claim_review=row["claim_review"])


def test_true_new_factory_execute_repairs_and_regrades_with_one_blind_reference_and_full_reasoning(tmp_path, monkeypatch):
    source = tmp_path / "source.txt"
    source.write_text(SOURCE, encoding="utf-8")
    output = tmp_path / "output"
    run_id = engine.create_run(output, sources=[source], targets=["sft"], reasoning_required=True,
        review_repair={"mode": "auto", "max_rounds": 1}, sft_output_style="separated")
    def generate(self, unit):
        row = sample()
        row["messages"][1]["content"] = "**WRONG_DRAFT: Inspect** with power on."
        return [{**row, "id": unit["id"], "source_id": unit["source_id"],
            "source_context": deepcopy(unit), "quotes": [SOURCE]}]
    monkeypatch.setattr(engine.Workflow, "sft", generate)
    client = OfflineBlindClient(repair=True)
    flow = engine.Workflow(output, run_id, Path(__file__).resolve().parents[1], generator=client)
    state = flow.execute()
    assert state["status"] == "completed", state.get("error")
    assert len(client.calls) == 4 and sum(call["data"].get("phase") == "blind_reference" for call in client.calls) == 1
    record, = engine.read_json(flow.path / "artifacts/sft.records.json")
    assert record["claim_review"]["version"] == 3 and not record["claim_review"]["schema_failures"]
    assert record["review_repair"]["history"][0]["status"] == "rejected"
    assert record["review_repair"]["attempts"] == 1
    exported = json.loads((flow.path / "artifacts/sft.jsonl").read_text(encoding="utf-8"))
    assert exported["messages"][1]["reasoning_content"] == "Inspection requires power-off."
    assert "WRONG_DRAFT" not in canonical(exported)
    resumed_client = OfflineBlindClient()
    assert engine.Workflow(output, run_id, flow.root, generator=resumed_client).execute(resume_run=True)["status"] == "completed"
    assert not resumed_client.calls


@pytest.mark.parametrize("value", [True, False, 0, 2, "1", []])
def test_anchor_version_is_strict_and_stops_before_any_model_call(tmp_path, value):
    client, row = OfflineBlindClient(), sample()
    flow = workflow(tmp_path, client)
    flow.recipe["claim_review_anchor_protocol_version"] = value
    with pytest.raises(ValueError, match="invalid_claim_review_anchor_protocol_version"):
        flow._review_claim_score("sft", row, {"teacher_evidence": SOURCE}, ["sample"])
    assert not client.calls
    with pytest.raises(ValueError, match="invalid_claim_review_anchor_protocol_version"):
        snapshot_node_prompts(None, recipe_version=22, claim_review_protocol_version=2,
            claim_review_anchor_protocol_version=value)


def test_anchor_catalog_selects_new_grade_without_mutating_protocol_two_prompt_pin():
    settings = {"recipe_version": 22, "claim_review_protocol_version": 2,
        "claim_review_format_feedback_version": 2, "claim_refutation_release_version": 1,
        "claim_reference_policy_version": 1}
    prior = snapshot_node_prompts(None, **settings)
    current = snapshot_node_prompts(None, **settings, claim_review_anchor_protocol_version=1)
    assert prior["review"]["workflow.review_score"] == render(get("workflow.review_score", "1.16.0"))
    assert current["review"]["workflow.review_score"] == render(get("workflow.review_score", "1.17.0"))
    assert prior["review"]["workflow.review_reference"] == current["review"]["workflow.review_reference"]
    assert validate_node_prompt_snapshot(current, "system", **settings, claim_review_anchor_protocol_version=1) == current
