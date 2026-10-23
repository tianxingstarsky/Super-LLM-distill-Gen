"""Training-workflow use cases. Depends only on the inward-facing driver port."""
from __future__ import annotations

from typing import Any

from lib.contexts.workflows.application.ports import WorkflowRunPort
from lib.contexts.workflows.domain.targets import TARGETS
from lib.contexts.workflows.domain.creation import validate_creation, validate_generation_review
from lib.domain.web_research import validate_web_research
from lib.domain.workflow_generation import validate_node_generation
from lib.domain.reasoning_trim import validate_reasoning_trim
from lib.domain.workflow_node_prompts import validate_node_prompts
from lib.domain.workflow_package_review import validate_package_review
from lib.domain.workflow_qa_director import validate_qa_director
from lib.contexts.workflows.domain.production import validate_production
from lib.domain.cpt_processing import validate_cpt_processing
from lib.domain.agent_experience import validate_agent_experience


class WorkflowRunApplication:
    """Creation, execution, recovery and live run inspection."""

    def __init__(self, driver: WorkflowRunPort):
        self._driver = driver

    def create_run(self, **recipe: Any) -> str:
        fields = {key: recipe[key] for key in (
            "targets", "max_units", "chunk_chars", "tasks", "sample_count", "concurrency",
            "batch_size", "node_models", "conversation_turns", "brief",
            "agent_replay_mode", "evaluation_sources", "web_research", "sources",
            "node_generation", "reasoning_trim", "node_prompts", "package_review", "qa_director", "production",
            "cpt_processing", "review_repair", "repair_inputs", "agent_experience", "reasoning_required") if key in recipe}
        targets, node_models = validate_creation(**fields)
        validate_generation_review(targets, recipe.get("review_repair"), recipe.get("repair_inputs"))
        research = validate_web_research(recipe.get("web_research"), brief=recipe.get("brief", ""),
                                         sources=recipe.get("sources", ()), targets=targets)
        return self._driver.create(**{**recipe, "targets": targets, "node_models": node_models,
                                      "web_research": research,
                                      "node_generation": validate_node_generation(recipe.get("node_generation")),
                                      "node_prompts": validate_node_prompts(recipe.get("node_prompts")),
                                      "agent_experience": validate_agent_experience(recipe.get("agent_experience")),
                                      "package_review": validate_package_review(recipe.get("package_review")),
                                      "qa_director": validate_qa_director(recipe.get("qa_director")),
                                      "production": validate_production(recipe.get("production"), targets),
                                      "reasoning_trim": validate_reasoning_trim(recipe.get("reasoning_trim")),
                                      **({"cpt_processing": validate_cpt_processing(recipe["cpt_processing"])}
                                         if "cpt_processing" in recipe else {})})

    def default_sft_output_style(self) -> str:
        """Suggest the current preference when configuring a new SFT run."""
        return self._driver.default_sft_output_style()

    def agent_replay_capabilities(self) -> dict:
        return self._driver.agent_replay_capabilities()

    def web_research_capabilities(self) -> dict:
        return self._driver.web_research_capabilities()

    def save_web_research_connection(self, api_key: str) -> dict:
        """Save only the local connection, without starting a probe or a run."""
        return self._driver.save_web_research_connection(api_key)

    def check_web_research_connection(self) -> str:
        """Check the optional public search provider without starting a run."""
        return self._driver.check_web_research_connection()

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

    def read_streams(self, run_id: str, *, stage: str | None = None) -> list[dict]:
        """Return durable, bounded live previews, separate from training samples."""
        return self._driver.read_streams(run_id, **({"stage": stage} if stage is not None else {}))

    def read_stream_delta(self, run_id: str, request_id: str, *, offset: int = 0,
                          limit_bytes: int = 65536, stage: str | None = None) -> dict:
        """Read only a selected request's next bounded batch of original deltas."""
        return self._driver.read_stream_delta(run_id, request_id, offset=offset,
                                              limit_bytes=limit_bytes, stage=stage)

    def recipe(self, run_id: str) -> dict:
        return self._driver.recipe(run_id)

    def is_active(self, run_id: str) -> bool:
        return self._driver.is_active(run_id)

    def cancel(self, run_id: str) -> None:
        self._driver.cancel(run_id)

    def live_artifacts(self, run_id: str, *, stage: str | None = None, limit: int = 3) -> dict:
        """Preview completed node rows during generation, separate from releases."""
        return self._driver.live_artifacts(run_id, stage=stage, limit=max(0, min(int(limit), 20)))

    def quarantined_inputs(self, run_id: str, limit: int = 100) -> list[dict]:
        return self._driver.quarantined_inputs(run_id, max(0, min(int(limit), 100)))

    def source_files(self, workspace_id: str, suffixes: frozenset[str], limit: int = 500) -> list[dict]:
        return self._driver.source_files(workspace_id, suffixes, max(0, min(int(limit), 5000)))
