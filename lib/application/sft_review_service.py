"""Use cases for review and release of SFT conversation samples."""
from __future__ import annotations

from lib.application.sft_review_ports import SftReviewDriver
from lib.domain.review_queue import review_query
from lib.domain.sft_review import sft_review_record, validate_sft_record


class SftReviewApplication:
    def __init__(self, driver: SftReviewDriver):
        self._driver = driver

    def reviewable_runs(self) -> list[dict]:
        return self._driver.reviewable_runs()

    def queue(self, run_id: str, *, offset: int = 0, limit: int = 20,
              decision: str | None = None) -> dict:
        offset, limit, decision = review_query(offset, limit, decision)
        return self._driver.queue(run_id, offset=offset, limit=limit, decision=decision)

    def decide(self, run_id: str, sample_id: str, *, decision: str, reviewer: str,
               expected_hash: str, reason: str = "", candidate: dict | None = None) -> dict:
        row = validate_sft_record(self._driver.row(run_id, sample_id))
        record = sft_review_record(row, decision=decision, reviewer=reviewer,
                                   reason=reason, candidate=candidate)
        if record["sample_id"] != sample_id or record["source_hash"] != expected_hash:
            raise ValueError("stale_sft_sample_refresh_required")
        return self._driver.record_decision(run_id, expected_hash, record)

    def release(self, run_id: str) -> bytes:
        return self._driver.release(run_id)

    def prepare_release(self, run_id: str) -> dict:
        return self._driver.prepare_release(run_id)

    def release_archive(self, run_id: str, release_id: str, expected_hash: str):
        return self._driver.release_archive(run_id, release_id, expected_hash)

    def start_release(self, run_id: str) -> dict:
        return self._driver.start_release(run_id)

    def release_job(self, run_id: str) -> dict | None:
        return self._driver.release_job(run_id)

    def cancel_release(self, run_id: str) -> dict | None:
        return self._driver.cancel_release(run_id)
