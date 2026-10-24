"""Lossless bounded review responses with whole-prefix, source-bound inputs.

Windows limit the number of judgments returned, never the candidate/context.
Raw model replies stay in each window. The full assembled response is labelled
as a program aggregate, then passes the existing complete review validators.
"""
from __future__ import annotations

from copy import deepcopy

from lib.domain import adversarial_claim_review as protocol
from lib.domain.claim_review import _digest
from lib.domain.review_condition_contract import validate_premises
from lib.domain.workflow_quality import verdict
from .wire_schema import validate_review_wire_response


MAX_REVIEW_UNITS = 12
MAX_REVIEW_CHARACTERS = 2400


request_digest = _digest


def project_review_window_hints(request):
    """Send only this window's fallible ledger hints, never trim evidence.

    The full first-context receipt remains in the request checkpoint and audit.
    Its other entries are not facts and must not fill every later context.
    All candidate fields, reasoning, original units, source and visible history
    remain byte-for-byte equal. Label the view instead of claiming it is the
    complete signed inventory response.
    """
    if (not isinstance(request, dict) or request.get("phase") != "claim_adjudication"
            or "review_window" not in request):
        return request
    validate_review_window_request({key: value for key, value in request.items() if key != "format_repair"})
    projected = deepcopy(request)
    original = request["challenge_review"]
    selected = set(request["review_window"]["unit_ids"])
    projected["challenge_review"]["condition_ledger"] = [
        deepcopy(entry) for entry in original["condition_ledger"] if entry["unit_id"] in selected]
    projected["condition_ledger_view"] = {"version": 1, "scope": "current_review_window_only",
        "original_response_sha256": _digest(original), "original_unit_count": len(original["condition_ledger"]),
        "visible_unit_ids": deepcopy(request["review_window"]["unit_ids"]),
        "not_evidence": True}
    return projected


def review_window_request(request, unit_ids):
    """Bind a focus to the complete immutable parent, including other spans."""
    protocol._core(request, request.get("phase"))
    if request.get("condition_contract_version") != 1:
        raise ValueError("review_window_requires_condition_contract")
    all_ids = [unit["id"] for unit in request["target_units"]]
    if (not isinstance(unit_ids, list) or not unit_ids
            or any(not isinstance(identifier, str) for identifier in unit_ids)
            or unit_ids != [identifier for identifier in all_ids if identifier in unit_ids]):
        raise ValueError("invalid_review_window_units")
    window = {"version": 1, "parent_scope_sha256": request["scope"]["scope_sha256"],
              "unit_ids": deepcopy(unit_ids)}
    window["sha256"] = _digest(window)
    return {**deepcopy(request), "review_window": window}


def validate_review_window_request(request):
    if not isinstance(request, dict) or not isinstance(request.get("review_window"), dict):
        raise ValueError("invalid_review_window_request")
    if type(request["review_window"].get("version")) is not int:
        raise ValueError("invalid_review_window_request")
    parent = {key: deepcopy(value) for key, value in request.items() if key != "review_window"}
    if request != review_window_request(parent, request["review_window"].get("unit_ids")):
        raise ValueError("stale_review_window_request")
    return parent


def plan_review_windows(request):
    groups, current, characters = [], [], 0
    for unit in request["target_units"]:
        if current and (len(current) >= MAX_REVIEW_UNITS
                        or characters + len(unit["text"]) > MAX_REVIEW_CHARACTERS):
            groups.append(current)
            current, characters = [], 0
        current.append(unit["id"])
        characters += len(unit["text"])
    if current:
        groups.append(current)
    return [review_window_request(request, group) for group in groups]


def split_review_window(request):
    parent = validate_review_window_request(request)
    identifiers = request["review_window"]["unit_ids"]
    if len(identifiers) < 2:
        raise ValueError("review_window_indivisible")
    middle = len(identifiers) // 2
    return [review_window_request(parent, identifiers[:middle]),
            review_window_request(parent, identifiers[middle:])]


