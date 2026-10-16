"""Condition provenance guards new receipts without rewriting model verdicts."""
from copy import deepcopy

import jsonschema
import pytest

from lib.domain.adversarial_claim_review import (
    adversarial_claim_review_requests, challenge_review_requests,
    merge_adversarial_claim_reviews, validate_adversarial_claim_review,
    validate_challenge_review,
)
from lib.infrastructure.claim_review_wire_schema import claim_review_response_schema


@pytest.fixture(scope="session", autouse=True)
def mock_llm_server():
    # No network requests, including the repository's mock HTTP probe.
    yield


def payload():
    return {"messages": [
        {"role": "user", "content": "We downloaded the files. Which local option should I use?"},
        {"role": "assistant", "content": "The second option is local reading.",
         "reasoning_content": "Local reading uses saved files."},
        {"role": "user", "content": "Use the second option. There is no concurrent writer."},
        {"role": "assistant", "content": "If files were downloaded, local reading is possible.\n\nTry a snapshot.",
         "reasoning_content": "You can choose a procedure before execution. You validate its result after execution."},
    ]}


SOURCE = {"teacher_evidence_blocks": ["Saved copies are readable before a replacement is written."]}


def inventory(request):
    raw = {"claim_review_protocol_version": 4, "phase": "claim_challenge",
           "scope_sha256": request["scope"]["scope_sha256"], "issues": [], "limitations": []}
    if request.get("condition_contract_version") == 1:
        raw["condition_ledger"] = [{"unit_id": unit["id"], "premises": []}
                                   for unit in request["target_units"]]
    return raw


def basis():
    return {"kind": "logic", "evidence": [],
            "derivation": "Reading an already saved copy does not require a replacement write."}


def grade(request):
    raw = {"claim_review_protocol_version": 4, "scope_sha256": request["scope"]["scope_sha256"],
           "coverage": "complete", "checked_unit_ids": [unit["id"] for unit in request["target_units"]],
           "claim_checks": [{"unit_id": unit["id"], "status": "supported", "severity": "minor", "basis": basis()}
                            for unit in request["target_units"]],
           "challenge_checks": [], "verdict": {"keep": True, "grounded": True, "reasoning_valid": True,
               "correctness": 5, "scores": {key: 5 for key in
                   ("correctness", "reasoning", "grounding", "instruction", "safety")},
               "reason": "The proposed operations use saved files under the visible conditions."}}
    if request.get("condition_contract_version") == 1:
        for item in raw["claim_checks"]:
            item["premises"] = []
    return raw


def requests(version=1, body=None):
    body = payload() if body is None else body
    first = challenge_review_requests("multiturn", body, SOURCE, True, condition_contract_version=version)
    first_raws = [inventory(request) for request in first]
    receipts = [validate_challenge_review(raw, request) for raw, request in zip(first_raws, first)]
    second = adversarial_claim_review_requests("multiturn", body, SOURCE, True,
        challenge_reviews=receipts, condition_contract_version=version)
    return body, first, first_raws, receipts, second, [grade(request) for request in second]


def schema_validate(request, value):
    schema = claim_review_response_schema(request)
    jsonschema.Draft202012Validator.check_schema(schema)
    jsonschema.Draft202012Validator(schema).validate(value)


def premise(origin, use, quote, **locator):
    return {"origin": origin, "use": use, "quote": quote, **locator}


@pytest.mark.parametrize("use", ["support", "task_condition", "reference_resolution", "counterexample"])
def test_assumed_support_cannot_release_an_all_five_raw_verdict(use):
    body, _, _, _, review_requests, raws = requests()
    request, raw = review_requests[-1], raws[-1]
    item = raw["claim_checks"][0]
    item["premises"] = [premise("assumption", use, "The reader can see only the newly created batch.")]
    frozen = deepcopy(raw)
    schema_validate(request, raw)
    receipt = validate_adversarial_claim_review(raw, request)
    assert raw == frozen == receipt["raw_response"]
    assert receipt["raw_response"]["verdict"]["keep"] is True
    assert all(score == 5 for score in receipt["raw_response"]["verdict"]["scores"].values())
    assert receipt["verdict"]["keep"] is False and receipt["accepted"] is False
    guard = receipt["condition_guard"]
    assert guard["needs_review"] is True
    assert guard["unresolved_unit_ids"] == [item["unit_id"]]
    check = guard["checks"][0]
    assert check["reported_status"] == "supported" and check["effective_status"] == "insufficient"
    assert check["unsupported_assumption_used_for_support"] is True
    assert receipt["normalized_response"]["claim_checks"][0]["status"] == "insufficient"
    all_receipts = [validate_adversarial_claim_review(value, req) for value, req in zip(raws, review_requests)]
    assert merge_adversarial_claim_reviews(body, all_receipts)["keep"] is False


