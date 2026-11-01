"""Single-pass grouped review with exact, lossless per-unit authentication.

Grouping saves repeated declarations, not review coverage. The existing
condition-aware validator owns provenance and admission rules. Its two-phase
objects below are explicitly program normalizations, never provider calls.
"""
from copy import deepcopy

from lib.domain import claim_review as claims
from lib.domain import adversarial_claim_review as conditions
from lib.domain.review_target_units import target_units, validate_target_units
from lib.domain.review_condition_contract import validate_premises
from lib.domain.workflow_quality import verdict


_INITIAL = "consolidated_claim_review"
_FOCUSED = "focused_claim_review"
_SEMANTICS = "reviewer_reported_not_independently_verified"


def _hash_request(request):
    value = deepcopy(request)
    value["scope"].pop("scope_sha256", None)
    return claims._digest(value)


def consolidated_review_requests(target, payload, evidence, reasoning_required=False, review_requirements=None):
    """One complete prefix per supervised turn; no future turns are visible."""
    requests = []
    for core in claims.claim_review_requests(target, payload, evidence, reasoning_required, review_requirements):
        request = deepcopy(core)
        request.update(claim_review_protocol_version=6, phase=_INITIAL, condition_contract_version=1,
            target_units=target_units(core["target_fields"], core["scope"]["message_index"], segmentation_version=2))
        request["review_unit_ids"] = [unit["id"] for unit in request["target_units"]]
        request["scope"]["scope_sha256"] = _hash_request(request)
        requests.append(request)
    return requests


def _core(request):
    extra = {"phase", "condition_contract_version", "target_units", "review_unit_ids"}
    if (not isinstance(request, dict) or not isinstance(request.get("phase"), str)
            or request["phase"] not in {_INITIAL, _FOCUSED}):
        raise ValueError("invalid_consolidated_review_request")
    if request["phase"] == _FOCUSED:
        extra.add("focus")
    if (type(request.get("claim_review_protocol_version")) is not int
            or request["claim_review_protocol_version"] != 6 or type(request.get("condition_contract_version")) is not int
            or request["condition_contract_version"] != 1
            or not isinstance(request.get("scope"), dict)
            or request["scope"].get("scope_sha256") != _hash_request(request)):
        raise ValueError("invalid_consolidated_review_request")
    core = {key: deepcopy(value) for key, value in request.items() if key not in extra}
    core["claim_review_protocol_version"] = 1
    core["scope"]["scope_sha256"] = conditions._request_hash(core)
    claims._validate_request(core)
    validate_target_units(request.get("target_units"), core["target_fields"], core["scope"]["message_index"],
                          segmentation_version=2)
    identifiers = [unit["id"] for unit in request["target_units"]]
    selected = request.get("review_unit_ids")
    if (not isinstance(selected, list) or not selected or any(not isinstance(item, str) for item in selected)
            or len(selected) != len(set(selected)) or not set(selected) <= set(identifiers)
            or request["phase"] == _INITIAL and selected != identifiers):
        raise ValueError("invalid_consolidated_unit_scope")
    if request["phase"] == _FOCUSED:
        focus = request.get("focus")
        if (not isinstance(focus, dict) or set(focus) != {"prior_response_sha256", "claims"}
                or not isinstance(focus["prior_response_sha256"], str)
                or len(focus["prior_response_sha256"]) != 64
                or not isinstance(focus["claims"], list) or any(not isinstance(item, dict) for item in focus["claims"])
                or [item.get("unit_id") for item in focus["claims"]] != selected):
            raise ValueError("invalid_focused_review_scope")
    return core


def _expanded(value, request):
    core = _core(request)
    if (not isinstance(value, dict) or set(value) != {"groups", "coverage", "verdict", "limitations"}
            or not isinstance(value["coverage"], str) or value["coverage"] not in {"complete", "partial"}
            or not isinstance(value["groups"], list) or not value["groups"]
            or not isinstance(value["limitations"], list)):
        raise ValueError("invalid_consolidated_review_response")
    if not isinstance(value["verdict"], dict) or set(value["verdict"]) != claims._GRADE_FIELDS:
        raise ValueError("invalid_claim_review_verdict_fields")
    verdict(value["verdict"])
    for limitation in value["limitations"]:
        claims._text(limitation, "missing_consolidated_review_limitation")
    units = {unit["id"]: unit for unit in request["target_units"]}
    by_unit = {}
    for group in value["groups"]:
        if (not isinstance(group, dict) or set(group) != {"unit_ids", "status", "severity", "basis", "premises"}
                or not isinstance(group["unit_ids"], list) or not group["unit_ids"]
                or not isinstance(group["status"], str) or group["status"] not in {"supported", "refuted", "insufficient"}
                or not isinstance(group["severity"], str) or group["severity"] not in {"material", "minor"}):
            raise ValueError("invalid_consolidated_review_group")
        for identifier in group["unit_ids"]:
            if (not isinstance(identifier, str) or identifier not in request["review_unit_ids"]
                    or identifier in by_unit):
                raise ValueError("invalid_consolidated_unit_coverage")
            conditions._basis(group["basis"], core, units[identifier]["text"])
            if any("message_index" in reference and core["messages"][reference["message_index"]]["role"] == "assistant"
                   for reference in group["basis"]["evidence"]):
                raise ValueError("condition_historical_assistant_not_evidence")
            validate_premises(group["premises"], request)
            by_unit[identifier] = {"unit_id": identifier,
                **{key: deepcopy(group[key]) for key in ("status", "severity", "basis", "premises")}}
    if set(by_unit) != set(request["review_unit_ids"]):
        raise ValueError("invalid_consolidated_unit_coverage")
    return [by_unit[identifier] for identifier in request["review_unit_ids"]]


