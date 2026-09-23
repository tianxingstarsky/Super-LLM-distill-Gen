"""Filesystem adapter for SFT audit events and versioned release bundles."""
from __future__ import annotations

from datetime import datetime, timezone
import io
import json
from pathlib import Path
import re
import zipfile

from filelock import FileLock, Timeout

from lib.domain.sft_review import sft_identity, validate_sft_record, validate_sft_revision
from lib.infrastructure.training_workflow import canonical, file_hash, list_runs, read_json, run_path, verify_artifacts
from lib.io_utils import atomic_json


class FilesystemSftReviewDriver:
    def __init__(self, output: Path):
        self.output = Path(output)

    def _run(self, run_id: str) -> Path:
        if not re.fullmatch(r"[a-f0-9]{32}", run_id or ""):
            raise ValueError("invalid_run_id")
        path = run_path(self.output, run_id)
        state = read_json(path / "state.json")
        if state.get("status") not in {"completed", "needs_attention"} or "sft" not in state.get("targets", []):
            raise ValueError("sft_run_not_ready")
        verify_artifacts(path)
        if not (path / "artifacts" / "sft.jsonl").is_file():
            raise ValueError("sft_artifact_missing")
        return path

    def reviewable_runs(self) -> list[dict]:
        result = []
        for state in list_runs(self.output):
            if state.get("status") not in {"completed", "needs_attention"} or "sft" not in state.get("targets", []):
                continue
            try:
                path = self._run(state["id"])
                with (path / "artifacts" / "sft.jsonl").open(encoding="utf-8") as handle:
                    count = sum(1 for line in handle if line.strip())
            except (OSError, ValueError, KeyError, TypeError):
                continue
            result.append({"id": state["id"], "name": state.get("name", "SFT 工作流"),
                           "status": state["status"], "updated_at": state.get("updated_at", ""),
                           "sample_count": count})
        return result

    @staticmethod
    def _read_rows(path: Path) -> list[dict]:
        evidence_by_payload = {}
        records_path = path / "artifacts" / "sft.records.json"
        if records_path.is_file():
            records = read_json(records_path)
            if isinstance(records, list):
                for record in records:
                    if not isinstance(record, dict) or record.get("status") != "eligible":
                        continue
                    payload = {"messages": record.get("messages")}
                    if record.get("tools"):
                        payload["tools"] = record["tools"]
                    try:
                        evidence_by_payload.setdefault(canonical(validate_sft_record(payload)), {
                            key: record[key] for key in ("id", "source_id", "kind", "location", "evidence_level", "judge", "quotes", "citations")
                            if key in record
                        })
                    except (TypeError, ValueError):
                        continue
        rows = []
        with (path / "artifacts" / "sft.jsonl").open(encoding="utf-8") as handle:
            for line in handle:
                if line.strip():
                    row = validate_sft_record(json.loads(line))
                    rows.append({"sample_id": sft_identity(row), "row": row,
                                 "evidence": evidence_by_payload.get(canonical(row), {})})
        return rows

    def _review_path(self, run_id: str) -> Path:
        return run_path(self.output, run_id) / "human-review" / "sft.json"

    def _load_review(self, run_id: str) -> dict:
        path = self._review_path(run_id)
        if not path.exists():
            return {"version": 1, "events": [], "current": {}}
        state = read_json(path)
        if not isinstance(state, dict) or not isinstance(state.get("events"), list) or not isinstance(state.get("current"), dict):
            raise ValueError("invalid_sft_review_state")
        return state

    def queue(self, run_id: str) -> dict:
        rows = self._read_rows(self._run(run_id))
        current = self._load_review(run_id).get("current", {})
        items = [{"sample_id": row["sample_id"], "row": row["row"], "evidence": row["evidence"],
                  "review": current.get(row["sample_id"])} for row in rows]
        counts = {"approved": 0, "rejected": 0, "skipped": 0, "pending": 0}
        for item in items:
            decision = (item["review"] or {}).get("decision")
            counts[decision if decision in {"approved", "rejected", "skipped"} else "pending"] += 1
        return {"items": items, "total": len(items), "counts": counts}

    def row(self, run_id: str, sample_id: str) -> dict:
        if not isinstance(sample_id, str) or not re.fullmatch(r"[a-f0-9]{64}", sample_id):
            raise ValueError("invalid_sft_sample_id")
        for item in self._read_rows(self._run(run_id)):
            if item["sample_id"] == sample_id:
                return item["row"]
        raise ValueError("sft_sample_not_found")

    def record_decision(self, run_id: str, expected_hash: str, record: dict) -> dict:
        row = self.row(run_id, record.get("sample_id", ""))
        if sft_identity(row) != expected_hash or record.get("source_hash") != expected_hash:
            raise ValueError("stale_sft_sample_refresh_required")
        candidate = validate_sft_revision(row, record.get("candidate"))
        if record.get("sample_id") != expected_hash:
            raise ValueError("invalid_sft_sample_id")
        if record.get("decision") not in {"approved", "rejected", "skipped"}:
            raise ValueError("invalid_sft_decision")
        if record["decision"] != "approved" and candidate != row:
            raise ValueError("only_approved_sft_samples_may_be_revised")
        if not isinstance(record.get("reviewer"), str) or not record["reviewer"].strip() or len(record["reviewer"]) > 128:
            raise ValueError("invalid_reviewer")
        if not isinstance(record.get("reviewed_at"), str) or not isinstance(record.get("reason", ""), str) or len(record.get("reason", "")) > 2000:
            raise ValueError("invalid_sft_review_record")
        review_path = self._review_path(run_id)
        review_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            with FileLock(str(review_path) + ".lock", timeout=10):
                state = self._load_review(run_id)
                state["events"].append(dict(record))
                state["current"][record["sample_id"]] = dict(record)
                atomic_json(review_path, state)
        except Timeout as error:
            raise ValueError("sft_review_busy_retry") from error
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
                    raise ValueError("orphaned_sft_reviews_refresh_required")
                pending = [row["sample_id"] for row in rows
                           if current.get(row["sample_id"], {}).get("decision") not in {"approved", "rejected"}]
                if pending:
                    raise ValueError(f"sft_review_incomplete:{len(pending)}")
                approved = [current[row["sample_id"]]["candidate"] for row in rows
                            if current[row["sample_id"]]["decision"] == "approved"]
                if not approved:
                    raise ValueError("no_approved_sft_samples")

                releases = path / "releases"
                releases.mkdir(parents=True, exist_ok=True)
                versions = []
                for item in releases.iterdir():
                    match = re.fullmatch(r"\.?sft-v(\d{4})(?:\.pending)?", item.name)
                    if match:
                        versions.append(int(match.group(1)))
                version = max(versions, default=0) + 1
                destination = releases / f"sft-v{version:04d}"
                staging = releases / f".sft-v{version:04d}.pending"
                staging.mkdir(parents=True, exist_ok=False)
                data_path = staging / "sft.jsonl"
                temporary_data = staging / ".sft.pending"
                temporary_data.write_text("".join(canonical(row) + "\n" for row in approved), encoding="utf-8")
                temporary_data.replace(data_path)
                evidence_path = staging / "review.json"
                temporary_evidence = staging / ".review.pending"
                temporary_evidence.write_text(json.dumps({"events": state["events"], "current": current},
                                                          ensure_ascii=False, sort_keys=True, indent=2), encoding="utf-8")
                temporary_evidence.replace(evidence_path)
                manifest = {"status": "human_reviewed", "target": "sft", "run_id": run_id,
                            "version": version, "created_at": datetime.now(timezone.utc).isoformat(),
                            "source_artifact_sha256": verify_artifacts(path)["sha256"].get("sft.jsonl"),
                            "counts": {"candidate": len(rows), "approved": len(approved),
                                       "rejected": len(rows) - len(approved)},
                            "sha256": {"sft.jsonl": file_hash(data_path), "review.json": file_hash(evidence_path)}}
                atomic_json(staging / "manifest.json", manifest)
                staging.rename(destination)
                buffer = io.BytesIO()
                with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
                    for item in sorted(destination.iterdir()):
                        if item.is_file():
                            archive.write(item, item.name)
                return buffer.getvalue()
        except Timeout as error:
            raise ValueError("sft_review_busy_retry") from error
