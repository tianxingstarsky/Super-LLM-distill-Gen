"""Run a frozen, optional reinforcement-learning comparison through durable ports."""
from typing import Protocol


GRPO_STAGES = ("baseline", "grpo_train", "grpo_eval")


class GrpoValidationPort(Protocol):
    def prepare(self, plan) -> dict: ...
    def completed_stage(self, stage: str) -> dict | None: ...
    def execute_stage(self, stage: str) -> dict: ...
    def commit_stage(self, stage: str, receipt: dict) -> None: ...
    def report(self, *, status: str, issue: str | None = None) -> dict: ...


class RunGrpoValidation:
    """Commit baseline, one-pass policy optimization, and held-out evaluation."""

    def __init__(self, port: GrpoValidationPort):
        self.port = port

    def execute(self, plan, *, action="preflight"):
        if action not in {"preflight", "baseline", "run", "report"}:
            raise ValueError("grpo_unknown_action")
        prepared = self.port.prepare(plan)
        if not prepared["passed"]:
            return self.port.report(status="blocked", issue="grpo_preflight_failed")
        if action in {"preflight", "report"}:
            return self.port.report(status="preflight_passed" if action == "preflight" else "recorded_results")
        for stage in GRPO_STAGES[:1] if action == "baseline" else GRPO_STAGES:
            if self.port.completed_stage(stage) is not None:
                continue
            self.port.report(status="running", issue=stage)
            try:
                receipt = self.port.execute_stage(stage)
                self.port.commit_stage(stage, receipt)
            except Exception:
                self.port.report(status="failed", issue=stage)
                raise
            if receipt.get("status") != "completed":
                return self.port.report(status="incomplete", issue=stage)
        return self.port.report(status="baseline_completed" if action == "baseline" else "completed")
