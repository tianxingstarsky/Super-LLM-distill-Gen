"""Use cases for review and release of CPT corpus samples."""
from __future__ import annotations

from lib.application.corpus_review_ports import CorpusReviewDriver
from lib.domain.corpus_review import corpus_review_record, validate_corpus_row


class CorpusReviewApplication:
    def __init__(self, driver: CorpusReviewDriver):
        self._driver = driver

    def reviewable_runs(self) -> list[dict]:
        return self._driver.reviewable_runs()

    def queue(self, run_id: str, *, offset: int = 0, limit: int = 20) -> dict:
        result = self._driver.queue(run_id)
        start, size = max(0, int(offset)), max(1, min(int(limit), 100))
        return {"items": result["items"][start:start + size], "total": result["total"],
                "counts": result["counts"]}

    def decide(self, run_id: str, sample_id: str, *, decision: str, reviewer: str,
               expected_hash: str, reason: str = "", text: str | None = None) -> dict:
        row = validate_corpus_row(self._driver.row(run_id, sample_id))
        record = corpus_review_record(row, decision=decision, reviewer=reviewer,
                                      reason=reason, text=text)
        if record["sample_id"] != sample_id or record["source_hash"] != expected_hash:
            raise ValueError("stale_cpt_sample_refresh_required")
        return self._driver.record_decision(run_id, expected_hash, record)

    def release(self, run_id: str) -> bytes:
        return self._driver.release(run_id)
