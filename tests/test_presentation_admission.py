"""Style advice cannot waive factual, task or custom-checklist rejection."""
from copy import deepcopy
import pytest
from lib.contexts.workflows.domain.presentation_admission import style_admission
from lib.contexts.workflows.domain.reasoning_checklist import validate_reasoning_checklist, checklist_digest
from lib.contexts.workflows.application.check_reasoning import CheckReasoning
from lib.contexts.workflows.application.generate_cot import GenerateCot
from lib.domain.creation_draft import validate_creation_draft
from lib.domain.workflow_generation import validate_node_generation, style_for_sample
from test_generation_node_use_cases import MemoryGenerationPort, conversation, REASONING, ANSWER, score

CONFIG = {"version": 6, "style_admission": "advisory", "rules": [
    {"id": "task", "requirement": "保持用户语言、实际任务和操作安全。", "allow_na": False}]}
STYLE = {"keep": False, "adherence": 3, "reason": "关键短语已经互译，但未逐句翻译。"}
BODY = {"reasoning": REASONING, "answer": ANSWER}


@pytest.mark.parametrize("config", [None, {"version": 5}, {"version": 6}, {"version": 6, "style_admission": "strict"}])
def test_historical_and_explicit_strict_style_still_blocks(config):
    assert style_admission(config, STYLE) == {"allowed": False, "advisories": []}


def test_advice_preserves_the_original_negative_grade_and_body(monkeypatch):
    port = MemoryGenerationPort([BODY])
    port.check_style = lambda *args, **kwargs: deepcopy(STYLE)
    monkeypatch.setattr(CheckReasoning, "execute", lambda *args, **kwargs: {"version": 6, "passed": True})
    row, = GenerateCot(port, {"node_generation": {"cot": {"self_check": CONFIG}}}).generate_styled(
        conversation(), {"instruction": "自然分析，解释关键双语概念。"})
    assert row["status"] == "eligible" and row["repair_attempts"] == 0
    assert row["reasoning"] == [REASONING] and row["answer"] == ANSWER
    assert row["style_check"] == STYLE and row["judge"] == score()
    assert row["presentation_advisories"][0]["assessment"] == STYLE
    assert sum(c[1] == "generation" for c in port.calls) == 1
    from lib.contexts.workflows.domain.targets import training_record
    assert training_record("cot", row) == {"question": row["question"], "reasoning": [REASONING], "answer": ANSWER}


@pytest.mark.parametrize("failure", ["content", "mandatory_rule", "evidence_unresolved"])
def test_advice_cannot_override_substantive_or_custom_rule_failure(monkeypatch, failure):
    port = MemoryGenerationPort([BODY, BODY])
    port.check_style = lambda *args, **kwargs: deepcopy(STYLE)
    if failure == "content":
        port.assess = lambda *args, **kwargs: {**score(), "keep": False, "reason": "结论与事实不符。"}
    passed = failure == "content"
    issue = "reasoning_agent_evidence_unresolved" if failure == "evidence_unresolved" else "reasoning_agent_found_gap"
    monkeypatch.setattr(CheckReasoning, "execute", lambda *args, **kwargs: {
        "version": 6, "passed": passed, "issue": issue, "candidate_sha256": checklist_digest(BODY)})
    row, = GenerateCot(port, {"node_generation": {"cot": {"self_check": CONFIG}}}).generate_styled(
        conversation(), {"instruction": "自然分析。"})
    assert row["status"] == "quarantined"
    assert not row.get("presentation_advisories")
    assert row["reasoning"] == [REASONING] and row["answer"] == ANSWER


def test_policy_roundtrips_without_entering_the_authoring_prompt():
    assert validate_reasoning_checklist(CONFIG) == CONFIG
    assert validate_node_generation({"cot": {"self_check": CONFIG}})["cot"]["self_check"] == CONFIG
    assert "advisory" not in str(style_for_sample({"self_check": CONFIG}, "sample"))
    with pytest.raises(ValueError): validate_reasoning_checklist({**CONFIG, "version": 5})
    with pytest.raises(ValueError): validate_reasoning_checklist({**CONFIG, "style_admission": "ignore_facts"})
    values = {"workflow-cot-checklist-version:default": 6, "workflow-cot-style-admission:default": "advisory"}
    assert validate_creation_draft(values) == values


def test_advice_does_not_waive_a_directed_task_contract(monkeypatch):
    port = MemoryGenerationPort([BODY, BODY])
    port.check_style = lambda *args, **kwargs: deepcopy(STYLE)
    port.check_contract = lambda *args, **kwargs: {
        "keep": False, "adherence": 2, "reason": "未解决用户指定的任务。"}
    monkeypatch.setattr(CheckReasoning, "execute", lambda *args, **kwargs: {
        "version": 6, "passed": True, "candidate_sha256": checklist_digest(BODY)})
    sample = {**conversation(), "qa_contract": {"question": "维护时先做什么？"}}
    row, = GenerateCot(port, {
        "node_generation": {"cot": {"self_check": CONFIG}},
        "qa_director": {"question_rules": "自然提问。", "answer_rules": "解决指定任务。"},
    }).generate_styled(sample, {"instruction": "自然分析。"})
    assert row["status"] == "quarantined"
    assert row["feedback"]["contract"] == "未解决用户指定的任务。"
    assert not row.get("presentation_advisories")
