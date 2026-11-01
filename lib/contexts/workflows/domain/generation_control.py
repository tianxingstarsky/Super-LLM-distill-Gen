"""Bind trusted generation receipts to their actual assistant message positions."""
from __future__ import annotations

from copy import deepcopy


def feedback_control_scope(receipt):
    """One target can join multiple author turns, so its label needs all sources."""
    segments = receipt.get("reasoning_segments", [])
    controls = {turn.get("phase"): (turn.get("response") or {}).get("response_details", {}).get("generation_control")
                for turn in receipt.get("transcript", [])}
    selected = [controls.get(segment.get("phase")) for segment in segments]
    if not selected or (not any(isinstance(control, dict) for control in selected)
            and not isinstance(receipt.get("response_details", {}).get("generation_control"), dict)):
        return None
    missing = any(not isinstance(control, dict) for control in selected)
    efforts = {control["effort"] for control in selected if isinstance(control, dict)}
    return {"version": 1, "status": "partial" if missing else "mixed" if len(efforts) > 1 else "complete",
            "phases": [segment.get("phase") for segment in segments]}


def generation_controls_metadata(turns):
    """Project known author calls only; absent history never inherits a later effort.

    Callers supply (message_index, provenance) pairs from generation responses,
    not model-authored JSON, review calls or source conversation messages.
    """
    entries = []
    for message_index, provenance in turns:
        control = provenance.get("generation_control")
        if not isinstance(control, dict):
            continue
        entry = {"message_index": message_index, "generation_control": deepcopy(control)}
        if isinstance(provenance.get("reasoning_call"), dict):
            entry["reasoning_call"] = deepcopy(provenance["reasoning_call"])
        entries.append(entry)
    return {"generation_controls": {"version": 1, "assistant_turns": entries}} if entries else {}
