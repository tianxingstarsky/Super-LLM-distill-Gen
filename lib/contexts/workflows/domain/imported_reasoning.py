"""Keep input reconstruction receipts distinct from authored output provenance."""
from __future__ import annotations

from copy import deepcopy


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
