"""Verified dataset read/delivery adapter; never imports the generation engine."""
from __future__ import annotations
import hashlib
import json
from pathlib import Path

from lib.infrastructure.workflow_store import list_runs, read_json, run_path, verify_artifacts, is_active as run_is_active
from lib.contexts.datasets.infrastructure.release_catalog import list_releases as catalog_releases, release_file as catalog_file
from lib.contexts.datasets.infrastructure import archive as workflow_archive
from lib.infrastructure import review_release_jobs
from lib.infrastructure.json_stream import iter_json_records
from lib.domain.dataset_assets import DIRECT_DOWNLOAD_LIMIT_BYTES
from lib.contexts.workflows.contracts import training_record
from lib.contexts.datasets.domain.delivery import has_deliverable_results, delivery_manifest_matches, require_packageable


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


class FilesystemDatasetDriver:
    def __init__(self, root: Path, output: Path):
        self.root = Path(root)
        self.output = Path(output)

    def task_runs(self) -> list[dict]:
        from lib.infrastructure.workflow_task_inventory import task_runs
        return task_runs(self.output)

    def state(self, run_id: str) -> dict:
        return read_json(run_path(self.output, run_id) / "state.json")

    def artifact_preview(self, run_id: str, target: str, limit: int, offset: int = 0) -> list[dict]:
        if limit <= 0:
            return []
        run = run_path(self.output, run_id)
        filename = "agent.negative.jsonl" if target == "agent_negative" else f"{target}.jsonl"
        path = run / "artifacts" / filename
        manifest = run / "artifacts" / "manifest.json"
        from lib.contexts.datasets.infrastructure.verified_preview import verify_preview, inventory_identity
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

    def _check_export_credentials(self, run_id: str) -> None:
        from lib.infrastructure.export_credentials import ExportCredentialGuard
        folder = run_path(self.output, run_id) / "artifacts"
        if folder.is_dir():
            ExportCredentialGuard.from_root(self.root).check_folder(folder)

    def bundle(self, run_id: str) -> bytes:
        self._check_export_credentials(run_id)
        return workflow_archive.bundle_bytes(run_path(self.output, run_id))

    def prepare_bundle(self, run_id: str) -> dict:
        self._check_export_credentials(run_id)
        return workflow_archive.prepare_bundle(run_path(self.output, run_id))

    def prepared_bundle(self, run_id: str) -> dict | None:
        self._check_export_credentials(run_id)
        return workflow_archive.prepared_bundle(run_path(self.output, run_id))

    def start_bundle(self, run_id: str) -> dict:
        state = self.state(run_id)
        path = run_path(self.output, run_id)
        require_packageable(state, active=run_is_active(path))
        self._check_export_credentials(run_id)
        manifest = verify_artifacts(path)
        require_packageable(state, active=False, manifest=manifest)
        if self.state(run_id) != state or run_is_active(path):
            raise ValueError("workflow_not_ready_to_package")
        return review_release_jobs.start_release_job(self.output, run_id, "workflow", 0)

    def bundle_job(self, run_id: str) -> dict | None:
        return review_release_jobs.release_job(self.output, run_id, "workflow")

    def cancel_bundle(self, run_id: str) -> dict | None:
        return review_release_jobs.cancel_release_job(self.output, run_id, "workflow")

    def artifact_file(self, run_id: str, filename: str) -> bytes:
        self._check_export_credentials(run_id)
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
        from lib.contexts.datasets.infrastructure.verified_preview import verified_snapshot, inventory_identity

        path = run_path(self.output, run_id)
        manifest, identity = verified_snapshot(path)
        contents = {"manifest": manifest, "files": self._verified_files(path, manifest),
                    "quality": self._verified_quality(path, manifest),
                    "bundle": workflow_archive.display_prepared_bundle(path, manifest),
                    "manifest_bytes": (path / "artifacts" / "manifest.json").stat().st_size}
        if inventory_identity(path) != identity:
            raise ValueError("package_files_changed")
        return contents

    def sample_statistics(self, run_id: str) -> dict:
        from lib.contexts.datasets.infrastructure.statistics import cached_statistics
        contents = self.package_contents(run_id)
        run = run_path(self.output, run_id)
        return {**contents, "statistics": cached_statistics(run, contents["manifest"]),
                "job": review_release_jobs.release_job(self.output, run_id, "statistics")}

    def start_sample_statistics(self, run_id: str) -> dict:
        return review_release_jobs.start_release_job(self.output, run_id, "statistics", 0)

    def cancel_sample_statistics(self, run_id: str) -> dict | None:
        return review_release_jobs.cancel_release_job(self.output, run_id, "statistics")

    def artifact_location(self, run_id: str) -> str:
        return str(run_path(self.output, run_id) / "artifacts")

    def list_releases(self) -> list[dict]:
        return catalog_releases(self.output)

    def release_file(self, release_id: str, filename: str) -> bytes:
        return catalog_file(self.output, release_id, filename, settings_root=self.root)

    def reviewable_artifacts(self) -> list[str]:
        """Expose only completed SFT conversations whose run bundle still verifies."""
        result = []
        for run in list_runs(self.output):
            if not has_deliverable_results(run) or "sft" not in run.get("targets", []):
                continue
            try:
                path = run_path(self.output, run.get("id", ""))
                artifact = path / "artifacts" / "sft.jsonl"
                if not artifact.is_file():
                    continue
                manifest = verify_artifacts(path)
                if run_is_active(path) or not delivery_manifest_matches(run, manifest):
                    continue
            except (OSError, ValueError, KeyError, TypeError):
                continue
            result.append(str(artifact))
        return result
