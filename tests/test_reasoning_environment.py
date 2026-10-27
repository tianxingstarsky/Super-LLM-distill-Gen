"""Offline phase, precision, semantic coverage and retry regression. No model client."""
from copy import deepcopy
import json
import pytest

from lib.contexts.workflows.domain.reasoning_workspace import ReasoningWorkspace
from lib.contexts.workflows.domain.reasoning_evidence import ReasoningEvidence
from lib.contexts.workflows.domain.reasoning_checklist import DEFAULT_REASONING_RULES, checklist_from_text
from lib.contexts.workflows.application.check_reasoning import CheckReasoning
from test_reasoning_investigation import AgentPort, turn, RULES, BODY, CONTEXT, claim


def observation(workspace, requirement, status="satisfied"):
    field = next(iter(workspace.candidate))
    return {"requirement_id": requirement["id"], "status": status,
            "evidence": [{"source": field, "quote": workspace.candidate[field]}], "reason": "Check the actual bound statement."}


def check_entry(workspace, rule_id=None):
    rule_id = rule_id or workspace.rules[0]["id"]
    observations = [observation(workspace, r) for r in workspace.requirements if r["active"] and r["rule_id"] == rule_id]
    return {"rule_id": rule_id, "status": "met", "evidence": observations[0]["evidence"],
            "reason": "Bound statement meets this requirement.", "observations": observations}


def claim_entry(workspace, anchor):
    return {"anchor_id": anchor["id"], "status": "supported", "evidence": [{"source": anchor["field"], "quote": anchor["quote"]}],
            "reason": "This is the claim to verify.", "counterexample": "Check the stated scope without adding premises.",
            "calculations": [], "no_calculation_reason": "A stated value; compare its verbal relation separately.",
            "interpretation": {"stance": "asserted", "context_quote": anchor["quote"], "explanation": "Author's assertion."}}


def batch(workspace):
    return {"checks": [check_entry(workspace, r["id"]) for r in workspace.rules],
            "claims": [claim_entry(workspace, a) for a in workspace.anchors]}


def test_phase_selects_requirements_without_author_waiver_and_keeps_body():
    rule = next(r for r in DEFAULT_REASONING_RULES if r["id"] == "scope")
    for phase in ("prefix", "final"):
        w = ReasoningWorkspace([rule], {}, {"reasoning": "A bounded explanation.", **({"answer": "A bounded answer."} if phase == "final" else {})}, phase=phase)
        assert [r["id"] for r in w.requirements if not r["active"]] == ["scope:prefix" if phase == "final" else "scope:answer"]
        original = deepcopy(w.candidate)
        assert w.execute("record_check", check_entry(w))["passed"]
        assert original == w.candidate
        assert w.frozen_rules == [rule]


def test_custom_rule_with_same_id_cannot_be_silently_reinterpreted():
    w = ReasoningWorkspace(RULES, CONTEXT, BODY, phase="final")
    assert len(w.requirements) == 1 and w.requirements[0]["id"] == "scope:body"
    entry = check_entry(w)
    entry["observations"][0]["status"] = "not_applicable"
    assert w.execute("record_check", entry)["issue"] == "checklist_required_rule_waived"


def test_explicit_user_phase_contract_and_old_parser_are_versioned():
    text = "[prefix] 检查拟结论\n[final] 检查最终答案"
    config = checklist_from_text(text, version=5)
    w = ReasoningWorkspace(config["rules"], {}, {"reasoning": "说明"}, phase="prefix")
    assert len(w.rules) == 1 and len(w.requirements) == 2
    assert "phases" not in checklist_from_text(text, version=4)["rules"][0]


def test_required_observations_and_exact_evidence_cannot_be_waived():
    w = ReasoningWorkspace(RULES, CONTEXT, BODY, phase="final")
    entry = check_entry(w)
    entry["observations"][0]["evidence"][0]["quote"] += " invented"
    assert w.execute("record_check", entry)["issue"] == "checklist_evidence_not_in_candidate"
    entry = check_entry(w)
    entry["observations"][0]["requirement_id"] = "scope:prefix"
    assert not w.execute("record_check", entry)["ok"]
    assert not w.snapshot()["passed"]


