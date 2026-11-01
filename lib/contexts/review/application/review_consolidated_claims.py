"""Review a full turn once, then clarify only explicitly disputed claims."""
from typing import Protocol

from ..domain.consolidated_claim_review import focused_review_request, merge_consolidated_claim_reviews


class ConsolidatedReviewPort(Protocol):
    def check_cancel(self) -> None: ...
    def review(self, request: dict) -> tuple[dict, dict]: ...


class ReviewConsolidatedClaims:
    """Keep full turn context and durable receipts without an inventory pass."""

    def __init__(self, port: ConsolidatedReviewPort):
        self.port = port

    def execute(self, payload: dict, requests: list[dict], audit: dict) -> dict:
        merged = []
        for request in requests:
            self.port.check_cancel()
            initial, checkpoint = self.port.review(request)
            recorded = {"review": initial, "checkpoint": checkpoint}
            audit["assessments"].append(recorded)
            reviewed = {"review": initial}
            focused = focused_review_request(initial)
            if focused is not None:
                # One bounded clarification for concrete findings. It sees the
                # original prefix, not future turns or invented missing facts.
                self.port.check_cancel()
                clarified, checkpoint = self.port.review(focused)
                recorded["focused"] = {"review": clarified, "checkpoint": checkpoint}
                reviewed["focused"] = clarified
            merged.append(reviewed)
        check = merge_consolidated_claim_reviews(payload, merged)
        audit.update(status="complete", derived_verdict=check)
        return check
