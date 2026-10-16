"""Candidate-blind reference preparation and exact version-two claim receipts.

The first model sees only the actual earlier dialogue, sources and trusted
node settings. Its reference points are fallible review notes, not a source
of truth. The second review must compare each note with the current authored
answer and explanation. This is a structural contract, not a proof engine.
Version-one requests and helpers remain unchanged.
"""
from __future__ import annotations

from copy import deepcopy
import re

from lib.domain import claim_review as v1
from lib.domain.learner_context import learner_messages
from lib.domain.turn_review import supervised_assistant_turns, turn_review_verdict
from lib.domain.workflow_quality import accepted


CLAIM_REVIEW_PROTOCOL_VERSION = 2
_REFERENCE_FIELDS = {"claim_review_protocol_version", "phase", "scope", "coverage", "reference_points", "limitations"}
_REFERENCE_SCOPE = {"message_index", "prefix_sha256", "source_input_sha256", "source_evidence_sha256",
                    "reasoning_required", "review_requirements_sha256", "scope_sha256"}
_SEMANTIC_STATUS = "reviewer_reported_not_independently_verified"


def _hash_without_scope_hash(body):
    projected = deepcopy(body)
    projected["scope"].pop("scope_sha256", None)
    return v1._digest(projected)


def _valid_hash(value):
    return isinstance(value, str) and len(value) == 64 and all(char in "0123456789abcdef" for char in value)


def _basis(basis, request):
    if (not isinstance(basis, dict) or set(basis) != {"kind", "evidence", "derivation"}
            or not isinstance(basis["kind"], str) or basis["kind"] not in {"source", "context", "logic"}):
        raise ValueError("invalid_blind_review_basis")
    text = v1._text(basis["derivation"], "missing_blind_review_derivation")
    if text.strip().strip(".。!！ ").casefold() in {"correct", "supported", "valid", "consistent", "looks good",
            "no issues", "all good", "all correct", "正确", "有依据", "没有问题", "逻辑正确", "符合资料"}:
        raise ValueError("vacuous_blind_review_derivation")
    if not isinstance(basis["evidence"], list) or basis["kind"] != "logic" and not basis["evidence"]:
        raise ValueError("missing_blind_review_evidence")
    for reference in basis["evidence"]:
        v1._reference(reference, request, basis["kind"])


def blind_review_requests(target, payload, evidence, reasoning_required=False, review_requirements=None):
    """Return inputs whose identities do not depend on the current answer.

    Only actual messages strictly before each assistant are model-visible.
    No current answer, explanation, target_fields, full-candidate hash, prior
    grade or repair instruction is exposed. A replacement assistant body can
    therefore reuse the exact same blind request and paid checkpoint.
    """
    if (not isinstance(target, str) or target not in {"sft", "multiturn", "cot"}
            or not isinstance(payload, dict) or not payload or type(reasoning_required) is not bool):
        raise ValueError("invalid_blind_review_candidate")
    requirements = v1._requirements(review_requirements)
    messages = learner_messages(target, payload)
    if not isinstance(messages, list) or not messages:
        raise ValueError("blind_review_missing_messages")
    projected = [v1._message_projection(message) for message in messages]
    source = v1._source_projection(evidence)
    result = []
    for index, message in enumerate(messages):
        if message.get("role") != "assistant":
            continue
        if index == 0:
            raise ValueError("blind_review_missing_prefix")
        scope = {"message_index": index, "prefix_sha256": v1._digest(messages[:index]),
                 "source_input_sha256": v1._digest(evidence), "source_evidence_sha256": v1._digest(source),
                 "reasoning_required": reasoning_required, "review_requirements_sha256": v1._digest(requirements)}
        request = {"claim_review_protocol_version": CLAIM_REVIEW_PROTOCOL_VERSION, "phase": "blind_reference",
                   "target": target, "scope": scope, "messages": deepcopy(projected[:index]),
                   "source_evidence": deepcopy(source)}
        if requirements is not None:
            request["review_requirements"] = deepcopy(requirements)
        scope["scope_sha256"] = _hash_without_scope_hash(request)
        result.append(request)
    if not result:
        raise ValueError("blind_review_missing_assistant")
    return result


