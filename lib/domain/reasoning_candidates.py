"""Bounded selection of exact candidates assessed by the existing reviewer.

The two reported axes are projections of a model verdict, not executable proof
or reinforcement learning rewards. The caller owns durable request evidence
and supplies the exact training payload, including every supervised turn.
No network, sampling, concurrency or retry loop is implemented here.
"""
from __future__ import annotations

from copy import deepcopy
import hashlib
import json
from math import isfinite

from lib.domain.turn_review import supervised_assistant_turns, turn_review_verdict
from lib.domain.workflow_quality import accepted, canonical, verdict


REASONING_CANDIDATES_VERSION = 1
MAX_REASONING_CANDIDATES = 6
_VERDICT_FIELDS = ("keep", "grounded", "reasoning_valid", "correctness", "scores", "reason")


def validate_reasoning_candidates(value=None) -> dict:
    """Validate an optional internal cap; absence preserves the existing limit.

    The cap shares the workflow's existing scheduling and bounded review work;
    it is not permission to multiply every existing retry by this number.
    """
    if value is None:
        value = {}
    if (not isinstance(value, dict) or set(value) - {"candidate_limit", "selection"}
            or value.get("selection", "first_passing") != "first_passing"):
        raise ValueError("invalid_reasoning_candidates")
    limit = value.get("candidate_limit")
    if "candidate_limit" in value and (type(limit) is not int or not 1 <= limit <= MAX_REASONING_CANDIDATES):
        raise ValueError("invalid_reasoning_candidates")
    return {"selection": "first_passing", **({"candidate_limit": limit} if limit is not None else {})}


def reasoning_candidate_digest(payload: dict) -> str:
    """Bind assessment to the caller's exact training projection, not its ID."""
    if not isinstance(payload, dict) or not payload:
        raise ValueError("invalid_reasoning_candidate_payload")
    try:
        encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
                             allow_nan=False).encode("utf-8")
    except (TypeError, ValueError, UnicodeError) as error:
        raise ValueError("invalid_reasoning_candidate_payload") from error
    return hashlib.sha256(encoded).hexdigest()


def _reviewed_verdict(payload, check):
    check = deepcopy(verdict(check))
    if set(check) - set(_VERDICT_FIELDS) - {"turn_review_protocol_version", "aggregate_original", "turn_verdicts"}:
        raise ValueError("invalid_reasoning_candidate_verdict_fields")
    if "turn_review_protocol_version" in check and (
            type(check["turn_review_protocol_version"]) is not int or check["turn_review_protocol_version"] != 1):
        raise ValueError("invalid_reasoning_candidate_turn_version")
    if "aggregate_original" in check:
        original = check["aggregate_original"]
        if not isinstance(original, dict) or set(original) != set(_VERDICT_FIELDS):
            raise ValueError("invalid_reasoning_candidate_original_verdict")
        verdict(original)
    if "turn_verdicts" in check:
        # Existing merged checks carry diagnostic metadata. Recheck the actual
        # raw per-turn entries against this candidate before using aggregates.
        raw = {field: check[field] for field in _VERDICT_FIELDS}
        raw["turn_verdicts"] = check["turn_verdicts"]
        check = turn_review_verdict(raw, supervised_assistant_turns(payload))
    elif check["keep"] and supervised_assistant_turns(payload):
        # A successful aggregate cannot silently omit the scored assistant
        # turns. Early deterministic rejection can have no model turn grades.
        raise ValueError("missing_assistant_turn_reviews")
    return check


