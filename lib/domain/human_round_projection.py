"""Read-only feedback branches projected from a single real workflow version.

Legacy versions retain their actual generation checks. New versions score and
repair in one review stage. This projection never schedules a retry, combines
versions, or turns an unreviewed sample into a scored pass.
"""
from __future__ import annotations

from copy import deepcopy

from lib.domain.workflow_package_review import package_review_stage
from lib.domain.workflow_quality import accepted
from lib.domain.workflow_reasoning_route import cot_updates_sft
from lib.domain.workflow_targets import PREFERENCE_TARGETS


def _count(value):
    return value if type(value) is int and value >= 0 else None


def _stage(state, node):
    value = state.get("stages", {}).get(node, {})
    return {"status": value.get("status", "pending"), "nodes": [node],
            "done": _count(value.get("done")), "total": _count(value.get("total")),
            "phase": value.get("phase"), "error": value.get("error")}


def _verdict(value, source):
    if not isinstance(value, dict) or type(value.get("keep")) is not bool:
        return None
    try:
        passed = accepted(value) if "scores" in value else (
            value["keep"] and value.get("adherence", 5) >= 4)
    except (KeyError, TypeError):
        return None
    return {"source": source, "status": "accepted" if passed else "rejected",
            "reason": value.get("reason"), "verdict": deepcopy(value)}


def _record_projection(result, *, optional_enabled, max_depth):
    if not result:
        return None
    record = result.get("record", result)
    checks = []
    for key in ("judge", "qa_contract_check", "style_check", "consistency"):
        check = _verdict(record.get(key), key)
        if check:
            checks.append(check)
    contract = record.get("qa_contract_check", {})
    check = _verdict(contract.get("human_augmentation"), "human_augmentation")
    if check:
        checks.append(check)
    for index, turn in enumerate(record.get("turn_reviews", [])):
        for key in ("judge", "qa_contract_check"):
            check = _verdict(turn.get(key), f"turn_reviews.{index}.{key}")
            if check:
                checks.append(check)
        check = _verdict(turn.get("qa_contract_check", {}).get("human_augmentation"),
                         f"turn_reviews.{index}.human_augmentation")
        if check:
            checks.append(check)
    review = record.get("package_review", {})
    check = _verdict(review.get("verdict"), "package_review")
    if check:
        checks.append(check)
    receipt = record.get("review_repair") or {}
    if receipt:
        # These are the verdicts for the current reviewed content. Historical
        # generation verdicts must not imply that an edited answer passed.
        checks = []
        check = _verdict(receipt.get("verdict"), "review")
        if check:
            checks.append(check)
        elif receipt.get("status") in {"accepted", "rejected"}:
            checks.append({"source": "human_review" if receipt.get("mode") == "human" else "review",
                           "status": receipt["status"], "reason": receipt.get("reason"),
                           "score": receipt.get("score")})
    context = (result.get("lineage") or record.get("revision_context") or record.get("qa_contract", {}).get("human_design", {})
               .get("seed", {}).get("revision_context") or {})
    depth = _count(context.get("depth", 0))
    limited = max_depth is not None and depth is not None and depth >= max_depth
    status = record.get("status", "unknown")
    route = "accepted" if status == "eligible" else "rejected" if status in {"quarantined", "skipped"} else "unknown"
    if receipt.get("status") == "waiting_manual_review":
        route = "waiting_manual_review"
    reasons = list(dict.fromkeys(check["reason"] for check in checks
                                if check["status"] == "rejected" and check.get("reason")))
    if route == "rejected" and not reasons and record.get("reason"):
        reasons.append(record["reason"])
    return {"candidate_id": result.get("candidate_id", record.get("id")),
            "target": result.get("target"), "status": status, "route": route,
            "content_sha256": result.get("content_sha256"), "depth": depth,
            "can_revise": not limited and route in {"accepted", "rejected", "waiting_manual_review"},
            "depth_limited": limited, "reason": record.get("reason"),
            "reviewer_feedback": "\n".join(reasons), "checks": checks,
            "optional_score_status": ((receipt or review).get("status", "not_performed")
                                      if optional_enabled else "disabled"),
            "review_repair": deepcopy(receipt),
            "parent_run_id": context.get("parent_run_id"),
            "feedback": deepcopy(result.get("feedback", []))}