def validate_review_window_response(response, request):
    parent = validate_review_window_request(request)
    validate_review_wire_response(response, request)
    if type(response["claim_review_protocol_version"]) is not int:
        raise ValueError("invalid_claim_review_wire_response")
    focus = request["review_window"]["unit_ids"]
    core = protocol._core(parent, parent["phase"])
    if parent["phase"] == "claim_challenge":
        entries = response["condition_ledger"]
    else:
        entries = response["claim_checks"]
        if set(response["checked_unit_ids"]) != set(focus):
            raise ValueError("invalid_review_window_coverage")
        verdict(response["verdict"])
    identifiers = [entry["unit_id"] for entry in entries]
    if len(identifiers) != len(set(identifiers)) or set(identifiers) != set(focus):
        raise ValueError("invalid_review_window_coverage")
    units = {unit["id"]: unit for unit in parent["target_units"]}
    for entry in entries:
        validate_premises(entry["premises"], parent)
        if parent["phase"] == "claim_adjudication":
            protocol._basis(entry["basis"], core, units[entry["unit_id"]]["text"])
            if any("message_index" in evidence
                   and core["messages"][evidence["message_index"]]["role"] == "assistant"
                   for evidence in entry["basis"]["evidence"]):
                raise ValueError("condition_historical_assistant_not_evidence")
    return {"review_window_version": 1, "request": deepcopy(request), "raw_response": deepcopy(response),
            "structure_verified": True, "semantic_verification": "reviewer_reported_not_independently_verified"}


def assemble_review_windows(request, reviews):
    """No defaults, no missing/duplicated units, no partial-text recovery."""
    by_unit, responses = {}, []
    for review in reviews:
        if not isinstance(review, dict) or not isinstance(review.get("request"), dict):
            raise ValueError("invalid_review_window_receipt")
        if validate_review_window_request(review["request"]) != request:
            raise ValueError("stale_review_window_parent")
        if validate_review_window_response(review.get("raw_response"), review["request"]) != review:
            raise ValueError("altered_review_window_receipt")
        response = review["raw_response"]
        collection = "condition_ledger" if request["phase"] == "claim_challenge" else "claim_checks"
        for entry in response[collection]:
            if entry["unit_id"] in by_unit:
                raise ValueError("duplicate_review_window_coverage")
            by_unit[entry["unit_id"]] = deepcopy(entry)
        responses.append(response)
    ids = [unit["id"] for unit in request["target_units"]]
    if not responses or set(by_unit) != set(ids):
        raise ValueError("incomplete_review_window_coverage")
    assembled = {"claim_review_protocol_version": 4, "scope_sha256": request["scope"]["scope_sha256"]}
    if request["phase"] == "claim_challenge":
        assembled.update(phase="claim_challenge", issues=[],
            condition_ledger=[by_unit[identifier] for identifier in ids],
            limitations=[text for response in responses for text in response["limitations"]])
        receipt = protocol.validate_challenge_review(assembled, request)
    else:
        grades = [response["verdict"] for response in responses]
        aggregate = {name: all(grade[name] for grade in grades)
                     for name in ("keep", "grounded", "reasoning_valid")}
        aggregate.update(correctness=min(grade["correctness"] for grade in grades),
            scores={name: min(grade["scores"][name] for grade in grades) for name in grades[0]["scores"]},
            reason="; ".join(f"window[{index + 1}]: {grade['reason']}" for index, grade in enumerate(grades)))
        assembled.update(coverage="complete" if all(response["coverage"] == "complete" for response in responses)
                         else "partial", checked_unit_ids=ids, claim_checks=[by_unit[identifier] for identifier in ids],
                         challenge_checks=[], verdict=aggregate)
        receipt = protocol.validate_adversarial_claim_review(assembled, request)
    return receipt


