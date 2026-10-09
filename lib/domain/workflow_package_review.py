"""Optional, bounded AI review of final training records before packaging."""
from __future__ import annotations

from math import isfinite


MAX_PACKAGE_REVIEW_SAMPLES = 10_000
MIN_PACKAGE_REVIEW_PERCENT = 0.01
DEFAULT_PACKAGE_REVIEW_PERCENT = 1.0
DEFAULT_PACKAGE_REVIEW_LIMIT = 1_000
DEFAULT_ESCALATE_FAILURE_PERCENT = 0.0


def validate_package_review(value: dict | None) -> dict:
    """Keep earlier runs unchanged; normalize only enabled review settings."""
    if value is None:
        return {"enabled": False}
    if (not isinstance(value, dict)
            or set(value) - {"enabled", "mode", "sample_percent", "max_samples_per_target",
                             "escalate_failure_percent", "node"}):
        raise ValueError("invalid_package_review")
    if "node" in value and value["node"] != "jev":
        raise ValueError("invalid_package_review")
    routing = {"node": "jev"} if "node" in value else {}
    enabled = value.get("enabled", False)
    mode = value.get("mode", "sample")
    percent = value.get("sample_percent", DEFAULT_PACKAGE_REVIEW_PERCENT)
    limit = value.get("max_samples_per_target", DEFAULT_PACKAGE_REVIEW_LIMIT)
    escalation = value.get("escalate_failure_percent", DEFAULT_ESCALATE_FAILURE_PERCENT)
    if (type(enabled) is not bool or not isinstance(mode, str) or mode not in {"sample", "all"}
            or type(percent) not in {int, float}
            or not MIN_PACKAGE_REVIEW_PERCENT <= percent <= 100 or not isfinite(percent)
            or type(limit) is not int or not 1 <= limit <= MAX_PACKAGE_REVIEW_SAMPLES
            or type(escalation) not in {int, float} or not isfinite(escalation)
            or not 0 <= escalation <= 100):
        raise ValueError("invalid_package_review")
    if not enabled:
        return {"enabled": False, **routing}
    result = {"enabled": True, "mode": mode, "sample_percent": float(percent),
              "max_samples_per_target": limit, **routing}
    # Preserve the serialized shape/hash of older pinned recipes. New
    # production runs interpret absence as escalation on any failed sample.
    if "escalate_failure_percent" in value:
        result["escalate_failure_percent"] = float(escalation)
    return result


def package_review_stage(value: dict | None) -> str:
    """The absent routing marker retains historical package checkpoints."""
    return "jev" if validate_package_review(value).get("node") == "jev" else "package"
