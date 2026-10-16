"""Current-turn anchors bind model decisions to program-owned original spans.

The provider never supplies candidate quotes. Its exact raw response and the
program's normalized version-two representation are distinct audit objects.
Blind reference coverage is advisory here; actual incomplete candidate checks
and unresolved material claims/reference conflicts still veto release.
"""
from copy import deepcopy

from lib.domain import claim_review as v1
from lib.domain import blind_claim_review as v2
from lib.domain.review_target_units import target_units, validate_target_units
from lib.domain.workflow_quality import accepted
from lib.domain.turn_review import supervised_assistant_turns, turn_review_verdict


_FIELDS = {"claim_review_protocol_version", "scope_sha256", "coverage", "checked_unit_ids",
           "claim_checks", "reference_checks", "verdict"}


def _digest_request(request):
    value = deepcopy(request)
    value["scope"].pop("scope_sha256", None)
    return v1._digest(value)


def claim_review_requests_v3(target, payload, evidence, reasoning_required=False,
                             review_requirements=None, *, blind_reviews):
    requests = v2.claim_review_requests_v2(target, payload, evidence, reasoning_required,
        review_requirements, blind_reviews=blind_reviews)
    result = []
    for original in requests:
        request = deepcopy(original)
        request["claim_review_protocol_version"] = 3
        request["target_units"] = target_units(request["target_fields"], request["scope"]["message_index"])
        request["scope"]["scope_sha256"] = _digest_request(request)
        result.append(request)
    return result


def _base_request(request):
    if (not isinstance(request, dict) or type(request.get("claim_review_protocol_version")) is not int
            or request["claim_review_protocol_version"] != 3 or not isinstance(request.get("scope"), dict)
            or request["scope"].get("scope_sha256") != _digest_request(request)):
        raise ValueError("invalid_anchored_claim_review_request")
    validate_target_units(request.get("target_units"), request.get("target_fields"),
                          request["scope"].get("message_index"))
    base = deepcopy(request)
    base.pop("target_units")
    base["claim_review_protocol_version"] = 2
    base["scope"]["scope_sha256"] = _digest_request(base)
    v2._grade_components(base)
    return base