@pytest.mark.parametrize("status,failed,blocked", [("violated", True, False), ("unknown", False, True)])
def test_positive_summary_never_overrides_substantive_gap(status, failed, blocked):
    w = ReasoningWorkspace(RULES, CONTEXT, BODY, phase="final")
    entry = check_entry(w)
    entry["observations"][0]["status"] = status
    receipt = w.execute("record_check", entry)
    assert receipt["failed"] == failed and receipt["blocked"] == blocked and not receipt["passed"]
    w.execute("record_check", check_entry(w))
    assert not w.snapshot()["passed"]


@pytest.mark.parametrize("sentence,expected", [
    ("覆盖率为50.6%，不到一半。", "violated"), ("覆盖率为50.6%，超过一半。", "verified"),
    ("Coverage is 49.4%, less than half.", "verified"), ("Coverage is 49.4%, more than half.", "violated"),
    ("覆盖率为50%，至少一半。", "verified"), ("覆盖率为50%，超过一半。", "violated"),
])
def test_comparison_checked_even_when_model_claims_supported(sentence, expected):
    w = ReasoningWorkspace([], {}, {"reasoning": sentence}, phase="prefix")
    a = w.anchors[0]
    receipt = w.execute("record_claim", claim_entry(w, a))
    assert receipt["assertion_verification"]["status"] == expected
    assert receipt["passed"] == (expected == "verified")


def test_cross_sentence_comparison_and_source_absence_stay_unresolved_not_wrong():
    for sentence in ("这种特征覆盖不到一半任务。", "论文没有报告筛选阈值。"):
        w = ReasoningWorkspace([], {"source": "Only a selected paragraph."}, {"reasoning": sentence}, phase="prefix")
        assert w.anchors
        receipt = w.execute("record_claim", claim_entry(w, w.anchors[0]))
        assert receipt["blocked"] and not receipt["failed"] and not receipt["passed"]
        assert receipt["category"] == "evidence_unresolved"


def test_quoted_or_negated_comparison_is_not_forced_into_false_arithmetic_error():
    w = ReasoningWorkspace([], {}, {"reasoning": 'It is wrong to say "50.6% is less than half".'}, phase="prefix")
    entry = claim_entry(w, w.anchors[0])
    entry["interpretation"]["stance"] = "negated"
    entry["status"] = "not_claim"
    receipt = w.execute("record_claim", entry)
    assert not receipt["failed"] and receipt["passed"]  # exact rejected quote is not the author's assertion


@pytest.mark.parametrize("sentence", ["收入增长50.6%，但员工覆盖不到一半。", "工资是上年的50.6%，不到一半员工涨薪。", "覆盖率约50%，超过一半。"])
def test_unrelated_percent_cannot_be_used_as_comparison_operand(sentence):
    w = ReasoningWorkspace([], {}, {"reasoning": sentence}, phase="prefix")
    receipt = w.execute("record_claim", claim_entry(w, w.anchors[0]))
    assert receipt["blocked"] and not receipt["failed"]


def test_alias_recovery_cannot_change_evidence_or_erase_conflicting_keys():
    w = ReasoningWorkspace(RULES, CONTEXT, BODY, phase="final")
    entry = check_entry(w)
    entry["evidence"] = [{"field": "reasoning", "quote": BODY["reasoning"]}]
    result = w.execute("record_check", entry)
    assert result["ok"] and any(r["operation"] == "field_to_source" for r in result["input_repairs"])
    assert w.checks["scope"]["evidence"] == entry["evidence"]
    entry["evidence"][0]["source"] = "answer"
    assert w.execute("record_check", entry)["issue"] == "invalid_tool_arguments"


