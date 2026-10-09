"""Versioned CoT-to-SFT delivery with bounded, ordered lineage checks."""
from __future__ import annotations

from copy import deepcopy


def cot_updates_sft(recipe: dict) -> bool:
    """Historical recipes keep their independent SFT and CoT deliveries."""
    return (recipe.get("version", 0) >= 15
            and {"sft", "cot"}.issubset(recipe.get("targets", ())))


def cot_with_sft_context(sample: dict, result: dict) -> dict:
    """Carry the conversation and its provenance through downstream rewriting."""
    if result.get("status") != "eligible":
        return {**result, "reasoning_route": "sft_via_cot"}
    row = {**deepcopy(sample), **deepcopy(result), "reasoning_route": "sft_via_cot"}
    messages = row.get("messages")
    if not messages or messages[-1].get("role") != "assistant":
        raise ValueError("cot_sft_lineage_mismatch")
    for message in messages[:-1]:
        if message.get("role") != "assistant":
            continue
        reasoning = [message[key] for key in ("reasoning_content", "reasoning", "thinking")
                     if message.get(key) not in (None, "")]
        if any(not isinstance(value, str) for value in reasoning):
            issue = "cot_sft_invalid_reasoning_fields"
        elif reasoning and any(value != reasoning[0] for value in reasoning[1:]):
            issue = "cot_sft_conflicting_reasoning_fields"
        else:
            issue = None
        if issue:
            return {"id": sample["id"], "source_id": sample["source_id"],
                    "status": "quarantined", "reason": issue, "reasoning_route": "sft_via_cot"}
        if reasoning:
            message["reasoning_content"] = reasoning[0]
        message.pop("reasoning", None)
        message.pop("thinking", None)
    final = messages[-1]
    final["content"] = row["answer"]
    final["reasoning_content"] = "\n".join(row["reasoning"])
    # A rewritten answer has one canonical reasoning field, not stale aliases.
    final.pop("reasoning", None)
    final.pop("thinking", None)
    row["question"] = deepcopy(messages[:-1])
    return row


def finalized_cot_sft_row(row: dict) -> dict:
    """Project final CoT evidence into SFT without exposing an earlier answer."""
    result = deepcopy(row)
    if result.get("status") == "eligible":
        messages = result.get("messages")
        if not messages or messages[-1].get("role") != "assistant":
            raise ValueError("cot_sft_lineage_mismatch")
        messages[-1]["content"] = result["answer"]
        messages[-1]["reasoning_content"] = "\n".join(result["reasoning"])
    for key in ("question", "reasoning", "answer"):
        result.pop(key, None)
    return result


def finalized_sft_rows(originals, finalized_cot):
    """Merge in order using one row of memory, retaining upstream rejections.

    CoT receives only eligible SFT rows. Stage execution preserves input order,
    so mismatching or missing lineage is an error rather than an implicit
    fallback to an unprocessed SFT answer.
    """
    derived = iter(finalized_cot)
    try:
        for original in originals:
            if original.get("status") != "eligible":
                yield original
                continue
            final = next(derived, None)
            if final is None or final.get("id") != original.get("id"):
                raise ValueError("cot_sft_lineage_mismatch")
            yield finalized_cot_sft_row(final)
        if next(derived, None) is not None:
            raise ValueError("cot_sft_lineage_mismatch")
    finally:
        close = getattr(derived, "close", None)
        if close:
            close()
