"""Bounded delivery goals, independent of candidate request batch sizes."""
from __future__ import annotations

import math

from lib.domain.workflow_targets import TARGETS

MAX_PRODUCTION_GOAL = 1_000_000
MAX_PRODUCTION_ATTEMPTS = 3_000_000
MAX_PRODUCTION_ROUND_SIZE = 100_000


def validate_production(value, targets):
    if value is None:
        return None
    keys = {"version", "goals", "max_attempts", "max_rounds", "round_size",
            "min_acceptance_rate", "low_acceptance_rounds", "budget_usd", "item_retries"}
    version = value.get("version", 1) if isinstance(value, dict) else None
    if version == 2:
        keys.add("quantity_policy")
    if (not isinstance(value, dict) or set(value) - keys or type(value.get("version", 1)) is not int
            or version not in (1, 2)):
        raise ValueError("invalid_production")
    goals = value.get("goals", {})
    if (not isinstance(goals, dict) or set(goals) - set(targets) or set(goals) - set(TARGETS)
            or any(type(count) is not int or not 1 <= count <= MAX_PRODUCTION_GOAL for count in goals.values())):
        raise ValueError("invalid_production_goals")
    largest = max(goals.values(), default=1)
    policy = value.get("quantity_policy", "quality_first") if version == 2 else None
    if version == 2 and (not isinstance(policy, str) or policy not in {"quality_first", "bounded_replenishment"}):
        raise ValueError("invalid_production")
    quality_first = version == 2 and policy == "quality_first"
    result = {"version": version, "goals": dict(goals),
              "max_attempts": value.get("max_attempts", min(MAX_PRODUCTION_ATTEMPTS, largest * 3)),
              "max_rounds": value.get("max_rounds", 300),
              "round_size": value.get("round_size", min(10_000, largest)),
              "min_acceptance_rate": value.get("min_acceptance_rate", 0.2 if quality_first else 0.01),
              "low_acceptance_rounds": value.get("low_acceptance_rounds", 2 if quality_first else 3),
              "budget_usd": value.get("budget_usd", 0.0),
              "item_retries": value.get("item_retries", 2)}
    if version == 2:
        result["quantity_policy"] = policy
    for key, low, high in (("max_attempts", 1, MAX_PRODUCTION_ATTEMPTS), ("max_rounds", 1, 10_000),
                            ("round_size", 1, MAX_PRODUCTION_ROUND_SIZE), ("low_acceptance_rounds", 1, 100),
                            ("item_retries", 0, 5)):
        if type(result[key]) is not int or not low <= result[key] <= high:
            raise ValueError("invalid_production_limits")
    for key, low, high in (("min_acceptance_rate", 0.0, 1.0), ("budget_usd", 0.0, 1_000_000_000.0)):
        number = result[key]
        if type(number) not in (int, float) or not math.isfinite(number) or not low <= number <= high:
            raise ValueError("invalid_production_limits")
        result[key] = float(number)
    return result


def production_batch_size(config, counts, attempted, round_number, *, planning_requested=None):
    """Select one finite window; a million-row goal never allocates a million rows."""
    if round_number >= config["max_rounds"] or attempted >= config["max_attempts"]:
        return 0
    if quality_first_production(config):
        # A soft expectation allocates an initial candidate window, not a debt
        # to repay after every rejection or duplicate.
        considered = attempted if planning_requested is None else planning_requested
        remaining = max(0, max(config["goals"].values(), default=0) - considered)
    else:
        remaining = max((max(0, goal - counts.get(target, 0))
                         for target, goal in config["goals"].items()), default=0)
    return min(remaining, config["round_size"], config["max_attempts"] - attempted)


def quality_first_production(config):
    return config.get("version") == 2 and config.get("quantity_policy") == "quality_first"


def soft_expectation_production(config):
    return config.get("version") == 2


def production_completion_status(config, counts, selected_targets, *, source_limited=False, attention_required=False):
    """A shortfall is information, not a failure, for new soft expectations."""
    if soft_expectation_production(config):
        return "needs_attention" if source_limited or attention_required or any(counts.get(target, 0) == 0 for target in selected_targets) else "completed"
    unmet = any(counts.get(target, 0) < goal for target, goal in config["goals"].items())
    unavailable = any(counts.get(target, 0) == 0 for target in selected_targets if target not in config["goals"])
    return "needs_attention" if unmet or unavailable else "completed"
