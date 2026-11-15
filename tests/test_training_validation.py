"""Training budgets, source separation and durable application stages."""
from dataclasses import replace
import pytest

from lib.contexts.training.api import TrainingValidationPlan, RunTrainingValidation
from lib.contexts.training.domain.validation import STAGES, assert_disjoint_questions, validate_supervision


def plan():
    return TrainingValidationPlan(model="model", sft="train.jsonl", validation="validation.jsonl",
        cpt="corpus.md", evaluation="evaluation.jsonl", output="output")


@pytest.mark.parametrize("epochs", [0, .5, 1.1, 2, True])
def test_training_budget_cannot_repeat_or_silently_skip_the_curriculum(epochs):
    with pytest.raises(ValueError, match="one_pass"):
        replace(plan(), epochs=epochs)


def test_question_overlap_is_rejected_before_model_training():
    with pytest.raises(ValueError, match="question_leakage"):
        assert_disjoint_questions(["What is attention?"], ["Other question"], [" what IS attention? "])


def test_supervision_contains_the_end_marker_and_masks_the_complete_prompt():
    example = {"input_ids": [5, 6, 7, 8, 9, 10], "attention_mask": [1] * 6,
               "labels": [-100, -100, 7, 8, 9, 10]}
    assert validate_supervision(example, eos_token_id=9, trailing_whitespace_ids=(10,))["supervised_tokens"] == 4
    with pytest.raises(ValueError, match="incomplete_supervision"):
        validate_supervision({**example, "labels": [-100, -100, 7, 8, -100, 10]},
                             eos_token_id=9, trailing_whitespace_ids=(10,))


class Port:
    def __init__(self, *, passed=True, failure=None):
        self.passed, self.failure = passed, failure
        self.calls, self.saved = [], {}

    def prepare(self, plan):
        return {"passed": self.passed}

    def completed_stage(self, stage):
        record = self.saved.get(stage)
        return record if record and record["status"] == "completed" else None

    def execute_stage(self, stage):
        self.calls.append(stage)
        return {"status": "failed" if stage == self.failure else "completed"}

    def commit_stage(self, stage, result):
        self.saved[stage] = result

    def report(self, **values):
        return values


def test_failed_preflight_never_starts_a_model():
    port = Port(passed=False)
    assert RunTrainingValidation(port).execute(plan())["status"] == "blocked"
    assert not port.calls


def test_frozen_completed_stages_are_reused_and_failed_training_stops_progress():
    port = Port(failure="cpt_sft_train")
    app = RunTrainingValidation(port)
    assert app.execute(plan())["status"] == "incomplete"
    assert port.calls == list(STAGES[:-1])
    port.failure = None
    assert app.execute(plan())["status"] == "completed"
    assert port.calls == [*STAGES[:-1], "cpt_sft_train", "cpt_sft_eval"]
