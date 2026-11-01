"""Grouped review reduces declarations without changing admission evidence."""
from copy import deepcopy

import pytest

from lib.contexts.review.domain.consolidated_claim_review import (
    consolidated_review_requests, validate_consolidated_claim_review,
    focused_review_request, resolve_consolidated_review, merge_consolidated_claim_reviews,
    consolidated_checkpoint_key, validate_consolidated_claim_audit,
    claim_review_refutations,
)
from lib.domain.claim_review import _digest
from lib.domain.reasoning_candidates import assessment_from_verdict
from lib.contexts.review.domain.claim_repair_feedback import claim_repair_feedback
from tests.test_claim_review_workflow import sample, grade, SOURCE


@pytest.fixture(scope="session", autouse=True)
def mock_llm_server():
    yield


def response(request):
    return {"coverage": "complete", "verdict": grade(), "limitations": [], "groups": [{
        "unit_ids": deepcopy(request["review_unit_ids"]), "status": "supported", "severity": "material",
        "basis": {"kind": "source", "evidence": [{"quote": SOURCE, "source_path": ["teacher_evidence"]}],
                  "derivation": "Both the stated prerequisite and its explanation require power-off before inspection."},
        "premises": [{"origin": "source", "use": "support", "quote": SOURCE, "source_path": ["teacher_evidence"]}]}]}


def request_for(row=None):
    return consolidated_review_requests("sft", row or sample(), {"teacher_evidence": SOURCE}, True)[0]


def test_application_reviews_once_and_reuses_only_the_disputed_scope():
    from lib.contexts.review.application.review_consolidated_claims import ReviewConsolidatedClaims

    class Port:
        def __init__(self, dispute):
            self.requests, self.dispute = [], dispute

        def check_cancel(self):
            pass

        def review(self, request):
            self.requests.append(deepcopy(request))
            raw = response(request)
            if self.dispute and len(self.requests) == 1:
                original = raw["groups"][0]
                raw["groups"] = [dict(deepcopy(original), unit_ids=request["review_unit_ids"][:1],
                    status="insufficient"), dict(deepcopy(original), unit_ids=request["review_unit_ids"][1:])]
            checked = validate_consolidated_claim_review(raw, request)
            return checked, checkpoint(checked)

    for disputed, calls in ((False, 1), (True, 2)):
        port = Port(disputed)
        audit = {"assessments": [], "schema_failures": []}
        check = ReviewConsolidatedClaims(port).execute(sample(), [request_for()], audit)
        assert len(port.requests) == calls
        assert check["keep"] and audit["status"] == "complete"
        if disputed:
            assert port.requests[1]["review_unit_ids"] == port.requests[0]["review_unit_ids"][:1]
            assert port.requests[1]["messages"] == port.requests[0]["messages"]


def checkpoint(receipt):
    request = receipt["request"]
    key = consolidated_checkpoint_key(request)
    return {"stage": "review", "key": key, "checkpoint_sha256": _digest(["call", key]),
            "request_sha256": _digest(request), "review_request_sha256": _digest(request),
            "response_sha256": _digest(receipt["raw_response"])}


def audit_for(row, receipt, focused=None):
    record = {"review": receipt, "checkpoint": checkpoint(receipt)}
    to_merge = {"review": receipt}
    if focused:
        record["focused"] = {"review": focused, "checkpoint": checkpoint(focused)}
        to_merge["focused"] = focused
    check = merge_consolidated_claim_reviews(row, [to_merge])
    scope = receipt["request"]["scope"]
    return check, {"version": 6, "method": "consolidated_condition_claim_review", "status": "complete",
        **{key: scope[key] for key in ("candidate_sha256", "source_input_sha256", "source_evidence_sha256")},
        "assessments": [record], "schema_failures": [], "derived_verdict": check}


def test_grouped_response_preserves_every_span_and_authenticates_release_without_fake_calls():
    row = sample()
    request = request_for(row)
    raw = response(request)
    receipt = validate_consolidated_claim_review(raw, request)
    assert receipt["raw_response"] == raw
    assert len(receipt["expanded_claims"]) == len(request["target_units"]) > len(raw["groups"])
    assert "not_independent_review" in receipt["normalization_origin"]
    assert request["messages"] == row["messages"]
    assert focused_review_request(receipt) is None
    check, audit = audit_for(row, receipt)
    assert assessment_from_verdict(row, check, evidence_ref="offline-consolidated", method="llm_claim_review",
                                   claim_review=audit)["accepted"]
    tampered = deepcopy(audit)
    tampered["assessments"][0]["checkpoint"]["response_sha256"] = "0" * 64
    with pytest.raises(ValueError, match="stale_consolidated_review_checkpoint"):
        validate_consolidated_claim_audit(row, check, tampered)


