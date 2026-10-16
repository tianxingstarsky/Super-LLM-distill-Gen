"""Stopped review work and real assessment references remain distinguishable.

The provider is replaced by explicit schema/grade fixtures. These tests verify
audit identity and bounded execution, not the semantic accuracy of the grader.
"""
from copy import deepcopy
import json

import pytest

from lib.infrastructure.training_workflow import digest
from tests.test_reasoning_distillation_workflow import Probe, candidate
from tests.test_reasoning_warmup_review import WarmupProbe, candidate as dialogue_candidate
from tests.test_turn_review_and_reasoning_repair import grade, reviewed


@pytest.fixture(scope="session", autouse=True)
def mock_llm_server():
    yield


class RepeatedDraft(Probe):
    def _repair_content(self, target, row, evidence, key, check, instruction):
        self.repairs += 1
        return deepcopy(row), None


class InvalidRepair(Probe):
    def _repair_content(self, target, row, evidence, key, check, instruction):
        self.repairs += 1
        return deepcopy(row), "authored_reasoning_invalid_text"


class CycledDraft(Probe):
    def _review_score(self, target, row, evidence, key, instruction):
        actual_key = [*key, "score"]
        row["reasoning_assessment_method"] = "llm_review"
        row["reasoning_assessment_reference"] = {
            "kind": "llm_review", "key": actual_key, "checkpoint_sha256": digest(["call", actual_key])}
        return super()._review_score(target, row, evidence, key, instruction)

    def _repair_content(self, target, row, evidence, key, check, instruction):
        self.repairs += 1
        revised = deepcopy(row)
        if self.repairs == 1:
            revised["messages"][1]["reasoning_content"] = "Another unsupported inference in draft B."
        else:
            revised["messages"] = candidate()["messages"]
        return revised, None


class ActualScoreProbe(WarmupProbe):
    """Use the production score loop while retaining exact mock request keys."""

    def __init__(self, responses=(), *, rounds=0):
        super().__init__(responses)
        self.recipe.update(reasoning_distillation_audit_version=1)
        self.recipe["review_repair"]["max_rounds"] = rounds
        self.stage = "review"
        self.call_keys = []

    def ask(self, key, role, prompt_id, data, **kwargs):
        self.call_keys.append(deepcopy(key))
        return super().ask(key, role, prompt_id, data, **kwargs)


def test_repeated_draft_stops_without_spending_or_advertising_unused_score_slots():
    original = candidate()
    worker = RepeatedDraft([False], rounds=2)
    result, = worker._review_repair_item({"target": "sft", "record": original})
    audit = result["reasoning_distillation"]
    assert worker.scores == worker.repairs == 1
    assert result["status"] == "quarantined" and audit["final_status"] == "rejected"
    assert audit["status"] == "rejected" and audit["remaining"] == 0
    assert audit["selection_status"] == "needs_candidate" and audit["unused_assessment_slots"] == 2
    assert audit["repair_attempts_consumed"] == 1
    assert audit["stop_reason"] == "review_repair_repeated_candidate"
    assert audit["assessments"][0]["payload"]["messages"] == original["messages"]
    assert audit["final_candidate_sha256"] == digest(worker._review_payload("sft", result))


def test_invalid_repairs_exhaust_execution_even_without_consuming_all_scoring_slots():
    worker = InvalidRepair([False], rounds=2)
    result, = worker._review_repair_item({"target": "sft", "record": candidate()})
    audit = result["reasoning_distillation"]
    assert worker.scores == 1 and worker.repairs == 2
    assert result["status"] == "quarantined" and audit["status"] == "exhausted"
    assert audit["final_status"] == "rejected" and audit["remaining"] == 0
    assert audit["selection_status"] == "needs_candidate" and audit["unused_assessment_slots"] == 2
    assert audit["repair_attempts_consumed"] == 2 and audit["stop_reason"] == "review_limit_reached"
    assert audit["attempted"] == 1 and len(audit["assessments"]) == 1
    assert [item.get("repair_issue") for item in result["review_repair"]["history"][1:]] == [
        "authored_reasoning_invalid_text", "authored_reasoning_invalid_text"]


def test_return_to_earlier_rejected_body_restores_that_bodys_actual_assessment_reference():
    worker = CycledDraft([False, False], rounds=3)
    result, = worker._review_repair_item({"target": "sft", "record": candidate()})
    audit = result["reasoning_distillation"]
    first, second = audit["assessments"]
    first_ref = json.loads(first["evidence_ref"])["assessment"]
    second_ref = json.loads(second["evidence_ref"])["assessment"]
    assert worker.scores == worker.repairs == 2  # No third paid score for A.
    assert first["candidate_sha256"] != second["candidate_sha256"]
    assert audit["final_candidate_sha256"] == first["candidate_sha256"]
    assert first_ref != second_ref and result["reasoning_assessment_reference"] == first_ref
    assert result["reasoning_assessment_method"] == first["method"] == "llm_review"
    assert audit["status"] == "rejected" and audit["remaining"] == 0
    assert audit["stop_reason"] == "review_repair_repeated_candidate"
    assert result["review_repair"]["history"][-1]["reused_assessment"] is True