def project_feedback_branch(recipe, state, round_info=None, selected_result=None, quality=None, *, target=None):
    """Project one target/version without reading artifacts or starting work.

    A caller may provide a sealed quality summary. Otherwise state.quality is
    usable only after packaging has completed; running stage counters remain
    progress, not final accepted/rejected counts. Counts always concern this
    target/version, including when a run has several export targets.
    """
    recipe, state, info = recipe or {}, state or {}, round_info or {}
    targets = recipe.get("targets", state.get("targets", []))
    target = target or (selected_result or {}).get("target") or next(iter(targets), "sft")
    generation = "preference" if target in PREFERENCE_TARGETS else target
    if target == "sft" and cot_updates_sft(recipe):
        generation = "cot"
    review_config = recipe.get("package_review") or {}
    enabled = review_config.get("enabled") is True
    combined = recipe.get("review_repair") is not None
    mode = (recipe.get("review_repair") or {}).get("mode", "auto")
    review_node = "review" if combined else package_review_stage(review_config) if enabled else generation
    correction, package = _stage(state, review_node if combined else generation), _stage(state, "package")
    self_check = {**deepcopy(correction), "coupled_with_generation": not combined}
    scoring = _stage(state, review_node) if enabled or combined else {
        "status": "disabled", "nodes": [], "done": None, "total": None, "phase": None, "error": None}
    scoring.update(enabled=enabled and mode != "human", node=review_node,
                   mode=mode if combined else review_config.get("mode") if enabled else None)
    summary = quality
    if summary is None and package["status"] == "completed":
        summary = state.get("quality")
    values = (summary or {}).get("targets", {}).get(target, {})
    total, approved = _count(values.get("total")), _count(values.get("eligible"))
    rejected = total - approved if total is not None and approved is not None and total >= approved else None
    plan = values.get("package_review", {})
    unreviewed = _count(plan.get("unreviewed")) if enabled else None
    scoring.update(reviewed=_count(plan.get("reviewed")), accepted=_count(plan.get("accepted")),
                   rejected=_count(plan.get("rejected")), unreviewed=unreviewed,
                   coverage_percent=plan.get("coverage_percent"), coverage_status=plan.get("status"))
    repair_summary = (summary or {}).get("review_repair", {}).get("targets", {}).get(target, {})
    if combined and repair_summary:
        statuses = repair_summary.get("statuses", {})
        scored = sum(statuses.get(key, 0) for key in ("accepted", "rejected"))
        unreviewed = _count(statuses.get("waiting_manual_review", 0))
        scoring.update(reviewed=scored, accepted=_count(statuses.get("accepted", 0)),
                       rejected=_count(statuses.get("rejected", 0)), unreviewed=unreviewed)
    feedback = deepcopy(info.get("feedback_summary") or {"pending": 0, "ready": 0, "blocked": 0, "applied": 0})
    max_depth = _count((info.get("limits") or {}).get("max_revision_depth"))
    selected = _record_projection(selected_result, optional_enabled=enabled, max_depth=max_depth)
    status = info.get("status", state.get("status", "draft"))
    active = info.get("active", status == "running")
    running_nodes = [key for key, row in state.get("stages", {}).items() if row.get("status") == "running"]
    phase, next_action = "waiting", "none"
    if status in {"failed", "interrupted", "cancel_requested", "preparation_failed"}:
        phase, next_action = "interrupted", "resume" if status in {"failed", "interrupted"} else "none"
    elif status in {"queued", "prepared", "creating"}:
        phase = "waiting"
    elif active or status == "running":
        phase = ("repairing" if correction.get("phase") == "repairing" else "reviewing") if review_node in running_nodes and (enabled or combined) else "waiting" if combined else "generating"
        if "package" in running_nodes and package.get("phase") != "ai_review":
            phase = "packaging"
    elif status in {"draft", "cancelled"} and not selected:
        phase = "waiting"
    elif selected:
        if selected["route"] in {"rejected", "waiting_manual_review"}:
            phase = "revision_limit" if selected["depth_limited"] else "needs_revision"
            next_action = "revision_limit" if selected["depth_limited"] else "feedback_required"
        elif selected["route"] == "accepted":
            phase = "approved"
    elif rejected is not None and rejected > 0:
        phase, next_action = "needs_revision", "feedback_required"
    elif approved is not None and approved > 0:
        phase = "approved"
    elif status == "needs_attention":
        phase, next_action = "needs_revision", "feedback_required"
    if phase == "approved" and (selected is None or selected["can_revise"]):
        next_action = "feedback_optional"
    if not active and status not in {"queued", "prepared", "creating", "interrupted", "cancel_requested"}:
        if feedback.get("ready", 0):
            phase, next_action = "editing", "submit_revision"
        elif feedback.get("blocked", 0):
            phase, next_action = "revision_limit", "revision_limit"
    actionable = (not active and status not in {"queued", "prepared", "creating", "interrupted", "cancel_requested"}
                  and next_action in {"feedback_required", "feedback_optional", "submit_revision"})
    if info.get("pending_round_id") and info["pending_round_id"] != info.get("id", info.get("round_id")):
        actionable = False
        next_action = "wait_for_round"
    repair_attempts = (selected or {}).get("review_repair", {}).get("attempts", repair_summary.get("repair_attempts", 0))
    return {"phase": phase, "next_action": next_action, "actionable": actionable,
            "mode": mode, "repair_rounds": repair_attempts,
            "target": target, "generation_node": generation, "review_node": review_node,
            "generation_nodes": [] if recipe.get("repair_inputs") else [generation], "round_id": info.get("round_id", info.get("id")),
            "run_id": info.get("run_id", state.get("id")), "kind": info.get("kind"), "status": status,
            "parent_run_ids": list(dict.fromkeys(parent["run_id"] for parent in info.get("parent_results", [])
                                                  if parent.get("run_id"))),
            "stages": {"correction": correction, "self_check": self_check, "scoring": scoring, "package": package},
            "counts": {"total": total, "accepted": approved, "rejected": rejected, "unreviewed": unreviewed},
            "reason_counts": deepcopy(values.get("reasons", {})), "feedback": feedback,
            "routes": {"accepted": {"count": approved, "to": "package"},
                       "rejected": {"count": rejected, "to": "review" if combined else "human_feedback"},
                       "rebuild_waiting": {"count": feedback.get("ready", 0), "to": "review" if combined else generation},
                       "depth_limit": {"count": feedback.get("blocked", 0), "to": None}},
            "selected_result": selected, "selected_reason": selected.get("reason") if selected else None,
            "reviewer_feedback": selected.get("reviewer_feedback", "") if selected else "",
            "blocked_by_round_id": info.get("pending_round_id") if not actionable else None,
            "max_revision_depth": max_depth, "auto_rebuild": False, "merge_versions": False}
