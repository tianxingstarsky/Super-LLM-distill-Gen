"""Repair consumes exact disputed conditions without changing the review gate."""
from copy import deepcopy
import json

import pytest

from lib.contexts.review.domain.claim_repair_feedback import claim_repair_feedback
from lib.domain.workflow_node_prompts import snapshot_node_prompts
from lib.prompts import get, render
from tests.test_claim_review_windows import WindowClient, long_sample
from tests.test_condition_review_workflow import flow, evidence


@pytest.fixture(scope="session", autouse=True)
def mock_llm_server():
    yield


class DisputeClient(WindowClient):
    def chat(self, messages, **kwargs):
        from tests.test_workflow_production import model_request_data
        request = model_request_data(messages)
        raw = json.loads(super().chat(messages, **kwargs))
        if request.get("phase") == "claim_adjudication":
            item = raw["claim_checks"][0]
            item.update(status="insufficient", severity="material")
            unit = request["target_units"][-1]
            item["premises"] = [{"origin": "candidate", "use": "counterexample",
                                 "unit_id": unit["id"], "quote": unit["text"]}]
        return json.dumps(raw)


def evaluated(tmp_path):
    workflow, row = flow(tmp_path, DisputeClient()), long_sample()
    workflow._review_claim_score("sft", row, evidence(), ["conditions"])
    return workflow, row, workflow._review_payload("sft", row)


def test_packet_retains_exact_disputed_span_and_related_exception_as_fallible_feedback(tmp_path):
    workflow, row, payload = evaluated(tmp_path)
    before = deepcopy(row)
    packet = claim_repair_feedback(payload, evidence(), row["claim_review"])
    assert packet["reviewer_opinions_not_facts"] is True
    assert packet["semantic_verification"] == "provenance_only_not_entailment"
    assert packet["disputes"]
    for item in packet["disputes"]:
        unit = item["unit"]
        field = row["messages"][item["message_index"]][unit["field"]]
        assert field[unit["start"]:unit["end"]] == unit["text"]
        assert item["condition_provenance"]["effective_status"] == "insufficient"
        assert item["related_candidate_units"]
    assert row == before, "Feedback cannot delete or repair reasoning itself."


def test_superseded_body_is_not_given_stale_unit_positions_and_new_source_cannot_reuse_audit(tmp_path):
    workflow, row, payload = evaluated(tmp_path)
    changed = deepcopy(payload)
    changed["messages"][-1]["reasoning_content"] += " An explicit human correction."
    assert claim_repair_feedback(changed, evidence(), row["claim_review"]) is None
    with pytest.raises(ValueError, match="stale_claim_repair_evidence"):
        claim_repair_feedback(payload, {**evidence(), "teacher_evidence": "A different condition."}, row["claim_review"])


def test_forged_raw_window_or_derived_receipt_never_becomes_repair_feedback(tmp_path):
    workflow, row, payload = evaluated(tmp_path)
    audit = deepcopy(row["claim_review"])
    audit["assessments"][0]["review"]["condition_guard"]["checks"][0]["effective_status"] = "supported"
    with pytest.raises(ValueError):
        claim_repair_feedback(payload, evidence(), audit)
    audit = deepcopy(row["claim_review"])
    audit["assessments"][0]["windows"][0]["review"]["raw_response"]["claim_checks"][0]["premises"][0]["quote"] = "Invented exception."
    with pytest.raises(ValueError):
        claim_repair_feedback(payload, evidence(), audit)


def test_new_repair_template_is_versioned_user_editable_and_does_not_replace_historical_defaults():
    old = snapshot_node_prompts(None, recipe_version=21)
    new = snapshot_node_prompts(None, recipe_version=21, claim_repair_feedback_version=1)
    assert old["review"]["workflow.review_repair"] == render(get("workflow.review_repair", version="1.11.0"))
    assert new["review"]["workflow.review_repair"] == render(get("workflow.review_repair", version="1.12.0"))
    override = {"review": {"workflow.review_repair": "My controlled correction policy."}}
    assert snapshot_node_prompts(override, recipe_version=21, claim_repair_feedback_version=1)["review"]["workflow.review_repair"] == override["review"]["workflow.review_repair"]
    assert "不能为了证明候选正确而补出假设" in new["review"]["workflow.review_repair"]
    assert "不能删除争议推理" in new["review"]["workflow.review_repair"]
