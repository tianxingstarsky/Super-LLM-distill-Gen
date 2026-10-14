"""Visible extra explanations normalize without changing verdicts or evidence."""
from copy import deepcopy
import json
from types import SimpleNamespace

import pytest

from lib.domain.style_judge import style_verdict
from lib.infrastructure import training_workflow as engine
from lib.infrastructure.workflow_native_regeneration import regenerate_native_cot_item
from test_native_same_task_regeneration import selected_parent


@pytest.fixture(scope="session", autouse=True)
def mock_llm_server():
    yield


def standard():
    return {"keep": True, "adherence": 5, "reason": "断电条件与当前任务一致。"}


def explained():
    return {**standard(), "reasoning": "本条具体检查了先断电，再检查的安全依赖。"}


def test_actual_c7_visible_four_field_response_is_supported_without_rewriting_its_reason():
    # Literal visible response from the paused v2 prefix review. The separate
    # provider reasoning_content is deliberately absent from this fixture.
    raw = {"keep": True, "adherence": 5,
        "reason": "正文直接回应了两个问题：给出按表（relfrozenxid，含 reltoastrelid 左连与 greatest）和按库（pg_database.datfrozenxid）两段查询，并说明分别读取 pg_class 与 pg_database 的统计，与可见说明完全一致。对 age 的解释准确：它是从截止 XID 到当前事务 XID 之间经过的事务数，不是时钟时间，且 XID 由集群共享计数器推进、只在事务首次写库时分配，这些均由核验资料（transaction-id、routine-vacuuming、pg_class、pg_database）支持。datfrozenxid 被正确描述为库内各表 relfrozenxid 的最小值，并正确推出它是保守监测边界：可见说明称其为 unfrozen XIDs 的 lower bound，因此实际未冻结 XID 不早于该边界、其年龄不超过 age(datfrozenxid)，正文明确否定了反推‘库里确有至少该年龄的未冻结行’，方向没有说反。TOAST 需独立推进 relfrozenxid、relkind 'r'/'m' 过滤的作用、非表关系该列为无效值也都有 pg_class 文档支撑。仅一处措辞略松：先说‘监控单张表读这一列就够了’，随后又说明必须与 TOAST 取较老者，前后略有张力，但同段已把正确做法讲清，不影响结论，无需返工。",
        "reasoning": "无事实编造、无内部流程或工具信息泄入正文，任务匹配、条件与边界完整。"}
    original = deepcopy(raw)
    value = style_verdict(raw, maximum_reason_length=4000, inspect_reason=True)
    assert value == {key: raw[key] for key in ("keep", "adherence", "reason")}
    assert value["reason"] == original["reason"] and raw == original


def test_exact_three_fields_keep_historical_object_and_values():
    raw = standard()
    assert style_verdict(raw) is raw
    assert style_verdict(raw, maximum_reason_length=4000, inspect_reason=True) is raw


@pytest.mark.parametrize("keep,score", [(True, 5), (False, 5), (True, 2), (False, 1)])
def test_extra_visible_explanation_never_changes_acceptance_or_scores(keep, score):
    raw = {**explained(), "keep": keep, "adherence": score}
    before = deepcopy(raw)
    normalized = style_verdict(raw)
    assert normalized == {"keep": keep, "adherence": score, "reason": raw["reason"]}
    assert raw == before and normalized is not raw
    assert (normalized["keep"] and normalized["adherence"] >= 4) == (keep and score >= 4)


@pytest.mark.parametrize("field,value", [("keep", "true"), ("keep", 1), ("adherence", True),
    ("adherence", 5.0), ("adherence", 0), ("adherence", 6), ("reason", ""), ("reason", None),
    ("reasoning", ""), ("reasoning", "   "), ("reasoning", None), ("reasoning", ["explanation"])])
def test_four_field_schema_has_no_type_or_value_coercion(field, value):
    raw = {**explained(), field: value}
    before = deepcopy(raw)
    with pytest.raises(ValueError, match="invalid_style_judge_schema"):
        style_verdict(raw)
    assert raw == before


@pytest.mark.parametrize("field", ["extra", "score", "reasoning_content", "grounded"])
def test_unknown_fields_are_never_discarded_or_used_as_hidden_fallback(field):
    raw = {**standard(), field: "not an allowed visible explanation"}
    with pytest.raises(ValueError, match="invalid_style_judge_schema"):
        style_verdict(raw)


def test_visible_reason_cannot_be_replaced_by_extra_explanation():
    raw = explained()
    raw.pop("reason")
    with pytest.raises(ValueError, match="invalid_style_judge_schema"):
        style_verdict(raw)


@pytest.mark.parametrize("reason", ["x" * 4001, "invalid\x00reason", "unreadable\ufffdreason"])
def test_native_reason_preserves_existing_bounds_and_text_guards(reason):
    with pytest.raises(ValueError, match="invalid_style_judge_schema"):
        style_verdict({**explained(), "reason": reason}, maximum_reason_length=4000, inspect_reason=True)


def test_ordinary_adherence_method_uses_same_normalization_and_invalidates_only_bad_schema():
    raw, invalidated = explained(), []
    probe = SimpleNamespace(ask=lambda *args, **kwargs: raw,
                            invalidate_checkpoint=lambda key: invalidated.append(key))
    assert engine.Workflow.adherence_check(probe, "case", "workflow.style_check", {}) == standard()
    assert raw == explained() and invalidated == []
    raw["unexpected"] = True
    with pytest.raises(ValueError, match="invalid_style_judge_schema"):
        engine.Workflow.adherence_check(probe, "case", "workflow.style_check", {})
    assert invalidated == [["call", "case"]]


def test_native_prefix_and_style_preserve_raw_four_field_checkpoints_and_resume(tmp_path, monkeypatch):
    workflow, item, _, reviewer, sent, _ = selected_parent(tmp_path, monkeypatch)
    original_review = reviewer.chat
    def visible_explanation(messages, **kwargs):
        raw = json.loads(original_review(messages, **kwargs))
        if set(raw) == {"keep", "adherence", "reason"}:
            raw["reasoning"] = "本条具体检查了断电安全前提，保持现有判定和分数。"
        return json.dumps(raw, ensure_ascii=False)
    monkeypatch.setattr(reviewer, "chat", visible_explanation)
    row, = regenerate_native_cot_item(workflow, item)
    assert row["status"] == "eligible" and len(sent) == 2
    assert set(row["prefix_review"]) == set(row["style_check"]) == {"keep", "adherence", "reason"}
    for phase in ("prefix_check", "style"):
        key = [row["id"], "native_same_task_v1", 0, phase]
        raw = engine.read_json(workflow.path / "checkpoints/review" / (engine.digest(["call", key]) + ".json"))["data"]
        assert set(raw) == {"keep", "adherence", "reason", "reasoning"}
        assert raw["keep"] is True and raw["adherence"] == 5 and raw["reasoning"]
    calls = len(reviewer.calls)
    again, = regenerate_native_cot_item(workflow, item)
    assert again == row and len(sent) == 2 and len(reviewer.calls) == calls
