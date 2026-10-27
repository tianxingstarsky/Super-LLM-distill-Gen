"""Draft/final handoff identity, not a claim of semantic correctness."""
from copy import deepcopy

from .dialogue import dialogue_digest


def finalization_receipt(draft, candidate=None):
    receipt = {"version": 1, "mode": "draft_then_finalize",
               "independent_review": False, "draft": deepcopy(draft),
               "draft_sha256": dialogue_digest(draft)}
    if isinstance(candidate, dict):
        receipt["candidate_sha256"] = dialogue_digest(candidate)
        receipt["reasoning_changed"] = candidate.get("reasoning") != draft.get("reasoning")
    return receipt
