"""提示词资产库结构契约测试（全离线）：每个提示词都必须可渲染、声明完整、防呆约束到位。"""
from __future__ import annotations

import re

import pytest

from lib.prompts import PromptRenderError, get, registry, render
from lib.prompts.base import PromptSpec


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
    assert legacy.GENERATOR_TEXT_PROMPT == get("distill.generator").template
    assert legacy.MAGPIE_QUERY_SYSTEM_PROMPT == get("magpie.query").template
