"""Legacy composite adapter. New composition uses the bounded context drivers."""
from pathlib import Path
from lib.contexts.workflows.infrastructure.driver import FilesystemWorkflowRunDriver
from lib.contexts.datasets.infrastructure.driver import FilesystemDatasetDriver
from lib.contexts.review.infrastructure.agent_driver import FilesystemAgentReviewAdapter


class FilesystemWorkflowDriver(FilesystemWorkflowRunDriver):
    def __init__(self, root: Path, output: Path):
        super().__init__(root, output)
        self._datasets = FilesystemDatasetDriver(root, output)
        self._review = FilesystemAgentReviewAdapter(root, output)

    def artifact_preview(self, run_id: str, target: str, limit: int, offset: int = 0) -> list[dict]:
        return self._datasets.artifact_preview(run_id, target, limit, offset)

    def bundle(self, run_id: str) -> bytes:
        return self._datasets.bundle(run_id)

    def prepare_bundle(self, run_id: str) -> dict:
        return self._datasets.prepare_bundle(run_id)

    def prepared_bundle(self, run_id: str) -> dict | None:
        return self._datasets.prepared_bundle(run_id)

    def start_bundle(self, run_id: str) -> dict:
        return self._datasets.start_bundle(run_id)

    def bundle_job(self, run_id: str) -> dict | None:
        return self._datasets.bundle_job(run_id)

    def cancel_bundle(self, run_id: str) -> dict | None:
        return self._datasets.cancel_bundle(run_id)

    def artifact_file(self, run_id: str, filename: str) -> bytes:
        return self._datasets.artifact_file(run_id, filename)

    def package_inventory(self, run_id: str) -> dict:
        return self._datasets.package_inventory(run_id)

    def quality_report(self, run_id: str) -> dict:
        return self._datasets.quality_report(run_id)

    def package_contents(self, run_id: str) -> dict:
        return self._datasets.package_contents(run_id)

    def sample_statistics(self, run_id: str) -> dict:
        return self._datasets.sample_statistics(run_id)

    def start_sample_statistics(self, run_id: str) -> dict:
        return self._datasets.start_sample_statistics(run_id)

    def cancel_sample_statistics(self, run_id: str) -> dict | None:
        return self._datasets.cancel_sample_statistics(run_id)

    def artifact_location(self, run_id: str) -> str:
        return self._datasets.artifact_location(run_id)

    def list_releases(self) -> list[dict]:
        return self._datasets.list_releases()

    def release_file(self, release_id: str, filename: str) -> bytes:
        return self._datasets.release_file(release_id, filename)

    def reviewable_artifacts(self) -> list[str]:
        return self._datasets.reviewable_artifacts()

    def agent_review_queue(self, run_id: str, *, kind: str, offset: int,
                           limit: int, decision: str | None) -> dict:
        return self._review.agent_review_queue(run_id, kind=kind, offset=offset, limit=limit, decision=decision)

    def agent_review_decide(self, run_id: str, candidate_id: str, **decision) -> dict:
        return self._review.agent_review_decide(run_id, candidate_id, **decision)
