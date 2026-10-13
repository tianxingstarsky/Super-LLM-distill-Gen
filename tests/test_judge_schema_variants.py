"""Two exact judge schemas normalize without guessing or changing any score."""
from copy import deepcopy

import pytest

from lib.domain.workflow_quality import accepted, verdict


@pytest.fixture(scope="session", autouse=True)
def mock_llm_server():
    yield


def standard():
    return {"keep": True, "grounded": True, "reasoning_valid": True, "correctness": 4,
            "reason": "完整评审理由。", "scores": {"correctness": 4, "reasoning": 3,
                "grounding": 5, "instruction": 4, "safety": 5}}


def alias():
    value = standard()
    value["reasoning"] = value.pop("reason")
    return value


def flat():
    value = standard()
    return {**{key: child for key, child in value.items() if key != "scores"}, **value["scores"]}


@pytest.mark.parametrize("factory", [alias, flat])
def test_exact_schema_normalizes_without_changing_scores_or_mutating_raw(factory):
    raw = factory()
    original = deepcopy(raw)
    check = verdict(raw)
    assert check == standard()
    assert raw == original
    assert check is not raw and not accepted(check)  # reasoning=3 is not upgraded.
    check["scores"]["safety"] = 1
    assert raw == original


@pytest.mark.parametrize("factory", [alias, flat])
@pytest.mark.parametrize("field,bad", [("keep", 1), ("grounded", "true"), ("reasoning_valid", None),
                                     ("correctness", True), ("correctness", 0), ("correctness", 6)])
def test_normalized_schema_retains_strict_boolean_and_score_validation(factory, field, bad):
    raw = factory()
    raw[field] = bad
    original = deepcopy(raw)
    with pytest.raises(ValueError):
        verdict(raw)
    assert raw == original


@pytest.mark.parametrize("factory", [alias, flat])
@pytest.mark.parametrize("dimension,bad", [("reasoning", True), ("grounding", 4.0), ("instruction", "5"),
                                          ("safety", 0), ("safety", 6)])
def test_no_dimension_type_or_range_is_coerced(factory, dimension, bad):
    raw = factory()
    if factory is alias:
        raw["scores"][dimension] = bad
    else:
        raw[dimension] = bad
    with pytest.raises(ValueError, match="invalid_jev_dimensions"):
        verdict(raw)


@pytest.mark.parametrize("factory", [alias, flat])
@pytest.mark.parametrize("reason", ["", "  ", None, 5])
def test_empty_or_nontext_reason_is_never_invented(factory, reason):
    raw = factory()
    raw["reasoning" if factory is alias else "reason"] = reason
    with pytest.raises(ValueError, match="missing_judge_reason"):
        verdict(raw)


@pytest.mark.parametrize("factory", [alias, flat])
def test_extra_keys_or_missing_scores_do_not_get_guessed_into_a_schema(factory):
    raw = factory()
    raw["extra"] = "not a known shape"
    with pytest.raises(ValueError):
        verdict(raw)
    missing = factory()
    if factory is alias:
        missing["scores"].pop("safety")
    else:
        missing.pop("safety")
    with pytest.raises(ValueError, match="invalid_jev_dimensions"):
        verdict(missing)


def test_compatibility_does_not_choose_between_both_reason_names():
    raw = alias()
    raw["reason"] = ""
    with pytest.raises(ValueError, match="missing_judge_reason"):
        verdict(raw)
    raw["reason"] = "既有标准理由。"
    assert verdict(raw) is raw  # Already-standard reason wins without alias rewriting.


def test_standard_schema_keeps_existing_behavior_and_returns_actual_supplied_scores():
    raw = standard()
    assert verdict(raw) is raw
    assert verdict(raw)["scores"]["reasoning"] == 3