def _validated_claim_audit(payload, check, audit):
    """Bind derived decisions to exact raw claim responses and request keys."""
    if isinstance(audit, dict) and audit.get("version") == 4:
        return _validated_adversarial_claim_audit(payload, check, audit)
    if isinstance(audit, dict) and audit.get("version") in (2, 3):
        return _validated_blind_claim_audit(payload, check, audit)
    from lib.domain.claim_review import merge_claim_reviews
    verdict(check)
    expected_fields = {"version", "method", "candidate_sha256", "source_input_sha256",
        "source_evidence_sha256", "status", "assessments", "schema_failures", "derived_verdict"}
    if (not isinstance(audit, dict) or set(audit) != expected_fields
            or type(audit["version"]) is not int or audit["version"] != 1
            or audit["method"] != "prefix_only_model_claim_review" or audit["status"] != "complete"
            or audit["candidate_sha256"] != reasoning_candidate_digest(payload)
            or not isinstance(audit["assessments"], list) or not audit["assessments"]
            or not isinstance(audit["schema_failures"], list)):
        raise ValueError("invalid_reasoning_claim_audit")
    receipts, references_by_index = [], {}
    for item in audit["assessments"]:
        if not isinstance(item, dict) or set(item) != {"review", "checkpoint"}:
            raise ValueError("invalid_reasoning_claim_checkpoint")
        receipt, reference = item["review"], item["checkpoint"]
        if not isinstance(receipt, dict) or not isinstance(reference, dict):
            raise ValueError("invalid_reasoning_claim_checkpoint")
        request = receipt.get("request")
        if not isinstance(request, dict) or not isinstance(request.get("scope"), dict):
            raise ValueError("invalid_reasoning_claim_checkpoint")
        scope = request["scope"]
        fields = {"stage", "key", "checkpoint_sha256", "request_sha256", "review_request_sha256",
                  "response_sha256"}
        if (set(reference) - {"format_repair"} != fields
                or not isinstance(reference["stage"], str) or not reference["stage"]
                or not isinstance(reference["key"], list) or not reference["key"]):
            raise ValueError("invalid_reasoning_claim_checkpoint")
        call_data = request
        base_key = reference["key"]
        if "format_repair" in reference:
            feedback = reference["format_repair"]
            if (not isinstance(feedback, dict) or set(feedback) != {"code", "instruction"}
                    or feedback["code"] != "invalid_claim_review_response"
                    or not isinstance(feedback["instruction"], str) or not feedback["instruction"].strip()
                    or base_key[-2:] != ["format_repair", 1]):
                raise ValueError("invalid_reasoning_claim_checkpoint")
            call_data = {**request, "format_repair": feedback}
            base_key = base_key[:-2]
        if (base_key[-3:] != ["claim_review", scope.get("message_index"), scope.get("scope_sha256")]
                or reference["checkpoint_sha256"] != hashlib.sha256(
                        canonical(["call", reference["key"]]).encode("utf-8")).hexdigest()
                or reference["request_sha256"] != reasoning_candidate_digest(call_data)
                or reference["review_request_sha256"] != reasoning_candidate_digest(request)
                or reference["response_sha256"] != reasoning_candidate_digest(receipt.get("raw_response"))
                or scope.get("source_input_sha256") != audit["source_input_sha256"]
                or scope.get("source_evidence_sha256") != audit["source_evidence_sha256"]):
            raise ValueError("stale_reasoning_claim_checkpoint")
        receipts.append(receipt)
        references_by_index[scope["message_index"]] = (receipt, reference)
    failed_indices = set()
    for failure in audit["schema_failures"]:
        if (not isinstance(failure, dict) or set(failure) != {"message_index", "attempt", "code", "checkpoint"}
                or type(failure["message_index"]) is not int or failure["message_index"] in failed_indices
                or failure["message_index"] not in references_by_index or failure["attempt"] != 1
                or type(failure["attempt"]) is not int or not isinstance(failure["code"], str) or not failure["code"]):
            raise ValueError("invalid_reasoning_claim_schema_failure")
        failed_indices.add(failure["message_index"])
        receipt, completed_ref = references_by_index[failure["message_index"]]
        failed_ref = failure["checkpoint"]
        if ("format_repair" not in completed_ref or not isinstance(failed_ref, dict)
                or set(failed_ref) != set(completed_ref) - {"format_repair"}
                or failed_ref["stage"] != completed_ref["stage"]
                or failed_ref["key"] != completed_ref["key"][:-2]
                or failed_ref["checkpoint_sha256"] != hashlib.sha256(
                    canonical(["call", failed_ref["key"]]).encode("utf-8")).hexdigest()
                or failed_ref["request_sha256"] != completed_ref["review_request_sha256"]
                or failed_ref["review_request_sha256"] != completed_ref["review_request_sha256"]
                or not isinstance(failed_ref["response_sha256"], str)
                or len(failed_ref["response_sha256"]) != 64
                or any(char not in "0123456789abcdef" for char in failed_ref["response_sha256"])):
            raise ValueError("invalid_reasoning_claim_schema_failure")
    if any(("format_repair" in reference) != (index in failed_indices)
           for index, (_, reference) in references_by_index.items()):
        raise ValueError("incomplete_reasoning_claim_schema_history")
    merged = merge_claim_reviews(payload, receipts)
    if audit["derived_verdict"] != merged:
        raise ValueError("altered_reasoning_claim_aggregate")
    # A subsequent learner-context check can reject or lower this result. It
    # cannot turn a failed claim into success, raise grades, or rewrite turns.
    if (any(check[name] and not merged[name] for name in ("keep", "grounded", "reasoning_valid"))
            or check["correctness"] > merged["correctness"]
            or any(score > merged["scores"][name] for name, score in check["scores"].items())
            or check.get("turn_verdicts") != merged.get("turn_verdicts")):
        raise ValueError("reasoning_claim_gate_weakened")
    return deepcopy(audit)


