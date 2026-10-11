"""Authored rationale contracts accept useful short explanations and reject empty audits."""
from copy import deepcopy
import json

import pytest

from lib.domain.authored_reasoning import (
    AUTHORED_REASONING_JSON_CONTRACT, AUTHORED_REASONING_PROTOCOL_INSTRUCTION,
    AUTHORED_REASONING_VERSION, authored_reasoning_issue,
)
from lib.prompts.workflow_reasoning import COT_GENERATE_BODY, RATIONALE_CHECK_BODY, REASONING_RESEARCH_SOURCE


@pytest.fixture(scope="session", autouse=True)
def mock_llm_server():
    # These domain checks never require a model or a provider fixture.
    yield


def simple_candidate():
    return {
        "reasoning": "2+2=4。",
        "answer": "4",
        "reasoning_trace": {
            "task": "求2+2。",
            "premises": ["两个加数都是2。"],
            "steps": [{"basis": "题面给出2+2。", "inference": "两个相同加数相加等于2乘以这个加数。",
                       "result": "2×2=4。"}],
            "checks": [{"check": "用4−2代回检验第二个加数。", "result": "4−2=2，与题面第二个加数相同。"}],
            "limitations": [],
        },
    }


def derived_candidate():
    return {
        "reasoning": "先移去等式右侧的3：2x=10。再将两侧除以2：x=5。代回得到2×5+3=13，满足原式。",
        "answer": "x=5。",
        "reasoning_trace": {
            "task": "求解2x+3=13。",
            "premises": ["2x+3=13。"],
            "steps": [
                {"basis": "2x+3=13。", "inference": "等式两侧同时减3保持相等。", "result": "2x=10。"},
                {"basis": "2x=10且2不为0。", "inference": "等式两侧同时除以非零系数2。", "result": "x=5。"},
            ],
            "checks": [{"check": "将x=5代入原方程。", "result": "左侧2×5+3=13，与右侧13相等。"}],
            "limitations": ["方程中的x按实数解释。"],
        },
    }


def clarification_candidate():
    return {
        "reasoning": "当前消息只有‘请把它改好’，没有给出‘它’的指代或待改内容。先索取原文和目标，才能针对实际作品修改。",
        "answer": "请发来要修改的内容，并说明希望改进哪一方面。",
        "reasoning_trace": {
            "task": "回应‘请把它改好’并明确改写对象。",
            "premises": ["当前消息未说明‘它’指什么。", "当前对话没有待修改的文本。"],
            "steps": [{"basis": "改写对象和目标尚未给出。", "inference": "修改必须针对具体内容，未明确对象时选择内容会引入无依据假设。",
                       "result": "先请求待改内容和修改目标。"}],
            "checks": [{"check": "核对当前消息是否已经包含待改文本。", "result": "可见消息只有‘请把它改好’，未附带原文。"}],
            "limitations": ["只依据当前提供的对话，不推断未提供的先前上下文。"],
        },
    }


def insufficient_candidate():
    return {
        "reasoning": "已知距离10千米，但速度=距离÷时间，还缺少所用时间，因此不能得到唯一数值。",
        "answer": "无法确定速度；请提供走完10千米所用的时间。",
        "reasoning_trace": {
            "task": "由路程10千米求平均速度。",
            "premises": ["路程为10千米。", "题面未给出所用时间。"],
            "steps": [{"basis": "平均速度由路程和所用时间共同决定，当前只给路程。",
                       "inference": "同一路程若用时不同，路程除以时间的结果不同，无法选择唯一速度。",
                       "result": "需要补充时间后才能计算平均速度。"}],
            "checks": [{"check": "比较假设用时1小时与2小时的结果以核对唯一性，不把假设当事实。",
                        "result": "分别为10千米/小时和5千米/小时，说明现有条件不能唯一决定数值。"}],
            "limitations": ["两个用时仅用于展示欠定性，并非实际记录；题面尚未提供时间。"],
        },
    }


@pytest.mark.parametrize("factory", [simple_candidate, derived_candidate, clarification_candidate, insufficient_candidate])
def test_valid_simple_derivation_dialogue_and_insufficient_explanations(factory):
    candidate = factory()
    original = deepcopy(candidate)
    assert authored_reasoning_issue(candidate) is None
    assert candidate == original


def test_short_correct_answer_and_single_step_do_not_need_padding():
    candidate = simple_candidate()
    assert len(candidate["reasoning"]) < 10 and len(candidate["reasoning_trace"]["steps"]) == 1
    assert authored_reasoning_issue(candidate) is None
    candidate["answer"] = "正确"
    # A one-word final answer is legal; its semantic match is the judge's job.
    assert authored_reasoning_issue(candidate) is None


