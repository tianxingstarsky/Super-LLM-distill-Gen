"""Canonical reasoning fields for SFT exports without changing source evidence."""
from __future__ import annotations

from copy import deepcopy


REASONING_FIELDS = ("reasoning_content", "reasoning", "thinking")


def sft_reasoning_issue(messages) -> str | None:
    """Aliases may agree, but no field silently overrides another explanation."""
    for message in messages:
        if message.get("role") != "assistant":
            continue
        values = [message[key] for key in REASONING_FIELDS
                  if message.get(key) not in (None, "")]
        if any(not isinstance(value, str) for value in values):
            return "sft_invalid_reasoning_fields"
        if values and any(value != values[0] for value in values[1:]):
            return "sft_conflicting_reasoning_fields"
    return None


def sft_training_messages(messages, *, drop=False) -> list[dict]:
    """Export one canonical field or none, while keeping the original untouched."""
    if not drop and (issue := sft_reasoning_issue(messages)):
        raise ValueError(issue)
    result = deepcopy(messages)
    for message in result:
        if message.get("role") != "assistant":
            continue
        values = [message[key] for key in REASONING_FIELDS
                  if message.get(key) not in (None, "")]
        for key in REASONING_FIELDS:
            message.pop(key, None)
        if not drop and values:
            message["reasoning_content"] = values[0]
    return result
