"""Coordinate a local comparison through durable stages and a training port."""
from typing import Protocol
from ..domain.validation import STAGES, TrainingValidationPlan


class TrainingValidationPort(Protocol):
    def prepare(self, plan: TrainingValidationPlan) -> dict: ...
    def completed_stage(self, stage: str) -> dict | None: ...
    def execute_stage(self, stage: str) -> dict: ...
    def commit_stage(self, stage: str, result: dict) -> None: ...
    def report(self, *, status: str, issue: str | None = None) -> dict: ...


class RunTrainingValidation:
    def __init__(self, port: TrainingValidationPort):
        self.port = port

    def execute(self, plan: TrainingValidationPlan, *, action="run"):
        if action not in {"preflight", "run", "report"}:
            raise ValueError("training_validation_unknown_action")
        prepared = self.port.prepare(plan)
        if not prepared["passed"]:
            return self.port.report(status="blocked", issue="training_preflight_failed")
        if action == "preflight":
            return self.port.report(status="preflight_passed")
        if action == "report":
            return self.port.report(status="recorded_results")
        for stage in STAGES:
            if self.port.completed_stage(stage) is not None:
                continue
            self.port.report(status="running", issue=stage)
            try:
                result = self.port.execute_stage(stage)
            except Exception:
                self.port.report(status="failed", issue=stage)
                raise
            self.port.commit_stage(stage, result)
            if result.get("status") != "completed":
                return self.port.report(status="incomplete", issue=stage)
        return self.port.report(status="completed")