def _validated_blind_claim_audit(payload, check, audit):
    """Revalidate both model phases without treating blind notes as truth."""
    from lib.domain.blind_claim_review import validate_blind_review, merge_claim_reviews_v2
    verdict(check)
    fields = {"version", "method", "candidate_sha256", "source_input_sha256", "source_evidence_sha256",
              "status", "blind_reviews", "assessments", "schema_failures", "derived_verdict"}
    if (set(audit) != fields or type(audit["version"]) is not int or audit["version"] not in {2, 3}
            or audit["method"] != ({2: "blind_reference_model_claim_review",
                3: "anchored_reference_model_claim_review"}[audit["version"]]) or audit["status"] != "complete"
            or audit["candidate_sha256"] != reasoning_candidate_digest(payload)
            or not isinstance(audit["blind_reviews"], list) or not audit["blind_reviews"]
            or not isinstance(audit["assessments"], list) or not audit["assessments"]
            or not isinstance(audit["schema_failures"], list)):
        raise ValueError("invalid_reasoning_blind_claim_audit")
    wrapped, blind_by_index = {}, {}
    for phase, collection in (("blind_reference", audit["blind_reviews"]), ("claim_review", audit["assessments"])):
        for item in collection:
            if not isinstance(item, dict) or set(item) != {"review", "checkpoint"}:
                raise ValueError("invalid_reasoning_blind_claim_checkpoint")
            receipt, reference = item["review"], item["checkpoint"]
            request = receipt.get("request") if isinstance(receipt, dict) else None
            if not isinstance(request, dict) or not isinstance(reference, dict):
                raise ValueError("invalid_reasoning_blind_claim_checkpoint")
            scope = request.get("scope", {})
            if not isinstance(scope, dict):
                raise ValueError("invalid_reasoning_blind_claim_checkpoint")
            index = scope.get("message_index")
            if type(index) is not int or (phase, index) in wrapped:
                raise ValueError("invalid_reasoning_blind_claim_checkpoint")
            ref_fields = {"stage", "key", "checkpoint_sha256", "request_sha256", "review_request_sha256", "response_sha256"}
            if (set(reference) - {"format_repair"} != ref_fields or not isinstance(reference["stage"], str)
                    or not reference["stage"] or not isinstance(reference["key"], list)):
                raise ValueError("invalid_reasoning_blind_claim_checkpoint")
            base_key, call_data = reference["key"], request
            if "format_repair" in reference:
                feedback = reference["format_repair"]
                code = ("invalid_blind_review_response" if phase == "blind_reference" else
                        "invalid_claim_review_v3_response" if audit["version"] == 3 else "invalid_claim_review_v2_response")
                if (not isinstance(feedback, dict) or set(feedback) != {"code", "instruction"}
                        or feedback["code"] != code or not isinstance(feedback["instruction"], str)
                        or not feedback["instruction"].strip() or base_key[-2:] != ["format_repair", 1]):
                    raise ValueError("invalid_reasoning_blind_claim_checkpoint")
                base_key, call_data = base_key[:-2], {**request, "format_repair": feedback}
            expected_key = ["blind_reference", request.get("target"), scope.get("scope_sha256")]
            if ((base_key != expected_key if phase == "blind_reference" else
                    base_key[-3:] != ["claim_review", index, scope.get("scope_sha256")])
                    or reference["checkpoint_sha256"] != hashlib.sha256(
                        canonical(["call", reference["key"]]).encode("utf-8")).hexdigest()
                    or reference["request_sha256"] != reasoning_candidate_digest(call_data)
                    or reference["review_request_sha256"] != reasoning_candidate_digest(request)
                    or reference["response_sha256"] != reasoning_candidate_digest(receipt.get("raw_response"))
                    or scope.get("source_input_sha256") != audit["source_input_sha256"]
                    or scope.get("source_evidence_sha256") != audit["source_evidence_sha256"]):
                raise ValueError("stale_reasoning_blind_claim_checkpoint")
            if phase == "blind_reference":
                if validate_blind_review(receipt.get("raw_response"), request) != receipt:
                    raise ValueError("altered_reasoning_blind_reference")
                blind_by_index[index] = receipt
            wrapped[phase, index] = reference
    if set(blind_by_index) != {index for phase, index in wrapped if phase == "claim_review"}:
        raise ValueError("incomplete_reasoning_blind_claim_turns")
    for item in audit["assessments"]:
        request = item["review"]["request"]
        reference = blind_by_index[request["scope"]["message_index"]]
        if (request.get("reference_review") != reference["raw_response"]
                or request["scope"].get("blind_review_sha256") != reasoning_candidate_digest(reference["raw_response"])
                or request["scope"].get("blind_request_sha256") != reasoning_candidate_digest(reference["request"])):
            raise ValueError("stale_reasoning_blind_reference_binding")
    failures = set()
    for failure in audit["schema_failures"]:
        if (not isinstance(failure, dict) or set(failure) != {"phase", "message_index", "attempt", "code", "checkpoint"}
                or not isinstance(failure["phase"], str) or type(failure["message_index"]) is not int
                or type(failure["attempt"]) is not int or failure["attempt"] != 1
                or not isinstance(failure["code"], str) or not failure["code"]):
            raise ValueError("invalid_reasoning_blind_claim_schema_failure")
        slot = failure["phase"], failure["message_index"]
        completed, failed = wrapped.get(slot), failure["checkpoint"]
        if (slot in failures or completed is None or "format_repair" not in completed
                or not isinstance(failed, dict) or set(failed) != set(completed) - {"format_repair"}
                or failed["stage"] != completed["stage"] or failed["key"] != completed["key"][:-2]
                or failed["checkpoint_sha256"] != hashlib.sha256(
                    canonical(["call", failed["key"]]).encode("utf-8")).hexdigest()
                or failed["request_sha256"] != completed["review_request_sha256"]
                or failed["review_request_sha256"] != completed["review_request_sha256"]
                or not isinstance(failed["response_sha256"], str) or len(failed["response_sha256"]) != 64
                or any(char not in "0123456789abcdef" for char in failed["response_sha256"])):
            raise ValueError("invalid_reasoning_blind_claim_schema_failure")
        failures.add(slot)
    if any(("format_repair" in reference) != (slot in failures) for slot, reference in wrapped.items()):
        raise ValueError("incomplete_reasoning_blind_claim_schema_history")
    merge_reviews = merge_claim_reviews_v2
    if audit["version"] == 3:
        from lib.domain.anchored_claim_review import merge_claim_reviews_v3
        merge_reviews = merge_claim_reviews_v3
    merged = merge_reviews(payload, [item["review"] for item in audit["assessments"]])
    if audit["derived_verdict"] != merged:
        raise ValueError("altered_reasoning_blind_claim_aggregate")
    if (any(check[name] and not merged[name] for name in ("keep", "grounded", "reasoning_valid"))
            or check["correctness"] > merged["correctness"]
            or any(score > merged["scores"][name] for name, score in check["scores"].items())
            or check.get("turn_verdicts") != merged.get("turn_verdicts")):
        raise ValueError("reasoning_blind_claim_gate_weakened")
    return deepcopy(audit)