def test_same_receipt_reused_and_same_failure_stops_after_feedback():
    w = ReasoningWorkspace(RULES, CONTEXT, BODY, phase="final")
    args = {"source_id": "source.text", "start": 0, "length": 20}
    assert w.execute("read_source", args)["ok"]
    assert w.execute("read_source", args)["reused_local_receipt"]
    assert w.operation_metrics == {"executed": 1, "reused": 1}
    port = AgentPort([turn(("read_source", args)) for _ in range(2)])
    audit = CheckReasoning(port, {"version": 5, "rules": RULES}).execute(["sample"], CONTEXT, BODY, phase="final")
    assert len(port.calls) == 2 and audit["issue"] == "reasoning_agent_no_progress"
    first = audit["self_check_session"]["trace"][0]["observation"]
    assert first["already_delivered"] and first["environment"]["permissions"]["choose_tool_order"]
    assert audit["self_check_session"]["operation_metrics"]["executed"] == 1


def test_full_review_allows_different_tool_order_and_blind_independent_history():
    w = ReasoningWorkspace(RULES, CONTEXT, BODY, phase="final")
    full = batch(w)
    port = AgentPort([turn(("submit_review", full))], [turn(("record_claim", c)) for c in full["claims"]] + [turn(("record_check", full["checks"][0]))])
    audit = CheckReasoning(port, {"version": 5, "rules": RULES}).execute(["sample"], CONTEXT, BODY, phase="final")
    assert audit["passed"]
    assert "investigation_v3" in port.calls[0][0]
    reviewer = next(call for call in port.calls if call[1] == "jev")
    assert len(reviewer[2]) == 1
    assert json.loads(reviewer[2][0]["content"])["candidate"] == BODY


def test_duplicate_wording_does_not_reopen_committed_check():
    w = ReasoningWorkspace(RULES, CONTEXT, BODY, phase="final")
    entry = check_entry(w)
    w.execute("record_check", entry)
    entry["reason"] += " Different wording."
    assert w.execute("record_check", entry)["reused_committed_evidence"]


def test_finish_with_missing_work_is_unresolved_not_an_invalid_tool_retry():
    port = AgentPort([turn(("finish_review", {}))])
    audit = CheckReasoning(port, {"version": 5, "rules": RULES}).execute(["sample"], CONTEXT, BODY, phase="final")
    assert len(port.calls) == 1 and not audit["passed"]
    assert audit["issue"] == "reasoning_agent_evidence_unresolved"
    receipt = audit["self_check_session"]["trace"][0]["observation"]
    assert receipt["ok"] and receipt["category"] == "evidence_unresolved" and receipt["pending"]["rule_ids"]


def test_prompt_and_resume_version_isolation():
    from lib.domain.workflow_node_prompts import snapshot_node_prompts
    from lib.prompts import render, get
    from lib.infrastructure.training_workflow import SUPPORTED_RECIPE_VERSIONS
    assert {26, 27}.issubset(SUPPORTED_RECIPE_VERSIONS)
    assert snapshot_node_prompts({}, recipe_version=26)["cot"]["workflow.cot_check_agent"] == render(get("workflow.cot_check_agent", version="1.2.0"))
    assert snapshot_node_prompts({}, recipe_version=27)["cot"]["workflow.cot_check_agent"] == render(get("workflow.cot_check_agent", version="1.3.0"))


def test_unchanged_draft_reuses_review_identity_but_changed_body_cannot():
    from lib.contexts.workflows.application.generate_cot import GenerateCot
    usecase = GenerateCot(None, {"node_generation": {"cot": {"self_check": {"version": 5, "rules": RULES}}}})
    key = usecase._review_key({"id": "sample"}, "check", 0, CONTEXT, BODY)
    assert key == usecase._review_key({"id": "sample"}, "check", 1, CONTEXT, BODY)
    assert key != usecase._review_key({"id": "sample"}, "check", 1, CONTEXT, {**BODY, "answer": "Changed"})


