"""Validated, optional reasoning choices; absence preserves provider defaults."""
from __future__ import annotations

from copy import deepcopy
import re


REASONING_FORMATS = ("deepseek", "anthropic_adaptive", "anthropic_budget")


def valid_effort(value) -> bool:
    return (isinstance(value, str) and bool(re.fullmatch(r"[a-z][a-z0-9_-]{0,31}", value))
            and value not in {"auto", "none", "off", "disabled", "enabled", "adaptive"})


def effort_levels(value):
    if (not isinstance(value, list) or len(value) > 32
            or not all(valid_effort(item) for item in value)):
        return None
    return list(dict.fromkeys(value))


def validate_reasoning(value, *, max_output_tokens=None) -> dict:
    if value is None:
        return {"mode": "auto"}
    if not isinstance(value, dict) or set(value) - {"mode", "effort", "budget_tokens"}:
        raise ValueError("invalid_model_reasoning")
    mode = value.get("mode", "auto")
    if not isinstance(mode, str) or mode not in {"auto", "enabled", "disabled"}:
        raise ValueError("invalid_model_reasoning")
    if mode != "enabled" and set(value) & {"effort", "budget_tokens"}:
        raise ValueError("invalid_model_reasoning")
    if "effort" in value and not valid_effort(value["effort"]):
        raise ValueError("invalid_model_reasoning_effort")
    if "effort" in value and "budget_tokens" in value:
        raise ValueError("invalid_model_reasoning")
    if "budget_tokens" in value:
        budget = value["budget_tokens"]
        if type(budget) is not int or not 1024 <= budget <= 4_000_000:
            raise ValueError("invalid_model_reasoning_budget")
        if max_output_tokens is not None and budget >= max_output_tokens:
            raise ValueError("model_reasoning_budget_exceeds_output")
    return {"mode": mode, **{key: deepcopy(value[key]) for key in ("effort", "budget_tokens") if key in value}}
