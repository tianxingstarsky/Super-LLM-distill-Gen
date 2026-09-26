"""Use cases for reviewing preference pairs and releasing approved data."""
from __future__ import annotations

from datetime import datetime, timezone

from lib.application.preference_review_ports import PreferenceReviewDriver
from lib.domain.review_queue import review_query
from lib.domain.preference_review import review_record, validate_pair


class PreferenceReviewApplication:
    def __init__(self, driver: PreferenceReviewDriver):
        self._driver = driver
        self.target = getattr(driver, "target", "dpo")

    def reviewable_runs(self) -> list[dict]:
        return self._driver.reviewable_runs()

    def queue(self, run_id: str, *, offset: int = 0, limit: int = 20,
              decision: str | None = None) -> dict:
        offset, limit, decision = review_query(offset, limit, decision)
        return self._driver.queue(run_id, offset=offset, limit=limit, decision=decision)

    def decide(self, run_id: str, pair_id: str, *, decision: str, reviewer: str,
               expected_hash: str, reason: str = "", chosen: str | None = None,
               rejected: str | None = None) -> dict:
        pair = validate_pair(self._driver.pair(run_id, pair_id))
        record = review_record(pair, decision=decision, reviewer=reviewer,
                               reviewed_at=datetime.now(timezone.utc).isoformat(),
                               reason=reason, chosen=chosen, rejected=rejected,
                               target=self.target)
        if record["source_hash"] != expected_hash or record["pair_id"] != pair_id:
            raise ValueError("stale_preference_pair_refresh_required")
        return self._driver.record_decision(run_id, expected_hash, record)

    def release(self, run_id: str) -> bytes:
        return self._driver.release(run_id)
