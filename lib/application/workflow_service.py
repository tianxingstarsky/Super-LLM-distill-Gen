"""Compatibility facade for existing console/CLI integrations.

New dataset/review consumers use their own context APIs. Composition happens in
lib.bootstrap; this facade adds no business rules or concrete adapters.
"""
from __future__ import annotations
from typing import Any

from lib.contexts.workflows.api import WorkflowRunApplication, WorkflowRunPort
from lib.contexts.datasets.api import DatasetApplication
from lib.contexts.review.api import AgentReviewApplication


class WorkflowApplication(WorkflowRunApplication):
    def __init__(self, driver: WorkflowRunPort, *, datasets=None, review=None, reports=None):
        super().__init__(driver)
        self.datasets = datasets if datasets is not None else DatasetApplication(driver)
        self.review = review if review is not None else AgentReviewApplication(driver)
        self.reports = reports

    def artifact_preview(self, run_id: str, target: str, limit: int = 3, offset: int = 0) -> list[dict]:
        return self.datasets.artifact_preview(run_id, target, limit, offset)

    def bundle(self, run_id: str) -> bytes:
        return self.datasets.bundle(run_id)

    def prepare_bundle(self, run_id: str) -> dict:
        return self.datasets.prepare_bundle(run_id)

    def prepared_bundle(self, run_id: str) -> dict | None:
        return self.datasets.prepared_bundle(run_id)

    def start_bundle(self, run_id: str) -> dict:
        return self.datasets.start_bundle(run_id)

    def bundle_job(self, run_id: str) -> dict | None:
        return self.datasets.bundle_job(run_id)

    def cancel_bundle(self, run_id: str) -> dict | None:
        return self.datasets.cancel_bundle(run_id)

    def artifact_file(self, run_id: str, filename: str) -> bytes:
        return self.datasets.artifact_file(run_id, filename)

    def package_inventory(self, run_id: str) -> dict:
        return self.datasets.package_inventory(run_id)

    def quality_report(self, run_id: str) -> dict:
        return self.datasets.quality_report(run_id)

    def package_contents(self, run_id: str) -> dict:
        return self.datasets.package_contents(run_id)

    def artifact_location(self, run_id: str) -> str:
        return self.datasets.artifact_location(run_id)

    def sample_statistics(self, run_id: str) -> dict:
        return self.datasets.sample_statistics(run_id)

    def start_sample_statistics(self, run_id: str) -> dict:
        return self.datasets.start_sample_statistics(run_id)

    def cancel_sample_statistics(self, run_id: str) -> dict | None:
        return self.datasets.cancel_sample_statistics(run_id)

    def list_releases(self) -> list[dict]:
        return self.datasets.list_releases()

    def release_file(self, release_id: str, filename: str) -> bytes:
        return self.datasets.release_file(release_id, filename)

    def reviewable_artifacts(self) -> list[str]:
        return self.datasets.reviewable_artifacts()

    def agent_review_queue(self, run_id: str, *, kind: str = "positive", offset: int = 0,
                           limit: int = 5, decision: str | None = None) -> dict:
        return self.review.agent_review_queue(run_id, kind=kind, offset=offset, limit=limit, decision=decision)

    def agent_review_decide(self, run_id: str, candidate_id: str, **decision: Any) -> dict:
        return self.review.agent_review_decide(run_id, candidate_id, **decision)
