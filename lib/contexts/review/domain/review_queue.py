"""Shared status filters and page limits for training-data review queues."""
from __future__ import annotations

DECISIONS = frozenset({"approved", "rejected", "skipped", "pending"})


def review_query(offset: int, limit: int, decision: str | None) -> tuple[int, int, str | None]:
    if decision is not None and decision not in DECISIONS:
        raise ValueError("invalid_review_filter")
    return max(0, int(offset)), max(1, min(int(limit), 100)), decision
