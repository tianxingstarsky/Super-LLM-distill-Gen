"""Blind-input separation and conservative linked-review integrity tests."""
from copy import deepcopy
import json

import pytest

from lib.domain.blind_claim_review import (blind_review_requests, claim_review_requests_v2,
    merge_claim_reviews_v2, validate_blind_review, validate_claim_review_v2)
from lib.domain.claim_review import claim_review_requests, validate_claim_review
from tests.test_claim_review import candidate, digest, grade, response, review_requirements, source


def blind_response(request):
    return {"claim_review_protocol_version": 2, "phase": "blind_reference", "scope": deepcopy(request["scope"]),
            "coverage": "complete", "reference_points": [
                {"id": "existing-control", "point": "The number of reusable comparable controls is not established by a fixed new-training budget.",
                 "severity": "material", "basis": {"kind": "context", "evidence": [
                     {"message_index": 0, "field": "content", "quote": "已有对照数量未知"}],
                     "derivation": "The user explicitly leaves existing records unknown, so a new-training constraint cannot establish their absence."}},
                {"id": "historic-ratio", "point": "The reported 22 percent is historical sample composition, not a universal quota.",
                 "severity": "material", "basis": {"kind": "source", "evidence": [
                     {"source_path": ["teacher_evidence_blocks", 0], "quote": "该比例是本次样本的统计结果"}],
                     "derivation": "The source describes the sample used in that study, which does not prescribe a percentage for the current dataset."}}],
            "limitations": ["A quoted passage does not independently establish the full study's external validity."]}


def linked_requests(body=None, *, evidence=None, requirements=None):
    body = candidate() if body is None else body
    evidence = source() if evidence is None else evidence
    raw = blind_review_requests("multiturn", body, evidence, review_requirements=requirements)
    blind = [validate_blind_review(blind_response(item), item) for item in raw]
    return blind, claim_review_requests_v2("multiturn", body, evidence, review_requirements=requirements, blind_reviews=blind)


def grade_response(request):
    value = response(request)
    value["claim_review_protocol_version"] = 2
    value["reference_checks"] = [{"id": "existing-control", "relation": "consistent", "field": "reasoning_content",
        "quote": request["target_fields"]["reasoning_content"],
        "reason": "The explanation preserves unknown existing records rather than inferring zero from a training budget."},
        {"id": "historic-ratio", "relation": "not_claimed", "field": None, "quote": "",
         "reason": "The current user did not ask for class proportions and the reply makes no numerical composition claim."}]
    return value


def test_blind_request_contains_no_current_answer_explanation_hash_or_future_turn():
    body = candidate()
    body.update(judge=grade(), qa_contract={"question": "planned future user"})
    body["messages"][1].update(review={"reason": "old score"})
    first, second = blind_review_requests("multiturn", body, source())
    serialized = json.dumps(first, ensure_ascii=False)
    for absent in (body["messages"][1]["content"], body["messages"][1]["reasoning_content"],
                   body["messages"][2]["content"], "candidate_sha256", "message_sha256", "target_fields", "planned future user", "old score"):
        assert absent not in serialized
    assert first["messages"] == [body["messages"][0]]
    assert first["scope"]["prefix_sha256"] == digest(body["messages"][:1])
    assert len(second["messages"]) == 3
    assert second["messages"][1]["content"] == body["messages"][1]["content"]
    assert "review" not in second["messages"][1]


def test_changing_current_answer_and_reasoning_reuses_exact_blind_input_but_not_later_prefix():
    body = candidate()
    before = blind_review_requests("multiturn", body, source())
    changed = deepcopy(body)
    changed["messages"][1].update(content="A completely replaced answer.", reasoning_content="A repaired explanation.")
    after = blind_review_requests("multiturn", changed, source())
    assert before[0] == after[0]
    assert before[1] != after[1]
    assert before[0]["scope"]["scope_sha256"] == after[0]["scope"]["scope_sha256"]


def test_current_answer_is_not_required_to_prepare_reference_without_anchor():
    body = candidate()
    before = blind_review_requests("multiturn", body, source())[0]
    body["messages"][1].pop("content")
    body["messages"][1].pop("reasoning_content")
    assert blind_review_requests("multiturn", body, source())[0] == before


