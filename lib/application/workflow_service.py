"""Training-workflow use cases. Depends only on the inward-facing driver port."""
from __future__ import annotations

from typing import Any

from lib.application.workflow_ports import WorkflowDriver
from lib.domain.workflow_targets import TARGETS


class WorkflowApplication:
    """Use cases consumed by the console and command-line presentation layers."""

    def __init__(self, driver: WorkflowDriver):
        self._driver = driver

    def create_run(self, **recipe: Any) -> str:
        return self._driver.create(**recipe)

    def execute(self, run_id: str) -> dict:
        return self._driver.execute(run_id)

    def resume(self, run_id: str) -> dict:
        return self._driver.resume(run_id)

    def list_runs(self) -> list[dict]:
        return self._driver.list_runs()

    def state(self, run_id: str) -> dict:
        return self._driver.state(run_id)

    def recipe(self, run_id: str) -> dict:
        return self._driver.recipe(run_id)

    def is_active(self, run_id: str) -> bool:
        return self._driver.is_active(run_id)

    def cancel(self, run_id: str) -> None:
        self._driver.cancel(run_id)

    def artifact_preview(self, run_id: str, target: str, limit: int = 3) -> list[dict]:
        if target not in TARGETS and target != "agent_negative":
            raise ValueError("unknown_training_target")
        return self._driver.artifact_preview(run_id, target, max(0, min(int(limit), 100)))

    def quarantined_inputs(self, run_id: str) -> list[dict]:
        return self._driver.quarantined_inputs(run_id)

    def bundle(self, run_id: str) -> bytes:
        return self._driver.bundle(run_id)

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

    def list_releases(self) -> list[dict]:
        """List local versions in the output workspace with integrity status."""
        return self._driver.list_releases()

    def release_file(self, release_id: str, filename: str) -> bytes:
        """Read a release file only after checking its current manifest and digest."""
        return self._driver.release_file(release_id, filename)

    def source_files(self, workspace_id: str, suffixes: frozenset[str], limit: int = 500) -> list[dict]:
        return self._driver.source_files(workspace_id, suffixes, max(0, min(int(limit), 5000)))

    def reviewable_artifacts(self) -> list[str]:
        """Return completed, integrity-checked SFT files for the review queue import."""
        return self._driver.reviewable_artifacts()