def _validate_blind_request(request):
    fields = {"claim_review_protocol_version", "phase", "target", "scope", "messages", "source_evidence"}
    if (not isinstance(request, dict) or set(request) not in (fields, fields | {"review_requirements"})
            or type(request["claim_review_protocol_version"]) is not int
            or request["claim_review_protocol_version"] != CLAIM_REVIEW_PROTOCOL_VERSION
            or request["phase"] != "blind_reference" or not isinstance(request["target"], str)
            or request["target"] not in {"sft", "multiturn", "cot"}):
        raise ValueError("invalid_blind_review_request")
    scope = request["scope"]
    if (not isinstance(scope, dict) or set(scope) != _REFERENCE_SCOPE
            or type(scope["message_index"]) is not int or scope["message_index"] <= 0
            or type(scope["reasoning_required"]) is not bool):
        raise ValueError("invalid_blind_review_scope")
    if any(not _valid_hash(scope[key]) for key in _REFERENCE_SCOPE - {"message_index", "reasoning_required"}):
        raise ValueError("invalid_blind_review_scope")
    requirements = v1._requirements(request.get("review_requirements"))
    if "review_requirements" in request and requirements is None:
        raise ValueError("invalid_blind_review_requirements")
    if (scope["scope_sha256"] != _hash_without_scope_hash(request)
            or scope["source_evidence_sha256"] != v1._digest(request["source_evidence"])
            or scope["review_requirements_sha256"] != v1._digest(requirements)):
        raise ValueError("stale_blind_review_request")
    messages = request["messages"]
    if not isinstance(messages, list) or len(messages) != scope["message_index"]:
        raise ValueError("invalid_blind_review_prefix")
    if any(v1._message_projection(message) != message for message in messages):
        raise ValueError("invalid_blind_review_message_fields")


def validate_blind_review(value, request):
    """Bind reference notes to their real blind input without certifying them."""
    _validate_blind_request(request)
    if (not isinstance(value, dict) or set(value) != _REFERENCE_FIELDS
            or type(value["claim_review_protocol_version"]) is not int
            or value["claim_review_protocol_version"] != CLAIM_REVIEW_PROTOCOL_VERSION
            or value["phase"] != "blind_reference"):
        raise ValueError("invalid_blind_review_response")
    if value["scope"] != request["scope"]:
        raise ValueError("stale_blind_review_response")
    if not isinstance(value["coverage"], str) or value["coverage"] not in {"complete", "partial"}:
        raise ValueError("invalid_blind_review_coverage")
    points = value["reference_points"]
    if not isinstance(points, list) or not points:
        raise ValueError("missing_blind_review_reference_points")
    seen = set()
    for point in points:
        if not isinstance(point, dict) or set(point) != {"id", "point", "severity", "basis"}:
            raise ValueError("invalid_blind_review_reference_point")
        identifier = point["id"]
        if (not isinstance(identifier, str) or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}", identifier) is None
                or identifier in seen):
            raise ValueError("invalid_blind_review_reference_id")
        seen.add(identifier)
        v1._text(point["point"], "missing_blind_review_reference_text")
        if not isinstance(point["severity"], str) or point["severity"] not in {"material", "minor"}:
            raise ValueError("invalid_blind_review_severity")
        _basis(point["basis"], request)
    limitations = value["limitations"]
    if not isinstance(limitations, list):
        raise ValueError("invalid_blind_review_limitations")
    for limitation in limitations:
        v1._text(limitation, "invalid_blind_review_limitation")
    return {"claim_review_protocol_version": CLAIM_REVIEW_PROTOCOL_VERSION, "phase": "blind_reference",
            "request": deepcopy(request), "raw_response": deepcopy(value), "raw_response_sha256": v1._digest(value), "structure_verified": True,
            "semantic_verification": _SEMANTIC_STATUS}


