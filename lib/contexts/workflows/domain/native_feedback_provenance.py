"""Authenticate complete native feedback receipts and their immutable parent sessions."""
from __future__ import annotations

from copy import deepcopy
import json

from .native_feedback import (
    feedback_call_issue, feedback_tool, feedback_work_instruction,
    history_tool_ids, native_response_issue, prepare_native_feedback,
)
from .reasoning_checklist import checklist_digest


REUSED_FEEDBACK_ORIGIN = "reused_delivered_feedback"
REUSED_REASONING_SCOPE = "current_provider_turn_only_reused_feedback_and_parent_trace_preserved_separately"
ORIGINAL_REASONING_SCOPE = "new_provider_turns_only_original_and_feedback_preserved_separately"


def authenticate_native_feedback_receipt(receipt):
    """Bind native responses, observations and request history without certifying quality.

    The caller still authenticates the persisted parent and current model binding.
    This pure check recovers only envelopes present in the receipt, never missing
    reasoning or a tool-call ID. Reused receipts retain the full parent recursively.
    """
    try:
        return _authenticate_receipt(receipt)
    except (KeyError, TypeError, ValueError, IndexError, AttributeError, RecursionError):
        raise ValueError("native_feedback_receipt_provenance_mismatch") from None


def _authenticate_receipt(receipt):
    if receipt.get("feedback_request_origin") == "system_review_guidance":
        from .review_guidance import authenticate_review_guidance_receipt
        return authenticate_review_guidance_receipt(receipt)
    session, revised = receipt["continuation_session"], receipt["revised_assistant"]
    transcript, original = receipt["transcript"], receipt["original_assistant"]
    origin = receipt["feedback_request_origin"]
    reused = origin == REUSED_FEEDBACK_ORIGIN
    new_call = origin == "new_provider_tool_call"
    if (receipt["version"] != 1 or receipt["mode"] != "same_author_native_feedback"
            or receipt["status"] != "completed" or receipt["semantic_status"] != "not_assessed"
            or receipt["independent_review"] is not False or receipt["original_preserved"] is not True
            or receipt["issue"] is not None
            or session["author"] != receipt["author"] or session["stage"] != receipt["stage"]
            or session["role"] != "generation"
            or receipt["reasoning_scope"] != (REUSED_REASONING_SCOPE if reused else ORIGINAL_REASONING_SCOPE)):
        raise ValueError
    phases = ["read_feedback", "continue_answer"] if new_call else ["continue_answer"]
    if (origin not in {"new_provider_tool_call", "recorded_provider_tool_call", REUSED_FEEDBACK_ORIGIN}
            or len(transcript) != len(phases) or receipt["model_turns"] != len(phases)
            or [entry["phase"] for entry in transcript] != phases):
        raise ValueError
    segments = []
    for phase, entry in zip(phases, transcript):
        raw = entry["response"]
        if native_response_issue(raw["assistant"], raw.get("response_details", {}),
                                 require_answer=phase == "continue_answer"):
            raise ValueError
        segments.append({"phase": phase, "origin": "provider_reasoning_content",
                         "text": raw["assistant"]["reasoning_content"]})
    last = transcript[-1]["response"]
    value = {"reasoning": "\n\n".join(segment["text"] for segment in segments),
             "answer": revised["content"]}
    if (segments != receipt["reasoning_segments"] or revised != last["assistant"]
            or revised.get("tool_calls") not in (None, [])
            or receipt["response_details"] != last.get("response_details", {})
            or receipt["candidate_sha256"] != checklist_digest(value)
            or transcript[-1]["observations"] != []):
        raise ValueError

    if reused:
        guidance_version = receipt.get("guidance_version")
        if type(guidance_version) is not int or guidance_version not in {5, 6}:
            raise ValueError
        previous = receipt["previous_receipt"]
        parent = _authenticate_receipt(previous)
        original_session = parent["original_session"]
        expected_session = deepcopy(parent["continuation_session"])
        expected_session["messages"].append({"role": "system", "content": feedback_work_instruction(guidance_version)})
        if (receipt["previous_receipt_sha256"] != checklist_digest(previous)
                or session != expected_session
                or original != previous["original_assistant"]
                or any(receipt[field] != previous[field] for field in (
                    "original_session_sha256", "findings", "observation", "tool_feedback", "tools_preserved"))):
            raise ValueError
    else:
        tool_author = transcript[0]["response"]["assistant"] if new_call else original
        if feedback_call_issue(tool_author) or new_call and original.get("tool_calls"):
            raise ValueError
        guidance_version = receipt.get("guidance_version", 1)
        tail = [original]
        if new_call:
            tail.extend([{"role": "system", "content": feedback_work_instruction(guidance_version)}, tool_author])
        elif guidance_version in {4, 5, 6}:
            tail.insert(0, {"role": "system", "content": feedback_work_instruction(guidance_version)})
        tail.append(receipt["tool_feedback"])
        if session["messages"][-len(tail):] != tail:
            raise ValueError
        # Reconstruct the recorded request envelope, including recovery metadata
        # and its old tool set; all native text stays exactly as recorded.
        original_session = {**deepcopy(session), "messages": deepcopy(session["messages"][:-len(tail)])}
        if receipt["tools_preserved"] is False:
            original_session["tools"] = [tool for tool in original_session["tools"] if tool != feedback_tool()]
        tools, observation = prepare_native_feedback(original_session, original, receipt["findings"],
            guidance_version=guidance_version)
        call_id = tool_author["tool_calls"][0]["id"]
        expected_feedback = {"role": "tool", "tool_call_id": call_id,
            "content": json.dumps(observation, ensure_ascii=False, separators=(",", ":"))}
        if (checklist_digest(original_session) != receipt["original_session_sha256"]
                or tools != session["tools"] or observation != receipt["observation"]
                or receipt["tools_preserved"] != (tools == original_session["tools"])
                or receipt["tool_feedback"] != expected_feedback
                or call_id in history_tool_ids(original_session["messages"])
                or new_call and transcript[0]["observations"] != [expected_feedback]):
            raise ValueError
    if ("session_recovery" in receipt) != ("recovery" in original_session):
        raise ValueError
    if "recovery" in original_session and receipt["session_recovery"] != original_session["recovery"]:
        raise ValueError
    return {"original_session": deepcopy(original_session), "continuation_session": deepcopy(session),
            "revised_assistant": deepcopy(revised), "value": value}
