"""Pure provenance for a supplied native prefix and one provider continuation."""
from __future__ import annotations

from copy import deepcopy
import hashlib

from .native_feedback import native_response_issue
from .reasoning_checklist import checklist_digest
from .review_guidance import prepare_review_guidance, REVIEW_GUIDANCE_ORIGIN


PREFIX_REASONING_SCOPE = "original_native_prefix_plus_new_provider_continuation"
NATIVE_PREFIX_PARENT_RECEIPT = "native_prefix_parent_receipt"
DEFAULT_NATIVE_REFLECTION_SEED = (
    "\n\nWait, I need to check whether this conclusion really follows from the available facts. "
    "The point I need to reconsider is"
)


def native_feedback_guidance_version(node_generation, endpoint_pins):
    """Freeze beta only when every active native author uses its known protocol."""
    stages = [stage for stage in ("sft", "multiturn", "cot")
        if node_generation.get(stage, {}).get("enabled") is True
        and node_generation[stage].get("answer_mode") == "native_joint"]
    pins = endpoint_pins or {}
    return 8 if stages and all(
        pins.get(stage, {}).get("generation", {}).get("endpoint_origin") == "https://api.deepseek.com"
        and pins[stage]["generation"].get("api_format") == "chat" for stage in stages) else 7


def prepare_native_prefix_feedback(session, assistant, findings, *, reflection_seed=""):
    """Reuse bound observations without inventing tool calls or native thoughts."""
    note, observation = prepare_review_guidance(session, assistant, findings)
    if session.get("version") != 1:
        raise ValueError("native_prefix_original_session_invalid")
    # Removing a real tool record would change the author's evidence. This
    # route accepts only the explicit tool-free source contract.
    if session["tools"] or any(
            message.get("role") == "tool" or "tool_calls" in message
            or "function_call" in message or "prefix" in message
            for message in session["messages"]):
        raise ValueError("native_prefix_tool_history_unsupported")
    if assistant.get("tool_calls") or "function_call" in assistant:
        raise ValueError("native_prefix_completed_answer_required")
    if not isinstance(reflection_seed, str):
        raise ValueError("native_prefix_invalid_reflection_seed")
    prefix = assistant["reasoning_content"]
    try:
        prefix.encode("utf-8")
        reflection_seed.encode("utf-8")
    except UnicodeEncodeError:
        raise ValueError("native_prefix_invalid_reasoning") from None
    native_prefix_parent_segments(session, assistant)
    return note, observation


def native_prefix_parent_segments(session, assistant):
    """Keep every supplied segment's original attribution across explicit repairs."""
    if NATIVE_PREFIX_PARENT_RECEIPT not in session:
        return [{"phase": "supplied_prefix", "origin": "original_provider_reasoning_content",
                 "text": assistant["reasoning_content"]}]
    from .native_feedback_provenance import authenticate_native_feedback_receipt
    parent = session[NATIVE_PREFIX_PARENT_RECEIPT]
    if (not isinstance(parent, dict) or type(parent.get("guidance_version")) is not int
            or parent["guidance_version"] != 8):
        raise ValueError("native_prefix_parent_receipt_required")
    authenticated = authenticate_native_feedback_receipt(parent)
    clean_session = {key: value for key, value in session.items() if key != NATIVE_PREFIX_PARENT_RECEIPT}
    if (clean_session != authenticated["continuation_session"]
            or assistant != authenticated["revised_assistant"]
            or "".join(segment["text"] for segment in parent["reasoning_segments"]) != assistant["reasoning_content"]):
        raise ValueError("native_prefix_parent_candidate_mismatch")
    return deepcopy(parent["reasoning_segments"])


def native_prefix_messages(pending_messages, reasoning_prefix):
    """Describe the exact message projection applied by the beta transport."""
    return [*deepcopy(pending_messages), {"role": "assistant", "content": "",
        "prefix": True, "reasoning_content": reasoning_prefix}]