def _validated_adversarial_claim_audit(payload, check, audit):
    """Bind candidate-aware challenges and adjudication to their actual calls."""
    from lib.domain.adversarial_claim_review import validate_challenge_review, merge_adversarial_claim_reviews
    verdict(check)
    fields = {"version", "method", "candidate_sha256", "source_input_sha256", "source_evidence_sha256",
        "status", "challenge_reviews", "assessments", "schema_failures", "derived_verdict"}
    if (set(audit) != fields or type(audit["version"]) is not int or audit["version"] != 4
            or audit["method"] != "adversarial_anchored_claim_review" or audit["status"] != "complete"
            or audit["candidate_sha256"] != reasoning_candidate_digest(payload)
            or not isinstance(audit["challenge_reviews"], list) or not audit["challenge_reviews"]
            or not isinstance(audit["assessments"], list) or not audit["assessments"]
            or not isinstance(audit["schema_failures"], list)):
        raise ValueError("invalid_reasoning_adversarial_claim_audit")
    wrapped, challenge_by_index = {}, {}
    for phase, collection in (("claim_challenge", audit["challenge_reviews"]), ("claim_adjudication", audit["assessments"])):
        for item in collection:
            if not isinstance(item, dict) or set(item) != {"review", "checkpoint"}:
                raise ValueError("invalid_reasoning_adversarial_checkpoint")
            receipt, reference = item["review"], item["checkpoint"]
            request = receipt.get("request") if isinstance(receipt, dict) else None
            if not isinstance(request, dict) or not isinstance(reference, dict):
                raise ValueError("invalid_reasoning_adversarial_checkpoint")
            scope = request.get("scope", {})
            if not isinstance(scope, dict):
                raise ValueError("invalid_reasoning_adversarial_checkpoint")
            index = scope.get("message_index")
            if type(index) is not int or (phase, index) in wrapped or request.get("phase") != phase:
                raise ValueError("invalid_reasoning_adversarial_checkpoint")
            ref_fields = {"stage", "key", "checkpoint_sha256", "request_sha256", "review_request_sha256", "response_sha256"}
            if (set(reference) - {"format_repair"} != ref_fields or not isinstance(reference["stage"], str)
                    or not reference["stage"] or not isinstance(reference["key"], list)):
                raise ValueError("invalid_reasoning_adversarial_checkpoint")
            base_key, call_data = reference["key"], request
            if "format_repair" in reference:
                feedback = reference["format_repair"]
                code = "invalid_challenge_review_response" if phase == "claim_challenge" else "invalid_adversarial_review_response"
                if (not isinstance(feedback, dict) or set(feedback) != {"code", "instruction"}
                        or feedback["code"] != code or not isinstance(feedback["instruction"], str)
                        or not feedback["instruction"].strip() or base_key[-2:] != ["format_repair", 1]):
                    raise ValueError("invalid_reasoning_adversarial_checkpoint")
                base_key, call_data = base_key[:-2], {**request, "format_repair": feedback}
            expected_key = ["claim_challenge", request.get("target"), scope.get("scope_sha256")]
            if ((base_key != expected_key if phase == "claim_challenge" else
                    base_key[-3:] != ["claim_adjudication", index, scope.get("scope_sha256")])
                    or reference["checkpoint_sha256"] != hashlib.sha256(
                        canonical(["call", reference["key"]]).encode("utf-8")).hexdigest()
                    or reference["request_sha256"] != reasoning_candidate_digest(call_data)
                    or reference["review_request_sha256"] != reasoning_candidate_digest(request)
                    or reference["response_sha256"] != reasoning_candidate_digest(receipt.get("raw_response"))
                    or scope.get("source_input_sha256") != audit["source_input_sha256"]
                    or scope.get("source_evidence_sha256") != audit["source_evidence_sha256"]):
                raise ValueError("stale_reasoning_adversarial_checkpoint")
            if phase == "claim_challenge":
                if validate_challenge_review(receipt.get("raw_response"), request) != receipt:
                    raise ValueError("altered_reasoning_challenge_review")
                challenge_by_index[index] = receipt
            wrapped[phase, index] = reference
    if set(challenge_by_index) != {index for phase, index in wrapped if phase == "claim_adjudication"}:
        raise ValueError("incomplete_reasoning_adversarial_turns")
    for item in audit["assessments"]:
        request = item["review"]["request"]
        challenge = challenge_by_index[request["scope"]["message_index"]]
        if (request.get("challenge_review") != challenge["raw_response"]
                or request["scope"].get("challenge_review_sha256") != reasoning_candidate_digest(challenge["raw_response"])
                or request["scope"].get("challenge_request_sha256") != reasoning_candidate_digest(challenge["request"])):
            raise ValueError("stale_reasoning_adversarial_challenge_binding")
    failures = set()
    for failure in audit["schema_failures"]:
        if (not isinstance(failure, dict) or set(failure) != {"phase", "message_index", "attempt", "code", "checkpoint"}
                or not isinstance(failure["phase"], str) or type(failure["message_index"]) is not int
                or type(failure["attempt"]) is not int or failure["attempt"] != 1
                or not isinstance(failure["code"], str) or not failure["code"]):
            raise ValueError("invalid_reasoning_adversarial_schema_failure")
        slot = failure["phase"], failure["message_index"]
        completed, failed = wrapped.get(slot), failure["checkpoint"]
        if (slot in failures or completed is None or "format_repair" not in completed
                or not isinstance(failed, dict) or set(failed) != set(completed) - {"format_repair"}
                or failed["stage"] != completed["stage"] or failed["key"] != completed["key"][:-2]
                or failed["checkpoint_sha256"] != hashlib.sha256(
                    canonical(["call", failed["key"]]).encode("utf-8")).hexdigest()
                or failed["request_sha256"] != completed["review_request_sha256"]
                or failed["review_request_sha256"] != completed["review_request_sha256"]
                or not isinstance(failed["response_sha256"], str) or len(failed["response_sha256"]) != 64
                or any(char not in "0123456789abcdef" for char in failed["response_sha256"])):
            raise ValueError("invalid_reasoning_adversarial_schema_failure")
        failures.add(slot)
    if any(("format_repair" in reference) != (slot in failures) for slot, reference in wrapped.items()):
        raise ValueError("incomplete_reasoning_adversarial_schema_history")
    merged = merge_adversarial_claim_reviews(payload, [item["review"] for item in audit["assessments"]])
    if audit["derived_verdict"] != merged:
        raise ValueError("altered_reasoning_adversarial_aggregate")
    if (any(check[name] and not merged[name] for name in ("keep", "grounded", "reasoning_valid"))
            or check["correctness"] > merged["correctness"]
            or any(score > merged["scores"][name] for name, score in check["scores"].items())
            or check.get("turn_verdicts") != merged.get("turn_verdicts")):
        raise ValueError("reasoning_adversarial_gate_weakened")
    return deepcopy(audit)


