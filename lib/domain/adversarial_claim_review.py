"""Candidate-visible challenges and independent, program-anchored adjudication.

These receipts bind model opinions to exact current inputs. Neither a
challenge nor its dismissal is an independently proved factual result.
The provider's protocol-four response is never rewritten as protocol one;
the latter is a separately labelled program normalization for compatibility.
"""
from copy import deepcopy
import re

from lib.domain import claim_review as v1
from lib.domain.reasoning_fields import REASONING_FIELDS
from lib.domain.review_target_units import target_units, validate_target_units
from lib.domain.turn_review import supervised_assistant_turns, turn_review_verdict
from lib.domain.workflow_quality import accepted


_CORE = {"claim_review_protocol_version", "target", "scope", "messages", "target_fields", "source_evidence"}
_CHALLENGE = {"claim_review_protocol_version", "phase", "scope_sha256", "issues", "limitations"}
_GRADE = {"claim_review_protocol_version", "scope_sha256", "coverage", "checked_unit_ids",
          "claim_checks", "challenge_checks", "verdict"}
_SEMANTICS = "reviewer_reported_not_independently_verified"


def _request_hash(request):
    value = deepcopy(request)
    value["scope"].pop("scope_sha256", None)
    return v1._digest(value)


def _challenge_request(core, condition_contract_version=None):
    request = deepcopy(core)
    if condition_contract_version is not None:
        if type(condition_contract_version) is not int or condition_contract_version != 1:
            raise ValueError("invalid_claim_review_condition_contract_version")
        request["condition_contract_version"] = 1
    request.update(claim_review_protocol_version=4, phase="claim_challenge",
                   target_units=target_units(core["target_fields"], core["scope"]["message_index"],
                       segmentation_version=2 if condition_contract_version == 1 else None))
    request["scope"]["scope_sha256"] = _request_hash(request)
    return request


def _core(request, phase):
    fields = _CORE | {"phase", "target_units"}
    contract = request.get("condition_contract_version") if isinstance(request, dict) else None
    if contract is not None:
        if type(contract) is not int or contract != 1:
            raise ValueError("invalid_claim_review_condition_contract_version")
        fields |= {"condition_contract_version"}
    if phase == "claim_adjudication":
        fields |= {"challenge_review"}
    if (not isinstance(request, dict) or set(request) not in (fields, fields | {"review_requirements"})
            or type(request.get("claim_review_protocol_version")) is not int
            or request["claim_review_protocol_version"] != 4 or request.get("phase") != phase
            or not isinstance(request.get("scope"), dict)
            or request["scope"].get("scope_sha256") != _request_hash(request)):
        raise ValueError("invalid_adversarial_review_request")
    validate_target_units(request["target_units"], request.get("target_fields"),
                          request["scope"].get("message_index"),
                          segmentation_version=2 if contract == 1 else None)
    core = {key: deepcopy(value) for key, value in request.items()
            if key not in {"phase", "target_units", "challenge_review", "condition_contract_version"}}
    core["claim_review_protocol_version"] = 1
    if phase == "claim_adjudication":
        core["scope"].pop("challenge_request_sha256", None)
        core["scope"].pop("challenge_review_sha256", None)
    core["scope"]["scope_sha256"] = _request_hash(core)
    v1._validate_request(core)
    return core


def _basis(basis, request, quote=None):
    if (not isinstance(basis, dict) or set(basis) != {"kind", "evidence", "derivation"}
            or not isinstance(basis["kind"], str) or basis["kind"] not in {"source", "context", "logic"}):
        raise ValueError("invalid_adversarial_basis")
    derivation = v1._text(basis["derivation"], "missing_adversarial_derivation")
    if (derivation.strip().strip(".。!！ ").casefold() in {"correct", "supported", "valid", "consistent",
            "looks good", "no issues", "all good", "all correct", "正确", "有依据", "没有问题", "逻辑正确", "符合资料"}
            or quote is not None and derivation.strip() == quote.strip()):
        raise ValueError("vacuous_adversarial_derivation")
    if not isinstance(basis["evidence"], list) or basis["kind"] != "logic" and not basis["evidence"]:
        raise ValueError("missing_adversarial_evidence")
    for evidence in basis["evidence"]:
        v1._reference(evidence, request, basis["kind"])


