"""Human Agent-trace review, separate from execution and dataset delivery."""
from __future__ import annotations
from typing import Any

from lib.contexts.review.application.ports import AgentReviewPort


class AgentReviewApplication:
    def __init__(self, driver: AgentReviewPort):
        self._driver = driver

    def agent_review_queue(self, run_id: str, *, kind: str = "positive", offset: int = 0,
                           limit: int = 5, decision: str | None = None) -> dict:
        """Read a bounded, verified Agent trace queue for this workflow workspace."""
        return self._driver.agent_review_queue(run_id, kind=kind, offset=offset,
                                               limit=limit, decision=decision)

    def agent_review_decide(self, run_id: str, candidate_id: str, **decision: Any) -> dict:
        """Persist a review decision against the exact Agent trace and evidence version."""
        return self._driver.agent_review_decide(run_id, candidate_id, **decision)