def _normalization(request, expanded, value):
    """Reuse strict legacy rules; synthesized phases are not claimed as calls."""
    core = _core(request)
    challenge_request = conditions._challenge_request(core, 1)
    challenge = {"claim_review_protocol_version": 4, "phase": "claim_challenge",
        "scope_sha256": challenge_request["scope"]["scope_sha256"], "issues": [],
        "condition_ledger": [{"unit_id": claim["unit_id"], "premises": deepcopy(claim["premises"])}
                             for claim in expanded], "limitations": deepcopy(value["limitations"])}
    conditions.validate_challenge_review(challenge, challenge_request)
    grade_request = deepcopy(core)
    grade_request.update(claim_review_protocol_version=4, phase="claim_adjudication", condition_contract_version=1,
                         target_units=deepcopy(request["target_units"]), challenge_review=challenge)
    grade_request["scope"].update(challenge_request_sha256=claims._digest(challenge_request),
                                  challenge_review_sha256=claims._digest(challenge))
    grade_request["scope"]["scope_sha256"] = conditions._request_hash(grade_request)
    assembled = {"claim_review_protocol_version": 4, "scope_sha256": grade_request["scope"]["scope_sha256"],
        "coverage": value["coverage"], "checked_unit_ids": [unit["id"] for unit in request["target_units"]],
        "claim_checks": deepcopy(expanded), "challenge_checks": [], "verdict": deepcopy(value["verdict"])}
    return conditions.validate_adversarial_claim_review(assembled, grade_request)


def validate_consolidated_claim_review(value, request):
    expanded = _expanded(value, request)
    receipt = {"version": 6, "request": deepcopy(request), "raw_response": deepcopy(value),
        "raw_response_sha256": claims._digest(value), "expanded_claims": expanded,
        "response_origin": "single_provider_response_with_program_expansion", "structure_verified": True,
        "semantic_verification": _SEMANTICS}
    if request["phase"] == _INITIAL:
        normalization = _normalization(request, expanded, value)
        receipt.update(normalization_origin="program_condition_contract_normalization_not_independent_review",
            normalization=normalization, assembled_response=deepcopy(normalization["raw_response"]),
            **{key: deepcopy(normalization[key]) for key in ("condition_guard", "verdict", "turn_verdict", "accepted")})
    return receipt


def _checked(receipt):
    if not isinstance(receipt, dict) or not isinstance(receipt.get("request"), dict):
        raise ValueError("invalid_consolidated_review_receipt")
    expected = validate_consolidated_claim_review(receipt.get("raw_response"), receipt["request"])
    if receipt != expected:
        raise ValueError("altered_consolidated_review_receipt")
    return expected


def focused_review_request(receipt):
    """Return at most one exact dispute set; no rewrite or invented premises."""
    initial = _checked(receipt)
    if initial["request"]["phase"] != _INITIAL:
        raise ValueError("focused_review_cannot_recurse")
    effective = {item["unit_id"]: item["effective_status"] for item in initial["condition_guard"]["checks"]}
    disputed = [{**deepcopy(item), "status": effective[item["unit_id"]]} for item in initial["expanded_claims"]
                if item["severity"] == "material" and effective[item["unit_id"]] in {"refuted", "insufficient"}]
    if not disputed:
        return None
    request = deepcopy(initial["request"])
    request.update(phase=_FOCUSED, review_unit_ids=[item["unit_id"] for item in disputed],
                   focus={"prior_response_sha256": initial["raw_response_sha256"], "claims": disputed})
    request["scope"]["scope_sha256"] = _hash_request(request)
    return request


