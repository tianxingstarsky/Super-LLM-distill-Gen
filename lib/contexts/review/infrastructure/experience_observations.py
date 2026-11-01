"""Record one review as learning material; never score, repair or adopt it."""
from copy import deepcopy
from ..domain.candidate_content import candidate_digest

def record_advisory_review_observation(runtime, target, record, evolution):
    if runtime.recipe.get("review_advisory_policy_version") != 1:
        return None
    # A replay of a saved candidate must not recursively archive this collector's
    # own receipt and become a supposedly new experience every time.
    original = deepcopy(record)
    original.pop("experience_observation", None)
    receipt = original.get("review_repair") or {}
    conversation = {}
    if isinstance(original.get("messages"), list) and original["messages"]:
        conversation["messages"] = deepcopy(original["messages"])
    else:
        for field in ("question", "answer", "reasoning", "prompt", "chosen", "rejected", "text"):
            if field in original:
                conversation[field] = deepcopy(original[field])
    for field in ("tools", "turn_reviews"):
        if field in original:
            conversation[field] = deepcopy(original[field])
    episode = {"id": str(original["id"]), "target": target, "conversation": conversation,
        "review": deepcopy(receipt),
        "actual_delivery": {"status": original.get("status"), "reason": original.get("reason")},
        "assessment_meaning": "Reviewer advice is an observation, not independently established truth. "
            "Advisory release does not prove correctness or successful learning."}
    for field in ("review_observation", "native_generation", "learner_context_check"):
        if field in original:
            if field == "native_generation":
                episode[field] = {key: deepcopy(value) for key, value in original[field].items()
                    if key in {"issues", "instruction_scope_evidence", "leakage_evidence", "raw_reasoning_preserved"}}
            else:
                episode[field] = deepcopy(original[field])
    if original.get("claim_review"):
        episode["claim_audit_ref"] = {"sha256": candidate_digest(original["claim_review"]),
            "complete_audit_retained_in_source_record": True}
    source = original.get("checkpoint_context", {}).get("evidence", {})
    if not source:
        source = original.get("source_context") or {}
    if source:
        episode["source_evidence"] = {key: deepcopy(source[key]) for key in (
            "teacher_evidence", "text", "task", "source_kind", "source_id", "source_line_ranges") if key in source}
    return evolution.record_observation("reasoning", episode, run_id=runtime.state["id"],
        outcome="advisory_release" if original.get("status") == "eligible" else "unknown",
        evidence_status="model_assessed" if receipt.get("verdict") or original.get("review_observation") else "unverified",
        source_record=original, source_receipt={"kind": "workflow_advisory_review", "target": target,
            "recipe_sha256": runtime.state.get("recipe_hash"), "policy_version": 1,
            "experience": deepcopy(runtime.recipe.get("agent_experience", {}))})