def test_explicit_rework_uses_only_existing_repair_budget_and_does_not_score_parent_again():
    worker = Probe([True], rounds=2)
    worker.state = {"id": "offline-audit"}
    original = candidate()
    result, = worker._review_repair_item({"target": "sft", "record": original,
        "revision_context": {"candidate_id": "selected-parent"}})
    audit = result["reasoning_distillation"]
    assert worker.scores == worker.repairs == 1
    assert audit["candidate_limit"] == 2 and audit["attempted"] == 1
    assert audit["status"] == "accepted" and audit["remaining"] == 0
    assert audit["unused_assessment_slots"] == 1 and audit["repair_attempts_consumed"] == 1
    assert audit["assessments"][0]["payload"]["messages"] != original["messages"]
    assert original["messages"][1]["content"] == "An unchanged result proves improvement."


def test_passing_first_draft_closes_invocation_without_turning_unused_slots_into_more_work():
    worker = Probe([True], rounds=3)
    result, = worker._review_repair_item({"target": "sft", "record": candidate()})
    audit = result["reasoning_distillation"]
    assert worker.scores == 1 and worker.repairs == 0
    assert audit["status"] == audit["selection_status"] == "accepted"
    assert audit["remaining"] == 0 and audit["unused_assessment_slots"] == 3
    assert audit["repair_attempts_consumed"] == 0 and audit["stop_reason"] == "first_passing_candidate"


def test_turn_format_retry_receipt_points_to_successful_actual_request_not_invalid_first_call():
    original = dialogue_candidate()
    worker = ActualScoreProbe([grade(), reviewed({"messages": original["messages"]})])
    result, = worker._review_repair_item({"target": "multiturn", "record": original})
    assert result["status"] == "eligible" and len(worker.call_keys) == 2
    first_key, actual_key = worker.call_keys
    assert actual_key == [*first_key, "turn_format_repair", 1]
    reference = result["reasoning_assessment_reference"]
    assert reference["key"] == actual_key
    assert reference["checkpoint_sha256"] == digest(["call", actual_key])
    assert reference["checkpoint_sha256"] != digest(["call", first_key])
    assessment, = result["reasoning_distillation"]["assessments"]
    assert json.loads(assessment["evidence_ref"])["assessment"] == reference
    assert result["assistant_turn_review_retry"]["schema_failures"][0]["code"] == "missing_assistant_turn_reviews"
    assert assessment["payload"]["messages"] == original["messages"]


def test_local_missing_reasoning_guard_has_no_model_checkpoint_and_never_claims_fact_verification():
    original = dialogue_candidate()
    original["messages"][1].pop("reasoning_content")
    worker = ActualScoreProbe()
    result, = worker._review_repair_item({"target": "multiturn", "record": original})
    assert result["status"] == "quarantined" and not worker.call_keys
    reference = result["reasoning_assessment_reference"]
    assert reference == {"kind": "deterministic_guard", "code": "review_repair_reasoning_missing"}
    assert "checkpoint_sha256" not in reference and "key" not in reference
    assessment, = result["reasoning_distillation"]["assessments"]
    assert assessment["method"] == "deterministic_guard" and not assessment["accepted"]
    assert assessment["outcome"] == assessment["process"] == {"status": "unverified"}
    assert json.loads(assessment["evidence_ref"])["assessment"] == reference
    assert assessment["payload"]["messages"] == original["messages"]


@pytest.mark.parametrize("context_passes", [True, False])
def test_context_format_retry_keeps_fact_reference_and_actual_context_reference_separate(context_passes):
    original = dialogue_candidate()
    original["qa_contract"] = {"qa_type": "closed_book", "answer_policy": "answer"}
    context = {"keep": context_passes, "context_complete": context_passes, "leakage_free": context_passes,
               "reason": "实际对话完整。" if context_passes else "本轮答案依赖对话没有给出的指代。"}
    worker = ActualScoreProbe([reviewed({"messages": original["messages"]}), {**context, "type": "extra"}, context])
    worker.recipe.update(learner_context_review_version=1, learner_context_retry_version=1,
                         prompts={"workflow.review_score": {"version": "1.10.0"}})
    result, = worker._review_repair_item({"target": "multiturn", "record": original})
    assert len(worker.call_keys) == 3
    fact_key, invalid_context_key, actual_context_key = worker.call_keys
    assert actual_context_key == [*invalid_context_key, "format_repair", 1]
    reference = result["reasoning_assessment_reference"]
    assert reference["key"] == fact_key and reference["checkpoint_sha256"] == digest(["call", fact_key])
    assert reference["learner_context_checkpoint"] == {
        "key": actual_context_key, "checkpoint_sha256": digest(["call", actual_context_key])}
    assessment, = result["reasoning_distillation"]["assessments"]
    assert json.loads(assessment["evidence_ref"])["assessment"] == reference
    assert assessment["outcome"]["status"] == assessment["process"]["status"] == "pass"
    assert assessment["accepted"] is context_passes
    assert (result["status"] == "eligible") is context_passes
    assert result["learner_context_retry"]["attempts"] == 2
    assert assessment["payload"]["messages"] == original["messages"]