def _validate_prefix_transport(response, pending, prefix, *, answer_boundary=False):
    details = response.get("response_details", {})
    encoded = prefix.encode("utf-8")
    expected = {"version": 2 if answer_boundary else 3, "transport": "deepseek_chat_beta",
        "visible_prefix": "\n" if answer_boundary else "",
        **({} if answer_boundary else {"continuation_mode": "reasoning"}),
        "prefix_sha256": hashlib.sha256(encoded).hexdigest(),
        "prefix_utf8_bytes": len(encoded), "response_reasoning": "provider_continuation"}
    if details.get("reasoning_prefill", {}) != expected:
        raise ValueError("native_prefix_transport_receipt_mismatch")
    sent = [*deepcopy(pending), {"role": "assistant", "content": "\n" if answer_boundary else "",
                               "prefix": True, "reasoning_content": prefix}]
    if "request_messages" in response and response["request_messages"] != sent:
        raise ValueError("native_prefix_request_messages_mismatch")
    return sent


def _reasoning_only_response(response):
    raw = response.get("assistant") if isinstance(response, dict) else None
    details = response.get("response_details", {}) if isinstance(response, dict) else {}
    issue = native_response_issue(raw, details, require_answer=False)
    if issue:
        raise ValueError(issue)
    if details.get("finish_reason") != "stop" or raw["content"].strip():
        raise ValueError("native_prefix_reasoning_only_stop_required")
    if raw.get("tool_calls") or "function_call" in raw:
        raise ValueError("native_prefix_unexpected_tool_call")
    return raw, details


def complete_native_prefix_feedback(session, original, findings, response, *, reflection_seed="", final_response=None):
    """Build the receipt from original bytes and one complete raw response."""
    note, observation = prepare_native_prefix_feedback(session, original, findings, reflection_seed=reflection_seed)
    raw = response.get("assistant") if isinstance(response, dict) else None
    details = response.get("response_details", {}) if isinstance(response, dict) else {}
    if final_response is not None:
        raw, details = _reasoning_only_response(response)
    else:
        issue = native_response_issue(raw, details, require_answer=True)
        if issue:
            raise ValueError(issue)
    if raw.get("tool_calls") or "function_call" in raw:
        raise ValueError("native_prefix_unexpected_tool_call")
    original_reasoning = original["reasoning_content"]
    prefix = original_reasoning + reflection_seed
    pending = [*deepcopy(session["messages"]), deepcopy(note)]
    sent = _validate_prefix_transport(response, pending, prefix)
    full_reasoning = prefix + raw["reasoning_content"]
    final_sent = None
    last = raw
    if final_response is not None:
        last = final_response.get("assistant") if isinstance(final_response, dict) else None
        final_details = final_response.get("response_details", {}) if isinstance(final_response, dict) else {}
        # The saved first phase already contains a full native reasoning body.
        # The answer phase may return no additional reasoning, but its raw
        # channel must still be present rather than reconstructed.
        if (not isinstance(last, dict) or last.get("role") != "assistant"
                or not isinstance(last.get("reasoning_content"), str)
                or not isinstance(last.get("content"), str) or not last["content"].strip()):
            raise ValueError("native_prefix_final_answer_missing_or_invalid")
        if (not isinstance(final_details, dict) or final_details.get("finish_reason") != "stop"
                or final_details.get("incomplete_response")):
            raise ValueError("native_feedback_response_incomplete")
        if last.get("tool_calls") or "function_call" in last:
            raise ValueError("native_prefix_unexpected_tool_call")
        final_sent = _validate_prefix_transport(final_response, pending, full_reasoning, answer_boundary=True)
        full_reasoning += last["reasoning_content"]
        details = final_details
    revised = {**deepcopy(last), "reasoning_content": full_reasoning}
    value = {"reasoning": revised["reasoning_content"], "answer": revised["content"]}
    receipt = {"version": 1, "mode": "same_author_native_feedback", "guidance_version": 8,
        "feedback_request_origin": REVIEW_GUIDANCE_ORIGIN,
        "continuation_transport": "deepseek_chat_beta", "status": "completed", "issue": None,
        "semantic_status": "not_assessed", "independent_review": False, "original_preserved": True,
        "model_turns": 2 if final_response is not None else 1, "original_session": deepcopy(session),
        "original_assistant": deepcopy(original), "findings": deepcopy(findings),
        "author": session["author"], "stage": session["stage"],
        "original_session_sha256": checklist_digest(session), "observation": deepcopy(observation),
        "guidance_message": deepcopy(note), "tools_preserved": True,
        "reasoning_prefix": prefix, "request_messages": sent,
        "continuation_session": {**{key: deepcopy(value) for key, value in session.items()
                                    if key != NATIVE_PREFIX_PARENT_RECEIPT}, "messages": pending},
        "revised_assistant": revised, "response_details": deepcopy(details),
        "candidate_sha256": checklist_digest(value), "reasoning_scope": PREFIX_REASONING_SCOPE,
        "reasoning_segments": [
            *native_prefix_parent_segments(session, original),
            *([{"phase": "reflection_seed", "origin": "application_reflection_seed", "text": reflection_seed}]
              if reflection_seed else []),
            {"phase": "continue_answer", "origin": "provider_reasoning_content", "text": raw["reasoning_content"]}],
        "transcript": [{"phase": "continue_answer", "response": deepcopy(response), "observations": []}]}
    if reflection_seed:
        receipt["reflection_seed"] = reflection_seed
    if final_response is not None:
        receipt.update(finalization_reason="completed_reasoning_without_answer",
            finalization_request_messages=final_sent)
        receipt["reasoning_segments"].append({"phase": "finalize_answer", "origin": "provider_reasoning_content",
                                             "text": last["reasoning_content"]})
        receipt["transcript"].append({"phase": "finalize_answer", "response": deepcopy(final_response), "observations": []})
    if "recovery" in session:
        receipt["session_recovery"] = deepcopy(session["recovery"])
    return value, receipt


