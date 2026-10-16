"""Offline factory, transport, checkpoint and repair routing for premise review."""
from copy import deepcopy
import json
from pathlib import Path

import jsonschema
import pytest

from lib.domain.reasoning_candidates import assessment_from_verdict, select_reasoning_candidate
from lib.domain.workflow_quality import accepted, canonical
from lib.infrastructure import training_workflow as engine
from lib.prompts import get, render
from tests.test_blind_claim_workflow import OfflineBlindClient
from tests.test_claim_review_workflow import SOURCE, sample
from tests.test_workflow_production import model_request_data


@pytest.fixture(scope="session", autouse=True)
def mock_llm_server():
    # Offline fake clients cover the actual Workflow.ask path without a socket.
    yield


ROOT = Path(__file__).resolve().parents[1]


def flow(tmp_path, client, *, max_rounds=0, node_prompts=None, node_generation=None):
    output = tmp_path / "output"
    run_id = engine.create_run(output, brief="Arrange a safe equipment inspection.", targets=["sft"],
        reasoning_required=True, sft_output_style="separated", node_prompts=node_prompts,
        node_generation=node_generation,
        review_repair={"mode": "auto", "max_rounds": max_rounds})
    result = engine.Workflow(output, run_id, ROOT, generator=client)
    result.stage = "review"
    return result


def metadata(call):
    return next(json.loads(line) for line in call["data"]["node_task_instructions"].splitlines()
                if line.startswith('{"actual_source_leaf_paths"'))


def evidence():
    return {"teacher_evidence": SOURCE, "source_id": "paper", "source_kind": "document"}


def sourced_sample():
    row = sample()
    row.update(source_id="paper", source_context={"kind": "document", "text": SOURCE})
    return row


class ConditionClient(OfflineBlindClient):
    model = "deepseek-flash"

    def __init__(self, mode="assumption", **kwargs):
        super().__init__(**kwargs)
        self.mode = mode
        self.responses = []

    def chat(self, wire, **kwargs):
        raw = json.loads(super().chat(wire, **kwargs))
        data = model_request_data(wire)
        if data.get("phase") == "claim_adjudication" and data.get("condition_contract_version") == 1:
            item = raw["claim_checks"][0]
            if self.mode == "assumption":
                item["premises"] = [{"origin": "assumption", "use": "task_condition",
                    "quote": "Only a newly generated batch is visible to this reader."}]
            elif self.mode in {"insufficient", "material_insufficient"}:
                item.update(status="insufficient", severity="material" if self.mode == "material_insufficient" else "minor")
            elif self.mode == "partial":
                raw["coverage"] = "partial"
            elif self.mode in {"refuted", "mixed_refutation"}:
                item.update(status="refuted", severity="material")
                if self.mode == "mixed_refutation":
                    raw["claim_checks"][1].update(status="insufficient", severity="minor")
        self.responses.append(deepcopy(raw))
        return json.dumps(raw)


def test_factory_sends_two_condition_contexts_per_turn_and_preserves_full_source_and_raw(tmp_path):
    client, row = OfflineBlindClient(), sample(True)
    original = deepcopy(row["messages"])
    workflow = flow(tmp_path, client)
    assert workflow.recipe["claim_review_condition_contract_version"] == 1
    assert workflow.recipe["prompts"]["workflow.review_reference"]["version"] == "2.1.0"
    assert workflow.recipe["prompts"]["workflow.review_score"]["version"] == "1.19.0"
    check = workflow._review_claim_score("multiturn", row, evidence(), ["candidate"])
    assert accepted(check) and len(client.calls) == 4
    assert row["messages"] == original
    for call in client.calls:
        request = call["data"]
        assert request["condition_contract_version"] == 1
        assert [message["role"] for message in call["wire"]] == ["system", "system", "user"]
        source = json.loads(call["wire"][1]["content"].split("\n", 1)[1].rsplit("\n", 1)[0])
        assert source["source_evidence"] == evidence()
        assert "source_evidence" not in request and "source_evidence_ref" in request
        assert sum(SOURCE in message["content"] for message in call["wire"]) == 1
        assert '"response_json_schema":' not in canonical(call["wire"][:2])
        if request["scope"]["message_index"] == 1:
            assert "FUTURE_ONLY_736" not in canonical(request)
        if request["phase"] == "claim_adjudication":
            assert request["challenge_review"]["issues"] == []
            assert "verdict" not in request["challenge_review"]
    audit = row["claim_review"]
    for collection in ("challenge_reviews", "assessments"):
        for item in audit[collection]:
            receipt, checkpoint = item["review"], item["checkpoint"]
            saved = engine.read_json(workflow.path / "checkpoints/review" / (checkpoint["checkpoint_sha256"] + ".json"))
            assert saved["data"] == receipt["raw_response"]
            assert saved["sha256"] == checkpoint["response_sha256"] == engine.digest(receipt["raw_response"])
            call = next(call for call in client.calls if call["data"]["scope"]["scope_sha256"] ==
                        receipt["request"]["scope"]["scope_sha256"])
            schema = metadata(call)["response_json_schema"]
            jsonschema.Draft202012Validator.check_schema(schema)
            jsonschema.Draft202012Validator(schema).validate(receipt["raw_response"])
    assert assessment_from_verdict(workflow._review_payload("multiturn", row), check,
        evidence_ref="offline-condition-checkpoints", method="llm_claim_review", claim_review=audit)["accepted"]
    before = len(client.calls)
    assert accepted(workflow._review_claim_score("multiturn", row, evidence(), ["candidate"]))
    assert len(client.calls) == before