def challenge_review_requests(target, payload, evidence, reasoning_required=False, review_requirements=None,
                              *, condition_contract_version=None):
    """The challenger sees this exact candidate, never a fake blind reference."""
    return [_challenge_request(core, condition_contract_version) for core in v1.claim_review_requests(
        target, payload, evidence, reasoning_required, review_requirements)]


def validate_challenge_review(value, request):
    core = _core(request, "claim_challenge")
    contract = request.get("condition_contract_version") == 1
    fields = _CHALLENGE | ({"condition_ledger"} if contract else set())
    if (not isinstance(value, dict) or set(value) != fields
            or type(value["claim_review_protocol_version"]) is not int
            or value["claim_review_protocol_version"] != 4 or value["phase"] != "claim_challenge"):
        raise ValueError("invalid_challenge_review_response")
    if value["scope_sha256"] != request["scope"]["scope_sha256"]:
        raise ValueError("stale_challenge_review_response")
    if not isinstance(value["issues"], list):
        raise ValueError("invalid_challenge_review_issues")
    ledger = None
    if contract:
        from lib.domain.review_condition_contract import condition_ledger
        ledger = condition_ledger(value["condition_ledger"], request)
        # The first context inventories the literal candidate and its scope.
        # It does not supply a fallible verdict for the second context to defend.
        if value["issues"]:
            raise ValueError("condition_inventory_contains_verdict")
    units = {unit["id"] for unit in request["target_units"]}
    seen = set()
    for issue in value["issues"]:
        if not isinstance(issue, dict) or set(issue) != {"id", "unit_id", "severity", "problem", "counterexample", "basis"}:
            raise ValueError("invalid_challenge_review_issue")
        identifier = issue["id"]
        if (not isinstance(identifier, str) or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}", identifier) is None
                or identifier in seen):
            raise ValueError("invalid_challenge_review_issue_id")
        seen.add(identifier)
        if not isinstance(issue["unit_id"], str) or issue["unit_id"] not in units:
            raise ValueError("invalid_challenge_review_unit_id")
        if not isinstance(issue["severity"], str) or issue["severity"] not in {"material", "minor"}:
            raise ValueError("invalid_challenge_review_severity")
        v1._text(issue["problem"], "missing_challenge_review_problem")
        v1._text(issue["counterexample"], "missing_challenge_review_counterexample")
        _basis(issue["basis"], core)
    if not isinstance(value["limitations"], list):
        raise ValueError("invalid_challenge_review_limitations")
    for limitation in value["limitations"]:
        v1._text(limitation, "missing_challenge_review_limitation")
    receipt = {"claim_review_protocol_version": 4, "phase": "claim_challenge", "request": deepcopy(request),
            "raw_response": deepcopy(value), "raw_response_sha256": v1._digest(value),
            "structure_verified": True, "semantic_verification": _SEMANTICS}
    if ledger is not None:
        receipt["condition_ledger"] = ledger
    return receipt


def _challenge_receipt(receipt):
    if not isinstance(receipt, dict) or not isinstance(receipt.get("request"), dict):
        raise ValueError("invalid_challenge_review_receipt")
    expected = validate_challenge_review(receipt.get("raw_response"), receipt["request"])
    if receipt != expected:
        raise ValueError("altered_challenge_review_receipt")
    return expected


