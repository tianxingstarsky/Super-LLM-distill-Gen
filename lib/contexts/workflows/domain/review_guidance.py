"""Learner-visible review notes, distinct from private tool repair instructions."""
from copy import deepcopy

from .native_feedback import native_response_issue, prepare_native_feedback
from .reasoning_checklist import checklist_digest

REVIEW_GUIDANCE_ORIGIN = "system_review_guidance"
REVIEW_GUIDANCE_HEADER = "要注意 / Things to consider:\n"
REVIEW_GUIDANCE_SCOPE = "one_new_native_response_original_draft_preserved_separately"
REVIEW_GUIDANCE_PARENT_RECEIPT = "review_guidance_parent_receipt"


def _authenticated_legacy_parent(receipt):
    from .native_feedback_provenance import authenticate_native_feedback_receipt
    authenticated = authenticate_native_feedback_receipt(receipt)
    version = receipt.get("guidance_version", 1)
    if type(version) is not int or version not in {1, 2, 3, 4, 5, 6}:
        raise ValueError("review_guidance_legacy_parent_required")
    return authenticated


def restore_review_guidance_session(session, assistant):
    """Recover a certified original request, never trim arbitrary tool history."""
    if not isinstance(session, dict) or REVIEW_GUIDANCE_PARENT_RECEIPT not in session:
        return session
    parent = _authenticated_legacy_parent(session[REVIEW_GUIDANCE_PARENT_RECEIPT])
    current = {key: value for key, value in session.items() if key != REVIEW_GUIDANCE_PARENT_RECEIPT}
    if current != parent["continuation_session"] or assistant != parent["revised_assistant"]:
        raise ValueError("review_guidance_parent_candidate_mismatch")
    original = parent["original_session"]
    users = [index for index, message in enumerate(original["messages"]) if message.get("role") == "user"]
    if not users or any(message.get("role") != "system" for message in original["messages"][users[-1] + 1:]):
        raise ValueError("review_guidance_original_task_context_unavailable")
    return deepcopy(original)


def prepare_review_guidance(session, assistant, findings):
    """Validate recorded findings, then expose actionable notes as real system text.

    The reviewer owns the issue prose. No extra model rewrites it; the original
    quotation, disposition and source remain in the receipt, not in a fake user
    message or a fabricated tool response. An observation is not a new fact.
    """
    _, observation = prepare_native_feedback(session, assistant, findings, guidance_version=4)
    messages = session["messages"]
    users = [i for i, m in enumerate(messages) if m.get("role") == "user"]
    if not users or any(m.get("role") != "system" for m in messages[users[-1] + 1:]):
        raise ValueError("review_guidance_requires_pending_user")
    if assistant.get("tool_calls"):
        raise ValueError("review_guidance_requires_completed_answer")
    notes = [f["issue"] for f in findings if f.get("disposition") != "false_positive"]
    if not notes:
        raise ValueError("review_guidance_no_actionable_notes")
    content = REVIEW_GUIDANCE_HEADER + "\n\n".join(notes)
    content += ("\n\n结合用户当前的问题和实际可见的信息判断这些注意事项。"
                "尚未确定的条件仍须核实，不把建议当成已发生的事实。"
                " / Consider these notes together with the user's actual request and available evidence;"
                " a suggested condition is not an established fact.")
    return {"role": "system", "content": content}, observation


