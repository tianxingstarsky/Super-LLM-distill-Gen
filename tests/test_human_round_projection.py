"""Feedback routes are derived from actual per-version checks and coverage."""
from copy import deepcopy

import pytest

from lib.domain.human_round_projection import project_feedback_branch


@pytest.fixture(scope="session", autouse=True)
def mock_llm_server():
    yield


def verdict(*, keep=True, correctness=5, reason="Checked against the source."):
    return {"keep": keep, "grounded": True, "reasoning_valid": True, "correctness": correctness,
            "scores": {key: correctness for key in ("correctness", "grounding", "reasoning", "instruction", "safety")},
            "reason": reason}


def completed(*, eligible=1, total=1, review=None):
    values = {"eligible": eligible, "total": total, "reasons": {"duplicate": total - eligible}}
    if review is not None:
        values["package_review"] = review
    return {"status": "completed", "stages": {"sft": {"status": "completed"},
            "package": {"status": "completed"}}, "quality": {"targets": {"sft": values}}}


def result(*, status="eligible", depth=0, score=None, judge=None):
    record = {"id": "one", "status": status, "judge": judge or verdict(),
              "qa_contract": {"human_design": {"seed": {"revision_context": {"depth": depth}}}}}
    if score is not None:
        record["package_review"] = score
    if status == "quarantined":
        record["reason"] = "package_ai_review_rejected"
    return {"candidate_id": "one", "target": "sft", "record": record}


def test_running_generation_never_invents_final_counts_or_a_separate_check_stage():
    projection = project_feedback_branch({"targets": ["sft"]},
        {"status": "running", "stages": {"sft": {"status": "running", "done": 5, "total": 10}}})
    assert projection["phase"] == "generating"
    assert projection["counts"] == {"total": None, "accepted": None, "rejected": None, "unreviewed": None}
    assert projection["stages"]["self_check"]["coupled_with_generation"]
    assert projection["stages"]["self_check"]["nodes"] == ["sft"]
    assert projection["review_node"] == "sft" and not projection["stages"]["scoring"]["enabled"]


def test_completed_status_does_not_hide_mixed_rejected_results():
    projection = project_feedback_branch({"targets": ["sft"]}, completed(eligible=4, total=6))
    assert projection["phase"] == "needs_revision" and projection["counts"]["rejected"] == 2
    assert projection["routes"]["rebuild_waiting"]["count"] == 0
    assert projection["auto_rebuild"] is False and projection["merge_versions"] is False


def test_sample_not_selected_is_usable_but_never_called_a_scored_pass():
    recipe = {"targets": ["sft"], "package_review": {"enabled": True, "node": "jev", "mode": "sample"}}
    state = completed(review={"reviewed": 1, "accepted": 1, "rejected": 0,
        "unreviewed": 9, "coverage_percent": 10, "status": "sampled"})
    state["stages"]["jev"] = {"status": "completed"}
    projection = project_feedback_branch(recipe, state,
        selected_result=result(score={"status": "not_selected"}))
    assert projection["phase"] == "approved" and projection["actionable"]
    assert projection["next_action"] == "feedback_optional"
    assert projection["selected_result"]["optional_score_status"] == "not_selected"
    assert projection["counts"]["unreviewed"] == 9 and projection["review_node"] == "jev"


def test_reviewer_feedback_uses_real_failed_verdict_not_the_passed_source_check():
    selected = result(status="quarantined", score={"status": "rejected", "verdict":
        verdict(keep=True, correctness=2, reason="The second reply omits the shutdown condition.")})
    before = deepcopy(selected)
    projection = project_feedback_branch({"targets": ["sft"], "package_review": {"enabled": True, "node": "jev"}},
        completed(eligible=0), {"limits": {"max_revision_depth": 3}}, selected)
    assert projection["phase"] == "needs_revision"
    assert projection["reviewer_feedback"] == "The second reply omits the shutdown condition."
    assert [item["status"] for item in projection["selected_result"]["checks"]] == ["accepted", "rejected"]
    assert selected == before