@pytest.mark.parametrize("severity", ["minor", "material"])
def test_reported_insufficiency_needs_review_even_without_an_added_assumption(severity):
    _, _, _, _, review_requests, raws = requests()
    raw = raws[-1]
    raw["claim_checks"][0].update(status="insufficient", severity=severity)
    receipt = validate_adversarial_claim_review(raw, review_requests[-1])
    assert receipt["raw_response"] == raw
    assert receipt["condition_guard"]["needs_review"]
    assert not receipt["accepted"] and not receipt["verdict"]["keep"]
    assert not receipt["condition_guard"]["checks"][0]["unsupported_assumption_used_for_support"]


def test_real_visible_conditions_and_literal_candidate_scope_remain_eligible():
    body, _, _, _, review_requests, raws = requests()
    request, raw = review_requests[-1], raws[-1]
    identifier = request["target_units"][0]["id"]
    raw["claim_checks"][0]["premises"] = [
        premise("candidate", "task_condition", "If files were downloaded", unit_id=identifier),
        premise("context", "task_condition", "We downloaded the files.", message_index=0, field="content"),
        premise("context", "task_condition", "There is no concurrent writer.", message_index=2, field="content"),
        premise("source", "support", "Saved copies are readable", source_path=["teacher_evidence_blocks", 0]),
        premise("context", "reference_resolution", "The second option is local reading.", message_index=1, field="content"),
    ]
    schema_validate(request, raw)
    receipt = validate_adversarial_claim_review(raw, request)
    assert receipt["accepted"] and receipt["verdict"]["keep"]
    assert not receipt["condition_guard"]["needs_review"]
    assert receipt["condition_guard"]["semantic_verification"] == "provenance_only_not_entailment"
    all_receipts = [validate_adversarial_claim_review(value, req) for value, req in zip(raws, review_requests)]
    assert merge_adversarial_claim_reviews(body, all_receipts)["keep"]


def test_inventory_records_a_new_assumption_without_judging_or_upgrading_it():
    _, first, first_raws, _, _, _ = requests()
    request, raw = first[-1], first_raws[-1]
    raw["condition_ledger"][0]["premises"] = [premise("assumption", "task_condition", "A new exclusive reader is available.")]
    frozen = deepcopy(raw)
    schema_validate(request, raw)
    receipt = validate_challenge_review(raw, request)
    assert raw == frozen == receipt["raw_response"]
    assert len(receipt["condition_ledger"]["required_assumptions"]) == 1
    assert receipt["raw_response"]["issues"] == []
    assert "verdict" not in receipt and "accepted" not in receipt


def test_first_inventory_cannot_supply_a_challenge_for_the_arbiter_to_defend():
    _, first, first_raws, _, _, _ = requests()
    request, raw = first[-1], first_raws[-1]
    raw["issues"] = [{"id": "invented-judgment", "unit_id": request["target_units"][0]["id"],
                      "severity": "material", "problem": "An asserted problem.",
                      "counterexample": "An asserted counterexample.", "basis": basis()}]
    with pytest.raises(ValueError, match="condition_inventory_contains_verdict"):
        validate_challenge_review(raw, request)
    with pytest.raises(jsonschema.ValidationError):
        schema_validate(request, raw)


@pytest.mark.parametrize("change,error", [
    (lambda raw: raw["condition_ledger"].pop(), "invalid_condition_ledger_coverage"),
    (lambda raw: raw["condition_ledger"].append(deepcopy(raw["condition_ledger"][0])), "invalid_condition_ledger_coverage"),
    (lambda raw: raw["condition_ledger"][1].update(unit_id=raw["condition_ledger"][0]["unit_id"]), "invalid_condition_ledger_coverage"),
    (lambda raw: raw["condition_ledger"][0].update(verdict="supported"), "invalid_condition_ledger_entry"),
    (lambda raw: raw.pop("condition_ledger"), "invalid_challenge_review_response"),
])
def test_every_current_inventory_unit_has_exactly_one_declaration(change, error):
    _, first, first_raws, _, _, _ = requests()
    raw = first_raws[-1]
    change(raw)
    with pytest.raises(ValueError, match=error):
        validate_challenge_review(raw, first[-1])


