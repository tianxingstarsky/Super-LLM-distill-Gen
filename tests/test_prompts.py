"""提示词资产库结构契约测试（全离线）：每个提示词都必须可渲染、声明完整、防呆约束到位。"""
from __future__ import annotations

import re

import pytest

from lib.prompts import PromptRenderError, get, registry, render
from lib.prompts.base import PromptSpec


@pytest.fixture(scope="session", autouse=True)
def mock_llm_server():
    # Registry and rendering checks are offline and do not need the shared
    # provider fixture or compete for its HTTP port in parallel test runs.
    yield


def test_registry_ids_unique_and_versioned():
    specs = registry()
    assert len(specs) >= 10
    for spec in specs.values():
        assert re.match(r"^\d+\.\d+\.\d+$", spec.version), spec.id
        assert spec.purpose and spec.source


def test_all_templates_render_with_declared_variables():
    for spec in registry().values():
        sample = {v: "测试占位" for v in spec.variables}
        out = render(spec, **sample)
        assert isinstance(out, str) and out


def test_render_fails_fast_on_missing_or_extra_vars():
    spec = get("distill.reflector")
    with pytest.raises(PromptRenderError):
        render(spec, goal="x")  # 缺 history_steps/last_step
    with pytest.raises(PromptRenderError):
        render(spec, goal="x", history_steps="y", last_step="z", nope=1)


def test_placeholder_declarations_match_template():
    for spec in registry().values():
        # 模板中 {var}（排除 {{ }} 字面量）必须全部在 variables 里声明
        placeholders = set(re.findall(r"(?<!\{)\{(\w+)\}(?!\})", spec.template))
        assert placeholders <= set(spec.variables), f"{spec.id}: {placeholders - set(spec.variables)}"


def test_distill_generator_anti_repetition_constraints_baked_in():
    spec = get("distill.generator")
    t = spec.template
    assert "≤20%" in t          # 教训占比上限（防复述错误）
    assert "一句话教训" in t     # 错误只留教训
    assert "只包含正确操作" in t  # 最终只留正确
    assert "原计划" in t and "改用" in t  # 反思式结构


def test_distill_reflector_signal_priority():
    t = get("distill.reflector").template
    assert "exit_code" in t and "用户信号" in t and "优先级" in t


def test_json_output_contracts_declared():
    # 所有要求 JSON 输出的提示词都显式写了键名（评测器据此校验）
    assert '"translation"' in get("translation.zh2en").template
    assert '"faithful"' in get("translation.backcheck").template
    assert '"thinking"' in get("distill.generator").template
    assert '"correctness"' in get("judge.score").template


def test_magpie_query_no_vars_and_no_template_greetings():
    spec = get("magpie.query")
    assert spec.variables == ()
    assert "question" in spec.template.lower()


def test_legacy_module_compat():
    from lib.adapters import distill_prompts as legacy

    assert legacy.SUMMARIZER_TEXT_PROMPT == get("distill.summarizer").template
    assert "默认风格" in legacy.GENERATOR_TEXT_PROMPT  # v1.2.0 style_guide 已预填充
    assert legacy.MAGPIE_QUERY_SYSTEM_PROMPT == get("magpie.query").template


def test_configured_explanation_length_does_not_conflict_with_short_default():
    from lib.prompts import workflow
    current = get("workflow.cot_generate")
    assert current.version == "1.8.0"
    assert "用户未配置篇幅时直接可解的问题可以很短" in current.template
    assert "用户明确配置篇幅时核对实际正文与有效信息量" in current.template
    assert "具体版本行为、阈值、CLI开关、SQL字段和锁类型" in current.template
    historical = get("workflow.cot_generate", version="1.5.0")
    assert historical == workflow.COT_GENERATE_V1_5
    assert "用户未配置篇幅时直接可解的问题可以很短" not in historical.template
    assert "只返回" in render(historical) or "仅返回" in render(historical)


def test_precise_fact_review_preserves_old_prompt_snapshots():
    for name, current_version, old_version in (
        ("rationale_check", "1.6.0", "1.2.0"),
        ("review_score", "1.5.0", "1.3.0"),
        ("package_review", "1.4.0", "1.2.0"),
    ):
        current = get("workflow." + name)
        old = get("workflow." + name, version=old_version)
        assert current.version == current_version and old.version == old_version
        assert "没有依据的精确细节应省略或明确保留不确定性" in render(current)
        assert "没有依据的精确细节应省略或明确保留不确定性" not in render(old)


def test_repair_draft_errors_are_not_attributed_to_actual_user_and_old_version_stays_pinned():
    from lib.prompts import workflow
    current = get("workflow.review_repair")
    old = get("workflow.review_repair", version="1.3.0")
    assert current.version == "1.6.0" and old == workflow.REVIEW_REPAIR_V1_3
    assert "仅按实际question与可见文章判断用户的前提" in render(current)
    assert "不能将这些内部材料中的错误归因给用户" in render(current)
    assert "不叙述旧稿、返工、删除句子或遵守评语" in render(current)
    assert "仅按实际question与可见文章判断用户的前提" not in render(old)


def test_user_hypotheses_do_not_become_asserted_beliefs_and_previous_snapshots_remain_registered():
    from lib.prompts import workflow
    for name, new_version, old_version, previous in (
        ("cot_generate", "1.8.0", "1.6.0", workflow.COT_GENERATE_V1_6),
        ("review_repair", "1.6.0", "1.4.0", workflow.REVIEW_REPAIR_V1_4),
    ):
        assert get("workflow." + name).version == new_version
        assert "保留其语言中的不确定性" in render(get("workflow." + name))
        assert get("workflow." + name, version=old_version) == previous
        assert "保留其语言中的不确定性" not in render(previous)