def adversarial_claim_review_requests(target, payload, evidence, reasoning_required=False,
                                      review_requirements=None, *, challenge_reviews, condition_contract_version=None):
    cores = v1.claim_review_requests(target, payload, evidence, reasoning_required, review_requirements)
    if not isinstance(challenge_reviews, list) or len(challenge_reviews) != len(cores):
        raise ValueError("incomplete_challenge_review_receipts")
    by_index = {}
    for original in challenge_reviews:
        receipt = _challenge_receipt(original)
        index = receipt["request"]["scope"]["message_index"]
        if index in by_index:
            raise ValueError("duplicate_challenge_review_receipt")
        by_index[index] = receipt
    requests = []
    for core in cores:
        challenge = _challenge_request(core, condition_contract_version)
        receipt = by_index.get(core["scope"]["message_index"])
        if receipt is None or receipt["request"] != challenge:
            raise ValueError("stale_challenge_review_receipt")
        request = deepcopy(core)
        if condition_contract_version is not None:
            request["condition_contract_version"] = condition_contract_version
        request.update(claim_review_protocol_version=4, phase="claim_adjudication",
            target_units=deepcopy(challenge["target_units"]), challenge_review=deepcopy(receipt["raw_response"]))
        request["scope"].update(challenge_request_sha256=v1._digest(challenge),
                                challenge_review_sha256=receipt["raw_response_sha256"])
        request["scope"]["scope_sha256"] = _request_hash(request)
        requests.append(request)
    return requests


def _grade_core(request):
    core = _core(request, "claim_adjudication")
    challenge = _challenge_request(core, request.get("condition_contract_version"))
    receipt = validate_challenge_review(request["challenge_review"], challenge)
    if (request["scope"].get("challenge_request_sha256") != v1._digest(challenge)
            or request["scope"].get("challenge_review_sha256") != receipt["raw_response_sha256"]):
        raise ValueError("invalid_adversarial_review_request")
    return core, receipt


