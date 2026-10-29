"""User-selected handling of absent reasoning in imported assistant turns."""
from __future__ import annotations
import json

from lib.domain.reasoning_fields import REASONING_FIELDS
from .dialogue import dialogue_digest


CONVERSATION_REASONING_MODES = ("preserve", "fill_missing", "exclude")
INSUFFICIENT_EVIDENCE = "<INSUFFICIENT_EVIDENCE>"


def validate_conversation_reasoning(value: dict | None) -> dict | None:
    """Validate the optional creation contract; absence preserves legacy recipes."""
    if value is None:
        return None
    if (not isinstance(value, dict) or set(value) != {"version", "mode"}
            or type(value["version"]) is not int or value["version"] not in (1, 2, 3)
            or value["mode"] not in CONVERSATION_REASONING_MODES):
        raise ValueError("invalid_conversation_reasoning")
    return dict(value)


def has_recorded_reasoning(message: dict) -> bool:
    """Whitespace and absent aliases are missing, but real source text is retained."""
    return any(isinstance(message.get(field), str) and message[field].strip()
               for field in REASONING_FIELDS)


def conversation_reasoning_receipt(messages: list[dict], mode: str, *, version: int = 1) -> dict:
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
    return {"version": version, "mode": mode, "assistant_turns": len(turns),
            "existing_turns": len(turns) - missing, "missing_turns": missing,
            "synthesized_turns": 0, "remaining_missing_turns": missing,
            "source_messages_sha256": dialogue_digest(messages),
            "semantic_status": "not_assessed", "status": "preserved", "turns": turns}


def generated_reasoning_issue(response) -> str | None:
    """Check the response boundary, never declare its semantics correct."""
    return _generated_text_issue(response, "reasoning_content")


def generated_hints_issue(response, assistant_response: dict) -> str | None:
    """Reject invalid hints and literal answer copying, not certify paraphrases."""
    if issue := _generated_text_issue(response, "reasoning_hints"):
        return issue
    hints = "".join(response["reasoning_hints"].split())

    def argument_texts(value):
        if isinstance(value, str):
            yield value
        elif isinstance(value, dict):
            for item in value.values():
                yield from argument_texts(item)
        elif isinstance(value, list):
            for item in value:
                yield from argument_texts(item)

    def target_texts(value):
        if isinstance(value, str):
            yield value
        elif isinstance(value, list):
            for item in value:
                yield from target_texts(item)
        elif isinstance(value, dict):
            for key in ("content", "text", "tool_calls", "function", "arguments"):
                if key in value:
                    yield from target_texts(value[key])
                    if key == "arguments":
                        arguments = value[key]
                        if isinstance(arguments, str):
                            try:
                                arguments = json.loads(arguments)
                            except (TypeError, ValueError):
                                continue
                        yield from argument_texts(arguments)

    for text in target_texts(assistant_response):
        target = "".join(text.split())
        # Short shared terms are legitimate cues. This detects an entire
        # copied answer/payload only; semantic leakage still needs review.
        if target and (target == hints or (len(target) >= 32 and target in hints)):
            return "conversation_reasoning_hints_copy_response"
    return None


def _generated_text_issue(response, field: str) -> str | None:
    if not isinstance(response, dict) or set(response) != {field}:
        return "conversation_reasoning_invalid_response"
    content = response[field]
    if not isinstance(content, str) or not content.strip():
        return "conversation_reasoning_empty_response"
    if INSUFFICIENT_EVIDENCE in content:
        return "conversation_reasoning_insufficient_evidence"
    if "\ufffd" in content or "\x00" in content:
        return "conversation_reasoning_invalid_encoding"
    return None