@pytest.mark.parametrize("mode", ["assumption", "insufficient"])
def test_review_gap_waits_for_review_and_never_rewrites_the_candidate_despite_spare_rounds(tmp_path, mode):
    client = ConditionClient(mode)
    workflow = flow(tmp_path, client, max_rounds=3)
    original = sourced_sample()
    before = deepcopy(original["messages"])
    body_hash = engine.digest(workflow._review_payload("sft", original))
    source_hash = engine.digest(workflow._review_evidence(original))
    result, = workflow._review_repair_item({"target": "sft", "record": original})
    assert len(client.calls) == 2
    assert all("review_feedback" not in call["data"] for call in client.calls)
    assert result["messages"] == before == original["messages"]
    assert engine.digest(workflow._review_payload("sft", result)) == body_hash
    assert result["claim_review"]["candidate_sha256"] == body_hash
    assert result["claim_review"]["source_input_sha256"] == source_hash
    receipt = result["review_repair"]
    assert receipt["status"] == "waiting_manual_review"
    assert receipt["reason"] == receipt["stop_reason"] == "condition_support_pending_review"
    assert receipt["attempts"] == 0 and result["status"] == "quarantined"
    assert result["reason"] == "condition_support_pending_review"
    actual = result["claim_review"]["assessments"][0]["review"]
    assert actual["condition_guard"]["needs_review"] is True
    assert actual["raw_response"] == client.responses[-1]
    assert actual["raw_response"]["verdict"]["keep"] is True
    assert set(actual["raw_response"]["verdict"]["scores"].values()) == {5}
    assert actual["verdict"]["keep"] is False
    assert result["messages"][1]["reasoning_content"]
    if "reasoning_distillation" in result:
        assert result["reasoning_distillation"]["stop_reason"] == "condition_support_pending_review"
    repeated, = workflow._review_repair_item({"target": "sft", "record": original})
    assert len(client.calls) == 2
    assert repeated["review_repair"]["status"] == "waiting_manual_review"
    assert engine.digest(workflow._review_payload("sft", repeated)) == body_hash


def test_two_candidate_bodies_keep_the_same_system_and_source_prefix_by_phase(tmp_path):
    client = OfflineBlindClient()
    workflow = flow(tmp_path, client)
    row = sample()
    workflow._review_claim_score("sft", row, evidence(), ["first"])
    changed = sample()
    changed["messages"][1]["content"] = "Switch power off. Inspect afterwards. Record the result."
    workflow._review_claim_score("sft", changed, evidence(), ["second"])
    assert len(client.calls) == 4
    for phase in ("claim_challenge", "claim_adjudication"):
        one, two = [call for call in client.calls if call["data"]["phase"] == phase]
        assert one["wire"][:2] == two["wire"][:2]
        assert one["wire"][-1] != two["wire"][-1]
        assert metadata(one)["response_json_schema"]["properties"]["scope_sha256"] != metadata(two)["response_json_schema"]["properties"]["scope_sha256"]


def test_user_owned_review_node_prompts_remain_literal_and_reach_the_actual_wire(tmp_path):
    custom = {"review": {"workflow.review_reference": "USER_INVENTORY_LITERAL_{no_interpolation}",
                         "workflow.review_score": "USER_JUDGMENT_LITERAL_{no_interpolation}"}}
    client = OfflineBlindClient()
    workflow = flow(tmp_path, client, node_prompts=custom)
    for prompt_id, text in custom["review"].items():
        assert workflow.recipe["node_prompt_templates"]["review"][prompt_id] == text
    assert accepted(workflow._review_claim_score("sft", sample(), evidence(), ["candidate"]))
    assert len(client.calls) == 2
    for call in client.calls:
        prompt_id = "workflow.review_reference" if call["data"]["phase"] == "claim_challenge" else "workflow.review_score"
        assert custom["review"][prompt_id] in call["wire"][0]["content"]
        assert workflow.recipe["node_prompt_templates"]["review"][prompt_id] == custom["review"][prompt_id]
    assert client.model == "offline-blind-reference-review"


