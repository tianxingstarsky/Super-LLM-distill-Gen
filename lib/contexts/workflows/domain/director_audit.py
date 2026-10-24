"""Delivery counters for an enabled director, including runs with no usable input."""
from __future__ import annotations

from copy import deepcopy
from collections.abc import Mapping


def director_delivery_audit(audit: Mapping, type_weights: Mapping) -> dict:
    """Keep recorded counters intact; an unstarted director has zero coverage.

    An empty snapshot is normal when parsing or cleaning rejects every source.
    A partially recorded snapshot is not: publishing guessed counters would hide
    a broken execution receipt.
    """
    if not isinstance(audit, Mapping):
        raise ValueError("director_audit_incomplete")
    if not audit:
        return {
            "batches_planned": 0,
            "coverage": {
                qa_type: {key: 0 for key in ("planned", "assigned", "accepted", "rejected")}
                for qa_type in type_weights
            },
        }
    if (not isinstance(audit.get("coverage"), Mapping)
            or type(audit.get("batches_planned")) is not int
            or audit["batches_planned"] < 0):
        raise ValueError("director_audit_incomplete")
    return deepcopy(dict(audit))
