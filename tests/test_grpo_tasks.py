"""Pinned dataset splits and numeric ground truth at the external input boundary."""
from dataclasses import replace
import sys
from types import SimpleNamespace

import pytest

from lib.contexts.training.domain.grpo import GRPOValidationPlan
from lib.contexts.training.infrastructure.grpo_tasks import (
    GSM8K_CONFIG, GSM8K_ID, GSM8K_REVISION, load_grpo_tasks,
)


@pytest.fixture
def corpus(monkeypatch):
    rows = {split: [{"question": f"{split} original question {index}?",
                     "answer": f"Private gold explanation {index}. #### {index + 9000}"}
                    for index in range(8)] for split in ("train", "test")}
    calls = []

    def load_dataset(*args, **kwargs):
        calls.append((args, kwargs))
        return rows

    monkeypatch.setitem(sys.modules, "datasets", SimpleNamespace(load_dataset=load_dataset))
    return rows, calls


def plan(**values):
    return replace(GRPOValidationPlan(model="model", output="output", task_source="gsm8k",
                                     train_size=3, evaluation_size=3), **values)


def test_official_splits_are_independent_deterministic_and_preserve_original_indices(corpus):
    rows, calls = corpus
    train, evaluation, source = load_grpo_tasks(plan())
    assert (train, evaluation, source) == load_grpo_tasks(plan())
    assert load_grpo_tasks(plan(train_size=4))[1] == evaluation
    assert calls[0] == ((GSM8K_ID, GSM8K_CONFIG), {"revision": GSM8K_REVISION})
    assert source["license"] == "MIT" and source["revision"] == GSM8K_REVISION
    for split, tasks in (("train", train), ("test", evaluation)):
        assert len({task["source_index"] for task in tasks}) == 3
        for task in tasks:
            assert task["task_id"] == f"gsm8k/main/{split}/{task['source_index']}"
            assert rows[split][task["source_index"]]["question"] in task["prompt"]
            assert "Private gold explanation" not in task["prompt"]
            assert task["expected_answer"] not in task["prompt"]


def test_truth_uses_last_delimiter_and_exact_fraction_values(corpus):
    rows, _ = corpus
    for answer, expected in (("Private #### 99\nRevision #### 1,200", "1200"),
                             ("Private #### -12.5", "-25/2"),
                             ("Private #### 3/4", "3/4")):
        for row in rows["train"]:
            row["answer"] = answer
        assert all(task["expected_answer"] == expected for task in load_grpo_tasks(plan())[0])
    for answer in ("missing delimiter 42", "Private #### NaN", "Private #### inf", "Private #### 1/0",
                   "Private #### 42 or 43", "Private #### 42 meters"):
        for row in rows["train"]:
            row["answer"] = answer
        with pytest.raises(ValueError, match="grpo_gsm8k_"):
            load_grpo_tasks(plan())


def test_duplicate_questions_and_out_of_bounds_samples_fail_before_generation(corpus):
    rows, _ = corpus
    with pytest.raises(ValueError, match="sample_exceeds_split"):
        load_grpo_tasks(plan(train_size=9))
    for row in rows["train"]:
        row["question"] = "Duplicate question?"
    with pytest.raises(ValueError, match="duplicate_question"):
        load_grpo_tasks(plan())
    for split in ("train", "test"):
        for index, row in enumerate(rows[split]):
            row["question"] = f"Shared question {index}?"
    with pytest.raises(ValueError, match="question_leakage"):
        load_grpo_tasks(plan(train_size=8, evaluation_size=8))
