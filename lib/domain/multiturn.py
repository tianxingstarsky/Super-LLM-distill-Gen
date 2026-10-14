"""Structural checks for complete multi-turn chat training examples.

The checks here prove only that a trace is usable as a sequence of completed
turns. They cannot establish the truth of an answer or a recorded tool result.
"""
from __future__ import annotations

from lib.domain.workflow_quality import conversation_issue, text_issue


def user_actor_action(value):
    """Decode one untrusted actor action, never convert a stop into a turn.

    A missing optional end flag preserves existing message-only templates.
    Semantic completion is still checked independently on the actual dialogue.
    """
    if not isinstance(value, dict) or set(value) - {"message", "end", "stop", "reason"}:
        raise ValueError("invalid_multiturn_user_action")
    flags = set(value).intersection({"end", "stop"})
    if len(flags) > 1:
        raise ValueError("invalid_multiturn_user_action")
    end = value[next(iter(flags))] if flags else False
    if type(end) is not bool:
        raise ValueError("invalid_multiturn_user_action")
    if end:
        reason = value.get("reason")
        if "message" in value or text_issue(reason) or len(reason) > 3000:
            raise ValueError("invalid_multiturn_stop")
        return {"end": True, "reason": reason}
    if "reason" in value or text_issue(value.get("message")):
        raise ValueError("invalid_multiturn_user_action")
    return {"end": False, "message": value["message"]}


def completed_turn_ends(messages: list[dict], minimum_turns: int = 2) -> tuple[list[int], str | None]:
    """Return inclusive end indexes for user-to-final-assistant turns.

    Tool-call exchanges may occur *within* a turn. A turn ends only with an
    assistant text answer, and a new user must start the next turn.
    """
    issue = conversation_issue(messages)
    if issue:
        return [], issue
    ends: list[int] = []
    active = False
    finished = False
    started = False
    for index, message in enumerate(messages):
        role = message["role"]
        if role == "system":
            if started:
                return [], "system_message_out_of_place"
            continue
        started = True
        if role == "user":
            if active and not finished:
                return [], "unanswered_user_turn"
            active, finished = True, False
        elif role == "assistant":
            if not active or finished:
                return [], "assistant_without_user_turn"
            calls = message.get("tool_calls", message.get("toolCalls", []))
            if not calls:
                finished = True
                ends.append(index)
        elif role == "tool":
            if not active or finished:
                return [], "tool_outside_user_turn"
    if not finished:
        return [], "unanswered_user_turn"
    if len(ends) < minimum_turns:
        return [], "insufficient_completed_turns"
    return ends, None
