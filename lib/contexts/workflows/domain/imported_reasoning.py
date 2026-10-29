"""Keep input reconstruction receipts distinct from authored output provenance."""
from __future__ import annotations

from copy import deepcopy

from lib.domain.workflow_quality import canonical
from .targets import TRAINING_FIELDS


def retire_imported_reasoning(target: str, original: dict, updated: dict, *, origin: str) -> dict:
    """Keep an import receipt historical after an actual training-body edit."""
    receipt = original.get("conversation_reasoning")
    fields = TRAINING_FIELDS.get(target, ())
    if not isinstance(receipt, dict) or not fields:
        return updated
    before = {field: original[field] for field in (*fields, "tools") if field in original}
    after = {field: updated[field] for field in (*fields, "tools") if field in updated}
    if canonical(before) == canonical(after):
        return updated
    updated["conversation_reasoning"] = deepcopy(receipt)
    updated["conversation_reasoning_scope"] = "input_only"
    # An input-only receipt never owns the current output's provenance. A
    # caller may also already have attached a newly generated native response.
    if (original.get("conversation_reasoning_scope") != "input_only"
            and updated.get("reasoning_origin") in {"source", "synthetic_explanation", "none"}):
        updated["reasoning_origin"] = origin
    return updated


def imported_reasoning_metadata(record: dict, *, messages_preserved: bool) -> dict:
    """Copy an input receipt without treating reconstruction as quality approval."""
    receipt = record.get("conversation_reasoning")
    if not isinstance(receipt, dict):
        return {}
    metadata = {
        "conversation_reasoning": deepcopy(receipt),
        "conversation_reasoning_scope": "input_messages_preserved" if messages_preserved else "input_only",
    }
    if messages_preserved:
        metadata["reasoning_origin"] = (
            "synthetic_explanation" if receipt.get("synthesized_turns", 0) else
            "source" if receipt.get("existing_turns", 0) else "none")
    return metadata


def imported_turn_reasoning_origin(record: dict, message_index: int) -> str | None:
    """Resolve an extracted assistant's origin instead of inheriting a mixed record label."""
    receipt = record.get("conversation_reasoning")
    if not isinstance(receipt, dict) or record.get("conversation_reasoning_scope") == "input_only":
        return None
    for turn in receipt.get("turns", []):
        if turn.get("message_index") == message_index:
            return {"source": "source", "synthetic_explanation": "synthetic_explanation",
                    "missing": "none"}.get(turn.get("origin"))
    return None
