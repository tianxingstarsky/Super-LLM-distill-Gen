"""An exact opt-in format marker must not change verdicts or old QA schemas."""
from copy import deepcopy
from types import SimpleNamespace

import pytest

from lib.domain.style_judge import style_verdict
from lib.infrastructure.workflow_qa_director import WorkflowQADirector


def standard(*, keep=True, adherence=5):
    return {"keep": keep, "adherence": adherence, "reason": "实际话语满足当前约束。"}


@pytest.mark.parametrize("reasoning", [False, True])
@pytest.mark.parametrize("keep,score", [(True, 5), (False, 5), (True, 2), (False, 1)])
def test_exact_format_marker_is_removed_without_upgrading_a_judgment_or_mutating_raw(reasoning, keep, score):
    raw = {**standard(keep=keep, adherence=score), "type": "json_object"}
    if reasoning:
        raw["reasoning"] = "逐项检查实际对话，分数与keep保留原判定。"
    original = deepcopy(raw)
    normalized = style_verdict(raw, allow_format_metadata=True)
    assert normalized == standard(keep=keep, adherence=score)
    assert normalized is not raw and raw == original
    assert (normalized["keep"] and normalized["adherence"] >= 4) == (keep and score >= 4)


@pytest.mark.parametrize("allow", [False, None, 1, "true"])
def test_only_explicit_boolean_opt_in_accepts_extra_format_marker(allow):
    raw = {**standard(), "type": "json_object"}
    with pytest.raises(ValueError, match="invalid_style_judge_schema"):
        style_verdict(raw, allow_format_metadata=allow)


@pytest.mark.parametrize("value", [None, "json", "JSON_OBJECT", "json_object ", True, 1, {}, []])
def test_other_type_values_are_not_treated_as_redundant_metadata(value):
    raw = {**standard(), "type": value}
    original = deepcopy(raw)
    with pytest.raises(ValueError, match="invalid_style_judge_schema"):
        style_verdict(raw, allow_format_metadata=True)
    assert raw == original


@pytest.mark.parametrize("field,value", [("score", 5), ("extra", "x"), ("reasoning_content", "hidden"),
    ("keep", "true"), ("adherence", True), ("adherence", 5.0), ("reason", ""), ("reasoning", "")])
def test_metadata_tolerance_never_ignores_other_fields_or_relaxes_existing_score_contract(field, value):
    raw = {**standard(), "type": "json_object", field: value}
    with pytest.raises(ValueError, match="invalid_style_judge_schema"):
        style_verdict(raw, allow_format_metadata=True)


def test_format_marker_does_not_bypass_caller_reason_bounds_or_content_guards():
    for reason in ("x" * 4001, "bad\x00text", "bad\ufffdtext"):
        with pytest.raises(ValueError, match="invalid_style_judge_schema"):
            style_verdict({**standard(), "type": "json_object", "reason": reason},
                          allow_format_metadata=True, maximum_reason_length=4000, inspect_reason=True)


def qa_probe(raw, version=None):
    invalidated = []
    recipe = {"qa_director": {"answer_rules": "保持当前真实任务。", "question_rules": "正常对话。"}}
    if version is not None:
        recipe["style_verdict_protocol_version"] = version
    probe = SimpleNamespace(recipe=recipe, ask=lambda *args, **kwargs: raw,
        _invalidate_director_call=lambda key: invalidated.append(key))
    unit = {"qa_contract": {"qa_type": "closed_book", "answer_policy": "answer"}, "kind": "brief"}
    messages = [{"role": "user", "content": "能直接使用吗？"}, {"role": "assistant", "content": "满足条件即可使用。"}]
    return probe, unit, messages, invalidated


@pytest.mark.parametrize("reasoning", [False, True])
def test_new_qa_contract_check_uses_the_same_narrow_normalizer(reasoning):
    raw = {**standard(keep=False, adherence=2), "type": "json_object"}
    if reasoning:
        raw["reasoning"] = "原判定为不符合，不能因格式元数据升级。"
    original = deepcopy(raw)
    probe, unit, messages, invalidated = qa_probe(raw, 2)
    result = WorkflowQADirector.qa_contract_check(probe, ["case"], unit, messages)
    assert result == standard(keep=False, adherence=2) and not invalidated
    assert raw == original


@pytest.mark.parametrize("version", [None, 1, 3, True, 2.0, "2"])
@pytest.mark.parametrize("extra", [{"type": "json_object"}, {"reasoning": "可见解释"}])
def test_old_or_malformed_marker_keeps_qa_exact_three_field_schema(version, extra):
    raw = {**standard(), **extra}
    probe, unit, messages, invalidated = qa_probe(raw, version)
    with pytest.raises(ValueError, match="invalid_qa_director_judge_schema"):
        WorkflowQADirector.qa_contract_check(probe, ["case"], unit, messages)
    assert invalidated == [["case"]]


def test_new_qa_marker_does_not_discard_arbitrary_extra_fields():
    raw = {**standard(), "type": "json_object", "unrequested_score": 5}
    probe, unit, messages, invalidated = qa_probe(raw, 2)
    with pytest.raises(ValueError, match="invalid_qa_director_judge_schema"):
        WorkflowQADirector.qa_contract_check(probe, ["case"], unit, messages)
    assert invalidated == [["case"]]
