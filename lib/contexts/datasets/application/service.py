"""Dataset versions, verified previews, distributions and delivery use cases."""
from __future__ import annotations

from lib.contexts.datasets.application.ports import DatasetPort
from lib.contexts.workflows.contracts import TARGETS


class DatasetApplication:
    def __init__(self, driver: DatasetPort):
        self._driver = driver

    def task_runs(self) -> list[dict]:
        return self._driver.task_runs()

    def state(self, run_id: str) -> dict:
        return self._driver.state(run_id)

    def artifact_preview(self, run_id: str, target: str, limit: int = 3, offset: int = 0) -> list[dict]:
        if target not in TARGETS and target != "agent_negative":
            raise ValueError("unknown_training_target")
        return self._driver.artifact_preview(run_id, target, max(0, min(int(limit), 100)), max(0, int(offset)))

    def bundle(self, run_id: str) -> bytes:
        return self._driver.bundle(run_id)

    def prepare_bundle(self, run_id: str) -> dict:
        return self._driver.prepare_bundle(run_id)

    def prepared_bundle(self, run_id: str) -> dict | None:
        return self._driver.prepared_bundle(run_id)

    def start_bundle(self, run_id: str) -> dict:
        return self._driver.start_bundle(run_id)

    def bundle_job(self, run_id: str) -> dict | None:
        return self._driver.bundle_job(run_id)

    def cancel_bundle(self, run_id: str) -> dict | None:
        return self._driver.cancel_bundle(run_id)

    def artifact_file(self, run_id: str, filename: str) -> bytes:
        return self._driver.artifact_file(run_id, filename)

    def package_inventory(self, run_id: str) -> dict:
        return self._driver.package_inventory(run_id)

    def quality_report(self, run_id: str) -> dict:
        """Return the content report only after verifying its artifact manifest."""
        return self._driver.quality_report(run_id)

    def package_contents(self, run_id: str) -> dict:
        """Return inventory and quality evidence from one verified manifest read."""
        return self._driver.package_contents(run_id)

    def artifact_location(self, run_id: str) -> str:
        return self._driver.artifact_location(run_id)

    def sample_statistics(self, run_id: str) -> dict:
        """Read version-bound native sample distributions and actual file inventory."""
        return self._driver.sample_statistics(run_id)

    def start_sample_statistics(self, run_id: str) -> dict:
        return self._driver.start_sample_statistics(run_id)

    def cancel_sample_statistics(self, run_id: str) -> dict | None:
        return self._driver.cancel_sample_statistics(run_id)

    def list_releases(self) -> list[dict]:
        """List local versions in the output workspace with integrity status."""
        return self._driver.list_releases()

    def release_file(self, release_id: str, filename: str) -> bytes:
        """Read a release file only after checking its current manifest and digest."""
        return self._driver.release_file(release_id, filename)

    def reviewable_artifacts(self) -> list[str]:
        """Return completed, integrity-checked SFT files for the review queue import."""
        return self._driver.reviewable_artifacts()