def authenticate_review_guidance_receipt(receipt):
    """Called by the common receipt boundary; no I/O or quality approval."""
    session = receipt["original_session"]
    original, findings = receipt["original_assistant"], receipt["findings"]
    if "previous_receipt" in receipt or "previous_receipt_sha256" in receipt:
        previous = receipt["previous_receipt"]
        parent = _authenticated_legacy_parent(previous)
        if (receipt["previous_receipt_sha256"] != checklist_digest(previous)
                or session != parent["original_session"] or original != parent["revised_assistant"]):
            raise ValueError
    if REVIEW_GUIDANCE_PARENT_RECEIPT in session:
        raise ValueError
    note, observation = prepare_review_guidance(session, original, findings)
    continued = {**deepcopy(session), "messages": [*deepcopy(session["messages"]), note]}
    transcript = receipt["transcript"]
    if len(transcript) != 1:
        raise ValueError
    entry = transcript[0]
    response = entry["response"]
    revised, details = response["assistant"], response.get("response_details", {})
    if native_response_issue(revised, details, require_answer=True) or revised.get("tool_calls"):
        raise ValueError
    value = {"reasoning": revised["reasoning_content"], "answer": revised["content"]}
    expected = {
        "version": 1, "mode": "same_author_native_feedback", "guidance_version": 7,
        "feedback_request_origin": REVIEW_GUIDANCE_ORIGIN, "status": "completed", "issue": None,
        "semantic_status": "not_assessed", "independent_review": False, "original_preserved": True,
        "model_turns": 1, "author": session["author"], "stage": session["stage"],
        "original_session_sha256": checklist_digest(session), "observation": observation,
        "guidance_message": note, "continuation_session": continued, "tools_preserved": True,
        "revised_assistant": revised, "response_details": details,
        "candidate_sha256": checklist_digest(value), "reasoning_scope": REVIEW_GUIDANCE_SCOPE,
        "reasoning_segments": [{"phase": "continue_answer", "origin": "provider_reasoning_content", "text": value["reasoning"]}],
    }
    if (any(receipt.get(k) != v for k, v in expected.items())
            or entry["phase"] != "continue_answer" or entry["observations"] != []
            or "tool_feedback" in receipt):
        raise ValueError
    if ("session_recovery" in receipt) != ("recovery" in session):
        raise ValueError
    if "recovery" in session and receipt["session_recovery"] != session["recovery"]:
        raise ValueError
    return {"original_session": deepcopy(session), "continuation_session": continued,
            "revised_assistant": deepcopy(revised), "value": value, "guidance_message": note}


def review_guidance_message(receipt):
    from .native_feedback_provenance import authenticate_native_feedback_receipt
    authenticated = authenticate_native_feedback_receipt(receipt)
    if receipt.get("feedback_request_origin") != REVIEW_GUIDANCE_ORIGIN:
        raise ValueError("review_guidance_receipt_required")
    return deepcopy(authenticated["guidance_message"])


def authenticated_review_guidance_positions(record):
    """Prove public note positions against the exact authenticated author request.

    Old notes on subsequent repairs stay public only if both their text and
    preceding user anchor still occur in that request. Initial system rules
    are not granted new visibility, nor is any source material reclassified.
    """
    native = record.get("native_generation") or {}
    receipt = native.get("review_guidance")
    if receipt is None:
        return ()
    from .native_feedback_provenance import authenticate_native_feedback_receipt
    from .dialogue_style_context import authenticated_record_context_positions
    try:
        authenticated = authenticate_native_feedback_receipt(receipt)
        note = authenticated["guidance_message"]
        actual = authenticated["continuation_session"]["messages"]
        value = authenticated["value"]
        visible = record.get("question")
        messages = record.get("messages")
        if visible is None:
            if not isinstance(messages, list) or messages[-1].get("role") != "assistant":
                raise ValueError
            visible = messages[:-1]
        if not isinstance(visible, list) or not visible or visible[-1] != note:
            raise ValueError
        context_positions = set(authenticated_record_context_positions(record))
        if native.get("content_sha256") != checklist_digest([value["reasoning"], value["answer"]]):
            raise ValueError
        if messages is not None:
            if ([{k:v for k,v in m.items() if k != "reasoning_content"} for m in messages[:-1]] !=
                    [{k:v for k,v in m.items() if k != "reasoning_content"} for m in visible]
                    or messages[-1].get("content") != value["answer"]
                    or messages[-1].get("reasoning_content") != value["reasoning"]):
                raise ValueError
        if "answer" in record and record["answer"] != value["answer"]:
            raise ValueError
        if "reasoning" in record and record["reasoning"] != [value["reasoning"]]:
            raise ValueError
        actual_blocks = []
        for i, m in enumerate(actual):
            if m.get("role") == "user":
                block = []
                for nxt in actual[i+1:]:
                    if nxt.get("role") != "system":
                        break
                    block.append(nxt)
                actual_blocks.append((m, block))
        positions, anchor, block = [], None, []
        for i, m in enumerate(visible):
            if m.get("role") == "user":
                anchor, block = m, []
            elif m.get("role") == "system" and anchor is not None:
                block.append(m)
                if ((i not in context_positions and not m.get("content", "").startswith(REVIEW_GUIDANCE_HEADER))
                        or not any(u == anchor and b[:len(block)] == block for u,b in actual_blocks)):
                    raise ValueError
                if i not in context_positions:
                    positions.append(i)
            elif m.get("role") in {"assistant", "tool"}:
                anchor, block = None, []
        if len(visible)-1 not in positions:
            raise ValueError
        return tuple(positions)
    except (KeyError, TypeError, IndexError, ValueError, AttributeError):
        raise ValueError("review_guidance_public_context_mismatch") from None
