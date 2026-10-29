"""User-selected handling of absent reasoning in imported assistant turns."""
from __future__ import annotations

from lib.domain.reasoning_fields import REASONING_FIELDS
from .dialogue import dialogue_digest


CONVERSATION_REASONING_MODES = ("preserve", "fill_missing", "exclude")
INSUFFICIENT_EVIDENCE = "<INSUFFICIENT_EVIDENCE>"


def validate_conversation_reasoning(value: dict | None) -> dict | None:
    """Validate the optional creation contract; absence preserves legacy recipes."""
    if value is None:
        return None
    if (not isinstance(value, dict) or set(value) != {"version", "mode"}
            or type(value["version"]) is not int or value["version"] != 1
            or value["mode"] not in CONVERSATION_REASONING_MODES):
        raise ValueError("invalid_conversation_reasoning")
    return dict(value)


def has_recorded_reasoning(message: dict) -> bool:
    """Whitespace and absent aliases are missing, but real source text is retained."""
    return any(isinstance(message.get(field), str) and message[field].strip()
               for field in REASONING_FIELDS)


def conversation_reasoning_receipt(messages: list[dict], mode: str) -> dict:
    """Describe coverage without interpreting supplied reasoning as verified truth."""
    turns = []
    for index, message in enumerate(messages):
        if message.get("role") != "assistant":
            continue
        existing = has_recorded_reasoning(message)
        turns.append({"message_index": index,
                      "origin": "source" if existing else "missing",
                      "status": "preserved" if existing else "missing",
                      "source_scope": {"start_message_index": 0, "end_message_index": index}})
    missing = sum(turn["origin"] == "missing" for turn in turns)
    return {"version": 1, "mode": mode, "assistant_turns": len(turns),
            "existing_turns": len(turns) - missing, "missing_turns": missing,
            "synthesized_turns": 0, "remaining_missing_turns": missing,
            "source_messages_sha256": dialogue_digest(messages),
            "semantic_status": "not_assessed", "status": "preserved", "turns": turns}


def generated_reasoning_issue(response) -> str | None:
    """Check the response boundary, never declare its semantics correct."""
    if not isinstance(response, dict) or set(response) != {"reasoning_content"}:
        return "conversation_reasoning_invalid_response"
    content = response["reasoning_content"]
    if not isinstance(content, str) or not content.strip():
        return "conversation_reasoning_empty_response"
    if INSUFFICIENT_EVIDENCE in content:
        return "conversation_reasoning_insufficient_evidence"
    if "\ufffd" in content or "\x00" in content:
        return "conversation_reasoning_invalid_encoding"
    return None
