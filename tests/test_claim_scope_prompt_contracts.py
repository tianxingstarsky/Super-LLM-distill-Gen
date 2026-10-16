"""New editable claim-scope rubrics must not rewrite frozen warmup versions.

These checks establish the delivered review contract, not the semantic
correctness of an LLM's later assessment of a generated example.
"""
import hashlib

import pytest

from lib.prompts import get, render
from lib.prompts.workflow import spec
from lib.prompts.workflow_reasoning import (
    CLAIM_SCOPE_GENERATION, CLAIM_SCOPE_REVIEW, CLAIM_SCOPE_REPAIR,
    claim_scope_body, warmup_generation_instruction, warmup_review_instruction,
    warmup_repair_instruction,
)


@pytest.fixture(scope="session", autouse=True)
def mock_llm_server():
    yield


@pytest.mark.parametrize("helper,sha256", [
    (warmup_generation_instruction, "01e70bf1058312044d9431b0b01c727d9db1b28350fc769e9c5f0ae1021f75c9"),
    (warmup_review_instruction, "a0581860607724eb76e3c6389117a51171395b786a2a4b8724d7e2e9a82ea514"),
    (warmup_repair_instruction, "3b2895b24f72d4ae617d38c8668f6a9eb217344d84f7e1c3743dad997d2d6646"),
])
def test_warmup_v1_literal_is_frozen_independently_of_the_new_scope_rubric(helper, sha256):
    assert hashlib.sha256(helper(1).encode()).hexdigest() == sha256
    assert "结论适用范围审查" not in helper(1)


@pytest.mark.parametrize("prompt_id,sha256", [
    ("workflow.review_score", "32fbb88a7e9634c8ca2eacc0f4d100f5a3af2cc8a6d7ef9b3b9f54fcf90ad9f4"),
    ("workflow.review_repair", "4c24316404805a947ffeac59ac836bca1b8e9cd59eae87b03c59453a05fdf797"),
])
def test_existing_1_10_snapshots_retain_the_exact_requests_already_used_by_real_runs(prompt_id, sha256):
    frozen = get(prompt_id, version="1.10.0")
    assert hashlib.sha256(frozen.template.encode()).hexdigest() == sha256
    assert CLAIM_SCOPE_REVIEW not in render(frozen) and CLAIM_SCOPE_REPAIR not in render(frozen)


@pytest.mark.parametrize("phase,rule", [
    ("generation", CLAIM_SCOPE_GENERATION), ("review", CLAIM_SCOPE_REVIEW), ("repair", CLAIM_SCOPE_REPAIR),
])
def test_scope_is_an_append_only_new_default_and_does_not_replace_node_output_schema(phase, rule):
    previous = '角色与输出由本节点设定。仅返回 {"answer":"正文","quotes":[]}。\n保留用户原题。'
    updated = claim_scope_body(previous, phase=phase)
    assert updated == previous + "\n" + rule
    assert previous == '角色与输出由本节点设定。仅返回 {"answer":"正文","quotes":[]}。\n保留用户原题。'
    saved = spec("scope_test", "scope contract", updated, version="1.0.0")
    assert render(saved) == updated
    for phrase in ("任务", "条件", "范围", "唯一", "schema" if phase != "generation" else "契约"):
        assert phrase in rule


def test_generation_separates_multiple_valid_answers_from_unanswerability_and_honors_true_constraints():
    for phrase in ("作答条件是否充分", "按合适标准评审", "答案是否唯一", "四件事", "多个合格答案",
                   "仍可按目标、事实、约束和质量标准形成监督样本", "必要信息", "不要求每题机械生成反例",
                   "有充分逻辑或证据支持的必要条件仍可明确表达"):
        assert phrase in CLAIM_SCOPE_GENERATION
    for phrase in ("筛选启发式", "评测便利", "普遍充分条件", "局部阈值", "全域禁令", "因果保证"):
        assert phrase in CLAIM_SCOPE_GENERATION


def test_review_checks_the_general_inference_error_not_a_specific_paper_or_trigger_word():
    for phrase in ("对象、任务类型、条件、证据范围", "量词/结论强度", "筛选启发式", "普遍必要条件",
                   "局部阈值推广全域", "经验观察提升为因果保证", "可解性", "作答条件充分性", "可评审性",
                   "答案唯一性", "多个", "仍可被评审并用于监督", "同范围内合法的反例"):
        assert phrase in CLAIM_SCOPE_REVIEW
    assert "不因出现‘所有’等字词就机械拒绝" in CLAIM_SCOPE_REVIEW
    assert "限定范围内的实验描述" in CLAIM_SCOPE_REVIEW
    for forbidden in ("Self-Instruct", "SELF-INSTRUCT", "school safety", "82,439", "35,878"):
        assert forbidden not in CLAIM_SCOPE_GENERATION + CLAIM_SCOPE_REVIEW + CLAIM_SCOPE_REPAIR


def test_historical_population_statistics_cannot_become_current_user_distribution_or_gain():
    for phrase in ("历史研究或数据集", "保留其归属", "当前样本或工程", "待实测的假设"):
        assert phrase in CLAIM_SCOPE_GENERATION
    for phrase in ("历史研究的统计比例、标签分布和效果", "当前代表性测量", "工程收益", "效果保证",
                   "准确的研究归属", "待实测的迁移假设"):
        assert phrase in CLAIM_SCOPE_REVIEW
    for phrase in ("明确归还原研究", "未测量的当前用户工程", "不凭空补本地数据"):
        assert phrase in CLAIM_SCOPE_REPAIR


def test_proxy_comparisons_preserve_construct_validity_direction_denominator_and_comparability():
    for rule in (CLAIM_SCOPE_GENERATION, CLAIM_SCOPE_REVIEW, CLAIM_SCOPE_REPAIR):
        for phrase in ("代理指标", "目标构念", "领域", "长度", "模板", "方向", "分母", "地板"):
            assert phrase in rule
    for phrase in ("词面重叠低并不独自证明语义新颖", "事实正确", "任务有效", "是否可比", "尺度及上下界",
                   "推理中的比较建议与答案解释是否相符", "低分基线不能直接推出产生了有效新任务",
                   "实际可达性和解释依据", "经验地板", "绝对下界", "直接验证"):
        assert phrase in CLAIM_SCOPE_REVIEW
    assert "不把经验低分基线冒充绝对下界" in CLAIM_SCOPE_GENERATION
    assert "推理与答案采用同一比较方向" in CLAIM_SCOPE_REPAIR


def test_material_scope_error_cannot_keep_full_factual_score_or_be_washed_out_by_later_turns():
    for phrase in ("判material", "correctness/reasoning/grounding至不通过", "keep=false",
                   "不能只给轻微风格意见或保留事实满分", "具体助手消息", "缺少哪类支持",
                   "后续轮正确不能抵消早期实质错误", "逐轮索引/hash绑定", "不是事实正确性的独立证明"):
        assert phrase in CLAIM_SCOPE_REVIEW


def test_repair_requires_rebuilding_dependent_reasoning_without_merely_hedging_or_deleting_it():
    for phrase in ("答案与全部受影响的显式推理", "适用目标", "全域必要条件", "多个合格答案",
                   "不能只加‘可能’", "支持关系与结论", "不一律弱化", "不删reasoning", "用户/工具事实",
                   "语言互译", "知识应用口吻", "允许编辑字段/输出schema"):
        assert phrase in CLAIM_SCOPE_REPAIR


def test_unknown_scope_builder_phase_is_an_explicit_error():
    with pytest.raises(ValueError, match="invalid_claim_scope_prompt_phase"):
        claim_scope_body("原模板", phase="invalid")