def test_actual_record_replay_retains_all_text_and_does_not_waive_false_comparison():
    from pathlib import Path
    from scripts.replay_reasoning_environment import replay
    fixture = json.loads((Path(__file__).parent / "fixtures/reasoning_agent/environment_sessions.json").read_text(encoding="utf8"))
    report = replay(fixture)
    assert report["totals"]["candidate_bytes_unchanged"] and report["totals"]["api_calls"] == 0
    prior_false_pass = next(s for s in report["sessions"] if s["case_id"] == "01" and s["old"]["passed"])
    assert prior_false_pass["new"]["blocked"] and not prior_false_pass["new"]["passed"]
    # Lack of a bound antecedent is not enough to declare the text wrong.
    assert not prior_false_pass["new"]["failed"]
    for session in report["sessions"]:
        if session["case_id"] == "02" and session["old"]["passed"]:
            assert session["new"]["passed"]
    assert report["totals"]["old_errors"]["checklist_required_rule_waived"] == 1
    assert report["totals"]["new_errors"]["checklist_required_rule_waived"] == 0


def test_unresolved_v5_does_not_buy_a_repair_in_review_domain():
    from lib.contexts.review.application.review_candidate import ReviewCandidate
    from test_pipeline_use_cases import ReviewMemory, grade
    class Port(ReviewMemory):
        def score(self, *args):
            return grade(True)
        def check_reasoning(self, *args):
            return {"version": 5, "passed": False, "issue": "reasoning_agent_evidence_unresolved"}
        def repair(self, *args, **kwargs):
            raise AssertionError("Unresolved evidence must not buy a rewrite")
    row = {"id": "sample", "status": "eligible", "question": CONTEXT["prompt"], "reasoning": [BODY["reasoning"]], "answer": BODY["answer"]}
    result, = ReviewCandidate(Port(), {"review_repair": {"mode": "auto", "max_rounds": 2},
        "node_generation": {"cot": {"self_check": {"version": 5, "rules": RULES}}}}).execute({"target": "cot", "record": row})
    assert result["reason"] == "reasoning_agent_incomplete"
    assert result["review_repair"]["attempts"] == 0
    assert result["reasoning"] == row["reasoning"] and result["answer"] == row["answer"]


def test_copying_a_recipe_keeps_user_phase_constraints():
    from test_workflow_reuse import recipe
    from lib.presentation.streamlit.workflow_reuse import recipe_to_draft
    config = checklist_from_text("[prefix] 检查拟结论\n? [final] 检查适用条件", version=5)
    original = recipe(node_generation={"cot": {"enabled": True, "self_check": config}})
    frozen = deepcopy(original)
    values = recipe_to_draft(original, "Original", "default", [])["values"]
    restored = checklist_from_text(values["workflow-cot-checklist-text:default"], version=values["workflow-cot-checklist-version:default"])
    assert restored == config and original == frozen


def test_deterministic_defect_reaches_repair_with_original_claim_and_receipt():
    from lib.contexts.workflows.domain.reasoning_investigation import investigation_feedback
    w = ReasoningWorkspace([], {}, {"reasoning": "覆盖率为50.6%，不到一半。"}, phase="prefix")
    original = claim_entry(w, w.anchors[0])
    w.execute("record_claim", original)
    state = w.snapshot()
    audit = {"version": 5, "issue": "reasoning_agent_found_gap", "candidate_sha256": "unchanged",
             "self_check": state["assessment"], "self_check_verification": state["verification"], "self_check_session": state}
    feedback = investigation_feedback({"reasoning_checklist": audit})["reasoning_checklist"]
    assert feedback["failed_claims"][0]["evidence"] == original["evidence"]
    assert feedback["assertion_receipts"][0]["issue"] == "comparison_value_conflict"
    assert feedback["assertion_receipts"][0]["reported_percent"] == "50.6"