def assessment_from_verdict(payload: dict, check: dict, *, evidence_ref: str,
                            score_threshold: float = .8, method: str = "llm_review",
                            claim_review=None) -> dict:
    """Project existing feedback without inventing another model schema.

    ``evidence_ref`` identifies the actual reviewer request/checkpoint. It is a
    caller-owned reference, not a claim that this pure function authenticated a
    service response. ``method`` comes from the actual caller path, never from
    a model-authored JSON field. Local guard rejection leaves the semantic
    axes unverified; it cannot certify either the facts or the inference.

    A clearly tentative route rejected within the same authored explanation is
    not inherently an error. Whether that route is genuinely resolved, or an
    asserted error remains in an earlier supervised turn, is assessed by the
    reviewer. This function preserves its reason and conservative turn result;
    it does not infer logical correctness from keywords, length or formatting.
    """
    if (not isinstance(evidence_ref, str) or not evidence_ref.strip()
            or len(evidence_ref) > 2048 or "\x00" in evidence_ref):
        raise ValueError("invalid_reasoning_candidate_evidence_ref")
    if (type(score_threshold) not in {int, float} or not isfinite(score_threshold)
            or not 0 <= score_threshold <= 1):
        raise ValueError("invalid_reasoning_candidate_threshold")
    if not isinstance(method, str) or method not in {"llm_review", "llm_claim_review", "deterministic_guard"}:
        raise ValueError("invalid_reasoning_candidate_assessment_method")
    if method != "llm_claim_review" and claim_review is not None:
        raise ValueError("unexpected_reasoning_claim_audit")
    body_hash = reasoning_candidate_digest(payload)
    raw_verdict = deepcopy(check)
    if method == "llm_claim_review":
        claim_review = _validated_claim_audit(payload, check, claim_review)
    check = _reviewed_verdict(payload, check)
    pending_review = bool(method == "llm_claim_review" and any(
        item.get("review", {}).get("condition_guard", {}).get("needs_review")
        for item in claim_review.get("assessments", [])))
    review_gap_only = pending_review and not any(
        claim["status"] == "refuted"
        for item in claim_review["assessments"]
        for claim in item["review"]["raw_response"]["claim_checks"])
    outcome_passed = (check["grounded"] and check["correctness"] >= 4
                      and check["scores"]["correctness"] >= 4 and check["scores"]["grounding"] >= 4)
    process_passed = check["reasoning_valid"] and check["scores"]["reasoning"] >= 4
    passed = accepted(check) and min(check["scores"].values()) / 5 >= score_threshold
    if method == "deterministic_guard" and passed:
        raise ValueError("deterministic_guard_cannot_accept_reasoning_candidate")
    result = {
        "version": REASONING_CANDIDATES_VERSION,
        "candidate_sha256": body_hash,
        "payload": deepcopy(payload),
        "payload_encoding": "canonical_json_utf8",
        "method": method,
        "evidence_ref": evidence_ref,
        "score_threshold": float(score_threshold),
        "outcome": ({"status": "unverified"} if method == "deterministic_guard" or review_gap_only else
                    {"status": "pass" if outcome_passed else "fail", "grounded": check["grounded"],
                     "correctness": check["correctness"]}),
        "process": ({"status": "unverified"} if method == "deterministic_guard" or review_gap_only else
                    {"status": "pass" if process_passed else "fail", "reasoning_valid": check["reasoning_valid"]}),
        "accepted": passed,
    }
    if method == "llm_claim_review":
        result.update(derived_verdict=raw_verdict, claim_review=claim_review)
        if review_gap_only:
            result["review_status"] = "pending_review"
    else:
        result["raw_verdict"] = raw_verdict
    result["verdict"] = check
    return result


