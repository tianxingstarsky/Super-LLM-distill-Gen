"""Reuse complete, authenticated failed drafts without manufacturing an error."""
from copy import deepcopy

from lib.domain.reasoning_candidates import select_reasoning_candidate, assessment_from_verdict
from lib.domain.turn_review import reasoning_text
from lib.domain.workflow_quality import canonical
from .candidate_content import candidate_digest


def _check_assessment(assessment):
    if not isinstance(assessment, dict):
        raise ValueError("invalid_reviewed_failure_assessment")
    payload = assessment.get("payload")
    select_reasoning_candidate([{"candidate": payload, "assessment": assessment}])
    # A schema/transport failure or missing support is not a content failure.
    return (assessment["method"] == "llm_claim_review" and not assessment["accepted"]
        and assessment.get("review_status") != "pending_review"
        and not any(item.get("review", {}).get("condition_guard", {}).get("needs_review")
                    for item in assessment.get("claim_review", {}).get("assessments", []))
        and assessment["outcome"]["status"] == "fail" and assessment["process"]["status"] == "fail")


def find_reviewed_failure(sample):
    """Only same-input drafts with both task and reasoning failures qualify."""
    actual = sample.get("messages", [])
    if not actual or actual[-1].get("role") != "assistant":
        return None
    assessments = (sample.get("reasoning_distillation") or {}).get("assessments", [])
    for assessment in reversed(assessments):
        if not _check_assessment(assessment):
            continue
        prior = assessment["payload"].get("messages", [])
        if (not prior or canonical(prior[:-1]) != canonical(actual[:-1])
                or canonical(assessment["payload"].get("tools", [])) != canonical(sample.get("tools", []))
                or prior[-1].get("role") != "assistant" or not reasoning_text(prior[-1]).strip()
                or prior[-1].get("tool_calls") or prior[-1].get("toolCalls")
                or canonical(prior[-1]) == canonical(actual[-1])):
            continue
        return {"response": [deepcopy(prior[-1])], "source": {"version": 1, "kind": "reviewed_failure",
            "assessment": deepcopy(assessment), "source_id": sample.get("source_id"),
            "interpretation": "reviewer_confirmed_task_and_reasoning_failure_not_independent_truth"}}
    return None


def validate_reviewed_failure_pair(record):
    source = record.get("preference_negative_source")
    if source is None:
        return False
    if (not isinstance(source, dict) or source.get("version") != 1 or source.get("kind") != "reviewed_failure"
            or not source.get("source_id") or source.get("source_id") != record.get("source_id")
            or not _check_assessment(source.get("assessment"))):
        raise ValueError("invalid_reviewed_failure_source")
    payload = source["assessment"]["payload"]
    if (canonical(payload.get("messages")) != canonical([*record["prompt"], *record["rejected"]])
            or canonical(payload.get("tools", [])) != canonical(record.get("tools", []))):
        raise ValueError("reviewed_failure_body_or_task_changed")
    if not any(reasoning_text(message).strip() for message in record["rejected"]):
        raise ValueError("reviewed_failure_reasoning_missing")
    return True


def remember_failed_positive(record, response_assessment, evidence, *, threshold):
    """Keep a real failed positive for reuse after its correction is assessed."""
    audit = response_assessment.get("claim_review")
    if not audit or response_assessment.get("condition_pending"):
        return
    payload = {"messages": [*deepcopy(record["prompt"]), *deepcopy(record["chosen"])],
        **({"tools": deepcopy(record["tools"])} if record.get("tools") else {})}
    assessment = assessment_from_verdict(payload, response_assessment["verdict"], method="llm_claim_review",
        claim_review=audit, score_threshold=threshold,
        evidence_ref="reviewed_preference_response:" + candidate_digest(evidence))
    if not _check_assessment(assessment):
        return
    history = deepcopy(record.get("preference_review_failures", []))
    if not any(item["candidate_sha256"] == assessment["candidate_sha256"] for item in history):
        history.append(assessment)
    record["preference_review_failures"] = history


def reuse_failed_positive(record):
    """Replace the synthetic comparison only with a verified same-task draft."""
    if record.get("preference_negative_source"):
        validate_reviewed_failure_pair(record)
        return False
    sample = {"messages": [*record["prompt"], *record["chosen"]], "tools": record.get("tools", []),
        "source_id": record.get("source_id"), "reasoning_distillation": {
            "assessments": record.get("preference_review_failures", [])}}
    selected = find_reviewed_failure(sample)
    if not selected:
        return False
    record["rejected"] = selected["response"]
    record["preference_negative_source"] = selected["source"]
    return True
