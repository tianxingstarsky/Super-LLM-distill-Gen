"""Training-workflow use cases. Depends only on the inward-facing driver port."""
from __future__ import annotations

from typing import Any

from lib.application.workflow_ports import WorkflowDriver
from lib.domain.workflow_targets import TARGETS
from lib.domain.workflow_creation import validate_creation
from lib.domain.web_research import validate_web_research


class WorkflowApplication:
    """Use cases consumed by the console and command-line presentation layers."""

    def __init__(self, driver: WorkflowDriver):
        self._driver = driver

    def create_run(self, **recipe: Any) -> str:
        fields = {key: recipe[key] for key in (
            "targets", "max_units", "chunk_chars", "tasks", "sample_count", "concurrency",
            "batch_size", "node_models", "conversation_turns", "brief",
            "agent_replay_mode", "evaluation_sources", "web_research", "sources") if key in recipe}
        targets, node_models = validate_creation(**fields)
        research = validate_web_research(recipe.get("web_research"), brief=recipe.get("brief", ""),
                                         sources=recipe.get("sources", ()), targets=targets)
        return self._driver.create(**{**recipe, "targets": targets, "node_models": node_models,
                                      "web_research": research})

    def default_sft_output_style(self) -> str:
        """Suggest the current preference when configuring a new SFT run."""
        return self._driver.default_sft_output_style()

    def agent_replay_capabilities(self) -> dict:
        return self._driver.agent_replay_capabilities()

    def web_research_capabilities(self) -> dict:
        return self._driver.web_research_capabilities()

    def web_research_results(self, run_id: str) -> dict | None:
        """Return checked public planning leads for this workspace's run."""
        return self._driver.web_research_results(run_id)

    def check_agent_sandbox(self) -> dict:
        return self._driver.check_agent_sandbox()

    def execute(self, run_id: str) -> dict:
        return self._driver.execute(run_id)

    def resume(self, run_id: str) -> dict:
        return self._driver.resume(run_id)

    def list_runs(self) -> list[dict]:
        return self._driver.list_runs()

    def task_runs(self) -> list[dict]:
        return self._driver.task_runs()

    def state(self, run_id: str) -> dict:
        return self._driver.state(run_id)

    def recipe(self, run_id: str) -> dict:
        return self._driver.recipe(run_id)

    def is_active(self, run_id: str) -> bool:
        return self._driver.is_active(run_id)

    def cancel(self, run_id: str) -> None:
        self._driver.cancel(run_id)

    def artifact_preview(self, run_id: str, target: str, limit: int = 3, offset: int = 0) -> list[dict]:
        if target not in TARGETS and target != "agent_negative":
            raise ValueError("unknown_training_target")
        return self._driver.artifact_preview(run_id, target, max(0, min(int(limit), 100)), max(0, int(offset)))

    def quarantined_inputs(self, run_id: str, limit: int = 100) -> list[dict]:
        return self._driver.quarantined_inputs(run_id, max(0, min(int(limit), 100)))

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

    def agent_review_queue(self, run_id: str, *, kind: str = "positive", offset: int = 0,
                           limit: int = 5, decision: str | None = None) -> dict:
        """Read a bounded, verified Agent trace queue for this workflow workspace."""
        return self._driver.agent_review_queue(run_id, kind=kind, offset=offset,
                                               limit=limit, decision=decision)

    def agent_review_decide(self, run_id: str, candidate_id: str, **decision: Any) -> dict:
        """Persist a review decision against the exact Agent trace and evidence version."""
        return self._driver.agent_review_decide(run_id, candidate_id, **decision)
