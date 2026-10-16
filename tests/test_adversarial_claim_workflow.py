"""Replay pre-condition-inventory adversarial receipts without upgrading them.

The current factory contract is exercised in test_condition_review_workflow.
"""
from copy import deepcopy
import json
from pathlib import Path

import pytest

from lib.domain.reasoning_candidates import assessment_from_verdict
from lib.domain.workflow_node_prompts import snapshot_node_prompts, validate_node_prompt_snapshot
from lib.domain.workflow_quality import accepted, canonical
from lib.infrastructure import training_workflow as engine
from lib.prompts import get, render
from tests.test_blind_claim_workflow import (OfflineBlindClient, adversarial_grade_response, challenge_response)
from tests.test_claim_review_workflow import Probe, SOURCE, grade, sample
from tests.test_workflow_production import model_request_data


@pytest.fixture(scope="session", autouse=True)
def mock_llm_server():
    yield


def workflow(tmp_path, client):
    output = tmp_path / "output"
    run_id = engine.create_run(output, brief="Arrange safe inspection.", targets=["sft"],
        reasoning_required=True, review_repair={"mode": "auto", "max_rounds": 0})
    path = engine.run_path(output, run_id)
    recipe = engine.read_json(path / "recipe.json")
    recipe.pop("claim_review_condition_contract_version")
    for prompt_id, version in (("workflow.review_reference", "2.0.1"), ("workflow.review_score", "1.18.0")):
        spec = get(prompt_id, version)
        recipe["node_prompt_templates"]["review"][prompt_id] = render(spec)
        recipe["prompts"][prompt_id] = {"version": version, "sha256": engine.digest(spec.template)}
    engine.atomic_json(path / "recipe.json", recipe)
    state = engine.read_json(path / "state.json")
    state["recipe_hash"] = engine.digest(recipe)
    engine.atomic_json(path / "state.json", state)
    flow = engine.Workflow(output, run_id, Path(__file__).resolve().parents[1], generator=client)
    flow.stage = "review"
    return flow


def assert_wire_boundary(call):
    text = call["data"].get("node_task_instructions", call["wire"][0]["content"])
    table, = [json.loads(line) for line in text.splitlines()
        if line.startswith("{") and '"response_field_names"' in line]
    fields = (["claim_review_protocol_version", "phase", "scope_sha256", "issues", "limitations"]
        if call["data"]["phase"] == "claim_challenge" else ["claim_review_protocol_version", "scope_sha256",
            "coverage", "checked_unit_ids", "claim_checks", "challenge_checks", "verdict"])
    assert table["response_field_names"] == fields
    assert "scope" not in fields and "reference_checks" not in fields
    assert table["current_target_unit_ids"] == [unit["id"] for unit in call["data"]["target_units"]]
    if call["data"]["phase"] == "claim_adjudication":
        assert table["current_challenge_issue_ids"] == [issue["id"] for issue in call["data"]["challenge_review"]["issues"]]
    return table