def windowed_review_record(receipt, windows):
    """Keep the compatibility normalization distinct from actual raw replies."""
    record = deepcopy(receipt)
    record["assembled_response"] = record.pop("raw_response")
    record["response_origin"] = "program_aggregate_of_review_windows"
    return {"review": record, "windows": deepcopy(windows)}


def validate_windowed_review_record(record):
    if not isinstance(record, dict) or set(record) != {"review", "windows"}:
        raise ValueError("invalid_windowed_review_record")
    review = record["review"]
    if not isinstance(review, dict) or review.get("response_origin") != "program_aggregate_of_review_windows":
        raise ValueError("invalid_windowed_review_origin")
    windows = record["windows"]
    if not isinstance(windows, list) or not windows:
        raise ValueError("missing_review_windows")
    for window in windows:
        if not isinstance(window, dict) or set(window) != {"review", "checkpoint"}:
            raise ValueError("invalid_review_window_checkpoint")
        validate_review_window_checkpoint(window["review"], window["checkpoint"])
    receipt = assemble_review_windows(review["request"], [window["review"] for window in windows])
    if windowed_review_record(receipt, windows) != record:
        raise ValueError("altered_windowed_review_record")
    return receipt


def validate_review_window_checkpoint(review, reference):
    if not isinstance(reference, dict):
        raise ValueError("invalid_review_window_checkpoint")
    fields = {"stage", "key", "checkpoint_sha256", "request_sha256", "review_request_sha256", "response_sha256"}
    if (set(reference) - {"format_repair"} != fields or not isinstance(reference["stage"], str)
            or not reference["stage"] or not isinstance(reference["key"], list)):
        raise ValueError("invalid_review_window_checkpoint")
    request, key = review["request"], reference["key"]
    data = request
    if "format_repair" in reference:
        feedback = reference["format_repair"]
        code = ("invalid_challenge_review_response" if request["phase"] == "claim_challenge"
                else "invalid_adversarial_review_response")
        if (not isinstance(feedback, dict) or set(feedback) != {"code", "instruction"}
                or feedback["code"] != code or not isinstance(feedback["instruction"], str)
                or not feedback["instruction"].strip() or key[-2:] != ["format_repair", 1]):
            raise ValueError("invalid_review_window_checkpoint")
        data, key = {**request, "format_repair": feedback}, key[:-2]
    if (key != ["claim_window", request["phase"], request["review_window"]["sha256"]]
            or reference["checkpoint_sha256"] != _digest(["call", reference["key"]])
            or reference["request_sha256"] != _digest(data)
            or reference["review_request_sha256"] != _digest(request)
            or reference["response_sha256"] != _digest(review["raw_response"])):
        raise ValueError("stale_review_window_checkpoint")


