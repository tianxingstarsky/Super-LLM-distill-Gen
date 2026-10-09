"""Explainable per-round escalation after bounded package AI sampling.

This module decides coverage only. Actual model calls continue through the
workflow's normal checkpoints, cancellation checks and both budget ledgers.
"""
from __future__ import annotations

from lib.domain.workflow_package_review import (
    DEFAULT_ESCALATE_FAILURE_PERCENT, validate_package_review,
)


def package_review_escalation(config: dict, *, sampled: int, failed: int,
                             candidates: int, production: bool) -> dict | None:
    """Return a safe report iff this target/round needs full AI review.

    Zero threshold means any failure; a positive threshold compares failures
    with completed sample reviews, never with the whole unreviewed population.
    Sampling is an operational trigger, not a confidence bound or proof.
    """
    if (type(sampled) is not int or type(failed) is not int or type(candidates) is not int
            or not 0 <= failed <= sampled <= candidates):
        raise ValueError("production_review_integrity_error")
    review = validate_package_review(config)
    if (not production or not review["enabled"] or review["mode"] != "sample"
            or not failed or sampled == candidates):
        return None
    threshold = review.get("escalate_failure_percent", DEFAULT_ESCALATE_FAILURE_PERCENT)
    if 100 * failed < threshold * sampled:
        return None
    return {
        "escalated": True,
        "scope": "target_current_round",
        "trigger": "sample_failure_threshold",
        "initial_sampled": sampled,
        "sample_rejected": failed,
        "sample_failure_percent": round(100 * failed / sampled, 4),
        "threshold_percent": threshold,
        "additional_reviews": candidates - sampled,
        "reuse_completed_reviews": True,
    }
