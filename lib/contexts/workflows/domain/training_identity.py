"""Identity of the actual learner input, independent of its training view."""
from __future__ import annotations

import hashlib
from lib.domain.workflow_quality import canonical


def actual_qa_input_messages(row, target=None):
    """Keep public task/tool context; private reasoning is not a new input."""
    if target == "cot" or (target is None and row.get("question") is not None
                           and row.get("answer") is not None and row.get("reasoning") is not None):
        question = row.get("question")
        messages = question if isinstance(question, list) else [{"role": "user", "content": question}]
    elif target in {"dpo", "orpo", "rlaif"} or (target is None and isinstance(row.get("prompt"), list)):
        messages = row.get("prompt")
    else:
        original = row.get("messages")
        messages = original[:-1] if isinstance(original, list) and original else None
    if (not isinstance(messages, list) or not messages
            or any(not isinstance(message, dict) for message in messages)
            or not any(message.get("role") == "user" for message in messages)):
        return None
    private = {"reasoning_content", "reasoning", "thinking", "reasoning_summary", "reasoning_details", "thinking_blocks"}
    return [{name: value for name, value in message.items() if name not in private} for message in messages]


def actual_qa_input_identity(row, target=None):
    messages = actual_qa_input_messages(row, target)
    return hashlib.sha256(canonical(messages).encode("utf-8")).hexdigest() if messages is not None else None