def prepare_native_prefix_finalization(receipt):
    """Authenticate a saved reasoning-only STOP before buying one answer phase."""
    try:
        session, original, findings = receipt["original_session"], receipt["original_assistant"], receipt["findings"]
        seed = receipt.get("reflection_seed", "")
        note, _ = prepare_native_prefix_feedback(session, original, findings, reflection_seed=seed)
        prefix = original["reasoning_content"] + seed
        pending = [*deepcopy(session["messages"]), note]
        transcript = receipt["transcript"]
        if len(transcript) != 1:
            raise ValueError
        response = transcript[0]["response"]
        raw, details = _reasoning_only_response(response)
        sent = _validate_prefix_transport(response, pending, prefix)
        expected = {"version": 1, "mode": "same_author_native_feedback", "guidance_version": 8,
            "feedback_request_origin": "system_review_guidance", "continuation_transport": "deepseek_chat_beta",
            "status": "incomplete", "issue": "native_feedback_answer_missing", "semantic_status": "not_assessed",
            "independent_review": False, "original_preserved": True, "model_turns": 1,
            "original_session": deepcopy(session), "original_assistant": deepcopy(original), "findings": deepcopy(findings),
            "transcript": [{"phase": "continue_answer", "response": deepcopy(response), "observations": []}],
            "reasoning_segments": [], "reasoning_prefix": prefix, "guidance_message": note,
            "request_messages": sent, "response_details": deepcopy(details)}
        if seed:
            expected["reflection_seed"] = seed
        if receipt != expected:
            raise ValueError
        return {"session": deepcopy(session), "pending_messages": pending,
                "reasoning_prefix": prefix + raw["reasoning_content"]}
    except (KeyError, TypeError, IndexError, AttributeError, ValueError, RecursionError):
        raise ValueError("native_prefix_incomplete_receipt_mismatch") from None


def authenticate_native_prefix_feedback_receipt(receipt):
    """Reconstruct every composed field without certifying semantic quality."""
    transcript = receipt["transcript"]
    finalizing = "finalization_reason" in receipt
    if len(transcript) != (2 if finalizing else 1):
        raise ValueError("native_prefix_transcript_mismatch")
    value, expected = complete_native_prefix_feedback(receipt["original_session"],
        receipt["original_assistant"], receipt["findings"], transcript[0]["response"],
        reflection_seed=receipt.get("reflection_seed", ""),
        final_response=transcript[1]["response"] if finalizing else None)
    if receipt != expected:
        raise ValueError("native_prefix_receipt_mismatch")
    return {"original_session": deepcopy(receipt["original_session"]),
        "continuation_session": deepcopy(receipt["continuation_session"]),
        "revised_assistant": deepcopy(receipt["revised_assistant"]),
        "value": value, "guidance_message": deepcopy(receipt["guidance_message"])}
