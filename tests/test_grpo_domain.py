"""Exact GRPO tasks keep single-pass budgets and held-out inputs auditable."""
from dataclasses import FrozenInstanceError, replace
from fractions import Fraction
import json
import re

import pytest

from lib.contexts.training.domain.grpo import (
    GRPOValidationPlan, TASK_FAMILIES, build_verifiable_tasks, summarize_rewards,
    validate_capacity_resume,
)


def plan():
    return GRPOValidationPlan(model="model", output="output")


@pytest.mark.parametrize("field,value", [
    ("epochs", 0), ("epochs", 1.01), ("epochs", True), ("epochs", float("nan")),
    ("epochs", float("inf")), ("num_iterations", 2), ("num_iterations", True),
    ("num_generations", 1), ("num_generations", 3), ("train_size", 0),
    ("evaluation_size", True), ("seed", -1), ("seed", False),
    ("learning_rate", float("nan")), ("learning_rate", float("inf")),
    ("temperature", 0), ("beta", float("nan")), ("beta", -0.1),
    ("max_completion_length", 0), ("lora_rank", True), ("task_source", "unknown"),
])
def test_invalid_budget_cannot_repeat_data_or_hide_nonfinite_values(field, value):
    with pytest.raises(ValueError, match="grpo_"):
        replace(plan(), **{field: value})


def test_budget_is_frozen_serializable_and_allows_fractional_single_pass():
    configured = replace(plan(), epochs=0.5)
    assert GRPOValidationPlan(**json.loads(json.dumps(configured.to_dict()))) == configured
    assert configured.num_iterations == 1
    assert configured.beta == 0
    with pytest.raises(FrozenInstanceError):
        configured.epochs = 2


def test_default_capacity_is_32k_and_parent_run_accepts_only_text():
    assert plan().max_completion_length == 32768
    assert plan().parent_run == ""
    for invalid in (None, 1, False):
        with pytest.raises(ValueError, match="invalid_parent_run"):
            replace(plan(), parent_run=invalid)


def test_capacity_resume_normalizes_old_parent_and_keeps_one_total_epoch():
    parent = replace(plan(), max_completion_length=4096)
    old_configuration = parent.to_dict()
    old_configuration.pop("parent_run", None)
    child = replace(parent, output="continued-output", parent_run=parent.output,
                    max_completion_length=32768)
    assert validate_capacity_resume(old_configuration, child) == parent
    assert child.epochs == parent.epochs == 1.0
    assert child.num_iterations == parent.num_iterations == 1
    assert "parent_run" not in old_configuration


def test_capacity_resume_requires_parent_and_strictly_increased_capacity():
    parent = replace(plan(), max_completion_length=4096)
    for child in (replace(parent, max_completion_length=32768),
                  replace(parent, parent_run=" ", max_completion_length=32768)):
        with pytest.raises(ValueError, match="parent_required"):
            validate_capacity_resume(parent.to_dict(), child)
    for capacity in (2048, 4096):
        with pytest.raises(ValueError, match="increased_capacity"):
            validate_capacity_resume(parent.to_dict(),
                replace(parent, parent_run=parent.output, max_completion_length=capacity))


def test_capacity_resume_rejects_changes_to_tasks_sampling_and_training_budget():
    parent = replace(plan(), max_completion_length=4096)
    child = replace(parent, output="continued-output", parent_run=parent.output,
                    max_completion_length=32768)
    for field, value in (("model", "another-model"), ("task_source", "gsm8k"),
                         ("seed", 1730), ("train_size", 16), ("evaluation_size", 12),
                         ("epochs", 0.5), ("num_generations", 2), ("batch_size", 2),
                         ("gradient_accumulation", 8), ("max_prompt_length", 1024),
                         ("learning_rate", 1e-5), ("lora_rank", 16),
                         ("temperature", 0.8), ("beta", 0.04)):
        with pytest.raises(ValueError, match="configuration_changed:" + field):
            validate_capacity_resume(parent.to_dict(), replace(child, **{field: value}))


def test_tasks_are_deterministic_balanced_and_disjoint_before_baseline():
    train, evaluation = build_verifiable_tasks()
    assert (train, evaluation) == build_verifiable_tasks()
    assert len(train) == 32 and len(evaluation) == 24
    assert set(task.family for task in train) == set(TASK_FAMILIES)
    assert set(task.family for task in evaluation) == set(TASK_FAMILIES)
    assert set(task.prompt for task in train).isdisjoint(task.prompt for task in evaluation)
    assert len({task.task_id for task in train + evaluation}) == 56
    assert set(task.split for task in train) == {"train"}
    assert set(task.split for task in evaluation) == {"evaluation"}
    assert build_verifiable_tasks(seed=1730)[0] != train
    for task in train + evaluation:
        assert task.to_dict()["expected_answer"] == task.expected_answer
        assert task.prompt.endswith(r"Put your final numerical answer in \boxed{}.")
        assert r"\boxed{" + task.expected_answer + "}" not in task.prompt
        assert str(Fraction(task.expected_answer)) == task.expected_answer
    with pytest.raises(FrozenInstanceError):
        train[0].expected_answer = "0"


def test_seeded_known_tasks_have_manually_checked_exact_targets():
    train, _ = build_verifiable_tasks()
    # 43/16-4/7=237/112; 28/23+34/3=866/69.
    assert train[0].expected_answer == "16353/96992"
    # 36 km/h=10 m/s; distance=1170+2100, elapsed=117+62+420.
    assert train[1].expected_answer == "3270/599"
    # Exactly one: 118+102+91 - 2*(47+43+37) + 3*17.
    assert train[2].expected_answer == "108"
    # 200*(4/11)+58, then retain 5/6.
    assert train[4].expected_answer == "3595/33"


def test_divisibility_targets_match_direct_enumeration_on_both_splits():
    for task in sum(build_verifiable_tasks(), ()):
        if task.family != "divisibility_counting":
            continue
        lower, upper, a, b, excluded = map(int, re.findall(r"\d+", task.prompt.split("\n\n")[0]))
        actual = sum((n % a == 0 or n % b == 0) and n % excluded != 0
                     for n in range(lower, upper + 1))
        assert task.expected_answer == str(actual)


def test_metrics_report_outcomes_and_keep_empty_evaluation_unknown():
    assert summarize_rewards([1, 0, 1, 0]) == {"count": 4, "correct": 2, "accuracy": 0.5}
    assert summarize_rewards([]) == {"count": 0, "correct": 0, "accuracy": None}
    with pytest.raises(ValueError, match="invalid_correctness_reward"):
        summarize_rewards([True])