def _revalidate_blind(receipt):
    if not isinstance(receipt, dict) or not isinstance(receipt.get("request"), dict):
        raise ValueError("invalid_blind_review_receipt")
    expected = validate_blind_review(receipt.get("raw_response"), receipt["request"])
    if receipt != expected:
        raise ValueError("altered_blind_review_receipt")
    return expected


def claim_review_requests_v2(target, payload, evidence, reasoning_required=False, review_requirements=None, *, blind_reviews):
    """Attach only matching blind notes to a new exact candidate review."""
    expected_blind = blind_review_requests(target, payload, evidence, reasoning_required, review_requirements)
    if not isinstance(blind_reviews, list) or len(blind_reviews) != len(expected_blind):
        raise ValueError("incomplete_blind_review_receipts")
    by_index = {}
    for receipt in blind_reviews:
        receipt = _revalidate_blind(receipt)
        index = receipt["request"]["scope"]["message_index"]
        if index in by_index:
            raise ValueError("duplicate_blind_review_receipt")
        by_index[index] = receipt
    for request in expected_blind:
        receipt = by_index.get(request["scope"]["message_index"])
        if receipt is None or receipt["request"] != request:
            raise ValueError("stale_blind_review_receipt")
    requests = v1.claim_review_requests(target, payload, evidence, reasoning_required, review_requirements)
    for request in requests:
        receipt = by_index[request["scope"]["message_index"]]
        request["claim_review_protocol_version"] = CLAIM_REVIEW_PROTOCOL_VERSION
        request["reference_review"] = deepcopy(receipt["raw_response"])
        request["scope"]["blind_review_sha256"] = v1._digest(receipt["raw_response"])
        request["scope"]["blind_request_sha256"] = v1._digest(receipt["request"])
        request["scope"]["scope_sha256"] = _hash_without_scope_hash(request)
    return requests


def _grade_components(request):
    fields = {"claim_review_protocol_version", "target", "scope", "messages", "target_fields", "source_evidence", "reference_review"}
    if (not isinstance(request, dict) or set(request) not in (fields, fields | {"review_requirements"})
            or type(request["claim_review_protocol_version"]) is not int
            or request["claim_review_protocol_version"] != CLAIM_REVIEW_PROTOCOL_VERSION):
        raise ValueError("invalid_claim_review_v2_request")
    scope = request["scope"]
    if (not isinstance(scope, dict) or not _valid_hash(scope.get("blind_review_sha256"))
            or not _valid_hash(scope.get("blind_request_sha256"))
            or scope.get("scope_sha256") != _hash_without_scope_hash(request)):
        raise ValueError("stale_claim_review_v2_request")
    reference = request["reference_review"]
    if not isinstance(reference, dict) or v1._digest(reference) != scope["blind_review_sha256"]:
        raise ValueError("stale_claim_review_blind_response")
    blind_request = {"claim_review_protocol_version": CLAIM_REVIEW_PROTOCOL_VERSION, "phase": "blind_reference",
                     "target": request["target"], "scope": deepcopy(reference.get("scope")),
                     "messages": deepcopy(request["messages"][:-1]), "source_evidence": deepcopy(request["source_evidence"])}
    if "review_requirements" in request:
        blind_request["review_requirements"] = deepcopy(request["review_requirements"])
    blind = validate_blind_review(reference, blind_request)
    if (v1._digest(blind_request) != scope["blind_request_sha256"]
            or blind_request["scope"]["source_input_sha256"] != scope.get("source_input_sha256")
            or blind_request["scope"]["source_evidence_sha256"] != scope.get("source_evidence_sha256")
            or blind_request["scope"]["reasoning_required"] != scope.get("reasoning_required")
            or blind_request["scope"]["message_index"] != scope.get("message_index")):
        raise ValueError("stale_claim_review_blind_request")
    core = deepcopy(request)
    core.pop("reference_review")
    core["claim_review_protocol_version"] = 1
    core["scope"].pop("blind_review_sha256")
    core["scope"].pop("blind_request_sha256")
    core["scope"]["scope_sha256"] = _hash_without_scope_hash(core)
    v1._validate_request(core)
    return core, blind


