"""Offline regression checks for prompt evolution, not semantic truth tests."""
from dataclasses import replace
from hashlib import sha256

import pytest

from lib.prompts.base import render
from lib.prompts import get
from lib.prompts.workflow import COT_GENERATE_V1_6, RATIONALE_CHECK_V1_2
from lib.prompts.workflow_reasoning import (
    COT_GENERATE_BODY,
    RATIONALE_CHECK_BODY,
    TASK_ADAPTIVE_REASONING_BODY,
    TASK_ADAPTIVE_REASONING_REVIEW_BODY,
    task_adaptive_reasoning_template,
)


@pytest.fixture(scope="session", autouse=True)
def mock_llm_server():
    yield


def test_historical_generation_and_review_bodies_keep_exact_snapshot_hashes():
    assert sha256(COT_GENERATE_BODY.encode("utf-8")).hexdigest() == (
        "c91013b877549e6426022dbba69cd9b831db5bac7b5dac2ce3bc9fec96f921d6"
    )
    assert sha256(RATIONALE_CHECK_BODY.encode("utf-8")).hexdigest() == (
        "79fa806dc5ad061c565068050d8dd3a3c16e7aeca3c35aba92047f05d87debb9"
    )


@pytest.mark.parametrize("old,review", [
    (COT_GENERATE_V1_6, False),
    (RATIONALE_CHECK_V1_2, True),
])
def test_new_shape_instruction_is_renderable_without_mutating_old_spec(old, review):
    before = old.template
    new = replace(old, version="99.0.0", template=task_adaptive_reasoning_template(
        before, review=review,
    ))
    rendered = render(new)
    assert old.template == before
    assert render(old) != rendered
    if review:
        assert "真正的" in rendered
    else:
        assert "真实依赖" in rendered
    assert "任务适配的推理" in rendered
    assert '"reasoning_trace"' in rendered or '"reasoning_valid"' in rendered


def test_new_generation_drops_only_our_generic_subtask_order_instruction():
    old = COT_GENERATE_V1_6.template
    new = task_adaptive_reasoning_template(old)
    assert "先识别需要的子任务再按依赖解决" in old
    assert "先识别需要的子任务再按依赖解决" not in new
    assert "数学推导、算法、时间关系、操作前置条件与多轮对话" in new
    assert "不能为了多样化随机打乱真实依赖" in new
    assert "用户明确指定编号、结构、推导方法或篇幅时遵守其要求" in new


def test_supplement_preserves_custom_format_and_is_idempotent():
    custom = "用户格式：先算x，再代回。编号用第一步、第二步。"
    new = task_adaptive_reasoning_template(custom)
    assert new.startswith(custom + "\n")
    assert task_adaptive_reasoning_template(new) == new
    assert task_adaptive_reasoning_template(
        task_adaptive_reasoning_template(custom, review=True), review=True,
    ) == task_adaptive_reasoning_template(custom, review=True)


def test_shape_guidance_keeps_short_and_ambiguous_tasks_legal():
    assert "短问不必长答" in TASK_ADAPTIVE_REASONING_BODY
    assert "信息不足时可用很短的解释支持澄清" in TASK_ADAPTIVE_REASONING_BODY
    assert "不是必须轮换的模板、行为配额或新步骤清单" in TASK_ADAPTIVE_REASONING_BODY
    assert "没有错误前提就不制造纠错" in TASK_ADAPTIVE_REASONING_BODY
    assert "不设置统一长度或步骤数下限" in TASK_ADAPTIVE_REASONING_REVIEW_BODY
    assert "随机调换依赖、错误因果或漏掉必要前提仍须拒绝" in TASK_ADAPTIVE_REASONING_REVIEW_BODY


@pytest.mark.parametrize("prompt_id,version,expected_hash", [
    ("workflow.multiturn_user", "1.1.0", "62ee0affebd7e4f1352da1cf9e1ad1b1dc456c6361cff5af2acb773a09fca03b"),
    ("workflow.multiturn_user", "1.2.0", "1cd0d66ace94241257d57a1c2b82ca0d23881136013950195005aae1143b2880"),
    ("workflow.question_fit", "1.0.0", "57bb308d4719faf0a38b96fa95c17d947350971baa8531b9e3174618b781a595"),
    ("workflow.question_fit", "1.1.0", "7ff830e0f02dd77018607c697f40222aee311b022c0e5f89cec730561dbe5d7b"),
])
def test_natural_expression_evolution_keeps_every_prior_role_snapshot(prompt_id, version, expected_hash):
    # Explicit version addresses must still resolve the exact body used by
    # existing runs; a reused module alias must not erase an older spec.
    assert sha256(get(prompt_id, version=version).template.encode("utf-8")).hexdigest() == expected_hash


@pytest.mark.parametrize("prompt_id,version,old_version", [
    ("workflow.multiturn_user", "1.3.0", "1.2.0"),
    ("workflow.question_fit", "1.2.0", "1.1.0"),
])
def test_new_natural_expression_versions_are_latest_and_do_not_alias_old_body(prompt_id, version, old_version):
    current = get(prompt_id)
    assert current.version == version
    assert current.template != get(prompt_id, version=old_version).template
