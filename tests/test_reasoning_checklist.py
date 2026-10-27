"""Checklist claims are evidence-bound, independently judged and recoverable."""
from copy import deepcopy
import pytest

from lib.contexts.workflows.domain.reasoning_checklist import (
    checklist_from_text, checklist_evidence_issue, checklist_digest,
    default_checklist_text, validate_reasoning_checklist,
)
from lib.contexts.workflows.application.check_reasoning import CheckReasoning
from lib.contexts.workflows.application.generate_cot import GenerateCot
from lib.domain.workflow_generation import validate_node_generation, style_for_sample
from lib.domain.workflow_node_prompts import snapshot_node_prompts, validate_node_prompt_snapshot
from lib.domain.prompt_library import validate_prompt_payload
from test_generation_node_use_cases import MemoryGenerationPort, conversation, REASONING, ANSWER


CONFIG = {"version": 1, "rules": [{"id": "check", "requirement": "核对维护顺序。", "allow_na": False}]}
CANDIDATE = {"reasoning": REASONING, "answer": ANSWER}


def test_semantic_repair_does_not_attach_previous_draft_grades(monkeypatch):
    """A newly rejected draft must not inherit its predecessor's good score."""
    revised = {"reasoning": "重新检查后仍错误地建议带电操作。", "answer": "直接带电检查。"}
    port = MemoryGenerationPort([CANDIDATE, revised])
    port.check_style = lambda *args, **kwargs: {
        "keep": False, "adherence": 3, "reason": "缺少用户要求的关键术语释义。"}
    audits = iter([
        {"version": 6, "passed": True, "issue": None},
        {"version": 6, "passed": False, "issue": "reasoning_agent_found_gap"},
    ])
    monkeypatch.setattr(CheckReasoning, "execute", lambda *args, **kwargs: next(audits))
    recipe = {"node_generation": {"cot": {"self_check": {**CONFIG, "version": 6}}}}
    row = GenerateCot(port, recipe).generate_styled(conversation(), {"instruction": "自然推导。"})[0]
    assert row["status"] == "quarantined"
    assert row["reasoning"] == [revised["reasoning"]] and row["answer"] == revised["answer"]
    assert row["judge"] is None and row["style_check"] is None
    assert not row["reasoning_checklist"]["passed"]
    assert sum(call[1] == "assessment" for call in port.calls) == 1


def assessment(status="met", quote=REASONING):
    return {"checks": [{"rule_id": "check", "status": status,
        "evidence": [{"field": "reasoning", "quote": quote}] if quote else [],
        "reason": "比较断电与开盖的顺序，先断电才能开始维护。"}]}


@pytest.fixture(scope="session", autouse=True)
def mock_llm_server():
    yield


@pytest.mark.parametrize("mutate,code", [
    (lambda a: a["checks"].clear(), "checklist_rule_coverage_missing"),
    (lambda a: a["checks"].append(deepcopy(a["checks"][0])), "checklist_invalid_rule"),
    (lambda a: a["checks"][0]["evidence"].clear(), "checklist_missing_evidence"),
    (lambda a: a["checks"][0]["evidence"][0].update(quote="虚构的已执行检查。"), "checklist_evidence_not_in_candidate"),
    (lambda a: a["checks"][0].update(status="not_applicable"), "checklist_required_rule_waived"),
])
def test_incomplete_or_unbound_claims_are_not_accepted(mutate, code):
    value = assessment(); mutate(value)
    assert checklist_evidence_issue(CONFIG["rules"], CANDIDATE, value) == code


def test_not_applicable_requires_permission_and_reason():
    rules = deepcopy(CONFIG["rules"]); rules[0]["allow_na"] = True
    value = assessment("not_applicable", "")
    value["checks"][0]["reason"] = "本题只询问术语释义，不含设备维护操作。"
    assert checklist_evidence_issue(rules, CANDIDATE, value) is None
    value["checks"][0]["reason"] = ""
    assert checklist_evidence_issue(rules, CANDIDATE, value) == "checklist_invalid_rule"


def test_literal_evidence_is_not_enough_if_independent_review_finds_gap():
    port = MemoryGenerationPort([assessment(), assessment("unmet")])
    audit = CheckReasoning(port, CONFIG).execute(["sample", 0], {"task": "实际用户任务"}, CANDIDATE, phase="final")
    assert not audit["passed"] and audit["issue"] == "checklist_reviewer_found_gap"
    assert port.calls[0][1] == "generation" and port.calls[1][1] == "jev"
    assert port.calls[0][3] == port.calls[1][3]  # No author self-score to bias the reviewer.
    assert audit["candidate_sha256"] == checklist_digest(CANDIDATE)
    assert audit["self_check"] == assessment()


def test_author_found_gap_stops_without_spending_on_another_reviewer():
    port = MemoryGenerationPort([assessment("unmet")])
    audit = CheckReasoning(port, CONFIG).execute(["sample", 0], {}, CANDIDATE, phase="final")
    assert not audit["passed"] and len(port.calls) == 1


def test_body_and_rule_changes_receive_new_checkpoint_identities():
    port = MemoryGenerationPort([assessment(), assessment(), assessment(), assessment()])
    checker = CheckReasoning(port, CONFIG)
    checker.execute(["sample", 0], {}, CANDIDATE, phase="final")
    changed = {**CANDIDATE, "answer": ANSWER + "遵守此顺序。"}
    checker.execute(["sample", 0], {}, changed, phase="final")
    assert port.calls[0][0] != port.calls[2][0]
    assert "reasoning" in port.calls[2][3]["candidate"]


