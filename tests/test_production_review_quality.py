"""Per-target, per-round escalation thresholds preserve legacy review behavior."""
from copy import deepcopy

import pytest

from lib.domain.workflow_package_review import validate_package_review
from lib.infrastructure.production_review_quality import package_review_escalation


def decide(*, sampled=10, failed=1, candidates=100, production=True, **config):
    return package_review_escalation({"enabled": True, "mode": "sample", **config},
        sampled=sampled, failed=failed, candidates=candidates, production=production)


def test_production_defaults_to_any_failure_and_reports_actual_scope():
    assert decide() == {
        "escalated": True, "scope": "target_current_round",
        "trigger": "sample_failure_threshold", "initial_sampled": 10,
        "sample_rejected": 1, "sample_failure_percent": 10.0,
        "threshold_percent": 0.0, "additional_reviews": 90,
        "reuse_completed_reviews": True,
    }


def test_threshold_uses_reviewed_sample_and_includes_exact_boundary():
    assert decide(escalate_failure_percent=10.) is not None
    assert decide(escalate_failure_percent=10.01) is None
    assert decide(sampled=3, failed=1, candidates=100_000,
                  escalate_failure_percent=30.) is not None


@pytest.mark.parametrize("options", [
    {"production": False}, {"failed": 0}, {"sampled": 0, "failed": 0},
    {"sampled": 100}, {"mode": "all"}, {"enabled": False},
])
def test_no_extra_calls_when_legacy_passed_disabled_or_already_all(options):
    assert decide(**options) is None


def test_explicit_threshold_normalizes_but_old_serialized_shape_stays_identical():
    old = {"enabled": True, "mode": "sample", "sample_percent": 1.0,
           "max_samples_per_target": 1000}
    assert validate_package_review(old) == old
    new = {**old, "escalate_failure_percent": 3}
    before = deepcopy(new)
    assert validate_package_review(new) == {**old, "escalate_failure_percent": 3.0}
    assert new == before


@pytest.mark.parametrize("threshold", [True, None, "5", -1, 100.1, float("nan"), float("inf")])
def test_invalid_threshold_is_rejected_before_any_model_call(threshold):
    with pytest.raises(ValueError, match="invalid_package_review"):
        decide(escalate_failure_percent=threshold)


@pytest.mark.parametrize("sampled,failed,candidates", [(1, 2, 3), (-1, 0, 10),
    (True, 0, 10), (10, 1, 9), (1, -1, 10)])
def test_inconsistent_coverage_is_fatal_integrity_failure(sampled, failed, candidates):
    with pytest.raises(ValueError, match="production_review_integrity_error"):
        decide(sampled=sampled, failed=failed, candidates=candidates)