def test_missing_duplicate_fabricated_evidence_and_assumptions_do_not_pass():
    request = request_for()
    original = response(request)
    for change in (lambda raw: raw["groups"][0]["unit_ids"].pop(),
                   lambda raw: raw["groups"][0]["unit_ids"].append(raw["groups"][0]["unit_ids"][0])):
        raw = deepcopy(original)
        change(raw)
        with pytest.raises(ValueError, match="invalid_consolidated_unit_coverage"):
            validate_consolidated_claim_review(raw, request)
    raw = deepcopy(original)
    raw["groups"][0]["basis"]["evidence"][0]["quote"] = "The source never says this."
    with pytest.raises(ValueError, match="fabricated_claim_review_evidence_quote"):
        validate_consolidated_claim_review(raw, request)
    raw = deepcopy(original)
    raw["groups"][0]["premises"] = [{"origin": "assumption", "use": "support", "quote": "The power is already off."}]
    receipt = validate_consolidated_claim_review(raw, request)
    assert not receipt["accepted"]
    assert receipt["condition_guard"]["needs_review"]
    assert focused_review_request(receipt) is not None
    check, audit = audit_for(sample(), receipt)
    assessment = assessment_from_verdict(sample(), check, evidence_ref="unproved", method="llm_claim_review", claim_review=audit)
    assert assessment["review_status"] == "pending_review" and not assessment["accepted"]
    feedback = claim_repair_feedback(sample(), {"teacher_evidence": SOURCE}, audit, include_quality_feedback=True)
    assert len(feedback["disputes"]) == len(request["target_units"])
    assert all(item["condition_provenance"]["required_assumptions"] for item in feedback["disputes"])


def test_targeted_resolution_only_revisits_explicit_dispute_and_keeps_other_claims():
    row = sample()
    request = request_for(row)
    raw = response(request)
    first, *rest = raw["groups"][0]["unit_ids"]
    other = deepcopy(raw["groups"][0])
    raw["groups"][0].update(unit_ids=[first], status="insufficient")
    other["unit_ids"] = rest
    raw["groups"].append(other)
    initial = validate_consolidated_claim_review(raw, request)
    targeted = focused_review_request(initial)
    assert targeted["review_unit_ids"] == [first]
    for key in ("messages", "target_fields", "target_units", "source_evidence"):
        assert targeted[key] == request[key]
    focused = validate_consolidated_claim_review(response(targeted), targeted)
    final = resolve_consolidated_review(initial, focused)
    assert final["accepted"]
    assert final["expanded_claims"][1:] == initial["expanded_claims"][1:]
    check, audit = audit_for(row, initial, focused)
    assert assessment_from_verdict(row, check, evidence_ref="resolved", method="llm_claim_review", claim_review=audit)["accepted"]
    assert not claim_repair_feedback(row, {"teacher_evidence": SOURCE}, audit)["disputes"]
    raw["groups"][0]["status"] = "refuted"
    refuted = validate_consolidated_claim_review(raw, request)
    _, before = audit_for(row, refuted)
    assert claim_review_refutations(before)
    second_request = focused_review_request(refuted)
    second = validate_consolidated_claim_review(response(second_request), second_request)
    _, after = audit_for(row, refuted, second)
    assert not claim_review_refutations(after)
    with pytest.raises(ValueError, match="focused_review_cannot_recurse"):
        focused_review_request(focused)
    wrong = deepcopy(focused)
    wrong["raw_response"]["groups"][0]["unit_ids"].append(rest[0])
    with pytest.raises(ValueError, match="invalid_consolidated_unit_coverage"):
        resolve_consolidated_review(initial, wrong)


def test_multiturn_stays_prefix_isolated_and_prior_assistant_cannot_certify_facts():
    row = sample(True)
    requests = consolidated_review_requests("multiturn", row, {"teacher_evidence": SOURCE}, True)
    assert len(requests) == 2
    assert "FUTURE_ONLY_736" not in str(requests[0])
    raw = response(requests[1])
    raw["groups"][0]["basis"] = {"kind": "context", "derivation": "An earlier assistant asserted the prerequisite.",
        "evidence": [{"message_index": 1, "field": "content", "quote": row["messages"][1]["content"]}]}
    with pytest.raises(ValueError, match="condition_historical_assistant_not_evidence"):
        validate_consolidated_claim_review(raw, requests[1])


def test_focused_clearance_cannot_erase_initial_learner_context_rejection():
    row = sample()
    request = request_for(row)
    raw = response(request)
    raw["groups"][0]["status"] = "insufficient"
    raw["verdict"].update(keep=False, reason="The learner cannot see the passage needed by the user question.")
    raw["verdict"]["scores"]["instruction"] = 2
    raw["limitations"] = ["Teacher-only material does not supply the learner's missing passage."]
    initial = validate_consolidated_claim_review(raw, request)
    focused_request = focused_review_request(initial)
    focused = validate_consolidated_claim_review(response(focused_request), focused_request)
    resolved = resolve_consolidated_review(initial, focused)
    assert not resolved["accepted"] and not resolved["condition_guard"]["needs_review"]
    assert resolved["verdict"]["scores"]["instruction"] == 2
    assert raw["verdict"]["reason"] in resolved["verdict"]["reason"]
    assert initial["raw_response"] == raw
    check, audit = audit_for(row, initial, focused)
    assert not assessment_from_verdict(row, check, evidence_ref="learner-context-defect",
        method="llm_claim_review", claim_review=audit)["accepted"]
