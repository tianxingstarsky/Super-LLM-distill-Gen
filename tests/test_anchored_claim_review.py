"""Program-owned current units cannot cite another turn or omit list items."""
from copy import deepcopy

import pytest

from lib.domain.anchored_claim_review import (claim_review_requests_v3,
    validate_claim_review_v3, merge_claim_reviews_v3)
from lib.domain.blind_claim_review import blind_review_requests, validate_blind_review
from tests.test_blind_claim_review import blind_response
from tests.test_claim_review import candidate, source, grade


@pytest.fixture(scope="session", autouse=True)
def mock_llm_server():
    yield


def requests(body=None, *, partial=False):
    body = candidate() if body is None else body
    blind = []
    for request in blind_review_requests("multiturn", body, source()):
        raw = blind_response(request)
        if partial:
            raw["coverage"] = "partial"
        blind.append(validate_blind_review(raw, request))
    return body, claim_review_requests_v3("multiturn", body, source(), blind_reviews=blind)


def response(request):
    return {"claim_review_protocol_version": 3, "scope_sha256": request["scope"]["scope_sha256"],
        "coverage": "complete", "checked_unit_ids": [unit["id"] for unit in request["target_units"]],
        "claim_checks": [{"unit_id": unit["id"], "status": "supported", "severity": "minor",
            "basis": {"kind": "logic", "evidence": [],
                      "derivation": "New-training constraints do not imply that previously stored checkpoints are inaccessible."}}
            for unit in request["target_units"]],
        "reference_checks": [{"id": point["id"], "relation": "not_claimed", "unit_id": None,
            "reason": "This background statement is not independently asserted by the current candidate."}
            for point in request["reference_review"]["reference_points"]], "verdict": grade()}


def test_current_ids_map_to_full_original_markdown_without_model_quotes():
    body = candidate()
    body["messages"][1]["content"] = "**Inspect first**\n\n- Record after inspection."
    _, items = requests(body)
    receipts = []
    for request in items:
        raw = response(request)
        frozen = deepcopy(raw)
        receipt = validate_claim_review_v3(raw, request)
        assert raw == frozen == receipt["raw_response"]
        assert all("quote" not in claim for claim in raw["claim_checks"])
        assert {claim["quote"] for claim in receipt["normalized_response"]["claim_checks"]} == {
            unit["text"] for unit in request["target_units"]}
        receipts.append(receipt)
    assert merge_claim_reviews_v3(body, receipts)["keep"]


def test_earlier_turn_ids_are_structurally_invalid_even_if_they_exist_in_history():
    _, items = requests()
    raw = response(items[1])
    raw["claim_checks"][0]["unit_id"] = items[0]["target_units"][0]["id"]
    with pytest.raises(ValueError, match="invalid_anchored_unit_id"):
        validate_claim_review_v3(raw, items[1])


@pytest.mark.parametrize("location", ["checked_unit_ids", "claim_checks"])
def test_every_current_unit_must_be_checked_not_just_a_correct_heading(location):
    _, items = requests()
    raw = response(items[0])
    raw[location].pop()
    with pytest.raises(ValueError, match="invalid_anchored_unit_coverage"):
        validate_claim_review_v3(raw, items[0])


def test_partial_blind_notes_do_not_make_a_fully_checked_conditional_answer_wrong():
    body, items = requests(partial=True)
    receipts = [validate_claim_review_v3(response(request), request) for request in items]
    assert receipts[0]["normalized_receipt"]["verdict"]["keep"] is False  # literal historical v2 rule
    assert receipts[0]["verdict"]["keep"] is True  # explicit new v3 coverage policy
    assert receipts[0]["request"]["reference_review"]["coverage"] == "partial"
    assert merge_claim_reviews_v3(body, receipts)["keep"] is True


def test_partial_candidate_coverage_and_material_refutation_still_veto_release():
    _, items = requests(partial=True)
    raw = response(items[0])
    raw["coverage"] = "partial"
    assert not validate_claim_review_v3(raw, items[0])["accepted"]
    raw = response(items[0])
    raw["claim_checks"][0].update(status="refuted", severity="material")
    assert not validate_claim_review_v3(raw, items[0])["accepted"]


def test_normalized_source_spans_and_raw_grade_cannot_be_changed_after_review():
    body, items = requests()
    receipts = [validate_claim_review_v3(response(request), request) for request in items]
    receipts[0]["normalized_response"]["claim_checks"][0]["quote"] = "forged text"
    with pytest.raises(ValueError, match="altered_anchored_claim_review_receipt"):
        merge_claim_reviews_v3(body, receipts)


def test_same_field_repeated_units_retain_the_strictest_reported_decision():
    body = candidate()
    body["messages"][1]["content"] = "Repeat this statement.\n\nRepeat this statement."
    _, items = requests(body)
    raw = response(items[0])
    raw["claim_checks"][1].update(status="refuted", severity="material")
    receipt = validate_claim_review_v3(raw, items[0])
    assert len(receipt["raw_response"]["claim_checks"]) > len(receipt["normalized_response"]["claim_checks"])
    assert not receipt["accepted"]