def test_joint_generation_repairs_actual_body_and_retains_audit_outside_training_text():
    port = MemoryGenerationPort([CANDIDATE, assessment("unmet"), CANDIDATE, assessment(), assessment()])
    recipe = {"node_generation": {"cot": {"self_check": CONFIG}}}
    row = GenerateCot(port, recipe).generate_styled(conversation(), {"instruction": "自然推导。"})[0]
    assert row["status"] == "eligible" and row["repair_attempts"] == 1
    assert row["reasoning"] == [REASONING] and row["answer"] == ANSWER
    assert row["reasoning_checklist"]["passed"]
    generations = [c for c in port.calls if len(c) == 5 and c[2] == "workflow.cot_generate"]
    assert generations[1][3]["feedback"]["reasoning_checklist"]["issue"] == "checklist_author_found_gap"
    assert "核对维护顺序" in generations[0][4]["instruction"]


def test_checklist_failure_never_dispatches_a_native_answer():
    from test_workflow_styled_generation import maintenance_reasoning_trace
    rationale = {"reasoning": REASONING, "reasoning_trace": maintenance_reasoning_trace()}
    port = MemoryGenerationPort([rationale, assessment("unmet")])
    port.request_native_answer = lambda *a, **k: pytest.fail("unverified prefix dispatched")
    value, audit = GenerateCot(port, {"node_generation": {"cot": {"self_check": CONFIG}}}).generate_native_answer(
        conversation(), {"prompt": conversation()["messages"][:-1], "source": {}}, {"instruction": "自然说明"}, None, 0)
    assert value is None and not audit["reasoning_checklist"]["passed"]


def test_rules_are_editable_and_templates_and_old_recipes_remain_literal():
    text = default_checklist_text() + "\n? 检查存在的程序路径。"
    assert len(checklist_from_text(text)["rules"]) == 8
    assert validate_prompt_payload("reasoning.checklist", {"text": text}) == {"text": text}
    with pytest.raises(ValueError): checklist_from_text("重复\n重复")
    with pytest.raises(ValueError): validate_reasoning_checklist({"version": True, "rules": CONFIG["rules"]})
    config = {"cot": {"enabled": True, "self_check": CONFIG}}
    assert validate_node_generation(config)["cot"]["self_check"] == CONFIG
    assert "self_check" not in style_for_sample(config["cot"], "sample")
    assert "self_check" not in validate_node_generation({"cot": {}})["cot"]
    for version in (21, 22, 23):
        snapshot = snapshot_node_prompts(None, recipe_version=version)
        assert ("workflow.cot_self_check" in snapshot["cot"]) == (version == 23)
    custom = "My literal evidence audit {unchanged}"
    snapshot = snapshot_node_prompts({"cot": {"workflow.cot_self_check": custom}}, recipe_version=23)
    assert snapshot["cot"]["workflow.cot_self_check"] == custom


def test_report_keeps_audit_separate_and_marks_it_stale_after_edit():
    from lib.domain.workflow_report import report_sample
    port = MemoryGenerationPort([assessment(), assessment()])
    audit = CheckReasoning(port, CONFIG).execute(["sample", 0], {}, CANDIDATE, phase="final")
    row = {**CANDIDATE, "reasoning_checklist": audit}
    report = report_sample("cot", row, CANDIDATE, released=False, position=0)
    assert report["reviews"]["verification"][0]["bound_to_current_body"] is True
    row["answer"] += "另一个结论。"
    report = report_sample("cot", row, CANDIDATE, released=False, position=0)
    assert report["reviews"]["verification"][0]["bound_to_current_body"] is False


@pytest.mark.parametrize("stale", [False, True])
def test_automatic_review_rechecks_changed_body_without_changing_raw_scores(stale):
    from lib.contexts.review.application.review_candidate import ReviewCandidate
    from test_pipeline_use_cases import ReviewMemory, grade
    checker = CheckReasoning(MemoryGenerationPort([assessment(), assessment()]), CONFIG)
    saved = checker.execute(["sample", 0], {}, CANDIDATE, phase="final")
    row = {"id": "sample", "status": "eligible", "question": [{"role": "user", "content": "维护时先做什么？"}],
           "reasoning": [REASONING], "answer": ANSWER, "reasoning_checklist": saved}
    if stale:
        row["answer"] += "然后执行其它操作。"
    class Port(ReviewMemory):
        checks = 0
        def payload(self, target, candidate):
            return {k: candidate[k] for k in ("question", "reasoning", "answer")}
        def score(self, *args):
            return grade(True)
        def independent_cot_assessment(self, *args, **kwargs):
            return {}
        def check_reasoning(self, *args):
            self.checks += 1
            return {"passed": False, "issue": "checklist_reviewer_found_gap"}
    port = Port()
    result, = ReviewCandidate(port, {"review_repair": {"mode": "auto", "max_rounds": 0},
        "node_generation": {"cot": {"self_check": CONFIG}}, "reasoning_distillation_audit_version": 1}).execute(
            {"target": "cot", "record": row})
    assert port.checks == int(stale)
    assert (result["status"] == "eligible") is not stale
    if stale:
        verdict = result["review_repair"]["verdict"]
        assert verdict["quality_verdict"] == grade(True)
        assert verdict["scores"] == grade(True)["scores"]
        assert verdict["keep"] is False