def select_reasoning_candidate(candidates: list[dict], config=None) -> dict:
    """Select the first passing exact body; reject stale or altered receipts.

    Each entry is ``{candidate: exact_training_payload, assessment: receipt}``.
    The caller can invoke this after each existing bounded attempt, stopping
    immediately on success. Remaining attempts are optional, never a quota.
    All provided receipts are validated, including those after a passing one.
    The selection does not certify factual truth or replace release checks.
    """
    config = validate_reasoning_candidates(config)
    limit = config.get("candidate_limit")
    if not isinstance(candidates, list):
        raise ValueError("invalid_reasoning_candidate_list")
    if limit is not None and len(candidates) > limit:
        raise ValueError("reasoning_candidate_limit_exceeded")
    assessments, rejected, pending, selected_index = [], [], [], None
    for index, entry in enumerate(candidates):
        if (not isinstance(entry, dict) or set(entry) != {"candidate", "assessment"}
                or not isinstance(entry["assessment"], dict)):
            raise ValueError("invalid_reasoning_candidate_entry")
        payload, assessment = entry["candidate"], entry["assessment"]
        if reasoning_candidate_digest(payload) != assessment.get("candidate_sha256"):
            raise ValueError("stale_reasoning_candidate_assessment")
        method = assessment.get("method")
        expected = assessment_from_verdict(payload, assessment.get(
                                           "derived_verdict" if method == "llm_claim_review" else "raw_verdict"),
                                           evidence_ref=assessment.get("evidence_ref"),
                                           score_threshold=assessment.get("score_threshold"),
                                           method=method,
                                           **({"claim_review": assessment.get("claim_review")}
                                              if method == "llm_claim_review" else {}))
        if assessment != expected:
            raise ValueError("invalid_reasoning_candidate_assessment")
        assessments.append(deepcopy(assessment))
        if assessment["accepted"] and selected_index is None:
            selected_index = index
        elif not assessment["accepted"]:
            collection = pending if assessment.get("review_status") == "pending_review" else rejected
            collection.append({"index": index, "candidate_sha256": assessment["candidate_sha256"],
                             "outcome": assessment["outcome"]["status"],
                             "process": assessment["process"]["status"],
                             "reason": assessment["verdict"]["reason"],
                             "evidence_ref": assessment["evidence_ref"]})
    status = ("accepted" if selected_index is not None else
              "waiting_manual_review" if pending else
              "rejected" if limit is None else "exhausted" if len(candidates) == limit else "needs_candidate")
    return {"version": REASONING_CANDIDATES_VERSION, "selection": "first_passing", "status": status,
            "candidate_limit": limit, "attempted": len(candidates),
            "remaining": limit - len(candidates) if limit is not None else None,
            "selected_index": selected_index,
            "candidate": deepcopy(candidates[selected_index]["candidate"]) if selected_index is not None else None,
            "assessments": assessments, "rejections": rejected,
            **({"pending_reviews": pending} if pending else {})}
