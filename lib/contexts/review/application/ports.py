"""Consumer-owned operations; no storage or SDK dependencies."""
from __future__ import annotations
from typing import Any, Protocol


class AgentReviewPort(Protocol):
    def agent_review_queue(self, run_id: str, *, kind: str, offset: int,
                           limit: int, decision: str | None) -> dict: ...
    def agent_review_decide(self, run_id: str, candidate_id: str, **decision: Any) -> dict: ...
