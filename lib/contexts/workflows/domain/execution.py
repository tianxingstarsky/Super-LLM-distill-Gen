"""Execution decisions that do not depend on files, workers or model clients."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from .targets import PREFERENCE_TARGETS


class WorkflowCancelled(Exception):
    """Cooperative cancellation, distinct from a failed model request."""


ExecutionRoute = Literal["batch", "production", "repair"]


def execution_route(*, repair_requested: bool, production_enabled: bool) -> ExecutionRoute:
    # A repair run must never regenerate its original source batch.
    if repair_requested:
        return "repair"
    return "production" if production_enabled else "batch"


@dataclass(frozen=True)
class BatchGenerationPlan:
    """Selected exports and their implicit generation dependencies."""

    targets: frozenset[str]
    director_enabled: bool
    reading_session: bool = False

    @property
    def needs_sft(self) -> bool:
        return bool(self.targets & (PREFERENCE_TARGETS | {"sft", "cot"}))

    @property
    def needs_candidates(self) -> bool:
        return self.needs_sft or "multiturn" in self.targets

    @property
    def needs_preferences(self) -> bool:
        return bool(self.targets & PREFERENCE_TARGETS)

    @property
    def can_plan_from_brief(self) -> bool:
        return bool(self.targets & ({"cpt", "multiturn", "sft", "cot"} | PREFERENCE_TARGETS))


@dataclass(frozen=True)
class TargetOutcome:
    target: str
    eligible: int
    negative: int = 0
    review_rejected: int = 0


@dataclass(frozen=True)
class BatchOutcome:
    """Completion facts; a successful export can still need human attention."""

    targets: tuple[TargetOutcome, ...]
    quarantined_targets: frozenset[str]
    source_quarantined: int
    input_quarantined: int
    deferred_inputs: int
    arithmetic_exhausted_cleanly: bool

    @property
    def status(self) -> Literal["completed", "needs_attention"]:
        for outcome in self.targets:
            empty_is_expected = outcome.target == "gsm8k" and self.arithmetic_exhausted_cleanly
            if ((not outcome.eligible and not empty_is_expected)
                    or outcome.negative or outcome.review_rejected):
                return "needs_attention"
        if (self.quarantined_targets or self.source_quarantined > 0
                or self.input_quarantined > 0 or self.deferred_inputs > 0):
            return "needs_attention"
        return "completed"