def resolve_consolidated_review(initial, focused=None):
    """Rebuild the full decision, changing only authenticated disputed units."""
    initial = _checked(initial)
    if initial["request"]["phase"] != _INITIAL:
        raise ValueError("missing_initial_consolidated_review")
    if focused is None:
        return initial
    focused = _checked(focused)
    expected = focused_review_request(initial)
    if expected is None or focused["request"] != expected:
        raise ValueError("stale_focused_review_binding")
    by_unit = {item["unit_id"]: deepcopy(item) for item in initial["expanded_claims"]}
    by_unit.update({item["unit_id"]: deepcopy(item) for item in focused["expanded_claims"]})
    expanded = [by_unit[item["unit_id"]] for item in initial["expanded_claims"]]
    # The focused call can correct a unit finding, but cannot erase an unrelated
    # whole-turn rejection (for example an unreadable learner-side question).
    # Use raw initial scores: an initial program-derived claim veto may still
    # clear naturally when that exact disputed unit is resolved with evidence.
    first_grade, focused_grade = initial["raw_response"]["verdict"], focused["raw_response"]["verdict"]
    grade = {name: first_grade[name] and focused_grade[name]
             for name in ("keep", "grounded", "reasoning_valid")}
    grade.update(correctness=min(first_grade["correctness"], focused_grade["correctness"]),
        scores={name: min(score, focused_grade["scores"][name]) for name, score in first_grade["scores"].items()},
        reason=first_grade["reason"] + "; focused clarification: " + focused_grade["reason"])
    combined = {"coverage": ("complete" if initial["raw_response"]["coverage"] == "complete"
                            and focused["raw_response"]["coverage"] == "complete" else "partial"),
        "verdict": grade,
        "limitations": initial["raw_response"]["limitations"] + focused["raw_response"]["limitations"]}
    normalization = _normalization(initial["request"], expanded, combined)
    return {"version": 6, "request": deepcopy(initial["request"]),
        "response_origin": "program_merge_of_initial_and_focused_review",
        "initial_response_sha256": initial["raw_response_sha256"],
        "focused_response_sha256": focused["raw_response_sha256"], "expanded_claims": expanded,
        "normalization_origin": "program_condition_contract_normalization_not_independent_review",
        "normalization": normalization, "assembled_response": deepcopy(normalization["raw_response"]),
        **{key: deepcopy(normalization[key]) for key in ("condition_guard", "verdict", "turn_verdict", "accepted")}}


def merge_consolidated_claim_reviews(payload, records):
    """Records contain initial receipts and an optional one-shot focused receipt."""
    normalized = []
    for record in records:
        if not isinstance(record, dict) or set(record) not in ({"review"}, {"review", "focused"}):
            raise ValueError("invalid_consolidated_review_record")
        normalized.append(resolve_consolidated_review(record["review"], record.get("focused"))["normalization"])
    return conditions.merge_adversarial_claim_reviews(payload, normalized)


def resolved_claim_assessments(audit):
    """Project final receipts; callers still authenticate full audits for release."""
    if not isinstance(audit, dict):
        return []
    if audit.get("version") == 6:
        return [resolve_consolidated_review(item["review"], item.get("focused", {}).get("review"))
                for item in audit.get("assessments", [])]
    return [deepcopy(item["review"]) for item in audit.get("assessments", [])]


def claim_review_refutations(audit):
    """Use final authenticated units; an overturned first opinion is history."""
    refuted = []
    for review in resolved_claim_assessments(audit):
        if audit["version"] == 6:
            normalized = review["normalization"]["normalized_response"]
        else:
            normalized = review["normalized_response"] if audit["version"] in {3, 4, 5} else review["raw_response"]
        for claim in normalized["claim_checks"]:
            if claim["status"] == "refuted":
                refuted.append({"message_index": review["request"]["scope"]["message_index"],
                    "field": claim["field"], "quote": claim["quote"], "severity": claim["severity"],
                    "reason": claim["basis"]["derivation"]})
    return refuted


def consolidated_checkpoint_key(request):
    _core(request)
    return [request["phase"], request["scope"]["scope_sha256"]]


def _checkpoint(review, reference):
    if not isinstance(reference, dict):
        raise ValueError("invalid_consolidated_review_checkpoint")
    fields = {"stage", "key", "checkpoint_sha256", "request_sha256", "review_request_sha256", "response_sha256"}
    if (set(reference) - {"format_repair"} != fields or not isinstance(reference["stage"], str)
            or not reference["stage"] or not isinstance(reference["key"], list)):
        raise ValueError("invalid_consolidated_review_checkpoint")
    request, data, key = review["request"], review["request"], reference["key"]
    if "format_repair" in reference:
        feedback = reference["format_repair"]
        if (not isinstance(feedback, dict) or set(feedback) != {"code", "instruction"}
                or feedback["code"] != "invalid_consolidated_review_response"
                or not isinstance(feedback["instruction"], str) or not feedback["instruction"].strip()
                or key[-2:] != ["format_repair", 1]):
            raise ValueError("invalid_consolidated_review_checkpoint")
        data, key = {**request, "format_repair": feedback}, key[:-2]
    if (key != consolidated_checkpoint_key(request)
            or reference["checkpoint_sha256"] != claims._digest(["call", reference["key"]])
            or reference["request_sha256"] != claims._digest(data)
            or reference["review_request_sha256"] != claims._digest(request)
            or reference["response_sha256"] != claims._digest(review["raw_response"])):
        raise ValueError("stale_consolidated_review_checkpoint")