def test_interrupted_judgment_reuses_the_completed_inventory_without_a_third_context(tmp_path):
    client = OfflineBlindClient(interrupt_at=2)
    workflow = flow(tmp_path, client)
    row = sample()
    with pytest.raises(RuntimeError, match="offline_blind_interrupt"):
        workflow._review_claim_score("sft", row, evidence(), ["candidate"])
    resumed_client = OfflineBlindClient()
    resumed = engine.Workflow(workflow.path.parent.parent, workflow.state["id"], workflow.root, generator=resumed_client)
    resumed.stage = "review"
    assert accepted(resumed._review_claim_score("sft", row, evidence(), ["candidate"]))
    assert len(resumed_client.calls) == 1
    assert resumed_client.calls[0]["data"]["phase"] == "claim_adjudication"
    assert resumed_client.calls[0]["data"]["challenge_review"]["issues"] == []


def test_an_explicit_refutation_still_uses_the_existing_bounded_repair_branch(tmp_path, monkeypatch):
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
    workflow = engine.Workflow(output, run_id, ROOT, generator=client)
    state = workflow.execute()
    assert state["status"] == "completed", state.get("error")
    assert len(client.calls) == 5
    assert sum("review_feedback" in call["data"] for call in client.calls) == 1
    record, = engine.read_json(workflow.path / "artifacts/sft.records.json")
    assert record["review_repair"]["status"] == "accepted" and record["review_repair"]["attempts"] == 1
    assert record["review_repair"]["history"][0]["status"] == "rejected"
    assert "WRONG_DRAFT" not in canonical(record["messages"])
    assert record["messages"][1]["reasoning_content"] == "Inspection requires power-off."
    assert not record["claim_review"]["assessments"][0]["review"]["condition_guard"]["needs_review"]
    resumed = OfflineBlindClient()
    assert engine.Workflow(output, run_id, ROOT, generator=resumed).execute(resume_run=True)["status"] == "completed"
    assert not resumed.calls


@pytest.mark.parametrize("version", [True, False, 0, 2, "1", []])
def test_invalid_condition_recipe_marker_is_rejected_before_calling_a_model(tmp_path, version):
    client = OfflineBlindClient()
    workflow = flow(tmp_path, client)
    workflow.recipe["claim_review_condition_contract_version"] = version
    with pytest.raises(ValueError, match="invalid_claim_review_condition_contract_version"):
        workflow._review_claim_score("sft", sample(), evidence(), ["candidate"])
    assert not client.calls


def test_legacy_v4_marker_absence_keeps_prior_builtin_prompts_and_receipts(tmp_path):
    client = OfflineBlindClient()
    workflow = flow(tmp_path, client)
    workflow.recipe.pop("claim_review_condition_contract_version")
    for prompt_id, version in (("workflow.review_reference", "2.0.1"), ("workflow.review_score", "1.18.0")):
        workflow.recipe["node_prompt_templates"]["review"][prompt_id] = render(get(prompt_id, version))
    row = sample()
    assert accepted(workflow._review_claim_score("sft", row, evidence(), ["candidate"]))
    assert len(client.calls) == 2
    assert all("condition_contract_version" not in call["data"] for call in client.calls)
    assert "condition_ledger" not in row["claim_review"]["challenge_reviews"][0]["review"]
    assert "condition_guard" not in row["claim_review"]["assessments"][0]["review"]


