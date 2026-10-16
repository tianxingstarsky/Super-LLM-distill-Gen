"""A real candidate-visible challenge cannot silently become a blind oracle."""
from copy import deepcopy

import pytest

from lib.domain import claim_review as v1
from lib.domain.adversarial_claim_review import (challenge_review_requests, validate_challenge_review,
    adversarial_claim_review_requests, validate_adversarial_claim_review, merge_adversarial_claim_reviews)


@pytest.fixture(scope="session", autouse=True)
def mock_llm_server():
    yield


def candidate():
    return {"messages": [
        {"role": "user", "content": "Can I inspect the saved state before writing a replacement?"},
        {"role": "assistant", "content": "**Inspect the state.**\n\nKeep its version recorded.",
         "reasoning_content": "Reading existing state does not require a later write."},
        {"role": "user", "content": "There is no concurrent writer."},
        {"role": "assistant", "content": "A snapshot can be inspected before replacement.",
         "reasoning_content": "The existing state remains available during the operation."}]}


def source():
    return {"teacher_evidence": "The saved state exists before the replacement is written."}


def basis():
    return {"kind": "logic", "evidence": [],
            "derivation": "Reading an already saved version does not depend on a later write being completed."}


def grade():
    return {"keep": True, "grounded": True, "reasoning_valid": True, "correctness": 5,
            "scores": {key: 5 for key in ("correctness", "reasoning", "grounding", "instruction", "safety")},
            "reason": "The candidate uses the available state without inventing a completed replacement."}


def issue(request, *, severity="material"):
    return {"id": "dependency-1", "unit_id": request["target_units"][0]["id"], "severity": severity,
            "problem": "The asserted dependency may require an unavailable state.",
            "counterexample": "A saved earlier version can remain available before the later write.", "basis": basis()}


def challenge_response(request, issues=None):
    return {"claim_review_protocol_version": 4, "phase": "claim_challenge",
            "scope_sha256": request["scope"]["scope_sha256"],
            "issues": [] if issues is None else issues, "limitations": []}


def requests(body=None, *, issues=False, severity="material", requirements=None):
    body = candidate() if body is None else body
    challenges = challenge_review_requests("multiturn", body, source(), True, requirements)
    receipts = [validate_challenge_review(challenge_response(request,
                [issue(request, severity=severity)] if issues else []), request) for request in challenges]
    return body, challenges, receipts, adversarial_claim_review_requests("multiturn", body,
        source(), True, requirements, challenge_reviews=receipts)


def response(request):
    return {"claim_review_protocol_version": 4, "scope_sha256": request["scope"]["scope_sha256"],
        "coverage": "complete", "checked_unit_ids": [unit["id"] for unit in request["target_units"]],
        "claim_checks": [{"unit_id": unit["id"], "status": "supported", "severity": "minor", "basis": basis()}
                         for unit in request["target_units"]],
        "challenge_checks": [{"id": item["id"], "unit_id": item["unit_id"], "decision": "dismissed", "basis": basis()}
                             for item in request["challenge_review"]["issues"]], "verdict": grade()}


def rehash(request):
    request["scope"].pop("scope_sha256", None)
    request["scope"]["scope_sha256"] = v1._digest(request)


def test_candidate_visible_challenge_and_raw_four_are_separate_from_program_normalization():
    body, challenges, challenge_receipts, items = requests()
    assert challenges[0]["messages"][-1] == body["messages"][1]
    assert challenges[0]["phase"] == "claim_challenge"
    assert challenge_receipts[0]["raw_response"]["issues"] == []
    assert challenge_receipts[0]["semantic_verification"] == "reviewer_reported_not_independently_verified"
    receipts = []
    for request in items:
        raw = response(request)
        frozen = deepcopy(raw)
        receipt = validate_adversarial_claim_review(raw, request)
        assert raw == frozen == receipt["raw_response"]
        assert "quote" not in raw["claim_checks"][0]
        assert receipt["normalized_response"]["claim_review_protocol_version"] == 1
        assert {claim["quote"] for claim in receipt["normalized_response"]["claim_checks"]} == {
            unit["text"] for unit in request["target_units"]}
        receipts.append(receipt)
    assert merge_adversarial_claim_reviews(body, receipts)["keep"]