def validate_consolidated_claim_audit(payload, check, audit):
    fields = {"version", "method", "candidate_sha256", "source_input_sha256", "source_evidence_sha256",
              "status", "assessments", "schema_failures", "derived_verdict"}
    if (not isinstance(audit, dict) or set(audit) != fields or type(audit["version"]) is not int
            or audit["version"] != 6 or audit["method"] != "consolidated_condition_claim_review"
            or audit["status"] != "complete" or audit["candidate_sha256"] != claims._digest(payload)
            or not isinstance(audit["assessments"], list) or not audit["assessments"]
            or not isinstance(audit["schema_failures"], list)):
        raise ValueError("invalid_consolidated_claim_audit")
    records, references, requests = [], {}, {}
    for record in audit["assessments"]:
        if not isinstance(record, dict) or set(record) not in ({"review", "checkpoint"}, {"review", "checkpoint", "focused"}):
            raise ValueError("invalid_consolidated_review_record")
        initial = _checked(record["review"])
        scope = initial["request"]["scope"]
        if any(scope[name] != audit[name] for name in ("candidate_sha256", "source_input_sha256", "source_evidence_sha256")):
            raise ValueError("stale_consolidated_claim_scope")
        pair = {"review": initial}
        parts = [record]
        if "focused" in record:
            focused = record["focused"]
            if not isinstance(focused, dict) or set(focused) != {"review", "checkpoint"}:
                raise ValueError("invalid_focused_review_record")
            pair["focused"] = _checked(focused["review"])
            parts.append(focused)
        resolve_consolidated_review(initial, pair.get("focused"))
        for part in parts:
            _checkpoint(part["review"], part["checkpoint"])
            references[part["checkpoint"]["review_request_sha256"]] = part["checkpoint"]
            requests[part["checkpoint"]["review_request_sha256"]] = part["review"]["request"]
        records.append(pair)
    failures = set()
    for failure in audit["schema_failures"]:
        if (not isinstance(failure, dict) or set(failure) != {"phase", "message_index", "attempt", "code", "checkpoint"}
                or failure["attempt"] != 1 or type(failure["attempt"]) is not int
                or type(failure["message_index"]) is not int or failure["phase"] not in {_INITIAL, _FOCUSED}
                or not isinstance(failure["code"], str) or not failure["code"]):
            raise ValueError("invalid_consolidated_schema_history")
        failed = failure["checkpoint"]
        completed = references.get(failed.get("review_request_sha256")) if isinstance(failed, dict) else None
        if (completed is None or "format_repair" not in completed or failed["review_request_sha256"] in failures
                or set(failed) != set(completed) - {"format_repair"}
                or failed["stage"] != completed["stage"] or failed["key"] != completed["key"][:-2]
                or failed["checkpoint_sha256"] != claims._digest(["call", failed["key"]])
                or failed["request_sha256"] != completed["review_request_sha256"]
                or not isinstance(failed["response_sha256"], str) or len(failed["response_sha256"]) != 64
                or any(char not in "0123456789abcdef" for char in failed["response_sha256"])):
            raise ValueError("invalid_consolidated_schema_history")
        request = requests[failed["review_request_sha256"]]
        if failure["phase"] != request["phase"] or failure["message_index"] != request["scope"]["message_index"]:
            raise ValueError("stale_consolidated_schema_history")
        failures.add(failed["review_request_sha256"])
    if any(("format_repair" in reference) != (identifier in failures) for identifier, reference in references.items()):
        raise ValueError("incomplete_consolidated_schema_history")
    merged = merge_consolidated_claim_reviews(payload, records)
    verdict(check)
    if audit["derived_verdict"] != merged:
        raise ValueError("altered_consolidated_claim_aggregate")
    if (any(check[name] and not merged[name] for name in ("keep", "grounded", "reasoning_valid"))
            or check["correctness"] > merged["correctness"]
            or any(score > merged["scores"][name] for name, score in check["scores"].items())
            or check.get("turn_verdicts") != merged.get("turn_verdicts")):
        raise ValueError("consolidated_claim_gate_weakened")
    return deepcopy(audit)