def validate_windowed_claim_audit(payload, check, audit):
    fields = {"version", "method", "candidate_sha256", "source_input_sha256", "source_evidence_sha256",
              "status", "challenge_reviews", "assessments", "schema_failures", "window_failures", "derived_verdict"}
    if (not isinstance(audit, dict) or set(audit) != fields or type(audit["version"]) is not int
            or audit["version"] != 5 or audit["method"] != "windowed_condition_claim_review"
            or audit["status"] != "complete" or audit["candidate_sha256"] != _digest(payload)):
        raise ValueError("invalid_windowed_claim_audit")
    verdict(check)
    challenges, assessments, refs, requests_by_hash = {}, [], {}, {}
    for phase, collection in (("claim_challenge", audit["challenge_reviews"]),
                              ("claim_adjudication", audit["assessments"])):
        if not isinstance(collection, list) or not collection:
            raise ValueError("missing_windowed_claim_phase")
        seen = set()
        for record in collection:
            receipt = validate_windowed_review_record(record)
            request = receipt["request"]
            scope = request["scope"]
            requests_by_hash[_digest(request)] = request
            index = scope["message_index"]
            if (index in seen or request["phase"] != phase
                    or scope["candidate_sha256"] != audit["candidate_sha256"]
                    or scope["source_input_sha256"] != audit["source_input_sha256"]
                    or scope["source_evidence_sha256"] != audit["source_evidence_sha256"]):
                raise ValueError("stale_windowed_claim_scope")
            seen.add(index)
            for window in record["windows"]:
                reference = window["checkpoint"]
                refs[reference["review_request_sha256"]] = reference
            if phase == "claim_challenge":
                challenges[index] = receipt
            else:
                challenge = challenges.get(index)
                if (challenge is None or request["challenge_review"] != challenge["raw_response"]
                        or scope["challenge_review_sha256"] != _digest(challenge["raw_response"])
                        or scope["challenge_request_sha256"] != _digest(challenge["request"])):
                    raise ValueError("stale_windowed_challenge_binding")
                assessments.append(receipt)
    if set(challenges) != {receipt["request"]["scope"]["message_index"] for receipt in assessments}:
        raise ValueError("incomplete_windowed_claim_turns")
    failures = set()
    if not isinstance(audit["schema_failures"], list) or not isinstance(audit["window_failures"], list):
        raise ValueError("invalid_windowed_schema_history")
    for failure in audit["schema_failures"]:
        if (not isinstance(failure, dict)
                or set(failure) != {"phase", "message_index", "attempt", "code", "checkpoint"}
                or type(failure["attempt"]) is not int or type(failure["message_index"]) is not int
                or not isinstance(failure["code"], str) or not failure["code"]
                or not isinstance(failure["checkpoint"], dict)):
            raise ValueError("invalid_windowed_schema_history")
        failed = failure["checkpoint"]
        completed = refs.get(failed.get("review_request_sha256"))
        if (failure["attempt"] != 1 or completed is None or "format_repair" not in completed
                or set(failed) != set(completed) - {"format_repair"}
                or failed["stage"] != completed["stage"]
                or failed["review_request_sha256"] in failures
                or failed["key"] != completed["key"][:-2]
                or failed["checkpoint_sha256"] != _digest(["call", failed["key"]])
                or failed["request_sha256"] != completed["review_request_sha256"]
                or not isinstance(failed["response_sha256"], str) or len(failed["response_sha256"]) != 64
                or any(char not in "0123456789abcdef" for char in failed["response_sha256"])):
            raise ValueError("invalid_windowed_schema_history")
        failures.add(failed["review_request_sha256"])
    if any(("format_repair" in ref) != (identifier in failures) for identifier, ref in refs.items()):
        raise ValueError("incomplete_windowed_schema_history")
    for failure in audit["window_failures"]:
        if (not isinstance(failure, dict) or set(failure) != {"code", "request", "schema_failures"}
                or not isinstance(failure["schema_failures"], list)):
            raise ValueError("invalid_windowed_output_limit_history")
        parent = validate_review_window_request(failure["request"])
        if (failure["code"] != "model_output_limit" or len(failure["request"]["review_window"]["unit_ids"]) < 2
                or _digest(parent) not in requests_by_hash):
            raise ValueError("invalid_windowed_output_limit_history")
        for schema_failure in failure["schema_failures"]:
            checkpoint = schema_failure["checkpoint"]
            if (schema_failure["attempt"] != 1
                    or checkpoint["review_request_sha256"] != _digest(failure["request"])
                    or checkpoint["request_sha256"] != _digest(failure["request"])
                    or checkpoint["checkpoint_sha256"] != _digest(["call", checkpoint["key"]])):
                raise ValueError("invalid_windowed_output_limit_history")
    merged = protocol.merge_adversarial_claim_reviews(payload, assessments)
    if audit["derived_verdict"] != merged:
        raise ValueError("altered_windowed_claim_aggregate")
    if (any(check[name] and not merged[name] for name in ("keep", "grounded", "reasoning_valid"))
            or check["correctness"] > merged["correctness"]
            or any(score > merged["scores"][name] for name, score in check["scores"].items())
            or check.get("turn_verdicts") != merged.get("turn_verdicts")):
        raise ValueError("windowed_claim_gate_weakened")
    return deepcopy(audit)
