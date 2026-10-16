"""Presentation-only citations retain raw bytes and source span receipts."""
from copy import deepcopy

import pytest

from lib.domain.claim_review import claim_review_requests, merge_claim_reviews, validate_claim_review
from lib.domain.blind_claim_review import merge_claim_reviews_v2, validate_claim_review_v2
from tests.test_claim_review import candidate, response, source
from tests.test_blind_claim_review import linked_requests, grade_response


@pytest.fixture(scope="session", autouse=True)
def mock_llm_server():
    yield


def body():
    value = candidate()
    value["messages"][1]["content"] = "**If you move the check earlier** — compare the existing pool."
    return value


def strip_claim_markers(value):
    for claim in value["claim_checks"]:
        claim["quote"] = claim["quote"].replace("**", "")
    return value


def test_v1_opt_in_preserves_raw_response_and_exact_source_span():
    payload = body()
    requests = claim_review_requests("multiturn", payload, source())
    raw = strip_claim_markers(response(requests[0]))
    original = deepcopy(raw)
    with pytest.raises(ValueError, match="fabricated_claim_review_quote"):
        validate_claim_review(raw, requests[0])
    receipts = [validate_claim_review(raw, requests[0], quote_matching_version=1),
                validate_claim_review(response(requests[1]), requests[1], quote_matching_version=1)]
    assert raw == original == receipts[0]["raw_response"]
    binding = receipts[0]["quote_bindings"][0]
    assert binding["match"] == "markdown_emphasis"
    text = requests[0]["target_fields"]["content"]
    assert text[binding["source_start"]:binding["source_end"]] == binding["source_text"]
    assert "**" in binding["source_text"]
    assert merge_claim_reviews(payload, receipts)["keep"] is True
    receipts[0]["quote_bindings"][0]["source_start"] += 1
    with pytest.raises(ValueError, match="altered_claim_review_receipt"):
        merge_claim_reviews(payload, receipts)


def test_v2_claim_and_reference_bindings_are_recomputed_without_rewriting_raw():
    payload = body()
    payload["messages"][1]["reasoning_content"] = "**Existing records** do not disappear when training is limited."
    _, requests = linked_requests(payload)
    receipts = []
    for request in requests:
        raw = strip_claim_markers(grade_response(request))
        raw["reference_checks"][0]["quote"] = raw["reference_checks"][0]["quote"].replace("**", "")
        frozen = deepcopy(raw)
        receipt = validate_claim_review_v2(raw, request, quote_matching_version=1)
        assert receipt["raw_response"] == frozen == raw
        receipts.append(receipt)
    paths = [item["response_path"] for item in receipts[0]["quote_bindings"]]
    assert ["claim_checks", 0, "quote"] in paths
    assert ["reference_checks", 0, "quote"] in paths
    assert merge_claim_reviews_v2(payload, receipts)["keep"] is True
    receipts[0]["quote_bindings"][-1]["source_text"] = "invented"
    with pytest.raises(ValueError, match="altered_claim_review_v2_receipt"):
        merge_claim_reviews_v2(payload, receipts)


@pytest.mark.parametrize("bad", [True, 0, 2, "1"])
def test_quote_binding_policy_is_strictly_versioned(bad):
    request = claim_review_requests("multiturn", body(), source())[0]
    with pytest.raises(ValueError, match="invalid_claim_review_quote_matching_version"):
        validate_claim_review(response(request), request, quote_matching_version=bad)


def test_opt_in_does_not_accept_changed_words_or_relax_source_evidence():
    request = claim_review_requests("multiturn", body(), source())[0]
    raw = strip_claim_markers(response(request))
    raw["claim_checks"][0]["quote"] = raw["claim_checks"][0]["quote"].replace("existing", "future")
    with pytest.raises(ValueError, match="fabricated_claim_review_quote"):
        validate_claim_review(raw, request, quote_matching_version=1)
    raw = response(request)
    raw["claim_checks"][0]["basis"] = {"kind": "source", "evidence": [
        {"source_path": ["teacher_evidence"], "quote": "保留**统计条件**才能解释比例。"}],
        "derivation": "This citation must still match the literal source, not a rendered variant."}
    with pytest.raises(ValueError, match="fabricated_claim_review_evidence_quote"):
        validate_claim_review(raw, request, quote_matching_version=1)


def test_legacy_exact_receipt_shape_and_revalidation_remain_literal():
    request = claim_review_requests("multiturn", candidate(), source())[0]
    receipt = validate_claim_review(response(request), request)
    assert "quote_bindings" not in receipt and "quote_matching_version" not in receipt


def test_reference_policy_accepts_concrete_counterexample_without_promoting_reference_to_oracle():
    payload = candidate()
    _, requests = linked_requests(payload)
    receipts = []
    for request in requests:
        raw = grade_response(request)
        raw["reference_checks"][0].update(relation="reference_error", counter_evidence={
            "kind": "logic", "evidence": [],
            "derivation": "A stored control can be read without any new training. Therefore a ban on new training does not imply that stored controls are unavailable."})
        with pytest.raises(ValueError, match="unsubstantiated_claim_review_reference_error"):
            validate_claim_review_v2(raw, request)
        receipt = validate_claim_review_v2(raw, request, reference_policy_version=1)
        assert receipt["raw_response"] == raw
        receipts.append(receipt)
    assert merge_claim_reviews_v2(payload, receipts)["keep"] is True
    receipts[0]["reference_policy_version"] = True
    with pytest.raises(ValueError, match="invalid_claim_reference_policy_version"):
        merge_claim_reviews_v2(payload, receipts)


def test_reference_counterexample_requires_specific_reason_and_matching_supported_claim():
    _, requests = linked_requests()
    raw = grade_response(requests[0])
    raw["reference_checks"][0].update(relation="reference_error", counter_evidence={
        "kind": "logic", "evidence": [], "derivation": "looks good"})
    with pytest.raises(ValueError, match="vacuous_blind_review_derivation"):
        validate_claim_review_v2(raw, requests[0], reference_policy_version=1)
    raw["reference_checks"][0]["counter_evidence"]["derivation"] = (
        "Stored checkpoints can be read without retraining; lack of retraining is not lack of checkpoint access.")
    raw["claim_checks"][1]["status"] = "insufficient"
    with pytest.raises(ValueError, match="unsubstantiated_claim_review_reference_error"):
        validate_claim_review_v2(raw, requests[0], reference_policy_version=1)