@pytest.mark.parametrize("field", ["reasoning", "answer", "reasoning_trace"])
def test_missing_candidate_output_is_rejected(field):
    candidate = simple_candidate()
    del candidate[field]
    assert authored_reasoning_issue(candidate) is not None


@pytest.mark.parametrize("field", ["task", "premises", "steps", "checks", "limitations"])
def test_every_audit_field_is_required(field):
    candidate = simple_candidate()
    del candidate["reasoning_trace"][field]
    assert authored_reasoning_issue(candidate) == "authored_reasoning_invalid_trace_fields"


@pytest.mark.parametrize("field,value", [("task", " "), ("premises", []), ("premises", [False]),
                                       ("steps", []), ("checks", []), ("limitations", "无")])
def test_invalid_empty_or_wrong_type_audit_fields_are_rejected(field, value):
    candidate = simple_candidate()
    candidate["reasoning_trace"][field] = value
    assert authored_reasoning_issue(candidate) is not None


@pytest.mark.parametrize("field,value", [("basis", "已知条件"), ("inference", "分析"),
                                       ("inference", "think step by step"), ("result", "得出结论")])
def test_empty_shell_steps_do_not_count_as_bridges(field, value):
    candidate = simple_candidate()
    candidate["reasoning_trace"]["steps"][0][field] = value
    assert authored_reasoning_issue(candidate) == "authored_reasoning_vacuous_step"


def test_repeated_steps_and_circular_support_are_rejected():
    candidate = simple_candidate()
    step = candidate["reasoning_trace"]["steps"][0]
    candidate["reasoning_trace"]["steps"].append(deepcopy(step))
    assert authored_reasoning_issue(candidate) == "authored_reasoning_repeated_step"
    candidate["reasoning_trace"]["steps"].pop()
    step["basis"] = step["result"]
    assert authored_reasoning_issue(candidate) == "authored_reasoning_circular_support"


@pytest.mark.parametrize("check", [
    {"check": "检查答案正确性", "result": "检查通过"},
    {"check": "用Python计算2+2", "result": "已经验证"},
    {"check": "Verify the answer", "result": "All checks passed"},
    {"check": "代回原方程。", "result": "代回原方程。"},
])
def test_blanket_or_false_verification_claims_are_rejected(check):
    candidate = simple_candidate()
    candidate["reasoning_trace"]["checks"] = [check]
    assert authored_reasoning_issue(candidate) == "authored_reasoning_vacuous_check"


def test_insufficient_answer_preserves_its_necessary_boundary():
    candidate = insufficient_candidate()
    candidate["reasoning_trace"]["limitations"] = []
    assert authored_reasoning_issue(candidate) == "authored_reasoning_missing_limitation"


def test_echoed_schema_descriptions_are_not_an_authored_explanation():
    assert authored_reasoning_issue(deepcopy(AUTHORED_REASONING_JSON_CONTRACT)) == "authored_reasoning_invalid_text"


def test_structural_protocol_does_not_claim_to_verify_semantic_truth():
    candidate = simple_candidate()
    candidate["answer"] = "5"
    # A detailed but wrong trace requires independent semantic review. The
    # local contract does not impersonate a theorem prover or an execution log.
    assert authored_reasoning_issue(candidate) is None


def test_protocol_is_appendable_to_custom_prompts_and_research_limits_are_explicit():
    assert AUTHORED_REASONING_VERSION == 1
    contract = json.dumps(AUTHORED_REASONING_JSON_CONTRACT, ensure_ascii=False, separators=(",", ":"))
    assert contract in AUTHORED_REASONING_PROTOCOL_INSTRUCTION
    assert AUTHORED_REASONING_PROTOCOL_INSTRUCTION in COT_GENERATE_BODY
    assert "隐藏推理" in AUTHORED_REASONING_PROTOCOL_INSTRUCTION
    assert "简单任务可仅一步" in AUTHORED_REASONING_PROTOCOL_INSTRUCTION
    assert "https://aclanthology.org/N18-1175/" in REASONING_RESEARCH_SOURCE
    assert "https://arxiv.org/abs/2305.04091" in REASONING_RESEARCH_SOURCE
    for failure in ("遗漏关键前提", "重复结论当前提", "跨步", "虚构", "真实记录", "正文"):
        assert failure in RATIONALE_CHECK_BODY