def validate_claim_review_v2(value, request):
    """Require every blind point comparison without requiring its repetition."""
    core, blind = _grade_components(request)
    if (not isinstance(value, dict) or set(value) != v1._RESPONSE_FIELDS | {"reference_checks"}
            or type(value["claim_review_protocol_version"]) is not int
            or value["claim_review_protocol_version"] != CLAIM_REVIEW_PROTOCOL_VERSION):
        raise ValueError("invalid_claim_review_v2_response")
    if value["scope"] != request["scope"]:
        raise ValueError("stale_claim_review_v2_response")
    raw_core = {key: deepcopy(part) for key, part in value.items() if key != "reference_checks"}
    raw_core["claim_review_protocol_version"] = 1
    raw_core["scope"] = deepcopy(core["scope"])
    previous = v1.validate_claim_review(raw_core, core)
    points = {point["id"]: point for point in blind["raw_response"]["reference_points"]}
    checks = value["reference_checks"]
    if not isinstance(checks, list) or len(checks) != len(points):
        raise ValueError("incomplete_claim_review_reference_checks")
    seen, failures = set(), []
    for check in checks:
        expected_fields = {"id", "relation", "field", "quote", "reason"}
        if isinstance(check, dict) and check.get("relation") == "reference_error":
            expected_fields.add("counter_evidence")
        if not isinstance(check, dict) or set(check) != expected_fields:
            raise ValueError("invalid_claim_review_reference_check")
        identifier, relation = check["id"], check["relation"]
        if not isinstance(identifier, str) or identifier not in points or identifier in seen:
            raise ValueError("invalid_claim_review_reference_check_id")
        seen.add(identifier)
        if not isinstance(relation, str) or relation not in {"consistent", "contradicted", "not_claimed", "uncertain", "reference_error"}:
            raise ValueError("invalid_claim_review_reference_relation")
        v1._text(check["reason"], "missing_claim_review_reference_reason")
        if relation == "not_claimed":
            if check["field"] is not None or check["quote"] != "":
                raise ValueError("invalid_claim_review_not_claimed_reference")
        elif (not isinstance(check["field"], str) or check["field"] not in request["target_fields"]
              or not isinstance(check["quote"], str) or not check["quote"].strip()
              or check["quote"] not in request["target_fields"][check["field"]]):
            raise ValueError("fabricated_claim_review_reference_quote")
        if relation == "reference_error":
            counter = check["counter_evidence"]
            if not isinstance(counter, dict) or counter.get("kind") not in ("source", "context"):
                raise ValueError("unsubstantiated_claim_review_reference_error")
            _basis(counter, blind["request"])
            supported = [claim for claim in value["claim_checks"]
                         if claim["field"] == check["field"] and claim["quote"] == check["quote"]
                         and claim["status"] == "supported" and claim["basis"]["kind"] in {"source", "context"}
                         and claim["basis"]["evidence"]]
            if not supported:
                raise ValueError("unsubstantiated_claim_review_reference_error")
        if points[identifier]["severity"] == "material" and relation in {"contradicted", "uncertain"}:
            failures.append(check)
    derived = deepcopy(previous["verdict"])
    partial = blind["raw_response"]["coverage"] != "complete"
    if failures or partial:
        derived["keep"] = False
        derived["grounded"] = False
        derived["reasoning_valid"] = False
        explanations = (["Blind reference coverage is partial; unchecked issues remain unverified."] if partial else [])
        explanations.extend(f"reference {item['id']}: {item['relation']} at {item['field']}: {item['quote']!r}: {item['reason']}" for item in failures)
        derived["reason"] = "; ".join([derived["reason"], *explanations])
    turn = {**deepcopy(derived), "message_index": scope_index(request),
            "content_sha256": request["scope"]["prefix_sha256"], "leakage_free": derived["reasoning_valid"],
            "critical_error": previous["turn_verdict"]["critical_error"] or bool(failures) or partial}
    return {"claim_review_protocol_version": CLAIM_REVIEW_PROTOCOL_VERSION,
            "request": deepcopy(request), "raw_response": deepcopy(value), "verdict": derived,
            "turn_verdict": turn, "accepted": accepted(derived) and not turn["critical_error"],
            "structure_verified": True, "semantic_verification": _SEMANTIC_STATUS}