def test_depth_bound_keeps_passed_version_but_blocks_another_rebuild():
    projection = project_feedback_branch({"targets": ["sft"]}, completed(),
        {"limits": {"max_revision_depth": 2}}, result(depth=2))
    assert projection["phase"] == "approved" and not projection["selected_result"]["can_revise"]
    assert not projection["actionable"]
    rejected = project_feedback_branch({"targets": ["sft"]}, completed(eligible=0),
        {"limits": {"max_revision_depth": 2}}, result(status="quarantined", depth=2))
    assert rejected["phase"] == "revision_limit" and rejected["next_action"] == "revision_limit"


def test_saved_feedback_waits_for_explicit_submission_and_limits_are_visible():
    info = {"feedback_summary": {"pending": 3, "ready": 2, "blocked": 1, "applied": 4},
            "limits": {"max_revision_depth": 3}}
    projection = project_feedback_branch({"targets": ["sft"]}, completed(eligible=0), info)
    assert projection["phase"] == "editing" and projection["next_action"] == "submit_revision"
    assert projection["routes"]["rebuild_waiting"]["count"] == 2
    assert projection["routes"]["depth_limit"]["count"] == 1


@pytest.mark.parametrize("target,targets,version,node", [
    ("multiturn", ["multiturn"], 18, "multiturn"), ("dpo", ["dpo"], 18, "preference"),
    ("sft", ["sft", "cot"], 18, "cot"), ("sft", ["sft", "cot"], 14, "sft")])
def test_feedback_returns_to_real_output_stage(target, targets, version, node):
    projection = project_feedback_branch({"targets": targets, "version": version}, {}, target=target)
    assert projection["generation_node"] == projection["review_node"] == node


def test_optional_jev_is_reviewing_only_when_that_stage_really_runs():
    projection = project_feedback_branch({"targets": ["sft"], "package_review": {"enabled": True, "node": "jev"}},
        {"status": "running", "stages": {"sft": {"status": "completed"}, "jev": {"status": "running"}}})
    assert projection["phase"] == "reviewing" and projection["stages"]["scoring"]["status"] == "running"


def test_historical_version_cannot_offer_launch_while_another_round_is_pending():
    projection = project_feedback_branch({"targets": ["sft"]}, completed(),
        {"id": "old", "pending_round_id": "new"}, result())
    assert projection["phase"] == "approved" and not projection["actionable"]
    assert projection["next_action"] == "wait_for_round" and projection["blocked_by_round_id"] == "new"


@pytest.mark.parametrize("status", ["draft", "cancelled"])
def test_empty_workspace_does_not_claim_feedback_is_ready(status):
    projection = project_feedback_branch({"targets": ["sft"]}, {"status": status})
    assert projection["phase"] == "waiting" and projection["next_action"] == "none"
    assert projection["routes"]["rebuild_waiting"]["count"] == 0
    assert not projection["actionable"] and projection["selected_result"] is None


def reviewed_recipe(*, mode="auto", jev=False, repair_inputs=None):
    return {"version": 18, "targets": ["sft"],
            "review_repair": {"mode": mode, "max_rounds": 2, "score_threshold": .8},
            "package_review": {"enabled": jev},
            **({"repair_inputs": repair_inputs} if repair_inputs is not None else {})}


def reviewed_state(*, accepted=1, rejected=0, waiting=0, attempts=0):
    return {"status": "completed" if not rejected and not waiting else "needs_attention",
            "stages": {"sft": {"status": "completed"}, "review": {"status": "completed"},
                       "package": {"status": "completed"}},
            "quality": {"targets": {"sft": {"total": accepted + rejected + waiting,
                                             "eligible": accepted}},
                        "review_repair": {"targets": {"sft": {
                            "statuses": {"accepted": accepted, "rejected": rejected,
                                         "waiting_manual_review": waiting}, "repair_attempts": attempts}}}}}


def test_v18_generation_waits_for_review_without_calling_it_repair():
    projection = project_feedback_branch(reviewed_recipe(),
        {"status": "running", "stages": {"sft": {"status": "running", "done": 5, "total": 10},
                                           "review": {"status": "pending"}}})
    assert projection["phase"] == "waiting"
    assert projection["review_node"] == "review"
    assert projection["stages"]["scoring"]["node"] == "review"
    assert not projection["stages"]["self_check"]["coupled_with_generation"]
    assert projection["counts"] == {"total": None, "accepted": None, "rejected": None, "unreviewed": None}


