"""Keep a native prefill receipt bound to the exact response it describes."""
from __future__ import annotations

from copy import deepcopy
import hashlib

from lib.domain.workflow_quality import canonical
from lib.domain.workflow_targets import TRAINING_FIELDS


_PREFILL_ORIGIN = "authored_prefix_native_answer"
_PREFILL_METADATA = (
    "reasoning_origin", "reasoning_prefill", "answer_response_details",
    "prefix_review", "reasoning_scope",
)


def retire_reasoning_prefill(target, original, updated, *, origin):
    """Archive changed native-prefill provenance without touching training text.

    This is private processing metadata, not a new model call. An unchanged
    score or review keeps the current receipt. The old exact answer, authored
    prefix and provider continuation remain available only in history after
    edits to the delivered task, answer or explanation.
    """
    receipt = original.get("reasoning_prefill")
    details = original.get("answer_response_details")
    response_receipt = details.get("reasoning_prefill") if isinstance(details, dict) else None
    def native_receipt(value):
        return isinstance(value, dict) and value.get("transport") == "deepseek_chat_beta"

    if (original.get("reasoning_origin") != _PREFILL_ORIGIN
            and not native_receipt(receipt) and not native_receipt(response_receipt)):
        return updated

    fields = TRAINING_FIELDS.get(target, ())
    if not fields:
        return updated
    before = {field: original[field] for field in fields if field in original}
    after = {field: updated[field] for field in fields if field in updated}
    if "tools" in original:
        before["tools"] = original["tools"]
    if "tools" in updated:
        after["tools"] = updated["tools"]
    before_body = canonical(before)
    if before_body == canonical(after):
        return updated

    messages = original.get("messages", [])
    messages = messages if isinstance(messages, list) else []
    final_index = next((index for index in range(len(messages) - 1, -1, -1)
                        if isinstance(messages[index], dict)
                        and messages[index].get("role") == "assistant"), None)
    final_message = messages[final_index] if final_index is not None else {}
    if target == "cot":
        old_answer, old_reasoning = original.get("answer"), original.get("reasoning")
        prompt = original.get("question")
    else:
        old_answer = final_message.get("content")
        old_reasoning = next((final_message[field] for field in
                              ("reasoning_content", "reasoning", "thinking")
                              if field in final_message), None)
        prompt = messages[:final_index] if final_index is not None else []

    entry = {
        "version": 1,
        "scope": "historical_authored_prefix_native_response_only",
        "superseded_by": origin,
        "target": target,
        "content_sha256": hashlib.sha256(before_body.encode("utf-8")).hexdigest(),
        "prompt_sha256": hashlib.sha256(canonical(prompt).encode("utf-8")).hexdigest(),
        "answer": deepcopy(old_answer),
        "reasoning": deepcopy(old_reasoning),
        **{field: deepcopy(original[field]) for field in _PREFILL_METADATA if field in original},
    }
    if "source_id" in original:
        entry["source_id"] = original["source_id"]
    if target != "cot" and final_index is not None:
        entry["end_message_index"] = final_index
    history = updated.get("reasoning_prefill_history", [])
    history = deepcopy(history) if isinstance(history, list) else []
    # Retry/resume callers may apply the same invalidation to an already
    # updated row. Keep one copy of the exact historical receipt.
    if not any(canonical(previous) == canonical(entry) for previous in history):
        history.append(entry)
    updated["reasoning_prefill_history"] = history
    updated["reasoning_origin"] = origin
    updated["reasoning_prefill"] = {
        "version": 1, "applied": False, "status": "not_applied_after_revision",
    }
    for field in ("answer_response_details", "prefix_review", "reasoning_scope"):
        updated.pop(field, None)
    return updated
