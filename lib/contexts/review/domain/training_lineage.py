"""Training views of one authenticated task are not independent examples.

Callers must authenticate revision contexts before using these pure rules.
Same question/source/family alone is deliberately insufficient proof.
"""
from __future__ import annotations

from lib.contexts.workflows.contracts import actual_qa_input_identity
from .candidate_content import candidate_digest, _manual_patch


TRAINING_TASK_VIEWS = frozenset({"sft", "cot", "dpo", "orpo", "rlaif"})


def revision_root(context):
    ancestors = context.get("ancestors") if isinstance(context, dict) else None
    if not ancestors:
        return None
    root = ancestors[0]
    return (root["run_id"], root["candidate_id"])


def repaired_candidate_identity(run_id, target, row, context, *, format_lineage=False):
    """New task views share an ID; historical recipe IDs stay frozen."""
    if format_lineage and target in TRAINING_TASK_VIEWS:
        root, actual = revision_root(context), actual_qa_input_identity(row, target)
        if root is None or actual is None:
            raise ValueError("review_format_lineage_missing")
        return candidate_digest([run_id, "training_task_view_v1", root, actual, row.get("source_id")])
    return candidate_digest([run_id, target, context["candidate_id"]])


def validate_format_view_selections(inputs, run_id):
    """Reject two versions of one format before buying conflicting repairs."""
    seen = set()
    for item in inputs:
        if item["target"] not in TRAINING_TASK_VIEWS:
            continue
        row = _manual_patch(item["target"], item["record"], item)
        identity = (item["target"], repaired_candidate_identity(run_id, item["target"], row,
            item["revision_context"], format_lineage=True))
        if identity in seen:
            raise ValueError("duplicate_review_repair_task_view")
        seen.add(identity)


def same_training_task_view(current, target, owner, owner_target):
    """Only distinct views with the exact task and authenticated root match."""
    if target not in TRAINING_TASK_VIEWS or owner_target not in TRAINING_TASK_VIEWS or target == owner_target:
        return False
    context = current.get("revision_context") or {}
    owner_context = owner.get("revision_context") or {}
    root = revision_root(context)
    owner_root = revision_root(owner_context)
    if (root is None or root != owner_root or current.get("source_id") != owner.get("source_id")
            or not current.get("source_id")):
        return False
    actual = actual_qa_input_identity(current, target)
    return actual is not None and actual == actual_qa_input_identity(owner, owner_target)