@pytest.mark.parametrize("mode", ["assumption", "insufficient", "material_insufficient", "partial"])
def test_unresolved_support_remains_unverified_and_pending_instead_of_failed_training_data(tmp_path, mode):
    client = ConditionClient(mode)
    workflow = flow(tmp_path, client, max_rounds=3, node_generation={"sft": {"enabled": True}})
    original = sourced_sample()
    original_messages = deepcopy(original["messages"])
    original_body = workflow._review_payload("sft", original)
    original_hash = engine.digest(original_body)
    source_hash = engine.digest(workflow._review_evidence(original))
    result, = workflow._review_repair_item({"target": "sft", "record": original})
    assert len(client.calls) == 2
    assert result["messages"] == original_messages
    assert engine.digest(workflow._review_payload("sft", result)) == original_hash
    audit = result["claim_review"]
    assert audit["candidate_sha256"] == original_hash and audit["source_input_sha256"] == source_hash
    actual = audit["assessments"][0]["review"]
    assert actual["raw_response"] == client.responses[-1]
    assert actual["raw_response"]["verdict"]["keep"] is True
    assert set(actual["raw_response"]["verdict"]["scores"].values()) == {5}
    assert actual["condition_guard"]["needs_review"]
    assert actual["turn_verdict"]["critical_error"] is False
    selection = result["reasoning_distillation"]
    assessment, = selection["assessments"]
    assert assessment["outcome"] == {"status": "unverified"}
    assert assessment["process"] == {"status": "unverified"}
    assert assessment["review_status"] == "pending_review" and not assessment["accepted"]
    assert selection["status"] == selection["selection_status"] == selection["final_status"] == "waiting_manual_review"
    assert selection["stop_reason"] == "condition_support_pending_review"
    assert selection["rejections"] == [] and len(selection["pending_reviews"]) == 1
    assert selection["repair_attempts_consumed"] == 0
    assert selection["remaining"] == 0 and selection["unused_assessment_slots"] == 3
    assert all("review_feedback" not in call["data"] for call in client.calls)
    pure = select_reasoning_candidate([{"candidate": original_body, "assessment": assessment}], {"candidate_limit": 4})
    assert pure["status"] == "waiting_manual_review" and pure["rejections"] == []
    assert pure["pending_reviews"][0]["outcome"] == pure["pending_reviews"][0]["process"] == "unverified"
    if mode == "partial":
        assert actual["raw_response"]["coverage"] == "partial"
        assert actual["condition_guard"]["coverage_complete"] is False
        assert actual["condition_guard"]["unresolved_unit_ids"] == []


@pytest.mark.parametrize("mode", ["refuted", "mixed_refutation"])
def test_actual_refutation_is_not_masked_as_an_unverified_gap_or_pending_only_selection(tmp_path, mode):
    client = ConditionClient(mode)
    workflow = flow(tmp_path, client)
    row = sample()
    body = workflow._review_payload("sft", row)
    check = workflow._review_claim_score("sft", row, evidence(), ["candidate"])
    actual = row["claim_review"]["assessments"][0]["review"]
    assert len(client.calls) == 2 and actual["raw_response"] == client.responses[-1]
    assert actual["raw_response"]["claim_checks"][0]["status"] == "refuted"
    assert actual["turn_verdict"]["critical_error"] is True
    assert actual["condition_guard"]["needs_review"] is (mode == "mixed_refutation")
    assessment = assessment_from_verdict(body, check, evidence_ref="offline-authenticated-refutation",
        method="llm_claim_review", claim_review=row["claim_review"])
    assert not assessment["accepted"]
    assert assessment["outcome"]["status"] == "fail"
    # This oracle refutes an answer/source claim, not a reasoning-field claim.
    # Preserve the separately reported process axis rather than inventing an
    # additional process error or masking the established outcome error.
    assert assessment["process"]["status"] != "unverified"
    assert assessment.get("review_status") != "pending_review"
    selection = select_reasoning_candidate([{"candidate": body, "assessment": assessment}])
    assert selection["status"] == "rejected"
    assert len(selection["rejections"]) == 1 and not selection.get("pending_reviews")


def test_mixed_refutation_and_gap_preserve_the_failure_while_waiting_before_any_repair(tmp_path):
    client = ConditionClient("mixed_refutation")
    workflow = flow(tmp_path, client, max_rounds=3, node_generation={"sft": {"enabled": True}})
    original = sourced_sample()
    original_body = workflow._review_payload("sft", original)
    result, = workflow._review_repair_item({"target": "sft", "record": original})
    assert len(client.calls) == 2 and all("review_feedback" not in call["data"] for call in client.calls)
    assert workflow._review_payload("sft", result) == original_body
    assert result["review_repair"]["status"] == "waiting_manual_review"
    assert result["review_repair"]["attempts"] == 0
    actual = result["claim_review"]["assessments"][0]["review"]
    assert actual["raw_response"] == client.responses[-1]
    assert actual["turn_verdict"]["critical_error"] is True and actual["condition_guard"]["needs_review"]
    selection = result["reasoning_distillation"]
    assessment, = selection["assessments"]
    assert assessment["outcome"]["status"] == "fail" and assessment.get("review_status") != "pending_review"
    assert selection["status"] == selection["final_status"] == "waiting_manual_review"
    assert len(selection["rejections"]) == 1 and not selection.get("pending_reviews")
    assert selection["rejections"][0]["outcome"] == "fail"
