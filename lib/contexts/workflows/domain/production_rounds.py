"""Round scheduling and completion decisions over committed production facts.

No directory, SQLite handle, provider or clock belongs to this state. Receipt
and progress dictionaries retain their saved wire shape for historical runs.
"""
from __future__ import annotations
from copy import deepcopy
from dataclasses import dataclass, field
from .production import (production_batch_size, quality_first_production,
    soft_expectation_production, evaluated_quality_stop)
from .targets import PREFERENCE_TARGETS


@dataclass
class ProductionProgress:
    recipe: dict
    config: dict
    production: dict
    receipts: list[dict]
    source_count: int
    deferred: int
    independent_arithmetic: bool
    low_rounds: dict = field(default_factory=dict)
    director_rounds: dict = field(default_factory=dict)

    @property
    def directed_targets(self):
        return set(self.recipe["targets"]) & (PREFERENCE_TARGETS | {"sft", "cot", "multiturn"})

    def may_stop_expansion(self, covered):
        allowed = (not soft_expectation_production(self.config) or not self.recipe["sources"]
                   or covered >= self.source_count)
        return allowed and not (evaluated_quality_stop(self.config) and self.deferred)


@dataclass
class ProductionRoundPlan:
    number: int
    size: int
    generation_size: int
    generation_offset: int
    targets: list[str]


def build_production_state(recipe, config, receipts, *, spent_usd=None):
    counts = {target: sum(item["eligible"].get(target, 0) for item in receipts) for target in recipe["targets"]}
    attempts = {target: sum(item["attempts"].get(target, 0) for item in receipts) for target in config["goals"]}
    state = {"version": 1, "round": len(receipts), "attempted": sum(item["attempted"] for item in receipts),
             "counts": counts, "goals": {target: {"goal": goal, "eligible": counts[target],
                 "remaining": max(0, goal - counts[target]), "attempted": attempts[target], "stop_reason": None}
                 for target, goal in config["goals"].items()}, "status": "running", "stop_reason": None,
             "budget_usd": config["budget_usd"]}
    if spent_usd is not None:
        state["spent_usd"] = spent_usd
    if soft_expectation_production(config):
        state.update(version=2, quantity_policy=config["quantity_policy"],
            yield_history={target: [{"round": receipt["round"], "attempted": receipt["attempts"].get(target, 0),
                "eligible": receipt["eligible"].get(target, 0)} for receipt in receipts[-config["low_acceptance_rounds"]:]]
                for target in config["goals"]})
        evidence = [dict(receipt["director_saturation"], round=receipt["round"])
                    for receipt in receipts if receipt.get("director_saturation")]
        if evidence:
            state["director_saturation_evidence"] = evidence
        if evaluated_quality_stop(config):
            state["quality_stop_version"] = 1
            state["quality_assessment_history"] = [
                {"round": receipt["round"], "targets": deepcopy(receipt.get("quality_assessments", {}))}
                for receipt in receipts[-max(2, config["low_acceptance_rounds"]):]]
            state["invalid_assessment_count"] = sum(
                item.get("unassessed", 0) for receipt in receipts
                for item in receipt.get("quality_assessments", {}).values())
        if not recipe["sources"]:
            state["planning_requested"] = sum(receipt.get("planning", {}).get("requested", receipt["attempted"])
                                               for receipt in receipts)
            exhausted = [dict(receipt["planning"], round=receipt["round"])
                         for receipt in receipts if receipt.get("planning", {}).get("exhausted")]
            if exhausted:
                state["planning_exhaustion"] = exhausted[-1]
        state["below_expectation"] = {target: progress["remaining"] for target, progress in state["goals"].items()
                                      if progress["remaining"]}
    return state