@pytest.mark.parametrize("mutation,error", [
    (lambda raw: raw.update(verdict=grade()), "invalid_challenge_review_response"),
    (lambda raw: raw.update(phase="blind_reference"), "invalid_challenge_review_response"),
    (lambda raw: raw.update(claim_review_protocol_version=True), "invalid_challenge_review_response"),
    (lambda raw: raw.update(scope_sha256="0" * 64), "stale_challenge_review_response"),
    (lambda raw: raw.update(issues=None), "invalid_challenge_review_issues"),
    (lambda raw: raw["issues"].append(deepcopy(raw["issues"][0])), "invalid_challenge_review_issue_id"),
    (lambda raw: raw["issues"][0].update(unit_id="m9u0001"), "invalid_challenge_review_unit_id"),
    (lambda raw: raw["issues"][0].update(counterexample=" "), "missing_challenge_review_counterexample"),
    (lambda raw: raw.update(limitations=[None]), "missing_challenge_review_limitation"),
])
def test_challenger_shape_and_current_unit_binding_are_strict(mutation, error):
    request = challenge_review_requests("multiturn", candidate(), source())[0]
    raw = challenge_response(request, [issue(request)])
    mutation(raw)
    with pytest.raises(ValueError, match=error):
        validate_challenge_review(raw, request)


@pytest.mark.parametrize("evidence", [
    {"quote": "Invented source sentence.", "source_path": ["teacher_evidence"]},
    {"quote": "Future message.", "message_index": 3, "field": "content"},
])
def test_challenger_cannot_forge_source_or_cite_future_context(evidence):
    request = challenge_review_requests("multiturn", candidate(), source())[0]
    raw = challenge_response(request, [issue(request)])
    raw["issues"][0]["basis"] = {"kind": "logic", "evidence": [evidence], "derivation": basis()["derivation"]}
    with pytest.raises(ValueError, match="fabricated_claim_review_evidence_quote|invalid_claim_review_context_reference"):
        validate_challenge_review(raw, request)


def test_actual_source_citation_and_empty_challenges_are_both_allowed():
    request = challenge_review_requests("multiturn", candidate(), source())[0]
    raw = challenge_response(request, [issue(request)])
    raw["issues"][0]["basis"] = {"kind": "source", "evidence": [
        {"quote": source()["teacher_evidence"], "source_path": ["teacher_evidence"]}],
        "derivation": "The source describes an existing saved version before replacement."}
    assert validate_challenge_review(raw, request)["structure_verified"]
    assert validate_challenge_review(challenge_response(request), request)["raw_response"]["issues"] == []


@pytest.mark.parametrize("change", ["candidate", "source", "requirements", "receipt"])
def test_arbitration_cannot_reuse_a_challenge_for_other_inputs(change):
    body, _, receipts, _ = requests()
    evidence, requirements = source(), None
    if change == "candidate":
        body["messages"][1]["content"] += " Changed."
    elif change == "source":
        evidence["teacher_evidence"] += " Changed."
    elif change == "requirements":
        requirements = {"learning_goal": "reading_comprehension"}
    else:
        receipts[0]["raw_response"]["limitations"].append("Changed after hashing.")
    with pytest.raises(ValueError, match="stale_challenge_review_receipt|altered_challenge_review_receipt"):
        adversarial_claim_review_requests("multiturn", body, evidence, True, requirements, challenge_reviews=receipts)


@pytest.mark.parametrize("location", ["checked_unit_ids", "claim_checks"])
def test_empty_challenge_does_not_bypass_full_current_candidate_coverage(location):
    _, _, _, items = requests()
    raw = response(items[0])
    raw[location].pop()
    with pytest.raises(ValueError, match="invalid_adversarial_unit_coverage"):
        validate_adversarial_claim_review(raw, items[0])


