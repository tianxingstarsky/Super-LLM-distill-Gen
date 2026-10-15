"""Bind complete assistant-turn assessments to the exact supervised content.

This contract checks coverage and conservative aggregation, not factual truth.
The original model response remains in the ordinary request checkpoint.
"""
from __future__ import annotations

from copy import deepcopy
import hashlib

from lib.domain.reasoning_fields import REASONING_FIELDS
from lib.domain.workflow_quality import accepted, canonical, verdict


def reasoning_text(message):
    return next((message[field] for field in REASONING_FIELDS
                 if isinstance(message.get(field), str) and message[field].strip()), "")


def supervised_assistant_turns(candidate, *, reasoning_required=False):
    messages = candidate.get("messages", [])
    return [{"message_index": index,
             "content_sha256": hashlib.sha256(canonical(messages[:index + 1]).encode("utf-8")).hexdigest(),
             "requires_reasoning": bool(reasoning_text(message)) or bool(reasoning_required)
                 and not (message.get("tool_calls") or message.get("toolCalls"))}
            for index, message in enumerate(messages)
            if isinstance(message, dict) and message.get("role") == "assistant"]


def turn_review_verdict(value, turns):
    """Reject omitted/stale/duplicate turns; no later recovery erases a bad turn."""
    if not isinstance(value, dict) or "turn_verdicts" not in value:
        raise ValueError("missing_assistant_turn_reviews")
    if set(value) != {"keep", "grounded", "reasoning_valid", "correctness", "scores", "reason", "turn_verdicts"}:
        raise ValueError("invalid_assistant_turn_review_aggregate")
    original = {key: deepcopy(part) for key, part in value.items() if key != "turn_verdicts"}
    check = verdict(original)
    assessments = value["turn_verdicts"]
    if not isinstance(assessments, list) or len(assessments) != len(turns) or not turns:
        raise ValueError("incomplete_assistant_turn_reviews")
    expected = {turn["message_index"]: turn for turn in turns}
    seen, reviewed = set(), []
    base_fields = {"keep", "grounded", "reasoning_valid", "correctness", "scores", "reason"}
    for assessment in assessments:
        if (not isinstance(assessment, dict) or set(assessment) != base_fields | {
                "message_index", "content_sha256", "leakage_free", "critical_error"}):
            raise ValueError("invalid_assistant_turn_review")
        index = assessment["message_index"]
        if type(index) is not int or index not in expected or index in seen:
            raise ValueError("invalid_assistant_turn_review_index")
        seen.add(index)
        if assessment["content_sha256"] != expected[index]["content_sha256"]:
            raise ValueError("stale_assistant_turn_review")
        if any(type(assessment[field]) is not bool for field in ("leakage_free", "critical_error")):
            raise ValueError("invalid_assistant_turn_review_boolean")
        result = verdict({field: assessment[field] for field in base_fields})
        reviewed.append({**deepcopy(assessment), "_accepted": accepted(result)
                         and assessment["leakage_free"] and not assessment["critical_error"]})
    failed = [item for item in reviewed if not item["_accepted"]]
    merged = {**check, "turn_review_protocol_version": 1,
              "aggregate_original": deepcopy(original),
              "turn_verdicts": [{key: part for key, part in item.items() if key != "_accepted"}
                                for item in reviewed]}
    merged["keep"] = check["keep"] and not failed
    merged["grounded"] = check["grounded"] and all(item["grounded"] for item in reviewed)
    merged["reasoning_valid"] = check["reasoning_valid"] and all(
        item["reasoning_valid"] and item["leakage_free"] for item in reviewed)
    merged["correctness"] = min(check["correctness"], *(item["correctness"] for item in reviewed))
    merged["scores"] = {name: min(score, *(item["scores"][name] for item in reviewed))
                        for name, score in check["scores"].items()}
    if failed:
        merged["reason"] = "; ".join(f"assistant[{item['message_index']}]: {item['reason']}" for item in failed)
    return merged