def apply_stop_signals(config, receipt, production, low_rounds, director_rounds,
                             *, may_stop_expansion, directed_targets):
    """Rebuild decisions solely from committed receipts, including resume."""
    evaluated = evaluated_quality_stop(config)
    consecutive = max(2, config["low_acceptance_rounds"]) if evaluated else config["low_acceptance_rounds"]
    saturation = receipt.get("director_saturation", {})
    for target in config["goals"]:
        tried = receipt["attempts"].get(target, 0)
        valid = True
        if evaluated:
            assessment = receipt.get("quality_assessments", {}).get(target, {})
            valid = bool(assessment.get("valid_for_saturation"))
            tried = assessment.get("assessed", 0)
        low_rounds[target] = (low_rounds[target] + 1 if valid and tried and
            receipt["eligible"].get(target, 0) / tried < config["min_acceptance_rate"] else 0)
        if (may_stop_expansion and low_rounds[target] >= consecutive
                and production["goals"][target]["remaining"]):
            production["goals"][target]["stop_reason"] = (
                "diminishing_returns" if soft_expectation_production(config) else "low_acceptance_rate")
        if evaluated:
            director_rounds[target] = (director_rounds[target] + 1 if target in directed_targets
                and saturation.get("valid_assessment") and saturation.get("fraction", 0) >= .8 else 0)
            exhausted = may_stop_expansion and director_rounds[target] >= consecutive
        else:
            exhausted = saturation.get("stop_expansion")
        if (soft_expectation_production(config) and target in directed_targets
                and production["goals"][target]["remaining"] and exhausted):
            production["goals"][target]["stop_reason"] = "director_saturation"


def quality_assessment_valid(row):
    """Only an actual quality outcome may contribute to saturation evidence.

    Parse/provider errors are operational failures. Older records sometimes
    conflated failed shape repairs with quality rejection; remain conservative
    when they do not carry an explicit verdict or failure classification.
    """
    if (row.get("request_error") or row.get("generation_failure_kind") == "invalid_output"
            or row.get("reason") in {"invalid_model_result", "request_retries_exhausted",
                                     "qa_director_plan_invalid_after_repair"}):
        return False
    for name in ("review_repair", "package_review"):
        if isinstance(row.get(name), dict) and row[name].get("error"):
            return False
    if row.get("status") in {"eligible", "duplicate", "surplus"}:
        return True
    if row.get("reason") in {"source_exhausted", "no_new_grounded_scenario"}:
        return True
    if row.get("generation_failure_kind") == "quality":
        return True
    review = row.get("review_repair")
    checks = [row.get("judge"), row.get("checks"),
              review.get("verdict") if isinstance(review, dict) else None]
    from lib.domain.workflow_quality import verdict
    for check in checks:
        for value in check if isinstance(check, list) else [check]:
            try:
                verdict(value)
            except ValueError:
                continue
            return True
    return False

def restore_stop_signals(progress):
    progress.low_rounds = {target: 0 for target in progress.config["goals"]}
    progress.director_rounds = {target: 0 for target in progress.config["goals"]}
    covered = 0
    for receipt in progress.receipts:
        covered += receipt.get("generation_attempted", receipt["attempted"])
        apply_stop_signals(progress.config, receipt, progress.production,
            progress.low_rounds, progress.director_rounds,
            may_stop_expansion=progress.may_stop_expansion(covered),
            directed_targets=progress.directed_targets)


