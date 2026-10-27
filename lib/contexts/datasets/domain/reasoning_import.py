"""Adapt explicit NeMo/OpenThoughts result envelopes, without certifying quality.

The original row remains in the source library. Only documented delimiters are
decoded; absent reasoning is never reconstructed from an answer or a score.
"""
from copy import deepcopy
from hashlib import sha256
import json


_THINK = ("<think>", "</think>")
_STRATOS = ("<|begin_of_thought|>", "<|end_of_thought|>",
            "<|begin_of_solution|>", "<|end_of_solution|>")


def _split_trace(text):
    if not isinstance(text, str):
        return None
    stripped = text.strip()
    if stripped.startswith(_STRATOS[0]):
        if any(stripped.count(tag) != 1 for tag in _STRATOS):
            raise ValueError("reasoning_import_incomplete_delimiters")
        _, rest = stripped.split(_STRATOS[0], 1)
        reasoning, rest = rest.split(_STRATOS[1], 1)
        if not rest.lstrip().startswith(_STRATOS[2]) or not rest.endswith(_STRATOS[3]):
            raise ValueError("reasoning_import_invalid_delimiter_order")
        answer = rest.lstrip()[len(_STRATOS[2]):-len(_STRATOS[3])]
        format_name = "openthoughts_stratos"
    elif stripped.startswith(_THINK[0]):
        if any(stripped.count(tag) != 1 for tag in _THINK):
            raise ValueError("reasoning_import_incomplete_delimiters")
        reasoning, answer = stripped[len(_THINK[0]):].split(_THINK[1], 1)
        format_name = "think_delimited"
    else:
        return None
    if not reasoning.strip() or not answer.strip():
        raise ValueError("reasoning_import_empty_reasoning_or_answer")
    return reasoning.strip(), answer.strip(), format_name


def adapt_reasoning_record(row):
    """Return a normalized-envelope candidate, or None for an unrelated format.

    Conversation roles and tool history are left for the existing import
    boundary to validate. Scores from an upstream run never imply acceptance.
    """
    if not isinstance(row, dict):
        return None
    candidate = deepcopy(row)
    nemo = "problem" in row and "generation" in row
    if nemo:
        if not isinstance(row["problem"], str) or not isinstance(row["generation"], str):
            raise ValueError("reasoning_import_invalid_nemo_record")
        if row.get("messages") or row.get("conversations"):
            raise ValueError("reasoning_import_ambiguous_conversation")
        candidate["messages"] = [{"role": "user", "content": row["problem"]},
                                 {"role": "assistant", "content": row["generation"]}]
    messages = candidate.get("messages", candidate.get("conversations"))
    if not isinstance(messages, list):
        return None
    formats = []
    for message in messages:
        if not isinstance(message, dict) or message.get("role", message.get("from")) not in {"assistant", "gpt"}:
            continue
        field = "content" if "content" in message else "value"
        decoded = _split_trace(message.get(field))
        if decoded is None:
            continue
        reasoning, answer, format_name = decoded
        if message.get("reasoning_content") not in (None, "", reasoning):
            raise ValueError("reasoning_import_conflicting_reasoning")
        message["reasoning_content"], message[field] = reasoning, answer
        formats.append(format_name)
    if not nemo and not formats:
        return None
    # Stratos stores its training system message separately. Preserve it once;
    # do not import NeMo's transport prompt as learner-visible context.
    system = row.get("system")
    if not nemo and system:
        if not isinstance(system, str):
            raise ValueError("reasoning_import_invalid_system")
        existing = [m for m in messages if isinstance(m, dict) and m.get("role", m.get("from")) == "system"]
        if existing and (len(existing) != 1 or existing[0].get("content", existing[0].get("value")) != system):
            raise ValueError("reasoning_import_conflicting_system")
        if not existing:
            messages.insert(0, {"role": "system", "content": system})
    candidate["reasoning_import"] = {
        "version": 1, "format": "nemo_generation" if nemo else formats[0],
        "decoded_turns": len(formats), "quality": "unassessed",
        "source_sha256": sha256(json.dumps(row, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest(),
    }
    return candidate