def test_cross_turn_id_wrong_offsets_and_bound_challenge_hashes_are_rejected():
    _, _, _, items = requests()
    raw = response(items[1])
    raw["claim_checks"][0]["unit_id"] = items[0]["target_units"][0]["id"]
    with pytest.raises(ValueError, match="invalid_adversarial_unit_id"):
        validate_adversarial_claim_review(raw, items[1])
    bad = deepcopy(items[0])
    bad["target_units"][0]["start"] += 1
    rehash(bad)
    with pytest.raises(ValueError, match="invalid_review_target_units"):
        validate_adversarial_claim_review(response(bad), bad)
    bad = deepcopy(items[0])
    bad["scope"]["challenge_review_sha256"] = "0" * 64
    rehash(bad)
    with pytest.raises(ValueError, match="invalid_adversarial_review_request"):
        validate_adversarial_claim_review(response(bad), bad)


@pytest.mark.parametrize("severity", ["minor", "material"])
def test_confirmed_issue_cannot_pass_even_with_original_all_five_scores(severity):
    _, _, _, items = requests(issues=True, severity=severity)
    raw = response(items[0])
    raw["claim_checks"][0].update(status="refuted", severity=severity)
    raw["challenge_checks"][0]["decision"] = "confirmed"
    receipt = validate_adversarial_claim_review(raw, items[0])
    assert raw["verdict"]["keep"] and raw["verdict"]["correctness"] == 5
    assert not receipt["verdict"]["keep"] and not receipt["accepted"]
    assert receipt["raw_response"] == raw


def test_refuted_minor_without_challenge_still_vetoes_release():
    _, _, _, items = requests()
    raw = response(items[0])
    raw["claim_checks"][0]["status"] = "refuted"
    receipt = validate_adversarial_claim_review(raw, items[0])
    assert receipt["normalized_receipt"]["verdict"]["keep"]
    assert not receipt["verdict"]["keep"]


@pytest.mark.parametrize("severity,blocked", [("material", True), ("minor", False)])
def test_unresolved_issue_uses_original_severity_and_dismissal_is_not_automatic_truth(severity, blocked):
    _, _, _, items = requests(issues=True, severity=severity)
    raw = response(items[0])
    raw["challenge_checks"][0]["decision"] = "unresolved"
    receipt = validate_adversarial_claim_review(raw, items[0])
    assert receipt["verdict"]["keep"] is not blocked
    raw["challenge_checks"][0].update(decision="dismissed", basis={"kind": "logic", "evidence": [], "derivation": "correct"})
    with pytest.raises(ValueError, match="vacuous_adversarial_derivation"):
        validate_adversarial_claim_review(raw, items[0])


@pytest.mark.parametrize("change,error", [
    (lambda raw: raw["challenge_checks"].clear(), "invalid_adversarial_challenge_checks"),
    (lambda raw: raw["challenge_checks"][0].update(unit_id="m9u0001"), "unbound_adversarial_challenge_check"),
    (lambda raw: raw["challenge_checks"][0].update(decision="confirmed"), "unconfirmed_adversarial_refutation"),
])
def test_each_challenge_is_decided_once_on_its_original_unit(change, error):
    _, _, _, items = requests(issues=True)
    raw = response(items[0])
    change(raw)
    with pytest.raises(ValueError, match=error):
        validate_adversarial_claim_review(raw, items[0])


def test_equal_literal_units_retain_separate_raw_checks_and_strictest_normalization():
    body = candidate()
    body["messages"][1]["content"] = "Repeated.\n\nRepeated."
    _, _, _, items = requests(body)
    raw = response(items[0])
    raw["claim_checks"][1]["status"] = "refuted"
    receipt = validate_adversarial_claim_review(raw, items[0])
    assert len(raw["claim_checks"]) == 3
    assert len(receipt["normalized_response"]["claim_checks"]) == 2
    assert not receipt["accepted"]