def validate_adversarial_claim_review(value, request):
    core, challenge = _grade_core(request)
    contract = request.get("condition_contract_version") == 1
    if (not isinstance(value, dict) or set(value) != _GRADE
            or type(value["claim_review_protocol_version"]) is not int or value["claim_review_protocol_version"] != 4):
        raise ValueError("invalid_adversarial_review_response")
    if value["scope_sha256"] != request["scope"]["scope_sha256"]:
        raise ValueError("stale_adversarial_review_response")
    units = {unit["id"]: unit for unit in request["target_units"]}
    checked = value["checked_unit_ids"]
    if (not isinstance(checked, list) or any(not isinstance(identifier, str) for identifier in checked)
            or len(checked) != len(set(checked)) or set(checked) != set(units)):
        raise ValueError("invalid_adversarial_unit_coverage")
    claims = value["claim_checks"]
    if not isinstance(claims, list) or len(claims) != len(units):
        raise ValueError("invalid_adversarial_unit_coverage")
    by_unit, condition_checks, unresolved = {}, [], []
    for claim in claims:
        fields = {"unit_id", "status", "severity", "basis"} | ({"premises"} if contract else set())
        if not isinstance(claim, dict) or set(claim) != fields:
            raise ValueError("invalid_adversarial_claim")
        identifier = claim["unit_id"]
        if not isinstance(identifier, str) or identifier not in units or identifier in by_unit:
            raise ValueError("invalid_adversarial_unit_id")
        if (not isinstance(claim["status"], str) or claim["status"] not in {"supported", "refuted", "insufficient"}
                or not isinstance(claim["severity"], str) or claim["severity"] not in {"material", "minor"}):
            raise ValueError("invalid_claim_review_claim_status")
        _basis(claim["basis"], core, units[identifier]["text"])
        if contract and any("message_index" in evidence
                and core["messages"][evidence["message_index"]]["role"] == "assistant"
                for evidence in claim["basis"]["evidence"]):
            # An earlier answer can resolve a referent through premises, but
            # cannot certify its own assertion through the old basis channel.
            raise ValueError("condition_historical_assistant_not_evidence")
        effective = deepcopy(claim)
        if contract:
            from lib.domain.review_condition_contract import validate_premises
            premises = validate_premises(claim["premises"], request)
            assumed_support = claim["status"] == "supported" and bool(
                premises["required_assumptions"] or premises["counterexample_assumptions"])
            if assumed_support:
                effective["status"] = "insufficient"
            if effective["status"] == "insufficient":
                unresolved.append(identifier)
            condition_checks.append({"unit_id": identifier, **premises,
                "reported_status": claim["status"], "effective_status": effective["status"],
                "unsupported_assumption_used_for_support": bool(assumed_support)})
        by_unit[identifier] = effective
    issues = {issue["id"]: issue for issue in challenge["raw_response"]["issues"]}
    decisions = value["challenge_checks"]
    if not isinstance(decisions, list) or len(decisions) != len(issues):
        raise ValueError("invalid_adversarial_challenge_checks")
    seen, blocked_issues = set(), []
    for decision in decisions:
        if not isinstance(decision, dict) or set(decision) != {"id", "unit_id", "decision", "basis"}:
            raise ValueError("invalid_adversarial_challenge_check")
        identifier = decision["id"]
        if not isinstance(identifier, str) or identifier not in issues or identifier in seen:
            raise ValueError("invalid_adversarial_challenge_check")
        seen.add(identifier)
        issue = issues[identifier]
        if decision["unit_id"] != issue["unit_id"]:
            raise ValueError("unbound_adversarial_challenge_check")
        if not isinstance(decision["decision"], str) or decision["decision"] not in {"confirmed", "dismissed", "unresolved"}:
            raise ValueError("invalid_adversarial_challenge_decision")
        _basis(decision["basis"], core)
        if decision["decision"] == "confirmed" and by_unit[issue["unit_id"]]["status"] != "refuted":
            raise ValueError("unconfirmed_adversarial_refutation")
        if decision["decision"] == "confirmed" or decision["decision"] == "unresolved" and issue["severity"] == "material":
            blocked_issues.append((issue, decision))
    # Equal literal paragraphs may appear at different offsets. Preserve each
    # raw unit decision while giving the old quote-based normalizer its most
    # conservative decision for that exact field/text pair.
    by_quote, ranks = {}, {"supported": 0, "insufficient": 1, "refuted": 2}
    for identifier, unit in units.items():
        claim = by_unit[identifier]
        normalized = {"field": unit["field"], "quote": unit["text"],
                      **{key: deepcopy(claim[key]) for key in ("status", "severity", "basis")}}
        key = unit["field"], unit["text"]
        previous = by_quote.get(key)
        if previous is None or (ranks[claim["status"]], claim["severity"] == "material") > (
                ranks[previous["status"]], previous["severity"] == "material"):
            by_quote[key] = normalized
    normalized = {"claim_review_protocol_version": 1, "scope": deepcopy(core["scope"]),
        "coverage": deepcopy(value["coverage"]), "checked_fields": list(core["target_fields"]),
        "claim_checks": list(by_quote.values()), "verdict": deepcopy(value["verdict"])}
    primary = v1.validate_claim_review(normalized, core)
    derived = deepcopy(primary["verdict"])
    refuted = [claim for claim in by_unit.values() if claim["status"] == "refuted"]
    material_missing = [claim for claim in by_unit.values() if claim["status"] == "insufficient" and claim["severity"] == "material"]
    blocked_claims = refuted + material_missing
    if blocked_claims or blocked_issues:
        derived["keep"] = False
        if blocked_issues or any(units[claim["unit_id"]]["field"] in REASONING_FIELDS
                                 or claim["basis"]["kind"] == "logic" for claim in blocked_claims):
            derived["reasoning_valid"] = False
        if blocked_issues or any(claim["basis"]["kind"] in {"source", "context"} for claim in blocked_claims):
            derived["grounded"] = False
        details = [f"{claim['unit_id']}: reviewer {claim['status']} {claim['severity']} claim" for claim in blocked_claims]
        details += [f"challenge {issue['id']}: {decision['decision']} ({issue['severity']})"
                    for issue, decision in blocked_issues]
        derived["reason"] = "; ".join([derived["reason"], *details])
    review_gap = contract and (bool(unresolved) or value["coverage"] != "complete")
    if review_gap:
        # Unproved support is a review gap, not proof the original is false.
        # A later orchestration guard stops automatic answer rewriting here.
        if not refuted and not blocked_issues:
            derived = deepcopy(value["verdict"])
        derived["keep"] = False
        derived["reason"] += "; condition_support_pending_review: " + ", ".join(unresolved)
    critical = (primary["turn_verdict"]["critical_error"] or bool(material_missing)
                or any(claim["severity"] == "material" for claim in refuted)
                or any(issue["severity"] == "material" for issue, _ in blocked_issues))
    if contract:
        # Review coverage/support gaps are unverified, not known critical
        # errors. Preserve the legacy normalizer separately for replay.
        critical = any(claim["severity"] == "material" for claim in refuted)
    turn = {**deepcopy(derived), "message_index": core["scope"]["message_index"],
            "content_sha256": core["scope"]["prefix_sha256"],
            "leakage_free": derived["reasoning_valid"], "critical_error": critical}
    receipt = {"claim_review_protocol_version": 4, "request": deepcopy(request), "raw_response": deepcopy(value),
            "normalized_response": normalized, "normalized_receipt": primary, "verdict": derived,
            "turn_verdict": turn, "accepted": accepted(derived) and not critical,
            "structure_verified": True, "semantic_verification": _SEMANTICS}
    if contract:
        receipt["condition_guard"] = {"version": 1, "checks": condition_checks,
            "needs_review": bool(review_gap), "unresolved_unit_ids": unresolved,
            "coverage_complete": value["coverage"] == "complete",
            "semantic_verification": "provenance_only_not_entailment"}
    return receipt


