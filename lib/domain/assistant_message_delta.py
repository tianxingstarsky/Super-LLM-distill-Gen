"""Apply bounded assistant edits without reserializing a learner's inputs."""
from __future__ import annotations

from copy import deepcopy

from lib.domain.workflow_quality import text_issue


MAX_ASSISTANT_UPDATE_CHARS = 100_000
_REASONING_FIELDS = ("reasoning_content", "reasoning", "thinking")


def apply_assistant_message_delta(messages, record, *, required_reasoning_indices=None):
    """Index the original message list; tools, roles and turn count are fixed.

    A revised reply owns its new explanation. Omitting reasoning_content
    removes only that reply's obsolete reasoning aliases; its exact native
    response is archived by the caller's existing provenance mechanism.
    """
    error = "review_repair_invalid_schema"
    if (not isinstance(messages, list) or not messages
            or any(not isinstance(message, dict) for message in messages)
            or not isinstance(record, dict) or set(record) != {"assistant_updates"}):
        raise ValueError(error)
    updates = record["assistant_updates"]
    if not isinstance(updates, list) or not 1 <= len(updates) <= len(messages):
        raise ValueError(error)
    required = set(required_reasoning_indices or [])
    if any(type(index) is not int or not 0 <= index < len(messages)
           or messages[index].get("role") != "assistant" for index in required):
        raise ValueError(error)
    result, seen = deepcopy(messages), set()
    for update in updates:
        if (not isinstance(update, dict) or not {"message_index", "content"} <= set(update)
                or set(update) - {"message_index", "content", "reasoning_content", "reasoning_trace"}
                or "reasoning_trace" in update and required_reasoning_indices is None):
            raise ValueError(error)
        index = update["message_index"]
        if type(index) is not int or not 0 <= index < len(messages) or index in seen:
            raise ValueError(error)
        seen.add(index)
        original = messages[index]
        if original.get("role") != "assistant":
            raise ValueError("review_repair_learner_task_changed")
        if original.get("tool_calls") or original.get("toolCalls"):
            raise ValueError("review_repair_tool_facts_changed")
        if index in required or required_reasoning_indices is not None and "reasoning_content" in update:
            from lib.domain.authored_reasoning import authored_reasoning_issue
            if issue := authored_reasoning_issue({"reasoning": update.get("reasoning_content"),
                    "answer": update.get("content"), "reasoning_trace": update.get("reasoning_trace")}):
                raise ValueError(issue)
        for field in ("content", "reasoning_content"):
            if field in update:
                value = update[field]
                if not isinstance(value, str) or len(value) > MAX_ASSISTANT_UPDATE_CHARS:
                    raise ValueError(error)
                if issue := text_issue(value):
                    raise ValueError(issue)
        message = result[index]
        for field in _REASONING_FIELDS:
            message.pop(field, None)
        message["content"] = update["content"]
        if "reasoning_content" in update:
            message["reasoning_content"] = update["reasoning_content"]
    if required - seen:
        raise ValueError("review_repair_reasoning_coverage_missing")
    return result