@pytest.mark.parametrize("jev", [False, True])
def test_v18_real_review_phase_and_counts_exist_without_separate_jev(jev):
    recipe = reviewed_recipe(jev=jev)
    running = project_feedback_branch(recipe,
        {"status": "running", "stages": {"sft": {"status": "completed"},
            "review": {"status": "running", "phase": "scoring", "done": 1, "total": 3}}})
    assert running["phase"] == "reviewing" and running["review_node"] == "review"
    final = project_feedback_branch(recipe, reviewed_state(accepted=2, rejected=1, attempts=2))
    assert final["stages"]["scoring"]["reviewed"] == 3
    assert final["stages"]["scoring"]["accepted"] == 2
    assert final["stages"]["scoring"]["rejected"] == 1
    assert final["counts"]["unreviewed"] == 0
    assert final["repair_rounds"] == 2
    assert final["routes"]["rejected"]["to"] == "review"


def test_v18_manual_waiting_score_opens_review_without_reusing_generation_judge():
    selected = result(status="quarantined", judge=verdict(keep=False, reason="Obsolete generator verdict."))
    selected["record"]["review_repair"] = {"mode": "human", "status": "waiting_manual_review",
        "score": None, "attempts": 0, "reason": "manual_review_required"}
    projection = project_feedback_branch(reviewed_recipe(mode="human"),
        reviewed_state(accepted=0, waiting=1), selected_result=selected)
    assert projection["mode"] == "human" and projection["review_node"] == "review"
    assert projection["actionable"] and projection["next_action"] == "feedback_required"
    assert projection["selected_result"]["route"] == "waiting_manual_review"
    assert projection["selected_result"]["checks"] == []
    assert projection["counts"]["unreviewed"] == 1
    assert "Obsolete generator verdict" not in projection["reviewer_feedback"]


@pytest.mark.parametrize("mode", ["auto", "human"])
def test_v18_current_review_receipt_replaces_stale_generation_checks(mode):
    selected = result(status="quarantined", judge=verdict())
    selected["record"]["review_repair"] = {"mode": mode, "status": "rejected", "score": .4,
        "attempts": 1, "reason": "The edited answer still reverses the condition."}
    if mode == "auto":
        selected["record"]["review_repair"]["verdict"] = verdict(keep=False, correctness=2,
            reason="The edited answer still reverses the condition.")
    projection = project_feedback_branch(reviewed_recipe(mode=mode), reviewed_state(accepted=0, rejected=1),
                                          selected_result=selected)
    checks = projection["selected_result"]["checks"]
    assert len(checks) == 1 and checks[0]["status"] == "rejected"
    assert checks[0]["source"] == ("review" if mode == "auto" else "human_review")
    assert projection["reviewer_feedback"] == "The edited answer still reverses the condition."
    assert projection["phase"] == "needs_revision" and projection["repair_rounds"] == 1


def test_v18_manual_review_child_has_only_review_routes_and_no_generation_nodes():
    selected = result()
    selected["record"]["revision_context"] = {"depth": 1, "parent_run_id": "original"}
    selected["record"]["review_repair"] = {"mode": "human", "status": "accepted",
        "score": .95, "attempts": 0, "reason": "Edited and scored by the user."}
    info = {"id": "manual-child", "kind": "manual_review", "status": "completed",
            "parent_results": [{"run_id": "original"}], "limits": {"max_revision_depth": 3},
            "feedback_summary": {"pending": 1, "ready": 1, "blocked": 0, "applied": 0}}
    projection = project_feedback_branch(reviewed_recipe(mode="human", repair_inputs=[{"target": "sft"}]),
        reviewed_state(), info, selected)
    assert projection["kind"] == "manual_review" and projection["review_node"] == "review"
    assert projection["generation_nodes"] == []
    assert projection["routes"]["rebuild_waiting"] == {"count": 1, "to": "review"}
    assert projection["routes"]["rejected"]["to"] == "review"
    assert projection["parent_run_ids"] == ["original"]
    assert projection["selected_result"]["parent_run_id"] == "original"
    assert projection["selected_result"]["depth"] == 1
