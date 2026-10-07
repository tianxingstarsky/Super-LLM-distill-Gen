"""Filesystem/model-backed workflow adapter for the application port."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

from lib.infrastructure.training_workflow import (
    Workflow, cancel as cancel_run, create_run, is_active as run_is_active,
    digest, list_runs, read_json, resume as resume_run, run_path, verify_artifacts,
)
from lib.infrastructure.release_catalog import list_releases as catalog_releases, release_file as catalog_file
from lib.infrastructure.json_stream import iter_json_records
from lib.infrastructure import workflow_archive
from lib.infrastructure import review_release_jobs
from lib.domain.dataset_assets import DIRECT_DOWNLOAD_LIMIT_BYTES
from lib.infrastructure.agent_docker_replay import IMAGE_ENV, validate_sandbox_image
from lib.domain.workflow_targets import training_record
from lib.domain.web_research import validate_web_research
from lib import workspace as WS


def _agent_preview_with_evidence(run: Path, rows: list[dict], offset: int) -> list[dict]:
    """Join verified Agent records to the requested native training page."""
    records_path = run / "artifacts" / "agent.records.json"
    if not rows or not records_path.is_file():
        return rows
    selected = []
    position = 0
    records = iter_json_records(records_path)
    try:
        for record in records:
            if record.get("status") != "eligible":
                continue
            if position >= offset:
                native = rows[position - offset]
                try:
                    matches = training_record("agent", record) == native
                except (KeyError, TypeError, ValueError):
                    matches = False
                if not matches or not isinstance(record.get("verification"), dict):
                    raise ValueError("artifact_integrity_error")
                fields = ("source_id", "source_name", "location", "source_location",
                          "evidence_level", "original_messages_sha256", "verification")
                selected.append({**native, **{key: record[key] for key in fields if key in record}})
                if len(selected) == len(rows):
                    break
            position += 1
    finally:
        records.close()
    if len(selected) != len(rows):
        raise ValueError("artifact_integrity_error")
    return selected


class FilesystemWorkflowDriver:
    def __init__(self, root: Path, output: Path):
        self.root = Path(root)
        self.output = Path(output)

    def create(self, **recipe) -> str:
        return create_run(self.output, settings_root=self.root, **recipe)

    def default_sft_output_style(self) -> str:
        from lib.infrastructure.training_workflow import preference_snapshot

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
        return Workflow(self.output, run_id, self.root).execute()

    def resume(self, run_id: str) -> dict:
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

    def artifact_preview(self, run_id: str, target: str, limit: int, offset: int = 0) -> list[dict]:
        if limit <= 0:
            return []
        run = run_path(self.output, run_id)
        filename = "agent.negative.jsonl" if target == "agent_negative" else f"{target}.jsonl"
        path = run / "artifacts" / filename
        manifest = run / "artifacts" / "manifest.json"
        from lib.infrastructure.verified_preview import verify_preview, inventory_identity
        identity = None
        if manifest.is_file():
            identity = verify_preview(run)
        if not path.exists():
            return []
        if not manifest.is_file():
            raise ValueError("incomplete_artifact_manifest")
        from lib.infrastructure.jsonl_preview import read_rows
        rows = read_rows(path, offset, limit)
        if target == "agent":
            rows = _agent_preview_with_evidence(run, rows, offset)
        if inventory_identity(run) != identity:
            raise ValueError("preview_file_changed")
        return rows

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

    def bundle(self, run_id: str) -> bytes:
        return workflow_archive.bundle_bytes(run_path(self.output, run_id))

    def prepare_bundle(self, run_id: str) -> dict:
        return workflow_archive.prepare_bundle(run_path(self.output, run_id))

    def prepared_bundle(self, run_id: str) -> dict | None:
        return workflow_archive.prepared_bundle(run_path(self.output, run_id))

    def start_bundle(self, run_id: str) -> dict:
        state = self.state(run_id)
        if state.get("status") not in {"completed", "needs_attention"}:
            raise ValueError("workflow_not_ready_to_package")
        return review_release_jobs.start_release_job(self.output, run_id, "workflow", 0)

    def bundle_job(self, run_id: str) -> dict | None:
        return review_release_jobs.release_job(self.output, run_id, "workflow")

    def cancel_bundle(self, run_id: str) -> dict | None:
        return review_release_jobs.cancel_release_job(self.output, run_id, "workflow")

    def artifact_file(self, run_id: str, filename: str) -> bytes:
        return workflow_archive.artifact_bytes(run_path(self.output, run_id), filename,
                                               DIRECT_DOWNLOAD_LIMIT_BYTES)

    def package_inventory(self, run_id: str) -> dict:
        path = run_path(self.output, run_id)
        manifest = verify_artifacts(path)
        return {"manifest": manifest, "files": self._verified_files(path, manifest)}

    @staticmethod
    def _verified_files(path: Path, manifest: dict) -> list[dict]:
        files = []
        for item in sorted((path / "artifacts").iterdir()):
            if not item.is_file() or item.name == "manifest.json":
                continue
            files.append({"name": item.name, "bytes": item.stat().st_size,
                          "sha256": manifest["sha256"].get(item.name, "")})
        return files

    @staticmethod
    def _verified_quality(path: Path, manifest: dict) -> dict:
        if "quality.json" not in manifest["sha256"]:
            raise ValueError("missing_verified_quality_report")
        payload = (path / "artifacts" / "quality.json").read_bytes()
        if hashlib.sha256(payload).hexdigest() != manifest["sha256"]["quality.json"]:
            raise ValueError("artifact_integrity_error")
        report = json.loads(payload)
        if not isinstance(report, dict):
            raise ValueError("invalid_quality_report")
        return report

    def quality_report(self, run_id: str) -> dict:
        path = run_path(self.output, run_id)
        manifest = verify_artifacts(path)
        return {"manifest": manifest, "quality": self._verified_quality(path, manifest)}

    def package_contents(self, run_id: str) -> dict:
        """Read a short-lived verified display snapshot and the two summaries."""
        from lib.infrastructure.verified_preview import verified_snapshot, inventory_identity

        path = run_path(self.output, run_id)
        manifest, identity = verified_snapshot(path)
        contents = {"manifest": manifest, "files": self._verified_files(path, manifest),
                    "quality": self._verified_quality(path, manifest),
                    "bundle": workflow_archive.display_prepared_bundle(path, manifest)}
        if inventory_identity(path) != identity:
            raise ValueError("package_files_changed")
        return contents

    def artifact_location(self, run_id: str) -> str:
        return str(run_path(self.output, run_id) / "artifacts")

    def list_releases(self) -> list[dict]:
        return catalog_releases(self.output)

    def release_file(self, release_id: str, filename: str) -> bytes:
        return catalog_file(self.output, release_id, filename)

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

    def reviewable_artifacts(self) -> list[str]:
        """Expose only completed SFT conversations whose run bundle still verifies."""
        result = []
        for run in list_runs(self.output):
            if run.get("status") not in {"completed", "needs_attention"} or "sft" not in run.get("targets", []):
                continue
            try:
                path = run_path(self.output, run.get("id", ""))
                artifact = path / "artifacts" / "sft.jsonl"
                if not artifact.is_file():
                    continue
                verify_artifacts(path)
            except (OSError, ValueError, KeyError, TypeError):
                continue
            result.append(str(artifact))
        return result

    def agent_review_queue(self, run_id: str, *, kind: str, offset: int,
                           limit: int, decision: str | None) -> dict:
        from lib.infrastructure.agent_review_driver import FilesystemAgentReviewDriver

        return FilesystemAgentReviewDriver(self.output).queue(
            run_id, kind=kind, offset=offset, limit=limit, decision=decision)

    def agent_review_decide(self, run_id: str, candidate_id: str, **decision) -> dict:
        from lib.infrastructure.agent_review_driver import FilesystemAgentReviewDriver

        return FilesystemAgentReviewDriver(self.output).decide(run_id, candidate_id, **decision)
