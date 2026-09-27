"""Arithmetic training must verify every annotation and the terminal answer."""
from copy import deepcopy

import pytest

from lib.domain.math_tasks import build_gsm8k, evaluate_integer_expression, validate_gsm8k


def test_multistep_families_are_deterministic_and_every_equation_is_checked():
    families = set()
    for seed in range(1000):
        sample = build_gsm8k("设备维护", seed, version=2)
        assert sample == build_gsm8k("设备维护", seed, version=2)
        assert validate_gsm8k(sample)
        assert len(sample["_steps"]) >= 2
        families.add(sample["_family"])
        for step in sample["_steps"]:
            assert evaluate_integer_expression(step["expression"]) == step["result"]
    assert families == {0, 1, 2, 3}


def test_legacy_generator_is_preserved_for_unversioned_recipes():
    sample = build_gsm8k("设备维护", 0)
    assert sample["_expression"] == "2 + 2" and sample["_result"] == 4
    assert sample["answer"] == "列式：2 + 2 = 4。因此答案是 4。 <<2 + 2=4>> #### 4"
    assert sample == build_gsm8k("设备维护", 0, version=1)
    with pytest.raises(ValueError, match="unsupported_math_generator_version"):
        build_gsm8k("设备维护", 0, version=3)


@pytest.mark.parametrize("replacement", ["#### 40", "#### 4 followed by unchecked text", "#### 5", "no final answer"])
def test_final_answer_must_be_exact_and_terminal(replacement):
    sample = build_gsm8k("设备维护", 0)
    sample["answer"] = sample["answer"].replace("#### 4", replacement)
    assert not validate_gsm8k(sample)


def test_correct_final_answer_does_not_hide_incorrect_intermediate_calculation():
    sample = build_gsm8k("设备维护", 0, version=2)
    damaged = deepcopy(sample)
    first = sample["_steps"][0]
    damaged["answer"] = damaged["answer"].replace(
        f"<<{first['expression']}={first['result']}>>", f"<<{first['expression']}={first['result'] + 1}>>")
    assert not validate_gsm8k(damaged)
    damaged["answer"] = sample["answer"] + " <<unclosed"
    assert not validate_gsm8k(damaged)


@pytest.mark.parametrize("expression", [None, "", "1+" * 600 + "1", "1+" * 50 + "1", "True + 1"])
def test_expression_limits_reject_bad_types_and_excessive_work(expression):
    with pytest.raises(ValueError):
        evaluate_integer_expression(expression)


def test_equal_result_cannot_replace_recorded_calculation():
    legacy = build_gsm8k("设备维护", 0)
    legacy["answer"] = "<<1 + 3=4>> #### 4"
    assert not validate_gsm8k(legacy)
    sample = build_gsm8k("设备维护", 0, version=2)
    first = sample["_steps"][0]
    sample["answer"] = sample["answer"].replace(
        f"<<{first['expression']}={first['result']}>>", f"<<1 * {first['result']}={first['result']}>>")
    assert not validate_gsm8k(sample)


@pytest.mark.parametrize("steps", [None, {}, [], [None], [{"expression":"2 * 2","result":True,"explanation":"step"}]])
def test_invalid_or_incomplete_recorded_steps_are_rejected(steps):
    sample = build_gsm8k("设备维护", 0, version=2)
    sample["_steps"] = steps
    assert not validate_gsm8k(sample)


def test_recorded_step_mutation_and_order_are_checked():
    sample = build_gsm8k("设备维护", 0, version=2)
    damaged = deepcopy(sample)
    damaged["_steps"][0]["result"] += 1
    assert not validate_gsm8k(damaged)
    damaged = deepcopy(sample)
    damaged["_steps"].reverse()
    assert not validate_gsm8k(damaged)
    damaged = deepcopy(sample)
    damaged["answer"] = damaged["answer"].replace('<<2 * 2=4>>', '<<(2*2)=4>>')
    assert validate_gsm8k(damaged)


@pytest.mark.parametrize("record", [None, [], "sample"])
def test_nonobject_math_record_is_rejected(record):
    assert not validate_gsm8k(record)
