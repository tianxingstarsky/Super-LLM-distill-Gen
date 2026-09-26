"""Filesystem/model-backed workflow adapter for the application port."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

from lib.infrastructure.training_workflow import (
    Workflow, cancel as cancel_run, create_run, is_active as run_is_active,
    list_runs, read_json, resume as resume_run, run_path, verify_artifacts,
)
from lib.infrastructure.release_catalog import list_releases as catalog_releases, release_file as catalog_file
from lib.infrastructure.json_stream import iter_json_records
from lib.infrastructure import workflow_archive
from lib.infrastructure import review_release_jobs
from lib.domain.dataset_assets import DIRECT_DOWNLOAD_LIMIT_BYTES
from lib.infrastructure.agent_docker_replay import IMAGE_ENV, validate_sandbox_image
from lib import workspace as WS


class FilesystemWorkflowDriver:
    def __init__(self, root: Path, output: Path):
        self.root = Path(root)
        self.output = Path(output)

    def create(self, **recipe) -> str:
        return create_run(self.output, **recipe)

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

    def recipe(self, run_id: str) -> dict:
        return read_json(run_path(self.output, run_id) / "recipe.json")

    def is_active(self, run_id: str) -> bool:
        return run_is_active(run_path(self.output, run_id))

    def cancel(self, run_id: str) -> None:
        cancel_run(self.output, run_id)

    def artifact_preview(self, run_id: str, target: str, limit: int) -> list[dict]:
        if limit <= 0:
            return []
        run = run_path(self.output, run_id)
        filename = "agent.negative.jsonl" if target == "agent_negative" else f"{target}.jsonl"
        path = run / "artifacts" / filename
        manifest = run / "artifacts" / "manifest.json"
        if manifest.is_file():
            verify_artifacts(run)
        if not path.exists():
            return []
        if not manifest.is_file():
            raise ValueError("incomplete_artifact_manifest")
        rows = []
        with path.open(encoding="utf-8") as handle:
            for line in handle:
                if line.strip():
                    rows.append(json.loads(line))
                    if len(rows) >= limit:
                        break
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
        """Verify large artifacts once per page render, then read the two summaries."""
        path = run_path(self.output, run_id)
        manifest = verify_artifacts(path)
        return {"manifest": manifest, "files": self._verified_files(path, manifest),
                "quality": self._verified_quality(path, manifest),
                "bundle": workflow_archive.prepared_bundle(path, verified_manifest=manifest)}

    def artifact_location(self, run_id: str) -> str:
        return str(run_path(self.output, run_id) / "artifacts")

    def list_releases(self) -> list[dict]:
        return catalog_releases(self.output)

    def release_file(self, release_id: str, filename: str) -> bytes:
        return catalog_file(self.output, release_id, filename)

    @staticmethod
    def source_files(workspace_id: str, suffixes: frozenset[str], limit: int) -> list[dict]:
        folder = WS.folder(workspace_id)
        return [{"path": str(path), "label": str(path.relative_to(folder))}
                for path in WS.source_files(workspace_id, suffixes=suffixes, limit=limit)]

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
