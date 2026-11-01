"""Retire inherited author effort when its exact conversation prefix is edited."""
from __future__ import annotations

from copy import deepcopy


def retire_generation_controls(target, original, updated, *, origin):
    """A new review score is not a new author call; an edited answer loses old labels."""
    old_controls, current = original.get("generation_controls"), updated.get("generation_controls")
    if not isinstance(old_controls, dict) or not isinstance(current, dict):
        return updated

    def messages(row):
        if target == "cot":
            question = row.get("question")
            if not isinstance(question, list):
                return []
            return [*question, {"role": "assistant", "content": row.get("answer"),
                                "reasoning_content": row.get("reasoning")}]
        return row.get("messages", [])

    old_messages, new_messages = messages(original), messages(updated)
    old_entries = old_controls.get("assistant_turns", [])
    kept, retired = [], []
    for entry in current.get("assistant_turns", []):
        index = entry.get("message_index")
        inherited = entry in old_entries
        same_prefix = (type(index) is int and 0 <= index < len(old_messages)
            and index < len(new_messages) and old_messages[:index + 1] == new_messages[:index + 1])
        (retired if inherited and not same_prefix else kept).append(deepcopy(entry))
    if not retired:
        return updated
    if kept:
        updated["generation_controls"] = {"version": 1, "assistant_turns": kept}
    else:
        updated.pop("generation_controls", None)
    # Exported metadata may have been copied back with a repaired payload.
    # Re-project only the surviving author evidence instead of retaining it.
    updated.pop("training_control", None)
    if any(entry["generation_control"] == updated.get("generation_control") for entry in retired):
        updated.pop("generation_control", None)
    for receipt in [*updated.get("turn_reviews", []), *(updated.get("reading_session") or {}).get("turns", [])]:
        if any(receipt.get("end_message_index") == entry["message_index"]
                and receipt.get("generation_control") == entry["generation_control"] for entry in retired):
            receipt.pop("generation_control", None)
    history = updated.setdefault("generation_control_history", [])
    event = {"version": 1, "target": target, "superseded_by": origin, "assistant_turns": retired}
    if event not in history:
        history.append(event)
    return updated
