"""A failed stage never advances policy training; saved evidence is authenticated."""
import json

import pytest

from lib.contexts.training.api import GRPOValidationPlan, RunGrpoValidation
from lib.contexts.training.infrastructure.grpo_validation import FilesystemGrpoValidation


def test_run_commits_each_stage_and_recovery_does_not_repeat_training():
    class Port:
        def __init__(self):
            self.saved, self.calls = {}, []
        def prepare(self, plan):
            return {"passed": True}
        def completed_stage(self, stage):
            return self.saved.get(stage)
        def execute_stage(self, stage):
            self.calls.append(stage)
            if stage == "grpo_train" and self.calls.count(stage) == 1:
                raise RuntimeError("interrupted")
            return {"status": "completed"}
        def commit_stage(self, stage, receipt):
            self.saved[stage] = receipt
        def report(self, **status):
            return status
    port = Port()
    application = RunGrpoValidation(port)
    plan = GRPOValidationPlan("local-model", "run")
    with pytest.raises(RuntimeError, match="interrupted"):
        application.execute(plan, action="run")
    assert list(port.saved) == ["baseline"]
    assert application.execute(plan, action="run")["status"] == "completed"
    calls = list(port.calls)
    application.execute(plan, action="run")
    assert port.calls == calls == ["baseline", "grpo_train", "grpo_train", "grpo_eval"]


def test_numeric_verification_never_promotes_a_truncated_correct_number(tmp_path, monkeypatch):
    monkeypatch.setattr("lib.contexts.training.infrastructure.grpo_validation.verify_numeric_answers",
        lambda answers, expected: [1.0])
    class Backend:
        def evaluate(self, model, adapter, cases, output, config):
            assert all("expected_answer" not in message for case in cases for message in case["messages"])
            assert [case["messages"][-1]["content"] for case in cases] == ["Compute six times seven."]
            return {"status": "completed", "summary": {}, "cases": [
                {"answer": r"Final answer: \boxed{42}", "reasoning": "6 * 7 = 42", "thinking_closed": True,
                 "ended_with_eos": False, "truncated": True, "expected_answer": "42"}]}
    driver = FilesystemGrpoValidation(Backend())
    driver.plan = GRPOValidationPlan("model", str(tmp_path))
    driver.output = tmp_path
    driver.tasks = {"evaluation": [{"task_id": "held-out", "family": "arithmetic",
        "prompt": "Compute six times seven.", "expected_answer": "42"}]}
    driver.manifest = {"identity": "experiment", "reward_protocol": "exact_final_numeric/v1"}
    receipt = driver.execute_stage("baseline")
    assert receipt["cases"][0]["verification"]["numeric_answer_correct"] is True
    assert receipt["cases"][0]["verification"]["answer_correct"] is False
    assert receipt["cases"][0]["reward"] == 0
    assert receipt["summary"]["accuracy"] == 0


def test_committed_stage_rejects_changed_native_output(tmp_path):
    driver = FilesystemGrpoValidation(None)
    driver.output = tmp_path
    driver.manifest = {"identity": "experiment"}
    directory = tmp_path / "baseline"
    directory.mkdir()
    native = directory / "case-0000.json"
    native.write_text(json.dumps({"raw_output": "original full reasoning"}), encoding="utf-8")
    receipt = {"status": "completed", "cases": []}
    driver.commit_stage("baseline", receipt)
    assert driver.completed_stage("baseline") == receipt
    native.write_text(json.dumps({"raw_output": "changed"}), encoding="utf-8")
    with pytest.raises(ValueError, match="artifact_changed"):
        driver.completed_stage("baseline")


def test_generation_batch_constraints_fail_before_loading_a_model():
    with pytest.raises(ValueError, match="generation_batch_not_divisible"):
        GRPOValidationPlan("model", "run", batch_size=3, num_generations=2, gradient_accumulation=2)
