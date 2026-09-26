"""Filesystem adapter for CPT corpus review events and versioned release bundles."""
from __future__ import annotations

from datetime import datetime, timezone
import io
import json
from pathlib import Path
import re
import zipfile

from filelock import FileLock, Timeout

from lib.domain.corpus_review import corpus_identity, validate_corpus_row
from lib.infrastructure.training_workflow import file_hash, list_runs, read_json, run_path, verify_artifacts
from lib.io_utils import atomic_json
from lib.infrastructure.review_artifacts import attach_review_evidence, iter_review_rows
from lib.infrastructure.review_index import review_index


class FilesystemCorpusReviewDriver:
    def __init__(self, output: Path):
        self.output = Path(output)

    def _run(self, run_id: str) -> Path:
        if not re.fullmatch(r"[a-f0-9]{32}", run_id or ""):
            raise ValueError("invalid_run_id")
        path = run_path(self.output, run_id)
        state = read_json(path / "state.json")
        if state.get("status") not in {"completed", "needs_attention"} or "cpt" not in state.get("targets", []):
            raise ValueError("cpt_run_not_ready")
        verify_artifacts(path)
        if not (path / "artifacts" / "cpt.jsonl").is_file():
            raise ValueError("cpt_artifact_missing")
        return path

    def reviewable_runs(self) -> list[dict]:
        result = []
        for state in list_runs(self.output):
            if state.get("status") not in {"completed", "needs_attention"} or "cpt" not in state.get("targets", []):
                continue
            try:
                path = self._run(state["id"])
                with review_index(path, "cpt", validate_corpus_row, corpus_identity) as index:
                    count = index.count
            except (OSError, ValueError, KeyError, TypeError):
                continue
            result.append({"id": state["id"], "name": state.get("name", "CPT 工作流"),
                           "status": state["status"], "updated_at": state.get("updated_at", ""),
                           "sample_count": count})
        return result

    @staticmethod
    def _read_rows(path: Path) -> list[dict]:
        rows = list(iter_review_rows(path, "cpt", validate_corpus_row, corpus_identity))
        return attach_review_evidence(rows, path, "cpt", corpus_identity)

    def _review_path(self, run_id: str) -> Path:
        return run_path(self.output, run_id) / "human-review" / "cpt.json"

    def _load_review(self, run_id: str) -> dict:
        path = self._review_path(run_id)
        if not path.exists():
            return {"version": 1, "events": [], "current": {}}
        value = read_json(path)
        if not isinstance(value, dict) or not isinstance(value.get("events"), list) or not isinstance(value.get("current"), dict):
            raise ValueError("invalid_cpt_review_state")
        return value

    def queue(self, run_id: str, *, offset: int = 0, limit: int = 20,
              decision: str | None = None) -> dict:
        path = self._run(run_id)
        current = self._load_review(run_id)["current"]
        with review_index(path, "cpt", validate_corpus_row, corpus_identity) as index:
            return index.page(current, offset=offset, limit=limit, decision=decision)

    def row(self, run_id: str, sample_id: str) -> dict:
        if not isinstance(sample_id, str) or not re.fullmatch(r"[a-f0-9]{64}", sample_id):
            raise ValueError("invalid_cpt_sample_id")
        with review_index(self._run(run_id), "cpt", validate_corpus_row, corpus_identity) as index:
            row = index.row(sample_id)
            if row is not None:
                return row
        raise ValueError("cpt_sample_not_found")

    def record_decision(self, run_id: str, expected_hash: str, record: dict) -> dict:
        row = self.row(run_id, record.get("sample_id", ""))
        if corpus_identity(row) != expected_hash or record.get("source_hash") != expected_hash:
            raise ValueError("stale_cpt_sample_refresh_required")
        candidate = validate_corpus_row(record.get("candidate"))
        if record.get("decision") not in {"approved", "rejected", "skipped"}:
            raise ValueError("invalid_corpus_decision")
        if record.get("decision") != "approved" and candidate != row:
            raise ValueError("only_approved_cpt_samples_may_be_revised")
        if record.get("sample_id") != expected_hash:
            raise ValueError("invalid_cpt_sample_id")
        if not isinstance(record.get("reviewer"), str) or not record["reviewer"].strip():
            raise ValueError("invalid_reviewer")
        if len(record["reviewer"]) > 128 or not isinstance(record.get("reviewed_at"), str):
            raise ValueError("invalid_cpt_review_record")
        if not isinstance(record.get("reason", ""), str) or len(record.get("reason", "")) > 2000:
            raise ValueError("invalid_review_reason")
        review_path = self._review_path(run_id)
        review_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            with FileLock(str(review_path) + ".lock", timeout=10):
                state = self._load_review(run_id)
                state["events"].append(dict(record))
                state["current"][record["sample_id"]] = dict(record)
                atomic_json(review_path, state)
        except Timeout as error:
            raise ValueError("cpt_review_busy_retry") from error
        return record

    def release(self, run_id: str) -> bytes:
        path = self._run(run_id)
        review_path = self._review_path(run_id)
        review_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            with FileLock(str(review_path) + ".lock", timeout=10):
                rows = self._read_rows(self._run(run_id))
                state = self._load_review(run_id)
                current = state["current"]
                sample_ids = {row["sample_id"] for row in rows}
                if set(current) - sample_ids:
                    raise ValueError("orphaned_cpt_reviews_refresh_required")
                pending = [row["sample_id"] for row in rows
                           if current.get(row["sample_id"], {}).get("decision") not in {"approved", "rejected"}]
                if pending:
                    raise ValueError(f"cpt_review_incomplete:{len(pending)}")
                approved = [current[row["sample_id"]]["candidate"] for row in rows
                            if current[row["sample_id"]]["decision"] == "approved"]
                if not approved:
                    raise ValueError("no_approved_cpt_samples")

                releases = path / "releases"
                releases.mkdir(parents=True, exist_ok=True)
                versions = []
                for item in releases.iterdir():
                    match = re.fullmatch(r"\.?cpt-v(\d{4})(?:\.pending)?", item.name)
                    if match:
                        versions.append(int(match.group(1)))
                version = max(versions, default=0) + 1
                destination = releases / f"cpt-v{version:04d}"
                staging = releases / f".cpt-v{version:04d}.pending"
                staging.mkdir(parents=True, exist_ok=False)
                data_path = staging / "cpt.jsonl"
                temporary = staging / ".cpt.pending"
                temporary.write_text("".join(json.dumps(row, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n"
                                              for row in approved), encoding="utf-8")
                temporary.replace(data_path)
                evidence = staging / "review.json"
                temporary_evidence = staging / ".review.pending"
                temporary_evidence.write_text(json.dumps({"events": state["events"], "current": current},
                                                          ensure_ascii=False, sort_keys=True, indent=2), encoding="utf-8")
                temporary_evidence.replace(evidence)
                manifest = {"status": "human_reviewed", "target": "cpt", "run_id": run_id,
                            "version": version, "created_at": datetime.now(timezone.utc).isoformat(),
                            "source_artifact_sha256": verify_artifacts(path)["sha256"].get("cpt.jsonl"),
                            "counts": {"candidate": len(rows), "approved": len(approved), "rejected": len(rows) - len(approved)},
                            "sha256": {"cpt.jsonl": file_hash(data_path), "review.json": file_hash(evidence)}}
                atomic_json(staging / "manifest.json", manifest)
                staging.rename(destination)
                buffer = io.BytesIO()
                with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
                    for item in sorted(destination.iterdir()):
                        if item.is_file():
                            archive.write(item, item.name)
                return buffer.getvalue()
        except Timeout as error:
            raise ValueError("cpt_review_busy_retry") from error