def test_source_user_or_trusted_node_setting_changes_invalidate_blind_cache_identity():
    base = blind_review_requests("multiturn", candidate(), source())[0]
    changed_user = candidate()
    changed_user["messages"][0]["content"] += " Different condition."
    changed_source = {**source(), "teacher_evidence": "Changed source facts."}
    settings = review_requirements()
    variants = [blind_review_requests("multiturn", changed_user, source())[0],
                blind_review_requests("multiturn", candidate(), changed_source)[0],
                blind_review_requests("multiturn", candidate(), source(), review_requirements=settings)[0]]
    assert all(item["scope"]["scope_sha256"] != base["scope"]["scope_sha256"] for item in variants)


def test_reference_notes_are_preserved_as_fallible_not_independently_verified():
    request = blind_review_requests("sft", candidate(), source())[0]
    raw = blind_response(request)
    before = deepcopy(raw)
    receipt = validate_blind_review(raw, request)
    assert receipt["raw_response"] == raw == before
    assert receipt["structure_verified"]
    assert receipt["semantic_verification"] == "reviewer_reported_not_independently_verified"
    assert "accepted" not in receipt and "verdict" not in receipt


@pytest.mark.parametrize("mutation", ["empty", "duplicate", "fake_source", "future_context", "bad_scope", "no_basis", "bad_version"])
def test_blind_reference_response_must_be_exact_complete_and_traceable(mutation):
    request = blind_review_requests("sft", candidate(), source())[0]
    raw = blind_response(request)
    if mutation == "empty":
        raw["reference_points"] = []
    elif mutation == "duplicate":
        raw["reference_points"].append(deepcopy(raw["reference_points"][0]))
    elif mutation == "fake_source":
        raw["reference_points"][1]["basis"]["evidence"][0]["quote"] = "An invented source claim."
    elif mutation == "future_context":
        raw["reference_points"][0]["basis"]["evidence"][0].update(message_index=2, quote="现在确认")
    elif mutation == "bad_scope":
        raw["scope"]["prefix_sha256"] = "0" * 64
    elif mutation == "no_basis":
        raw["reference_points"][0]["basis"]["derivation"] = "Correct"
    else:
        raw["claim_review_protocol_version"] = 1
    with pytest.raises(ValueError):
        validate_blind_review(raw, request)


def test_v2_grade_request_attaches_bound_raw_reference_without_duplicate_source():
    blind, requests = linked_requests(requirements=review_requirements())
    for receipt, request in zip(blind, requests):
        assert request["claim_review_protocol_version"] == 2
        assert request["reference_review"] == receipt["raw_response"]
        assert request["scope"]["blind_review_sha256"] == digest(receipt["raw_response"])
        assert request["scope"]["blind_request_sha256"] == digest(receipt["request"])
        assert "source_evidence" not in request["reference_review"]
        assert validate_claim_review_v2(grade_response(request), request)["accepted"]


@pytest.mark.parametrize("relation", ["contradicted", "uncertain"])
def test_material_reference_problem_vetoes_perfect_claim_and_model_scores(relation):
    _, requests = linked_requests()
    request = requests[0]
    value = grade_response(request)
    value["reference_checks"][0]["relation"] = relation
    before = deepcopy(value)
    receipt = validate_claim_review_v2(value, request)
    assert receipt["raw_response"] == value == before
    assert value["verdict"]["keep"] and set(value["verdict"]["scores"].values()) == {5}
    assert not receipt["accepted"] and not receipt["verdict"]["keep"]
    assert receipt["turn_verdict"]["critical_error"]
    assert set(receipt["verdict"]["scores"].values()) == {5}


def test_irrelevant_unclaimed_reference_does_not_force_background_into_answer():
    _, requests = linked_requests()
    value = grade_response(requests[0])
    assert value["reference_checks"][1]["relation"] == "not_claimed"
    receipt = validate_claim_review_v2(value, requests[0])
    assert receipt["accepted"]


def test_partial_blind_coverage_cannot_be_rescued_by_perfect_later_scores():
    body = candidate()
    blind_requests = blind_review_requests("multiturn", body, source())
    blind = []
    for request in blind_requests:
        value = blind_response(request)
        value["coverage"] = "partial"
        blind.append(validate_blind_review(value, request))
    request = claim_review_requests_v2("multiturn", body, source(), blind_reviews=blind)[0]
    receipt = validate_claim_review_v2(grade_response(request), request)
    assert not receipt["accepted"] and "unverified" in receipt["verdict"]["reason"]


