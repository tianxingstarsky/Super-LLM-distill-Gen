"""Pure rules for human review of supervised fine-tuning conversations."""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json

from lib.domain.workflow_quality import conversation_issue


def sft_identity(row: dict) -> str:
    payload = json.dumps(row, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def validate_sft_record(row: dict) -> dict:
    if not isinstance(row, dict) or set(row) - {"messages", "tools"} or "messages" not in row:
        raise ValueError("invalid_sft_record")
    if "tools" in row and (not isinstance(row["tools"], list) or any(not isinstance(tool, dict) for tool in row["tools"])):
        raise ValueError("invalid_sft_tools")
    messages = row["messages"]
    issue = conversation_issue(messages, final=True)
    if issue:
        raise ValueError(f"invalid_sft_conversation:{issue}")
    if len(json.dumps(row, ensure_ascii=False)) > 2_000_000:
        raise ValueError("sft_record_too_large")
    return deepcopy(row)


def validate_sft_revision(source: dict, candidate: dict) -> dict:
    original = validate_sft_record(source)
    revised = validate_sft_record(candidate)
    if set(revised) != set(original) or revised.get("tools") != original.get("tools"):
        raise ValueError("sft_training_metadata_is_immutable")
    if len(revised["messages"]) != len(original["messages"]):
        raise ValueError("sft_message_count_is_immutable")
    for before, after in zip(original["messages"], revised["messages"]):
        if before.get("role") != after.get("role") or set(before) != set(after):
            raise ValueError("sft_message_structure_is_immutable")
        allowed = {"content", "reasoning_content"} if before.get("role") == "assistant" else set()
        if any(before[key] != after[key] for key in before if key not in allowed):
            raise ValueError("sft_message_metadata_is_immutable")
        for key in allowed & set(after):
            value = after[key]
            if value is not None and not isinstance(value, str):
                raise ValueError("sft_revision_fields_must_be_text")
            if isinstance(value, str) and len(value) > 500_000:
                raise ValueError("sft_revision_too_long")
    return revised


def sft_review_record(row: dict, *, decision: str, reviewer: str,
                      reason: str = "", candidate: dict | None = None) -> dict:
    source = validate_sft_record(row)
    if decision not in {"approved", "rejected", "skipped"}:
        raise ValueError("invalid_sft_decision")
    if not isinstance(reviewer, str) or not reviewer.strip() or len(reviewer) > 128:
        raise ValueError("invalid_reviewer")
    if not isinstance(reason, str) or len(reason) > 2000:
        raise ValueError("invalid_review_reason")
    revised = source if candidate is None else validate_sft_revision(source, candidate)
    if decision != "approved" and revised != source:
        raise ValueError("only_approved_sft_samples_may_be_revised")
    identity = sft_identity(source)
    return {"sample_id": identity, "source_hash": identity, "decision": decision,
            "reviewer": reviewer.strip(), "reviewed_at": datetime.now(timezone.utc).isoformat(),
            "reason": reason.strip(), "candidate": revised}