def test_historical_factory_uses_two_candidate_aware_calls_and_actual_current_units_source_and_audit(tmp_path):
    client, row = OfflineBlindClient(), sample(True)
    row["messages"][1]["reasoning_content"] = "CURRENT_T1_REASONING_883"
    row["messages"][3]["reasoning_content"] = "FUTURE_T2_REASONING_998"
    row.update(judge=grade(), qa_contract={"guidance": "PRIVATE_QA_GUIDANCE_221"})
    flow = workflow(tmp_path, client)
    assert flow.recipe["claim_review_adversarial_protocol_version"] == 1
    assert flow.recipe["prompts"]["workflow.review_reference"]["version"] == "2.0.1"
    assert flow.recipe["prompts"]["workflow.review_score"]["version"] == "1.18.0"
    evidence = {"teacher_evidence": SOURCE, "source_id": "paper", "source_kind": "document"}
    check = flow._review_claim_score("multiturn", row, evidence, ["sample"])
    assert accepted(check) and len(client.calls) == 4
    challenges = [call for call in client.calls if call["data"]["phase"] == "claim_challenge"]
    arbiters = [call for call in client.calls if call["data"]["phase"] == "claim_adjudication"]
    assert len(challenges) == len(arbiters) == 2
    assert "CURRENT_T1_REASONING_883" in canonical(challenges[0]["data"])
    assert "FUTURE_T2_REASONING_998" not in canonical(challenges[0]["data"])
    assert "PRIVATE_QA_GUIDANCE_221" not in canonical(client.calls)
    assert "challenge_review" not in challenges[0]["data"] and "reference_review" not in canonical(client.calls)
    for call in client.calls:
        assert_wire_boundary(call)
        assert [message["role"] for message in call["wire"]] == ["system", "system", "user"]
        source = json.loads(call["wire"][1]["content"].removeprefix("<untrusted_source_memory>\n").removesuffix("\n</untrusted_source_memory>"))
        assert source["source_evidence"] == evidence
        assert "source_evidence" not in call["data"]
        assert sum(SOURCE in str(message["content"]) for message in call["wire"]) == 1
    audit = row["claim_review"]
    assert audit["version"] == 4 and audit["method"] == "adversarial_anchored_claim_review"
    assert "blind_reviews" not in audit and "blind_checkpoints" not in row["reasoning_assessment_reference"]
    assert "challenge_checkpoints" in row["reasoning_assessment_reference"]
    for slot in ("challenge_reviews", "assessments"):
        for item in audit[slot]:
            receipt, checkpoint = item["review"], item["checkpoint"]
            saved = engine.read_json(flow.path / "checkpoints/review" / (checkpoint["checkpoint_sha256"] + ".json"))
            assert saved["data"] == receipt["raw_response"]
            assert saved["sha256"] == checkpoint["response_sha256"] == engine.digest(receipt["raw_response"])
    for item in audit["assessments"]:
        receipt = item["review"]
        assert receipt["raw_response"]["claim_review_protocol_version"] == 4
        assert receipt["normalized_response"]["claim_review_protocol_version"] == 1
        assert receipt["normalized_receipt"]["raw_response"] == receipt["normalized_response"]
    assert assessment_from_verdict(flow._review_payload("multiturn", row), check, evidence_ref="real-v4-checkpoints",
        method="llm_claim_review", claim_review=audit)["accepted"] is True
    before = len(client.calls)
    assert accepted(flow._review_claim_score("multiturn", row, evidence, ["sample"]))
    assert len(client.calls) == before


class AdversarialProbe(Probe):
    def __init__(self, *, bad=False, severity="material", decision="confirmed", audit=False):
        super().__init__(audit=audit)
        self.bad, self.severity, self.decision = bad, severity, decision
        self.recipe.update(claim_review_protocol_version=2, claim_review_adversarial_protocol_version=1,
            claim_review_anchor_protocol_version=1, claim_refutation_release_version=1, claim_review_format_feedback_version=2)

    def ask(self, key, role, prompt_id, data, **kwargs):
        self.calls.append({"key": deepcopy(key), "role": role, "prompt_id": prompt_id,
            "data": deepcopy(data), "kwargs": deepcopy(kwargs)})
        return (challenge_response(data, bad=self.bad, severity=self.severity) if data["phase"] == "claim_challenge"
                else adversarial_grade_response(data, decision=self.decision))


def test_both_new_phases_honor_the_configured_review_model_role():
    flow, row = AdversarialProbe(), sample()
    flow.recipe["package_review"] = {"enabled": True}
    assert accepted(flow._review_claim_score("sft", row, flow._review_evidence(row), ["sample"]))
    assert [call["role"] for call in flow.calls] == ["jev", "jev"]