@pytest.mark.parametrize("mutation", ["missing", "duplicate", "unknown", "fake_quote", "not_claimed_quote", "extra", "no_reason", "no_field"])
def test_every_reference_comparison_is_explicit_and_exact(mutation):
    _, requests = linked_requests()
    request = requests[0]
    value = grade_response(request)
    if mutation == "missing":
        value["reference_checks"].pop()
    elif mutation == "duplicate":
        value["reference_checks"][1] = deepcopy(value["reference_checks"][0])
    elif mutation == "unknown":
        value["reference_checks"][0]["id"] = "not-a-real-point"
    elif mutation == "fake_quote":
        value["reference_checks"][0]["quote"] = "invented current output"
    elif mutation == "not_claimed_quote":
        value["reference_checks"][1]["quote"] = "claimed anyway"
    elif mutation == "extra":
        value["reference_checks"][0]["counter_evidence"] = {}
    elif mutation == "no_reason":
        value["reference_checks"][0]["reason"] = " "
    else:
        value["reference_checks"][0].pop("field")
    with pytest.raises(ValueError):
        validate_claim_review_v2(value, request)


def test_v2_rejects_legacy_responses_and_legacy_rejects_v2():
    body = candidate()
    _, requests = linked_requests(body)
    old = claim_review_requests("multiturn", body, source())[0]
    with pytest.raises(ValueError):
        validate_claim_review(response(old), requests[0])
    with pytest.raises(ValueError):
        validate_claim_review_v2(response(old), requests[0])
    with pytest.raises(ValueError):
        validate_claim_review(grade_response(requests[0]), old)
    assert validate_claim_review(response(old), old)["accepted"]


def test_blind_receipt_cannot_be_substituted_after_source_or_prefix_changes():
    body = candidate()
    blind, _ = linked_requests(body)
    for changed, evidence in ((body, {**source(), "teacher_evidence": "Changed facts."}),
                              ({"messages": [{"role": "user", "content": "Changed task."}, *body["messages"][1:]]}, source())):
        with pytest.raises(ValueError, match="stale_blind_review_receipt"):
            claim_review_requests_v2("multiturn", changed, evidence, blind_reviews=blind)


def test_repaired_current_assistant_can_reuse_blind_receipt_for_its_unchanged_prefix():
    body = {"messages": candidate()["messages"][:2]}
    blind, original_requests = linked_requests(body)
    body["messages"][1]["content"] += " We should inspect compatible prior records."
    changed = claim_review_requests_v2("multiturn", body, source(), blind_reviews=blind)
    assert changed[0]["scope"]["candidate_sha256"] != original_requests[0]["scope"]["candidate_sha256"]
    assert changed[0]["scope"]["blind_request_sha256"] == original_requests[0]["scope"]["blind_request_sha256"]
    assert validate_claim_review_v2(grade_response(changed[0]), changed[0])["accepted"]


def test_tampered_blind_or_grade_request_and_receipt_do_not_replay():
    body = candidate()
    blind, requests = linked_requests(body)
    altered = deepcopy(blind)
    altered[0]["raw_response"]["reference_points"][0]["point"] = "Replaced review note."
    with pytest.raises(ValueError, match="altered_blind_review_receipt"):
        claim_review_requests_v2("multiturn", body, source(), blind_reviews=altered)
    changed_request = deepcopy(requests[0])
    changed_request["reference_review"]["reference_points"][0]["point"] = "Replaced review note."
    with pytest.raises(ValueError):
        validate_claim_review_v2(grade_response(changed_request), changed_request)
    receipts = [validate_claim_review_v2(grade_response(request), request) for request in requests]
    receipts[0]["accepted"] = False
    with pytest.raises(ValueError, match="altered_claim_review_v2_receipt"):
        merge_claim_reviews_v2(body, receipts)


def test_merge_cannot_omit_bad_early_turn_or_erase_its_issue_with_later_recovery():
    body = candidate()
    _, requests = linked_requests(body)
    first = grade_response(requests[0])
    first["reference_checks"][0]["relation"] = "contradicted"
    receipts = [validate_claim_review_v2(first, requests[0]),
                validate_claim_review_v2(grade_response(requests[1]), requests[1])]
    result = merge_claim_reviews_v2(body, receipts)
    assert not result["keep"] and len(result["turn_verdicts"]) == 2
    assert result["turn_verdicts"][0]["critical_error"]
    with pytest.raises(ValueError, match="incomplete_claim_review_v2_turns"):
        merge_claim_reviews_v2(body, receipts[1:])