def scope_index(request):
    return request["scope"]["message_index"]


def merge_claim_reviews_v2(payload, receipts):
    """Rebuild current requests and revalidate both linked assessment phases."""
    if not isinstance(receipts, list) or not receipts:
        raise ValueError("missing_claim_review_v2_receipts")
    checked, signatures, original = [], set(), None
    for receipt in receipts:
        if not isinstance(receipt, dict) or not isinstance(receipt.get("request"), dict):
            raise ValueError("invalid_claim_review_v2_receipt")
        request = receipt["request"]
        expected = validate_claim_review_v2(receipt.get("raw_response"), request)
        if receipt != expected:
            raise ValueError("altered_claim_review_v2_receipt")
        if request["scope"]["candidate_sha256"] != v1._digest(payload):
            raise ValueError("stale_claim_review_v2_candidate")
        core, blind = _grade_components(request)
        rebuilt_blind = blind_review_requests(request["target"], payload, request["source_evidence"],
            request["scope"]["reasoning_required"], request.get("review_requirements"))
        # Raw source bookkeeping can be absent from the model-facing source;
        # the engine separately checks the original loader source input hash.
        matching = next((item for item in rebuilt_blind if scope_index(item) == scope_index(request)), None)
        if matching is None:
            raise ValueError("stale_claim_review_v2_prefix")
        matching["scope"]["source_input_sha256"] = request["scope"]["source_input_sha256"]
        matching["scope"]["scope_sha256"] = _hash_without_scope_hash(matching)
        if matching != blind["request"]:
            raise ValueError("stale_claim_review_v2_blind_prefix")
        rebuilt_core = v1.claim_review_requests(request["target"], payload, request["source_evidence"],
            request["scope"]["reasoning_required"], request.get("review_requirements"))
        matching_core = next(item for item in rebuilt_core if scope_index(item) == scope_index(request))
        matching_core["scope"]["source_input_sha256"] = request["scope"]["source_input_sha256"]
        matching_core["scope"]["scope_sha256"] = _hash_without_scope_hash(matching_core)
        if matching_core != core:
            raise ValueError("stale_claim_review_v2_prefix")
        signature = (request["target"], request["scope"]["source_input_sha256"], request["scope"]["source_evidence_sha256"],
                     request["scope"]["reasoning_required"], v1._digest(request.get("review_requirements")))
        signatures.add(signature)
        if len(signatures) != 1:
            raise ValueError("inconsistent_claim_review_v2_scopes")
        original = request
        checked.append(expected)
    indices = [scope_index(item["request"]) for item in checked]
    expected_indices = [scope_index(item) for item in blind_review_requests(original["target"], payload,
        original["source_evidence"], original["scope"]["reasoning_required"], original.get("review_requirements"))]
    if len(set(indices)) != len(indices) or sorted(indices) != expected_indices:
        raise ValueError("incomplete_claim_review_v2_turns")
    checked.sort(key=lambda item: scope_index(item["request"]))
    first = checked[0]["verdict"]
    aggregate = {"keep": all(item["verdict"]["keep"] for item in checked),
                 "grounded": all(item["verdict"]["grounded"] for item in checked),
                 "reasoning_valid": all(item["verdict"]["reasoning_valid"] for item in checked),
                 "correctness": min(item["verdict"]["correctness"] for item in checked),
                 "scores": {name: min(item["verdict"]["scores"][name] for item in checked) for name in first["scores"]},
                 "reason": "; ".join(f"assistant[{scope_index(item['request'])}]: {item['verdict']['reason']}" for item in checked)}
    if original["target"] == "cot":
        return aggregate
    merged = turn_review_verdict({**aggregate, "turn_verdicts": [item["turn_verdict"] for item in checked]},
                                 supervised_assistant_turns(payload))
    return {key: deepcopy(merged[key]) for key in (*v1._GRADE_FIELDS, "turn_verdicts")}