@pytest.mark.parametrize("severity,decision", [("material", "confirmed"), ("minor", "confirmed"), ("material", "unresolved")])
def test_challenger_findings_are_explicitly_adjudicated_and_actual_errors_veto_all_five_raw_scores(severity, decision):
    flow, row = AdversarialProbe(bad=True, severity=severity, decision=decision, audit=True), sample()
    result, = flow._review_repair_item({"target": "sft", "record": row})
    assert result["status"] == "quarantined" and len(flow.calls) == 2
    assessment, = result["reasoning_distillation"]["assessments"]
    assert assessment["accepted"] is False
    raw = result["claim_review"]["assessments"][0]["review"]["raw_response"]
    assert raw["verdict"]["keep"] is True and all(value == 5 for value in raw["verdict"]["scores"].values())
    assert raw["challenge_checks"][0]["decision"] == decision
    assert result["messages"][1]["reasoning_content"]
    if decision == "confirmed":
        assert result["claim_release_decision"]["blocked"] is True
        assert result["claim_release_decision"]["refuted_claims"][0]["quote"] in row["messages"][1]["content"]


@pytest.mark.parametrize("mutate", [
    lambda a: a["challenge_reviews"][0]["review"]["raw_response"]["limitations"].append("Changed after checkpoint"),
    lambda a: a["challenge_reviews"][0]["checkpoint"].__setitem__("response_sha256", "0" * 64),
    lambda a: a["assessments"][0]["review"]["request"]["scope"].__setitem__("challenge_review_sha256", "0" * 64),
    lambda a: a["assessments"][0]["review"]["normalized_response"]["claim_checks"][0].__setitem__("quote", "Changed actual unit"),
    lambda a: a["assessments"][0]["checkpoint"].__setitem__("checkpoint_sha256", "0" * 64),
])
def test_candidate_audit_rejects_changed_challenges_normalization_and_checkpoint_binding(mutate):
    flow, row = AdversarialProbe(), sample()
    check = flow._review_claim_score("sft", row, flow._review_evidence(row), ["sample"])
    audit = deepcopy(row["claim_review"])
    mutate(audit)
    with pytest.raises(ValueError):
        assessment_from_verdict(flow._review_payload("sft", row), check, evidence_ref="real-v4-checkpoints",
            method="llm_claim_review", claim_review=audit)


class FaultyBindingsClient(OfflineBlindClient):
    def chat(self, wire, **kwargs):
        value = super().chat(wire, **kwargs)
        data = model_request_data(wire)
        if data["phase"] == "claim_challenge":
            if data["scope"]["message_index"] == 1:
                self.prior_unit = data["target_units"][0]["id"]
                return value
            raw = challenge_response(data, bad=True)
            if "format_repair" not in data:
                raw["issues"][0]["unit_id"] = self.prior_unit
            return json.dumps(raw)
        raw = adversarial_grade_response(data, decision="dismissed")
        if data["challenge_review"]["issues"] and "format_repair" not in data:
            raw["challenge_checks"][0]["id"] = "foreign_issue"
        return json.dumps(raw)


def test_actual_wrong_previous_unit_and_issue_ids_get_one_retry_with_safe_current_bindings(tmp_path):
    client, row = FaultyBindingsClient(), sample(True)
    flow = workflow(tmp_path, client)
    check = flow._review_claim_score("multiturn", row,
        {"teacher_evidence": SOURCE, "source_id": "paper", "source_kind": "document"}, ["sample"])
    assert accepted(check) and len(client.calls) == 6
    retries = [call for call in client.calls if "format_repair" in call["data"]]
    assert len(retries) == 2
    for call in retries:
        table = assert_wire_boundary(call)
        detail = json.loads(call["data"]["format_repair"]["instruction"].split("Schema-only diagnostic: ", 1)[1])
        assert detail["response_field_names"] == table["response_field_names"]
        assert detail["current_target_unit_ids"] == table["current_target_unit_ids"]
        assert client.prior_unit not in canonical(detail)
        assert "foreign_issue" not in canonical(detail)
    failures = row["claim_review"]["schema_failures"]
    assert len(failures) == 2
    for failure in failures:
        checkpoint = failure["checkpoint"]
        saved = engine.read_json(flow.path / "checkpoints/review" / (checkpoint["checkpoint_sha256"] + ".json"))
        assert saved["sha256"] == checkpoint["response_sha256"]
    assessment_from_verdict(flow._review_payload("multiturn", row), check, evidence_ref="actual-v4-retries",
        method="llm_claim_review", claim_review=row["claim_review"])