def reference_error_case():
    body = {"messages": [{"role": "user", "content": "已有对照数量未知。报告里的22%是普遍比例吗？"},
                         {"role": "assistant", "content": "22%只是该次研究样本中的分类任务比例。",
                          "reasoning_content": "历史样本组成并不等于所有后续数据集的比例要求。"}]}
    raw_request = blind_review_requests("sft", body, source())[0]
    blind_raw = blind_response(raw_request)
    blind_raw["reference_points"][1]["point"] = "Every future dataset must contain exactly 22 percent classification tasks."
    blind_receipt = validate_blind_review(blind_raw, raw_request)
    request, = claim_review_requests_v2("sft", body, source(), blind_reviews=[blind_receipt])
    value = grade_response(request)
    proof = {"kind": "source", "evidence": [{"source_path": ["teacher_evidence_blocks", 0],
               "quote": "该比例是本次样本的统计结果"}],
             "derivation": "The statement explicitly scopes the ratio to this study's sample. A universal quota is therefore an unsupported reading of the reference."}
    value["claim_checks"][0]["basis"] = deepcopy(proof)
    value["reference_checks"][1] = {"id": "historic-ratio", "relation": "reference_error", "field": "content",
        "quote": request["target_fields"]["content"], "reason": "The answer preserves the study-specific scope, while the blind note incorrectly universalizes it.",
        "counter_evidence": proof}
    return body, request, value


def test_real_source_can_rebut_wrong_blind_reference_without_rejecting_correct_candidate():
    body, request, value = reference_error_case()
    receipt = validate_claim_review_v2(value, request)
    assert receipt["accepted"]
    assert "Every future dataset" in receipt["request"]["reference_review"]["reference_points"][1]["point"]
    assert receipt["raw_response"]["reference_checks"][1]["counter_evidence"]["evidence"]
    assert merge_claim_reviews_v2(body, [receipt])["keep"]


@pytest.mark.parametrize("mutation", ["missing_counter", "logic_only", "fake_source", "unsupported_claim", "different_quote"])
def test_reference_error_requires_exact_supported_claim_and_real_counter_evidence(mutation):
    _, request, value = reference_error_case()
    check = value["reference_checks"][1]
    if mutation == "missing_counter":
        check.pop("counter_evidence")
    elif mutation == "logic_only":
        check["counter_evidence"]["kind"] = "logic"
    elif mutation == "fake_source":
        check["counter_evidence"]["evidence"][0]["quote"] = "Fabricated authoritative counter-proof."
    elif mutation == "unsupported_claim":
        value["claim_checks"][0]["status"] = "insufficient"
    else:
        check["quote"] = "22%只是"
    with pytest.raises(ValueError):
        validate_claim_review_v2(value, request)


@pytest.mark.parametrize("alias", ["reasoning_content", "reasoning", "thinking"])
def test_entire_nonempty_reasoning_body_still_requires_claim_coverage(alias):
    body = {"messages": candidate()["messages"][:2]}
    body["messages"][1][alias] = "A separate full explanation with distinct assumptions. " * 300
    if alias != "reasoning_content":
        body["messages"][1].pop("reasoning_content")
    _, requests = linked_requests(body)
    request = requests[0]
    raw = response(request)
    raw["claim_review_protocol_version"] = 2
    raw["reference_checks"] = [{"id": point["id"], "relation": "not_claimed", "field": None, "quote": "",
        "reason": "This numerical background is not asserted in the current output."} for point in request["reference_review"]["reference_points"]]
    assert request["target_fields"][alias] == body["messages"][1][alias]
    assert validate_claim_review_v2(raw, request)["accepted"]
    raw["checked_fields"].remove(alias)
    raw["claim_checks"] = [claim for claim in raw["claim_checks"] if claim["field"] != alias]
    with pytest.raises(ValueError):
        validate_claim_review_v2(raw, request)


def test_complete_cot_lists_are_bound_and_merged_without_changing_legacy_shape():
    body = {"question": "已有对照数量未知。", "reasoning": ["Existing controls remain unknown.", "The budget alone cannot establish absence."],
            "answer": "Check existing comparable records first."}
    raw_blind, = blind_review_requests("cot", body, source())
    blind = validate_blind_review(blind_response(raw_blind), raw_blind)
    request, = claim_review_requests_v2("cot", body, source(), blind_reviews=[blind])
    assert request["target_fields"]["reasoning_content"] == "\n".join(body["reasoning"])
    receipt = validate_claim_review_v2(grade_response(request), request)
    assert set(merge_claim_reviews_v2(body, [receipt])) == set(grade())
