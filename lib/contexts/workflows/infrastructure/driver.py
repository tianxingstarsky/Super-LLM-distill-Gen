"""Filesystem adapter for run lifecycle and live execution inspection."""
from __future__ import annotations
import os
from pathlib import Path

from lib.infrastructure.workflow_store import (
    cancel as cancel_run, is_active as run_is_active,
    digest, list_runs, read_json, run_path,
)
from lib.infrastructure.json_stream import iter_json_records
from lib.infrastructure.agent_docker_replay import IMAGE_ENV, validate_sandbox_image
from lib.domain.web_research import validate_web_research
from lib import workspace as WS


class FilesystemWorkflowRunDriver:
    def __init__(self, root: Path, output: Path):
        self.root = Path(root)
        self.output = Path(output)

    def create(self, **recipe) -> str:
        from lib.infrastructure.training_workflow import create_run
        return create_run(self.output, settings_root=self.root, **recipe)

    def default_sft_output_style(self) -> str:
        from lib.infrastructure.generation_settings_file import preference_snapshot

        style = preference_snapshot(self.root)["values"]["cot_style"]
        return style if style in {"separated", "drop"} else "separated"

    def web_research_capabilities(self) -> dict:
        from lib.infrastructure.web_research_connection import connection_capabilities

        return connection_capabilities(self.root)

    def save_web_research_connection(self, api_key: str) -> dict:
        from lib.infrastructure.web_research_connection import save_connection

        return save_connection(self.root, api_key)

    def check_web_research_connection(self) -> str:
        from lib.infrastructure.brave_web_research import check_connection

        return check_connection(self.root)

    def web_research_results(self, run_id: str) -> dict | None:
        """Read only the bounded search checkpoint belonging to this run.

        The checkpoint is available before the large training package finishes,
        so reviewing the planning leads does not require loading that package.
        """
        from lib.infrastructure.brave_web_research import validate_search_document

        run = run_path(self.output, run_id)
        recipe = read_json(run / "recipe.json")
        config = validate_web_research(recipe.get("web_research"),
                                       brief=recipe.get("brief", ""),
                                       sources=recipe.get("sources", ()),
                                       targets=recipe.get("targets"))
        if config is None:
            return None
        checkpoint = run / "checkpoints" / "ingest" / f"{digest(['web_research', config])}.json"
        if not checkpoint.exists():
            return None
        if checkpoint.is_symlink() or not checkpoint.is_file():
            raise ValueError("web_research_integrity_error")
        saved = read_json(checkpoint)
        document = saved.get("data") if isinstance(saved, dict) else None
        if not isinstance(document, dict) or saved.get("sha256") != digest(document):
            raise ValueError("web_research_integrity_error")
        validate_search_document(config, document)
        safe = [{**({"query": row["query"]} if "query" in row else {}),
                 "title": row["title"], "url": row["url"], "snippet": row["snippet"]}
                for row in document["results"]]
        return {"query": config["query"], "results": safe,
                "queries": [config["query"], *config.get("more_queries", [])],
                "topic_retrieved_at": document.get("topic_retrieved_at", []),
                "retrieved_at": document.get("retrieved_at", "")}

    def check_agent_sandbox(self) -> dict:
        from lib.infrastructure.agent_docker_replay import check_environment
        return check_environment(os.environ.get(IMAGE_ENV))

    def agent_replay_capabilities(self) -> dict:
        try:
            configured = bool(validate_sandbox_image(os.environ.get(IMAGE_ENV)))
            return {"isolated_configured": configured}
        except ValueError:
            return {"isolated_configured": False, "configuration_invalid": True}

    def execute(self, run_id: str) -> dict:
        from lib.infrastructure.training_workflow import Workflow
        return Workflow(self.output, run_id, self.root).execute()

    def resume(self, run_id: str) -> dict:
        from lib.infrastructure.training_workflow import resume as resume_run
        return resume_run(self.output, run_id, self.root)

    def list_runs(self) -> list[dict]:
        return list_runs(self.output)

    def task_runs(self) -> list[dict]:
        from lib.infrastructure.workflow_task_inventory import task_runs
        return task_runs(self.output)

    def state(self, run_id: str) -> dict:
        return read_json(run_path(self.output, run_id) / "state.json")

    def read_streams(self, run_id: str, *, stage: str | None = None) -> list[dict]:
        from lib.infrastructure.workflow_stream_journal import read_streams

        run = run_path(self.output, run_id)
        state = read_json(run / "state.json")
        return read_streams(run, active=run_is_active(run), run_attempt=state.get("attempt", 0), stage=stage)

    def read_stream_delta(self, run_id: str, request_id: str, *, offset: int = 0,
                          limit_bytes: int = 65536, stage: str | None = None) -> dict:
        from lib.infrastructure.workflow_stream_journal import read_stream_delta

        run = run_path(self.output, run_id)
        state = read_json(run / "state.json")
        return read_stream_delta(run, request_id, active=run_is_active(run),
                                 run_attempt=state.get("attempt", 0), offset=offset,
                                 limit_bytes=limit_bytes, stage=stage)

    def recipe(self, run_id: str) -> dict:
        return read_json(run_path(self.output, run_id) / "recipe.json")

    def is_active(self, run_id: str) -> bool:
        return run_is_active(run_path(self.output, run_id))

    def cancel(self, run_id: str) -> None:
        cancel_run(self.output, run_id)

    def live_artifacts(self, run_id: str, *, stage: str | None = None, limit: int = 3) -> dict:
        from lib.infrastructure.workflow_live_artifacts import live_artifacts

        run = run_path(self.output, run_id)
        return live_artifacts(run, self.state(run_id), stage=stage, limit=limit)

    def quarantined_inputs(self, run_id: str, limit: int = 100) -> list[dict]:
        if limit <= 0:
            return []
        path = run_path(self.output, run_id) / "input_records.json"
        if not path.exists():
            return []
        rows = []
        records = iter_json_records(path)
        try:
            for row in records:
                if row.get("status") == "quarantined":
                    rows.append(row)
                    if len(rows) >= limit:
                        break
        finally:
            records.close()
        return rows

    @staticmethod
    def source_files(workspace_id: str, suffixes: frozenset[str], limit: int) -> list[dict]:
        folder = WS.folder(workspace_id)
        result = []
        for path in WS.source_files(workspace_id, suffixes=suffixes, limit=limit):
            relative = path.relative_to(folder)
            # Cache fingerprints distinguish revisions without filling the
            # source picker with a long storage path.
            parts = relative.parts
            label = (f"{path.name} · {parts[1][:8]}"
                     if len(parts) == 3 and parts[0] == "uploads" and len(parts[1]) == 64
                     else str(relative))
            result.append({"path": str(path), "label": label})
        return result