def test_true_factory_repair_rechallenges_changed_body_and_preserves_full_reasoning_and_resume(tmp_path, monkeypatch):
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
    client = OfflineBlindClient(repair=True)
    flow = engine.Workflow(output, run_id, Path(__file__).resolve().parents[1], generator=client)
    state = flow.execute()
    assert state["status"] == "completed", state.get("error")
    assert len(client.calls) == 5  # two phases per distinct candidate, plus the existing repair call
    assert sum(call["data"].get("phase") == "claim_challenge" for call in client.calls) == 2
    record, = engine.read_json(flow.path / "artifacts/sft.records.json")
    assert record["claim_review"]["version"] == 4 and record["review_repair"]["attempts"] == 1
    assert record["review_repair"]["history"][0]["status"] == "rejected"
    exported = json.loads((flow.path / "artifacts/sft.jsonl").read_text(encoding="utf-8"))
    assert exported["messages"][1]["reasoning_content"] == "Inspection requires power-off."
    assert "WRONG_DRAFT" not in canonical(exported)
    resumed = OfflineBlindClient()
    assert engine.Workflow(output, run_id, flow.root, generator=resumed).execute(resume_run=True)["status"] == "completed"
    assert not resumed.calls


@pytest.mark.parametrize("value", [True, False, 0, 2, "1", []])
def test_adversarial_marker_is_strict_and_checked_before_calling_a_model(tmp_path, value):
    client, row = OfflineBlindClient(), sample()
    flow = workflow(tmp_path, client)
    flow.recipe["claim_review_adversarial_protocol_version"] = value
    with pytest.raises(ValueError, match="invalid_claim_review_adversarial_protocol_version"):
        flow._review_claim_score("sft", row, {"teacher_evidence": SOURCE}, ["sample"])
    assert not client.calls
    with pytest.raises(ValueError, match="invalid_claim_review_adversarial_protocol_version"):
        snapshot_node_prompts(None, recipe_version=22, claim_review_protocol_version=2,
            claim_review_adversarial_protocol_version=value)


def test_adversarial_catalog_priority_and_user_templates_survive_fresh_snapshots():
    settings = {"recipe_version": 22, "claim_review_protocol_version": 2, "claim_review_format_feedback_version": 2,
        "claim_reference_policy_version": 1, "claim_refutation_release_version": 1, "claim_review_anchor_protocol_version": 1}
    old = snapshot_node_prompts(None, **settings)
    new = snapshot_node_prompts(None, **settings, claim_review_adversarial_protocol_version=1)
    assert old["review"]["workflow.review_score"] == render(get("workflow.review_score", "1.17.0"))
    assert old["review"]["workflow.review_reference"] == render(get("workflow.review_reference", "1.0.1"))
    assert new["review"]["workflow.review_score"] == render(get("workflow.review_score", "1.18.0"))
    assert new["review"]["workflow.review_reference"] == render(get("workflow.review_reference", "2.0.0"))
    assert validate_node_prompt_snapshot(new, "system", **settings, claim_review_adversarial_protocol_version=1) == new
    custom = {"review": {"workflow.review_reference": "USER_CHALLENGER_{literal}", "workflow.review_score": "USER_ARBITER_{literal}"}}
    frozen = snapshot_node_prompts(custom, **settings, claim_review_adversarial_protocol_version=1)
    assert frozen["review"]["workflow.review_reference"] == custom["review"]["workflow.review_reference"]
    assert frozen["review"]["workflow.review_score"] == custom["review"]["workflow.review_score"]
