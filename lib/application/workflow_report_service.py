"""Read-only, bounded reports for real workflow runs."""
from __future__ import annotations

from typing import Protocol

from lib.domain.workflow_targets import TARGETS


class WorkflowReportDriver(Protocol):
    def overview(self, run_id: str) -> dict: ...
    def samples(self, run_id: str, target: str, offset: int, limit: int) -> dict: ...
    def html(self, run_id: str, target: str, offset: int, limit: int, language: str) -> str: ...
    def html_snapshot(self, overview: dict, page: dict, language: str) -> str: ...
    def html_embedded_snapshot(self, overview: dict, page: dict, language: str) -> str: ...
    def retry(self, run_id: str, target: str | None = None) -> None: ...


class WorkflowReportApplication:
    """A report is a display snapshot, never permission to publish a dataset."""

    def __init__(self, driver: WorkflowReportDriver):
        self._driver = driver

    def overview(self, run_id: str) -> dict:
        return self._driver.overview(run_id)

    @staticmethod
    def _page(target, offset, limit):
        if target not in TARGETS:
            raise ValueError("unknown_training_target")
        return max(0, int(offset)), max(1, min(50, int(limit)))

    def samples(self, run_id: str, target: str, offset: int = 0, limit: int = 12) -> dict:
        offset, limit = self._page(target, offset, limit)
        return self._driver.samples(run_id, target, offset, limit)

    def html(self, run_id: str, target: str, offset: int = 0, limit: int = 12,
             language: str = "zh") -> str:
        offset, limit = self._page(target, offset, limit)
        if language not in {"zh", "en"}:
            raise ValueError("unsupported_report_language")
        return self._driver.html(run_id, target, offset, limit, language)

    def html_snapshot(self, overview: dict, page: dict, language: str = "zh") -> str:
        """Render already-read display snapshots without repeating disk reads."""
        if language not in {"zh", "en"}:
            raise ValueError("unsupported_report_language")
        return self._driver.html_snapshot(overview, page, language)

    def retry(self, run_id: str, target: str | None = None) -> None:
        """Retry disposable report preparation; never retry generation."""
        if target is not None and target not in TARGETS:
            raise ValueError("unknown_training_target")
        self._driver.retry(run_id, target)

    def html_embedded_snapshot(self, overview: dict, page: dict, language: str = "zh") -> str:
        """Embed the same complete report in its parent's natural page flow."""
        if language not in {"zh", "en"}:
            raise ValueError("unsupported_report_language")
        return self._driver.html_embedded_snapshot(overview, page, language)
