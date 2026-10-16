"""Protect the editable initial-design supplement and historical contracts.

These are prompt-asset and transport checks, not evidence that a real director
will design a useful multi-turn interaction on every attempt.
"""
from hashlib import sha256

import pytest

from lib.domain.workflow_qa_director import validate_dialogue_design
from lib.prompts import get, render
from lib.prompts.workflow_utterance import (
    MULTITURN_TASK_DESIGN_GUIDANCE,
    QA_DIRECTOR_V1_10,
    QA_DIRECTOR_V1_11,
)


def test_prior_director_body_and_its_source_receipt_remain_byte_identical():
    expected_body = "4b895e3365fadea728e3db9c26b7566a589774ec273803dd35513e520746d140"
    expected_source = "41e49fa43e9874b3a734938567ac5706eaa92ce5f09de662a3c115c696b96433"
    previous = get("workflow.qa_director", "1.10.0")
    assert sha256(QA_DIRECTOR_V1_10.encode("utf-8")).hexdigest() == expected_body
    assert sha256(render(previous).encode("utf-8")).hexdigest() == expected_body
    assert sha256(previous.source.encode("utf-8")).hexdigest() == expected_source


def test_new_body_adds_only_one_editable_supplement_without_rewriting_existing_schema():
    assert QA_DIRECTOR_V1_11.count(MULTITURN_TASK_DESIGN_GUIDANCE) == 1
    assert QA_DIRECTOR_V1_11.replace(MULTITURN_TASK_DESIGN_GUIDANCE + "\n", "", 1) == QA_DIRECTOR_V1_10
    assert MULTITURN_TASK_DESIGN_GUIDANCE not in QA_DIRECTOR_V1_10
    assert "复用已有dialogue_design字段，不新增输出字段" in MULTITURN_TASK_DESIGN_GUIDANCE


def test_initial_scope_is_actual_multiturn_target_not_source_words_or_an_extra_turn_command():
    instruction = MULTITURN_TASK_DESIGN_GUIDANCE
    assert "只用于初始批次设计，即request_phase不是next_turn" in instruction
    assert "本次实际targets数组含multiturn" in instruction
    assert "不要从教师资料、旧样本或用户发言中的同名词推定目标" in instruction
    assert "本补充不改变next_turn的自然结束条件" in instruction
    assert "初始设计不是继续命令或事实证据" in instruction


def test_task_selection_requires_actual_collaboration_without_underanswering_the_opening():
    instruction = MULTITURN_TASK_DESIGN_GUIDANCE
    for requirement in (
        "真正依赖助手先给出的结果", "实际可检查的完成表现", "依据真实回应选择的协作方向",
        "首答必须充分完成当时请求", "不故意少答、隐藏已知条件或留下假缺口",
        "不是共同知识、已发生事实或必须照演的台词", "不预写未来助手答复",
        "不设固定三步或轮次配额", "不把天然单轮任务硬派成多轮",
    ):
        assert requirement in instruction


def test_source_free_questions_fixed_user_records_and_natural_stops_keep_their_boundaries():
    instruction = MULTITURN_TASK_DESIGN_GUIDANCE
    for boundary in (
        "无来源知识的合规question_brief", "不夹带教师事实、答案或尚未发生的用户反馈",
        "保留原题", "不为符合多轮设计改写真实用户记录", "adaptive沿用已有skip_reason契约跳过",
        "目标已满足或没有新价值就停止", "单轮结束仍保留真实记录",
        "不因此获得多轮导出资格",
    ):
        assert boundary in instruction
    historical_stop = "已有一轮也可停止，不为多轮结构制造第二问，单轮不因此获得多轮导出资格；"
    assert historical_stop in QA_DIRECTOR_V1_10 and historical_stop in QA_DIRECTOR_V1_11


def test_existing_design_fields_carry_conditional_collaboration_without_schema_expansion():
    design = {
        "interaction_goal": "形成可用的标注说明；用户须先看到助手草案，才能指出哪些条目与当前流程不符。",
        "success_criteria": ["最终说明符合真实已表达的约束，存在可直接执行的检查办法。"],
        "turn_guidance": ["助手交付当前草案后，依据草案中的实际内容选择有价值的反馈，不预写固定追问。"],
        "stop_when": "已满足实际目标，或用户已没有有价值的新增约束时结束。",
    }
    assert validate_dialogue_design(design) == design
    with pytest.raises(ValueError, match="invalid_dialogue_design"):
        validate_dialogue_design({**design, "forced_followup_script": "硬凑一条新问题。"})