@pytest.mark.parametrize("stage", ["inventory", "grade"])
def test_forged_quoted_conditions_fail_domain_binding_even_with_valid_json_schema(stage):
    _, first, first_raws, _, second, second_raws = requests()
    request, raw = (first[-1], first_raws[-1]) if stage == "inventory" else (second[-1], second_raws[-1])
    rows = raw["condition_ledger"] if stage == "inventory" else raw["claim_checks"]
    rows[0]["premises"] = [premise("context", "task_condition", "Only today's batch is visible.", message_index=0, field="content")]
    schema_validate(request, raw)  # Schema describes shape; exact quote binding is domain-owned.
    validator = validate_challenge_review if stage == "inventory" else validate_adversarial_claim_review
    with pytest.raises(ValueError, match="fabricated_claim_review_evidence_quote"):
        validator(raw, request)


@pytest.mark.parametrize("bad_premise", [
    premise("candidate", "support", "If files were downloaded", unit_id="m3u0001"),
    premise("context", "support", "The second option is local reading.", message_index=1, field="content"),
    premise("context", "task_condition", "The second option is local reading.", message_index=1, field="content"),
    premise("context", "support", "Try a snapshot.", message_index=3, field="content"),
    premise("assumption", "support", "An invented premise.", source_path=[]),
])
def test_schema_and_domain_reject_invalid_provenance_roles_and_extra_locators(bad_premise):
    _, _, _, _, second, raws = requests()
    request, raw = second[-1], raws[-1]
    raw["claim_checks"][0]["premises"] = [bad_premise]
    with pytest.raises(ValueError):
        validate_adversarial_claim_review(raw, request)
    with pytest.raises(jsonschema.ValidationError):
        schema_validate(request, raw)


@pytest.mark.parametrize("change", [
    lambda raw: raw["claim_checks"][0].pop("premises"),
    lambda raw: raw["claim_checks"][0].update(premises={}),
    lambda raw: raw["claim_checks"][0].update(conditions=[]),
])
def test_premises_are_a_required_exact_field_only_in_the_new_grade_contract(change):
    _, _, _, _, second, raws = requests()
    raw = raws[-1]
    change(raw)
    with pytest.raises(ValueError):
        validate_adversarial_claim_review(raw, second[-1])
    with pytest.raises(jsonschema.ValidationError):
        schema_validate(second[-1], raw)


def test_legacy_requests_and_raw_receipts_keep_their_original_contract():
    body, first, first_raws, receipts, second, raws = requests(version=None)
    default_first = challenge_review_requests("multiturn", body, SOURCE, True)
    assert first == default_first
    for request, raw, receipt in zip(first, first_raws, receipts):
        assert "condition_contract_version" not in request
        assert "condition_ledger" not in raw and "condition_ledger" not in receipt
        assert set(raw) == {"claim_review_protocol_version", "phase", "scope_sha256", "issues", "limitations"}
        schema_validate(request, raw)
    for request, raw in zip(second, raws):
        assert "condition_contract_version" not in request
        assert all("premises" not in check for check in raw["claim_checks"])
        receipt = validate_adversarial_claim_review(raw, request)
        assert receipt["raw_response"] == raw and receipt["accepted"]
        assert "condition_guard" not in receipt
        schema_validate(request, raw)
    bad = deepcopy(raws[-1])
    bad["claim_checks"][0]["premises"] = []
    with pytest.raises(ValueError, match="invalid_adversarial_claim"):
        validate_adversarial_claim_review(bad, second[-1])


def test_opt_in_and_legacy_receipts_cannot_be_mixed_or_silently_reinterpreted():
    body, _, _, receipts, _, _ = requests()
    with pytest.raises(ValueError, match="stale_challenge_review_receipt"):
        adversarial_claim_review_requests("multiturn", body, SOURCE, True, challenge_reviews=receipts)
    body, _, _, legacy_receipts, _, _ = requests(version=None)
    with pytest.raises(ValueError, match="stale_challenge_review_receipt"):
        adversarial_claim_review_requests("multiturn", body, SOURCE, True, challenge_reviews=legacy_receipts,
            condition_contract_version=1)