def plan_next_round(progress):
    recipe, config = progress.recipe, progress.config
    production, receipts = progress.production, progress.receipts
    quality_first = quality_first_production(config)
    soft = soft_expectation_production(config)
    reached_reason = "expectation_reached" if soft else "goal_reached"
    planning_stop = production.get("planning_exhaustion", {}).get("stop_reason")
    math_progress = production["goals"].get("gsm8k", {})
    math_pending = (progress.independent_arithmetic and math_progress.get("remaining", 0)
                    and not math_progress.get("stop_reason"))
    if planning_stop and not math_pending:
        stop = planning_stop
        return stop
    if planning_stop:
        for target, goal in production["goals"].items():
            if target != "gsm8k" and goal["remaining"]:
                goal["stop_reason"] = planning_stop
    active = [target for target in recipe["targets"] if
        (target in config["goals"] and production["goals"][target]["remaining"] > 0
         and not production["goals"][target]["stop_reason"])
        or (target not in config["goals"] and not receipts)]
    if not active:
        stop = reached_reason if all(not item["remaining"] for item in production["goals"].values()) else "source_exhausted"
        if soft and recipe["sources"] and not config["goals"]:
            stop = "source_limit_reached" if progress.deferred else "source_coverage_complete"
        if stop == "source_exhausted" and any(item["stop_reason"] == "source_limit_reached"
                                              for item in production["goals"].values()):
            stop = "source_limit_reached"
        return stop
    size = production_batch_size(config, production["counts"], production["attempted"], len(receipts),
        planning_requested=production.get("planning_requested") if quality_first else None)
    if not size and receipts:
        stop = ("max_attempts" if production["attempted"] >= config["max_attempts"] else
                "max_rounds" if len(receipts) >= config["max_rounds"] else
                "candidate_budget_reached" if quality_first else "max_rounds")
        return stop
    if not size:
        size = min(progress.source_count or (recipe.get("sample_count") or recipe["tasks"]), config["round_size"])
    generation_size, generation_offset = size, production["attempted"]
    if progress.independent_arithmetic:
        # Arithmetic's large delivery window must never expand
        # paid generation. The other targets share only their own
        # candidate window and their own contiguous source offset.
        generation_offset = sum(receipt["generation_attempted"] for receipt in receipts)
        remaining_generation = []
        for target in list(active):
            if target == "gsm8k" or (recipe["sources"] and target in {"cpt", "agent"}):
                continue
            goal = production["goals"].get(target)
            if goal is not None:
                remaining = (max(0, goal["goal"] - goal["attempted"])
                             if quality_first else goal["remaining"])
                if not remaining and quality_first:
                    goal["stop_reason"] = "candidate_budget_reached"
                    active.remove(target)
            else:
                remaining = recipe.get("sample_count") or recipe["tasks"]
            remaining_generation.append(remaining)
        generation_size = min(size, max(remaining_generation, default=0))
    return ProductionRoundPlan(len(receipts) + 1, size, generation_size, generation_offset, active)


def check_round_availability(progress, plan, *, ready_count, source_count, arithmetic_count, arithmetic_stop):
    recipe, config = progress.recipe, progress.config
    production, receipts, active = progress.production, progress.receipts, plan.targets
    soft, quality_first = soft_expectation_production(config), quality_first_production(config)
    for target in ("cpt", "agent"):
        if recipe["sources"] and receipts and target in active:
            active.remove(target)
            if target in production["goals"]:
                production["goals"][target]["stop_reason"] = (
                    "source_limit_reached" if progress.deferred else
                    "source_coverage_complete" if soft else "source_exhausted")
    if not active:
        stop = ("arithmetic_space_exhausted" if arithmetic_stop == "arithmetic_space_exhausted" else
                "source_limit_reached" if any(item["stop_reason"] == "source_limit_reached"
                                             for item in production["goals"].values()) else
                "source_coverage_complete" if soft and recipe["sources"] else "source_exhausted")
        return stop
    if not ready_count and not source_count and not arithmetic_count:
        if recipe["sources"]:
            stop = ("source_limit_reached" if progress.deferred else
                    "source_coverage_complete" if quality_first else "source_exhausted")
            return stop
    if not ready_count and not source_count and arithmetic_count and recipe["sources"]:
        for target in list(active):
            if target != "gsm8k":
                active.remove(target)
                if target in production["goals"]:
                    production["goals"][target]["stop_reason"] = "source_exhausted"
    return None


def advance_round(progress, previous):
    production, config, receipts = progress.production, progress.config, progress.receipts
    covered = (sum(receipt["generation_attempted"] for receipt in receipts)
               if progress.independent_arithmetic else production["attempted"])
    for target in config["goals"]:
        if previous["goals"][target]["stop_reason"]:
            production["goals"][target]["stop_reason"] = previous["goals"][target]["stop_reason"]
    apply_stop_signals(config, receipts[-1], production, progress.low_rounds, progress.director_rounds,
        may_stop_expansion=progress.may_stop_expansion(covered), directed_targets=progress.directed_targets)
