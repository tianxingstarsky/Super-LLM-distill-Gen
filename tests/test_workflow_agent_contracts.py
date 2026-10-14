"""Stage boundaries are versioned and match the executable QA schemas."""
from hashlib import sha256

import pytest

from lib.prompts import get, render
from lib.prompts.workflow_agent_contracts import (
    SYSTEM_V2, QA_DIRECTOR_V1_8, SFT_V1_6, STYLE_CHECK_V1_3,
    _BATCH_TASK_SCHEMA, _NEXT_TURN_SCHEMA,
)
from lib.domain.workflow_qa_director import (
    validate_director_task, validate_director_skip, validate_dialogue_step,
)


@pytest.fixture(scope="session", autouse=True)
def mock_llm_server():
    yield


@pytest.mark.parametrize("prompt_id,version,body", [
    ("workflow.system", "2.0.0", SYSTEM_V2),
    ("workflow.qa_director", "1.8.0", QA_DIRECTOR_V1_8),
    ("workflow.sft", "1.6.0", SFT_V1_6),
    ("workflow.style_check", "1.3.0", STYLE_CHECK_V1_3),
])
def test_registered_stage_is_exact_complete_body_not_an_inherited_conflicting_tail(prompt_id, version, body):
    current = get(prompt_id)
    assert current.version == version
    assert render(current) == body


@pytest.mark.parametrize("prompt_id,version,expected_sha", [
    ("workflow.system", "1.0.0", "490a33085b54ce66313ee23ab1d547adfc79488e3bb88c9ad9a6313c56b50144"),
    ("workflow.qa_director", "1.7.0", "cec4bd8b2e119c62f60bd35c64761da80cee004f9f971a8c08e9be62da161f47"),
    ("workflow.sft", "1.5.0", "7b546275b6cac4801c36e1e9b5fbc8a06557ef763b05bb7d2b86356c657e3d3b"),
    ("workflow.style_check", "1.2.0", "b3cbb8612ea130fbc1c72f55f1f3478d0c3ee231f555ac05827dd8b8053b4173"),
])
def test_prior_stage_template_is_still_addressable_and_byte_identical(prompt_id, version, expected_sha):
    assert sha256(get(prompt_id, version=version).template.encode("utf-8")).hexdigest() == expected_sha


def test_qa_displayed_required_fields_match_domain_contract_for_short_ambiguous_task():
    required = {"id", "qa_type", "question", "visible_context", "answer_policy", "guidance", "evidence_quotes"}
    assert set(_BATCH_TASK_SCHEMA) == {"tasks"}
    assert set(_BATCH_TASK_SCHEMA["tasks"][0]) == required
    task = {"id": "original-id", "qa_type": "closed_book", "question": "又不行了",
            "visible_context": "", "answer_policy": "clarify", "guidance": "先确认失败对象和现象。",
            "evidence_quotes": [], "expression_design": {"form": "真实模糊求助", "clarity": "对象未定"},
            "dialogue_design": {"interaction_goal": "共同定位问题", "stop_when": "目标解决或无新信息"}}
    checked = validate_director_task(task, expected_type=None, expected_id="original-id")
    assert checked["question"] == "又不行了" and checked["answer_policy"] == "clarify"
    assert checked["expression_design"] == task["expression_design"]
    assert "question_brief、expression_design" in QA_DIRECTOR_V1_8


def test_qa_continuation_contract_keeps_separated_closed_book_placeholder_and_brief():
    assert set(_NEXT_TURN_SCHEMA) == {"continue", "user_message", "reason", "dialogue_state"}
    step = {"continue": True, "user_message": "内部占位设计", "reason": "真实上一轮澄清后可继续补充。",
            "dialogue_state": {"user_intent": "说明错误表现", "shared_understanding": ["尚未确定对象"]},
            "question_brief": {"topic": "软件使用", "communication_goal": "回应对方的澄清问题",
                               "expression_design": {"form": "补充信息"}}}
    checked = validate_dialogue_step(step, completed_turns=1)
    assert checked["question_brief"]["communication_goal"] == step["question_brief"]["communication_goal"]
    assert checked["user_message"] == "内部占位设计"
    stop = {"continue": False, "user_message": "", "reason": "目标已完成。", "dialogue_state": {}}
    assert validate_dialogue_step(stop, completed_turns=2)["continue"] is False
    with pytest.raises(ValueError, match="stopped_before_multiturn"):
        validate_dialogue_step(stop, completed_turns=1)


def test_qa_stop_and_line_contracts_remain_explicit():
    assert validate_director_skip({"id": "original-id", "skip_reason": "source_exhausted"}, "original-id") == {
        "id": "original-id", "skip_reason": "source_exhausted"}
    for required in ("source_line_protocol_version=1", "material_id", "start_line", "end_line",
                     "首尾均包含", "source_exhausted / no_new_grounded_scenario", "plan_repair.validation_code"):
        assert required in QA_DIRECTOR_V1_8
    assert "multi_source至少组合两段不同的逐字证据，同一文档的不同片段也可" in QA_DIRECTOR_V1_8


def test_sft_fixed_input_and_new_task_have_separate_scopes_without_new_thought_fields():
    assert "保持其内容、角色、可见文章与工具事实" in SFT_V1_6
    assert "固定用户输入不增加或修改visible_context" in SFT_V1_6
    assert "question原样返回实际最后一条用户文本" in SFT_V1_6
    assert "不额外撰写reasoning或reasoning_trace" in SFT_V1_6
    assert "长度" in SFT_V1_6 and "不能用隐藏资料猜定用户背景" in SFT_V1_6


def test_style_does_not_repeat_fact_admission_and_system_supports_native_plain_text():
    assert "本次不能把这些判断混入风格分" in STYLE_CHECK_V1_3
    assert "关键依赖失效时keep=false" not in STYLE_CHECK_V1_3
    assert "长期经验是可编辑的过程参考" in SYSTEM_V2
    assert "阶段明确要求纯文本最终回复时仅交付该回复" in SYSTEM_V2
    assert "不额外添加JSON包装" in SYSTEM_V2
