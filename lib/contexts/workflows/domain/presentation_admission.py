"""Optional style advice; factual and checklist admission remain separate gates."""
from copy import deepcopy


def style_admission(checklist, assessment):
    """Never changes a reviewer's grade or decides factual correctness.

    Callers must separately require content, task-contract and checklist passes.
    Frozen tasks without the explicit version-6 choice retain strict behavior.
    """
    satisfied = assessment["keep"] and assessment["adherence"] >= 4
    advisory = (isinstance(checklist, dict) and checklist.get("version") == 6
                and checklist.get("style_admission") == "advisory"
                and checklist.get("evidence_policy") != 2)
    return {"allowed": bool(satisfied or advisory), "advisories": (
        [{"kind": "presentation", "policy": "advisory", "assessment": deepcopy(assessment)}]
        if advisory and not satisfied else [])}