def test_material_insufficiency_is_not_lost_when_equal_unit_has_a_minor_refutation():
    body = candidate()
    body["messages"][1]["content"] = "Repeated.\n\nRepeated."
    _, _, _, items = requests(body)
    raw = response(items[0])
    raw["claim_checks"][0].update(status="insufficient", severity="material")
    raw["claim_checks"][1]["status"] = "refuted"
    receipt = validate_adversarial_claim_review(raw, items[0])
    assert not receipt["accepted"] and receipt["turn_verdict"]["critical_error"]
    assert not receipt["verdict"]["reasoning_valid"]


@pytest.mark.parametrize("mutation,error", [
    (lambda raw: raw.update(claim_review_protocol_version=3), "invalid_adversarial_review_response"),
    (lambda raw: raw.update(phase="claim_adjudication"), "invalid_adversarial_review_response"),
    (lambda raw: raw.update(scope_sha256="0" * 64), "stale_adversarial_review_response"),
    (lambda raw: raw["checked_unit_ids"].append(raw["checked_unit_ids"][0]), "invalid_adversarial_unit_coverage"),
    (lambda raw: raw["claim_checks"][0].update(quote="Model-written quote."), "invalid_adversarial_claim"),
    (lambda raw: raw["claim_checks"][0].update(status="probably"), "invalid_claim_review_claim_status"),
    (lambda raw: raw["claim_checks"][0].update(basis={"kind": "logic", "evidence": [], "derivation": "correct"}),
     "vacuous_adversarial_derivation"),
])
def test_arbiter_shape_and_derivations_are_strict(mutation, error):
    _, _, _, items = requests()
    raw = response(items[0])
    mutation(raw)
    with pytest.raises(ValueError, match=error):
        validate_adversarial_claim_review(raw, items[0])


def test_new_protocol_cot_payload_keeps_legacy_original_representation_bound():
    body = {"question": "Can I read the saved state?", "answer": "Yes, inspect the recorded state.",
            "reasoning": ["The saved state exists.", "A future replacement is a different operation."]}
    challenges = challenge_review_requests("cot", body, source(), True)
    opinions = [validate_challenge_review(challenge_response(request), request) for request in challenges]
    items = adversarial_claim_review_requests("cot", body, source(), True, challenge_reviews=opinions)
    receipts = [validate_adversarial_claim_review(response(request), request) for request in items]
    assert merge_adversarial_claim_reviews(body, receipts)["keep"]
    changed = deepcopy(body)
    changed["reasoning"] = "\n".join(changed["reasoning"])
    with pytest.raises(ValueError, match="stale_claim_review_candidate"):
        merge_adversarial_claim_reviews(changed, receipts)


def test_merge_rejects_mixed_frozen_requirements_even_when_original_candidate_is_identical():
    body, _, _, first = requests()
    _, _, _, second = requests(requirements={"learning_goal": "reading_comprehension"})
    receipts = [validate_adversarial_claim_review(response(first[0]), first[0]),
                validate_adversarial_claim_review(response(second[1]), second[1])]
    with pytest.raises(ValueError, match="inconsistent_claim_review_scopes"):
        merge_adversarial_claim_reviews(body, receipts)


def test_merge_checks_every_turn_and_original_payload_and_receipt_integrity():
    body, _, _, items = requests()
    receipts = [validate_adversarial_claim_review(response(request), request) for request in items]
    assert merge_adversarial_claim_reviews(body, receipts)["keep"]
    with pytest.raises(ValueError, match="incomplete_claim_review_turns"):
        merge_adversarial_claim_reviews(body, receipts[:1])
    bad = deepcopy(receipts)
    bad[0]["verdict"]["correctness"] = 1
    with pytest.raises(ValueError, match="altered_adversarial_review_receipt"):
        merge_adversarial_claim_reviews(body, bad)
    changed = deepcopy(body)
    changed["messages"][0]["content"] += " Changed."
    with pytest.raises(ValueError, match="stale_claim_review_candidate"):
        merge_adversarial_claim_reviews(changed, receipts)