def merge_adversarial_claim_reviews(payload, receipts):
    if not isinstance(receipts, list) or not receipts:
        raise ValueError("missing_adversarial_review_receipts")
    checked = []
    for receipt in receipts:
        if not isinstance(receipt, dict) or not isinstance(receipt.get("request"), dict):
            raise ValueError("invalid_adversarial_review_receipt")
        expected = validate_adversarial_claim_review(receipt.get("raw_response"), receipt["request"])
        if expected != receipt:
            raise ValueError("altered_adversarial_review_receipt")
        checked.append(expected)
    # Check every original candidate/prefix/source/requirements scope and the
    # complete set of assistant turns before aggregating the new veto policy.
    v1.merge_claim_reviews(payload, [receipt["normalized_receipt"] for receipt in checked])
    checked.sort(key=lambda receipt: receipt["request"]["scope"]["message_index"])
    first = checked[0]["verdict"]
    aggregate = {"keep": all(receipt["verdict"]["keep"] for receipt in checked),
        "grounded": all(receipt["verdict"]["grounded"] for receipt in checked),
        "reasoning_valid": all(receipt["verdict"]["reasoning_valid"] for receipt in checked),
        "correctness": min(receipt["verdict"]["correctness"] for receipt in checked),
        "scores": {key: min(receipt["verdict"]["scores"][key] for receipt in checked) for key in first["scores"]},
        "reason": "; ".join(f"assistant[{receipt['request']['scope']['message_index']}]: {receipt['verdict']['reason']}"
                            for receipt in checked)}
    if checked[0]["request"]["target"] == "cot":
        return aggregate
    merged = turn_review_verdict({**aggregate, "turn_verdicts": [receipt["turn_verdict"] for receipt in checked]},
                                 supervised_assistant_turns(payload))
    return {key: deepcopy(merged[key]) for key in (*v1._GRADE_FIELDS, "turn_verdicts")}
