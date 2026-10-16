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
from lib.domain.workflow_quality import accepted, verdict


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


def assessment_from_verdict(payload: dict, check: dict, *, evidence_ref: str,
                            score_threshold: float = .8, method: str = "llm_review") -> dict:
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
    if not isinstance(method, str) or method not in {"llm_review", "deterministic_guard"}:
        raise ValueError("invalid_reasoning_candidate_assessment_method")
    body_hash = reasoning_candidate_digest(payload)
    raw_verdict = deepcopy(check)
    check = _reviewed_verdict(payload, check)
    outcome_passed = (check["grounded"] and check["correctness"] >= 4
                      and check["scores"]["correctness"] >= 4 and check["scores"]["grounding"] >= 4)
    process_passed = check["reasoning_valid"] and check["scores"]["reasoning"] >= 4
    passed = accepted(check) and min(check["scores"].values()) / 5 >= score_threshold
    if method == "deterministic_guard" and passed:
        raise ValueError("deterministic_guard_cannot_accept_reasoning_candidate")
    return {
        "version": REASONING_CANDIDATES_VERSION,
        "candidate_sha256": body_hash,
        "payload": deepcopy(payload),
        "payload_encoding": "canonical_json_utf8",
        "method": method,
        "evidence_ref": evidence_ref,
        "score_threshold": float(score_threshold),
        "outcome": ({"status": "unverified"} if method == "deterministic_guard" else
                    {"status": "pass" if outcome_passed else "fail", "grounded": check["grounded"],
                     "correctness": check["correctness"]}),
        "process": ({"status": "unverified"} if method == "deterministic_guard" else
                    {"status": "pass" if process_passed else "fail", "reasoning_valid": check["reasoning_valid"]}),
        "accepted": passed,
        "raw_verdict": raw_verdict,
        "verdict": check,
    }


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
    assessments, rejected, selected_index = [], [], None
    for index, entry in enumerate(candidates):
        if (not isinstance(entry, dict) or set(entry) != {"candidate", "assessment"}
                or not isinstance(entry["assessment"], dict)):
            raise ValueError("invalid_reasoning_candidate_entry")
        payload, assessment = entry["candidate"], entry["assessment"]
        if reasoning_candidate_digest(payload) != assessment.get("candidate_sha256"):
            raise ValueError("stale_reasoning_candidate_assessment")
        expected = assessment_from_verdict(payload, assessment.get("raw_verdict"),
                                           evidence_ref=assessment.get("evidence_ref"),
                                           score_threshold=assessment.get("score_threshold"),
                                           method=assessment.get("method"))
        if assessment != expected:
            raise ValueError("invalid_reasoning_candidate_assessment")
        assessments.append(deepcopy(assessment))
        if assessment["accepted"] and selected_index is None:
            selected_index = index
        elif not assessment["accepted"]:
            rejected.append({"index": index, "candidate_sha256": assessment["candidate_sha256"],
                             "outcome": assessment["outcome"]["status"],
                             "process": assessment["process"]["status"],
                             "reason": assessment["verdict"]["reason"],
                             "evidence_ref": assessment["evidence_ref"]})
    status = ("accepted" if selected_index is not None else
              "rejected" if limit is None else "exhausted" if len(candidates) == limit else "needs_candidate")
    return {"version": REASONING_CANDIDATES_VERSION, "selection": "first_passing", "status": status,
            "candidate_limit": limit, "attempted": len(candidates),
            "remaining": limit - len(candidates) if limit is not None else None,
            "selected_index": selected_index,
            "candidate": deepcopy(candidates[selected_index]["candidate"]) if selected_index is not None else None,
            "assessments": assessments, "rejections": rejected}
