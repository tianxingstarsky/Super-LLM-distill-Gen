"""Versioned CoT-to-SFT delivery with bounded, ordered lineage checks."""
from __future__ import annotations

from copy import deepcopy


def _depth_projected_source_messages(sample: dict, result: dict):
    """Carry an explicit downstream depth without rebuilding old reasoning.

    Authoring requests intentionally omit earlier assistant reasoning. Their
    question therefore proves only the learner-input projection; it must not
    replace the canonical source conversation and erase those earlier chains.
    """
    from lib.domain.reasoning_depth import (
        RESOLVED_REASONING_DEPTHS, apply_reasoning_depth_control,
        is_reasoning_depth_control_message, reasoning_depth_control_message,
    )
    style = result.get("generation_style")
    depth = style.get("reasoning_depth") if isinstance(style, dict) else None
    question = result.get("question")
    if depth not in RESOLVED_REASONING_DEPTHS or not isinstance(question, list):
        return None
    controls = [message for message in question if is_reasoning_depth_control_message(message)]
    if not controls:
        return None
    if len(controls) != 1 or controls[0] != reasoning_depth_control_message(depth):
        raise ValueError("cot_sft_depth_control_mismatch")
    original = sample.get("messages")
    if not original or original[-1].get("role") != "assistant":
        raise ValueError("cot_sft_lineage_mismatch")

    def learner_input(messages):
        projected = []
        for message in messages:
            if not isinstance(message, dict):
                raise ValueError("cot_sft_lineage_mismatch")
            if is_reasoning_depth_control_message(message):
                continue
            hidden = {"reasoning_content", "reasoning", "thinking"} if message.get("role") == "assistant" else set()
            projected.append({key: value for key, value in message.items() if key not in hidden})
        return projected

    if learner_input(question) != learner_input(original[:-1]):
        raise ValueError("cot_sft_lineage_mismatch")
    projected = apply_reasoning_depth_control(original, depth)
    if (any(message.get("role") == "assistant" for message in original[:-1])
            and projected != original):
        # Every exported assistant can receive training loss. Rewriting only
        # the last response must not globally relabel earlier native/quick
        # turns as deep explanations they were never authored to provide.
        raise ValueError("reasoning_depth_history_control_mismatch")
    original_indices = [index for index, message in enumerate(original)
                        if not is_reasoning_depth_control_message(message)]
    projected_indices = [index for index, message in enumerate(projected)
                         if not is_reasoning_depth_control_message(message)]
    mapping = dict(zip(original_indices, projected_indices))
    return projected, mapping, depth


def cot_updates_sft(recipe: dict) -> bool:
    """Historical recipes keep their independent SFT and CoT deliveries."""
    return (recipe.get("version", 0) >= 15
            and {"sft", "cot"}.issubset(recipe.get("targets", ())))


def cot_with_sft_context(sample: dict, result: dict) -> dict:
    """Carry the conversation and its provenance through downstream rewriting."""
    if result.get("status") != "eligible":
        return {**result, "reasoning_route": "sft_via_cot"}
    row = {**deepcopy(sample), **deepcopy(result), "reasoning_route": "sft_via_cot"}
    depth_projection = _depth_projected_source_messages(sample, result)
    if depth_projection is not None:
        row["messages"], index_mapping, depth = depth_projection
        source_reviews = sample.get("turn_reviews")
        if isinstance(source_reviews, list):
            projected_reviews = deepcopy(source_reviews)
            for review in projected_reviews:
                old_index = review.get("end_message_index") if isinstance(review, dict) else None
                if (type(old_index) is not int or old_index not in index_mapping
                        or sample["messages"][old_index].get("role") != "assistant"):
                    raise ValueError("cot_sft_turn_review_index_mismatch")
                review["end_message_index"] = index_mapping[old_index]
            row["turn_reviews"] = projected_reviews
        row["reasoning_depth_route_audit"] = {
            "version": 1, "reasoning_depth": depth,
            "scope": "upstream_message_index_projection_not_new_review",
            "source_turn_reviews": deepcopy(source_reviews),
            "message_index_map": [{"source_index": source, "canonical_index": target}
                                  for source, target in index_mapping.items()],
        }
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
    # Failed trim/review checkpoints can contain only a rejection receipt.
    # The dependency still exists: losing its route would score the derived
    # SFT again and misalign the ordered canonical CoT iterator.
    result = {**deepcopy(row), "reasoning_route": "sft_via_cot"}
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
            if original.get("status") != "eligible" and original.get("reasoning_route") != "sft_via_cot":
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