def validate_claim_review_v3(value, request, *, reference_policy_version=None, quote_matching_version=None):
    if reference_policy_version is not None and (
            type(reference_policy_version) is not int or reference_policy_version != 1):
        raise ValueError("invalid_claim_reference_policy_version")
    if quote_matching_version is not None and (
            type(quote_matching_version) is not int or quote_matching_version != 1):
        raise ValueError("invalid_claim_review_quote_matching_version")
    base = _base_request(request)
    if (not isinstance(value, dict) or set(value) != _FIELDS
            or type(value["claim_review_protocol_version"]) is not int
            or value["claim_review_protocol_version"] != 3):
        raise ValueError("invalid_anchored_claim_review_response")
    if value["scope_sha256"] != request["scope"]["scope_sha256"]:
        raise ValueError("stale_anchored_claim_review_response")
    units = {unit["id"]: unit for unit in request["target_units"]}
    checked = value["checked_unit_ids"]
    if (not isinstance(checked, list) or any(not isinstance(item, str) for item in checked)
            or len(set(checked)) != len(checked) or set(checked) != set(units)):
        raise ValueError("invalid_anchored_unit_coverage")
    claims, seen = value["claim_checks"], set()
    if not isinstance(claims, list) or len(claims) != len(units):
        raise ValueError("invalid_anchored_unit_coverage")
    normalized_claims = []
    for claim in claims:
        if not isinstance(claim, dict) or set(claim) != {"unit_id", "status", "severity", "basis"}:
            raise ValueError("invalid_anchored_claim")
        identifier = claim["unit_id"]
        if not isinstance(identifier, str) or identifier not in units or identifier in seen:
            raise ValueError("invalid_anchored_unit_id")
        seen.add(identifier)
        unit = units[identifier]
        normalized_claims.append({"field": unit["field"], "quote": unit["text"],
            **{name: deepcopy(claim[name]) for name in ("status", "severity", "basis")}})
    # Identical literal units can occur twice in a field. V2 identifies a
    # quoted claim by field/text; keep the strictest reported decision there,
    # while the original v3 raw retains each separately checked occurrence.
    by_quote = {}
    ranks = {"supported": 0, "insufficient": 1, "refuted": 2}
    for claim in normalized_claims:
        key = claim["field"], claim["quote"]
        previous = by_quote.get(key)
        if previous is None or (ranks.get(claim["status"], -1), claim["severity"] == "material") > (
                ranks.get(previous["status"], -1), previous["severity"] == "material"):
            by_quote[key] = claim
    normalized_claims = list(by_quote.values())
    references = value["reference_checks"]
    if not isinstance(references, list):
        raise ValueError("invalid_anchored_reference_check")
    normalized_references = []
    for check in references:
        fields = {"id", "relation", "unit_id", "reason"}
        if isinstance(check, dict) and check.get("relation") == "reference_error":
            fields.add("counter_evidence")
        if not isinstance(check, dict) or set(check) != fields:
            raise ValueError("invalid_anchored_reference_check")
        identifier = check["unit_id"]
        if check["relation"] == "not_claimed":
            if identifier is not None:
                raise ValueError("invalid_anchored_unit_id")
            target = {"field": None, "quote": ""}
        else:
            if not isinstance(identifier, str) or identifier not in units:
                raise ValueError("invalid_anchored_unit_id")
            target = {"field": units[identifier]["field"], "quote": units[identifier]["text"]}
        normalized_references.append({**target, **{key: deepcopy(part) for key, part in check.items()
                                                   if key != "unit_id"}})
    normalized = {"claim_review_protocol_version": 2, "scope": deepcopy(base["scope"]),
        "coverage": deepcopy(value["coverage"]), "checked_fields": list(request["target_fields"]),
        "claim_checks": normalized_claims, "reference_checks": normalized_references,
        "verdict": deepcopy(value["verdict"])}
    # Version three always supports concrete counterexamples to fallible
    # reference constraints. The original blind output remains unmodified.
    old_receipt = v2.validate_claim_review_v2(normalized, base, reference_policy_version=1)
    core, blind = v2._grade_components(base)
    core_raw = {key: deepcopy(part) for key, part in normalized.items() if key != "reference_checks"}
    core_raw.update(claim_review_protocol_version=1, scope=deepcopy(core["scope"]))
    primary = v1.validate_claim_review(core_raw, core)
    derived = deepcopy(primary["verdict"])
    points = {point["id"]: point for point in blind["raw_response"]["reference_points"]}
    conflicts = [check for check in normalized_references
                 if points[check["id"]]["severity"] == "material"
                 and check["relation"] in {"contradicted", "uncertain"}]
    if conflicts:
        derived.update(keep=False, grounded=False, reasoning_valid=False)
        derived["reason"] = "; ".join([derived["reason"], *(
            f"reference {check['id']}: {check['relation']}: {check['reason']}" for check in conflicts)])
    critical = primary["turn_verdict"]["critical_error"] or bool(conflicts)
    turn = {**deepcopy(derived), "message_index": request["scope"]["message_index"],
        "content_sha256": request["scope"]["prefix_sha256"],
        "leakage_free": derived["reasoning_valid"], "critical_error": critical}
    return {"claim_review_protocol_version": 3, "request": deepcopy(request), "raw_response": deepcopy(value),
        "normalized_response": normalized, "normalized_receipt": old_receipt,
        "verdict": derived, "turn_verdict": turn, "accepted": accepted(derived) and not critical,
        "structure_verified": True, "semantic_verification": "reviewer_reported_not_independently_verified"}


def merge_claim_reviews_v3(payload, receipts):
    if not isinstance(receipts, list) or not receipts:
        raise ValueError("missing_anchored_claim_review_receipts")
    checked = []
    for receipt in receipts:
        if not isinstance(receipt, dict) or not isinstance(receipt.get("request"), dict):
            raise ValueError("invalid_anchored_claim_review_receipt")
        expected = validate_claim_review_v3(receipt.get("raw_response"), receipt["request"])
        if expected != receipt:
            raise ValueError("altered_anchored_claim_review_receipt")
        checked.append(expected)
    # Reuse exact candidate/prefix/source/requirements/blind-checkpoint binding
    # checks. Its older partial-reference gate is not the v3 release verdict.
    v2.merge_claim_reviews_v2(payload, [receipt["normalized_receipt"] for receipt in checked])
    checked.sort(key=lambda item: item["request"]["scope"]["message_index"])
    first = checked[0]["verdict"]
    result = {"keep": all(item["verdict"]["keep"] for item in checked),
        "grounded": all(item["verdict"]["grounded"] for item in checked),
        "reasoning_valid": all(item["verdict"]["reasoning_valid"] for item in checked),
        "correctness": min(item["verdict"]["correctness"] for item in checked),
        "scores": {name: min(item["verdict"]["scores"][name] for item in checked) for name in first["scores"]},
        "reason": "; ".join(f"assistant[{item['request']['scope']['message_index']}]: {item['verdict']['reason']}"
                            for item in checked)}
    if checked[0]["request"]["target"] == "cot":
        return result
    merged = turn_review_verdict({**result, "turn_verdicts": [item["turn_verdict"] for item in checked]},
                                  supervised_assistant_turns(payload))
    return {key: deepcopy(merged[key]) for key in (*v1._GRADE_FIELDS, "turn_verdicts")}