def test_finer_units_separate_strategy_selection_and_output_validation_without_deciding_truth():
    body = {"messages": [{"role": "user", "content": "Explain the two dependencies."},
        {"role": "assistant", "content": "Choose a procedure before execution. Validate its result after execution.",
         "reasoning_content": "A procedure can be selected in advance. Its output can be inspected later."}]}
    _, first, first_raws, receipts, second, raws = requests(body=body)
    units = first[0]["target_units"]
    assert [unit["text"] for unit in units if unit["field"] == "content"] == [
        "Choose a procedure before execution.", "Validate its result after execution."]
    assert all(unit["text"] == first[0]["target_fields"][unit["field"]][unit["start"]:unit["end"]]
               for unit in units)
    assert first_raws[0]["issues"] == [] and "verdict" not in receipts[0]
    assert len(receipts[0]["condition_ledger"]["units"]) == len(units)
    # Acceptance here is only the mocked reviewer's opinion; the segmentation
    # itself introduces no error label, severity, or correctness judgment.
    assert validate_adversarial_claim_review(raws[0], second[0])["accepted"]
    legacy = challenge_review_requests("multiturn", body, SOURCE, True)[0]
    assert len(legacy["target_units"]) == 2 < len(units)


def test_empty_conditions_do_not_claim_detection_of_an_unreported_hidden_assumption():
    _, _, _, _, second, raws = requests()
    receipt = validate_adversarial_claim_review(raws[-1], second[-1])
    assert receipt["accepted"] and not receipt["condition_guard"]["needs_review"]
    assert receipt["condition_guard"]["semantic_verification"] == "provenance_only_not_entailment"
    assert receipt["semantic_verification"] == "reviewer_reported_not_independently_verified"


@pytest.mark.parametrize("kind", ["context", "logic"])
def test_historical_assistant_cannot_bypass_premise_roles_through_basis_evidence(kind):
    _, _, _, _, second, raws = requests()
    request, raw = second[-1], raws[-1]
    assert raw["claim_checks"][0]["premises"] == []
    reference = {"quote": "The second option is local reading.", "message_index": 1, "field": "content"}
    raw["claim_checks"][0]["basis"] = {"kind": kind, "evidence": [reference],
        "derivation": "The previous answer's assertion is being offered as factual support."}
    frozen = deepcopy(raw)
    with pytest.raises(ValueError, match="condition_historical_assistant_not_evidence"):
        validate_adversarial_claim_review(raw, request)
    with pytest.raises(jsonschema.ValidationError):
        schema_validate(request, raw)
    assert raw == frozen

    # Prior assistant text still provides a genuine referent. Its location
    # belongs in the explicitly limited premise channel, not external evidence.
    raw["claim_checks"][0]["basis"] = basis()
    raw["claim_checks"][0]["premises"] = [premise("context", "reference_resolution", **reference)]
    schema_validate(request, raw)
    receipt = validate_adversarial_claim_review(raw, request)
    assert receipt["accepted"] and not receipt["condition_guard"]["needs_review"]

    # The role guard is opt-in. Replaying historical protocol four does not
    # retroactively reject its old evidence contract or add new premise fields.
    _, _, _, _, legacy_second, legacy_raws = requests(version=None)
    legacy_request, legacy_raw = legacy_second[-1], legacy_raws[-1]
    legacy_raw["claim_checks"][0]["basis"] = deepcopy(frozen["claim_checks"][0]["basis"])
    schema_validate(legacy_request, legacy_raw)
    legacy_receipt = validate_adversarial_claim_review(legacy_raw, legacy_request)
    assert legacy_receipt["accepted"] and legacy_receipt["raw_response"] == legacy_raw
    assert "condition_guard" not in legacy_receipt


@pytest.mark.parametrize("kind", ["context", "logic"])
def test_the_new_basis_role_guard_still_allows_actual_user_context(kind):
    _, _, _, _, second, raws = requests()
    request, raw = second[-1], raws[-1]
    raw["claim_checks"][0]["basis"] = {"kind": kind, "evidence": [
        {"quote": "We downloaded the files.", "message_index": 0, "field": "content"}],
        "derivation": "The actual user supplies the completed download as a task condition."}
    schema_validate(request, raw)
    assert validate_adversarial_claim_review(raw, request)["accepted"]
