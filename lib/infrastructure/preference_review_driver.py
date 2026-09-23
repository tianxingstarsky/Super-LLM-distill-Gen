"""Filesystem adapter for preference review, audit history, and release bundles."""
from __future__ import annotations

from datetime import datetime, timezone
import io
import json
from pathlib import Path
import re
import zipfile

from filelock import FileLock, Timeout

from lib.domain.preference_review import pair_identity, validate_pair
from lib.infrastructure.training_workflow import file_hash, list_runs, read_json, run_path, verify_artifacts
from lib.io_utils import atomic_json


class FilesystemPreferenceReviewDriver:
    def __init__(self, output: Path, *, target: str = "dpo"):
        if target not in {"dpo", "orpo"}:
            raise ValueError("invalid_preference_review_target")
        self.output = Path(output)
        self.target = target

    @property
    def _artifact_name(self) -> str:
        return f"{self.target}.jsonl"

    @property
    def _release_prefix(self) -> str:
        # Preserve existing DPO release paths while numbering ORPO independently.
        return "preference" if self.target == "dpo" else "orpo-preference"

    def _run(self, run_id: str) -> Path:
        if not re.fullmatch(r"[a-f0-9]{32}", run_id or ""):
            raise ValueError("invalid_run_id")
        path = run_path(self.output, run_id)
        state = read_json(path / "state.json")
        if state.get("status") not in {"completed", "needs_attention"} or self.target not in state.get("targets", []):
            raise ValueError("preference_run_not_ready")
        verify_artifacts(path)
        artifact = path / "artifacts" / self._artifact_name
        if not artifact.is_file():
            raise ValueError("preference_artifact_missing")
        return path

    def reviewable_runs(self) -> list[dict]:
        result = []
        for row in list_runs(self.output):
            if row.get("status") not in {"completed", "needs_attention"} or self.target not in row.get("targets", []):
                continue
            try:
                path = self._run(row["id"])
                count = len(self._read_pairs(path))
            except (OSError, ValueError, KeyError, TypeError):
                continue
            result.append({"id": row["id"], "name": row.get("name", f"{self.target.upper()} 工作流"),
                           "status": row["status"], "updated_at": row.get("updated_at", ""),
                           "pair_count": count, "target": self.target})
        return result

    def _read_pairs(self, path: Path) -> list[dict]:
        artifact = path / "artifacts" / self._artifact_name
        rows = []
        seen = set()
        with artifact.open(encoding="utf-8") as handle:
            for line in handle:
                if not line.strip():
                    continue
                pair = validate_pair(json.loads(line))
                pair_id = pair_identity(pair)
                if pair_id in seen:
                    raise ValueError("duplicate_preference_pair_id")
                seen.add(pair_id)
                rows.append({"pair_id": pair_id, "pair": pair})
        return rows

    def queue(self, run_id: str) -> dict:
        path = self._run(run_id)
        rows = self._read_pairs(path)
        current = self._verified_review_state(rows, self._load_review(run_id))["current"]
        items = [{"pair_id": row["pair_id"], "pair": row["pair"], "review": current.get(row["pair_id"])}
                 for row in rows]
        counts = {"approved": 0, "rejected": 0, "skipped": 0, "pending": 0}
        for item in items:
            decision = (item["review"] or {}).get("decision")
            counts[decision if decision in {"approved", "rejected", "skipped"} else "pending"] += 1
        return {"items": items, "total": len(items), "counts": counts}

    def pair(self, run_id: str, pair_id: str) -> dict:
        if not isinstance(pair_id, str) or not re.fullmatch(r"[a-f0-9]{64}", pair_id):
            raise ValueError("invalid_preference_pair_id")
        for row in self._read_pairs(self._run(run_id)):
            if row["pair_id"] == pair_id:
                return row["pair"]
        raise ValueError("preference_pair_not_found")

    def _review_path(self, run_id: str) -> Path:
        filename = "preferences.json" if self.target == "dpo" else "preferences-orpo.json"
        return run_path(self.output, run_id) / "human-review" / filename

    def _load_review(self, run_id: str) -> dict:
        path = self._review_path(run_id)
        if not path.exists():
            return {"version": 1, "events": [], "current": {}}
        data = read_json(path)
        if not isinstance(data, dict) or not isinstance(data.get("events"), list) or not isinstance(data.get("current"), dict):
            raise ValueError("invalid_preference_review_state")
        if data.get("target", self.target) != self.target:
            raise ValueError("preference_review_target_mismatch")
        return data

    def _validated_record(self, pair: dict, record: dict) -> dict:
        if not isinstance(record, dict):
            raise ValueError("invalid_preference_review_record")
        if record.get("target", "dpo") != self.target:
            raise ValueError("preference_review_target_mismatch")
        pair_id = pair_identity(pair)
        if record.get("pair_id") != pair_id or record.get("source_hash") != pair_id:
            raise ValueError("stale_preference_pair_refresh_required")
        candidate = validate_pair(record.get("candidate"))
        if set(candidate) != set(pair) or candidate.get("prompt") != pair.get("prompt") or candidate.get("tools", []) != pair.get("tools", []):
            raise ValueError("preference_context_is_immutable")
        if any(candidate[key] != pair[key] for key in pair if key not in {"chosen", "rejected"}):
            raise ValueError("preference_context_is_immutable")
        if record.get("decision") not in {"approved", "rejected", "skipped"}:
            raise ValueError("invalid_preference_decision")
        if not isinstance(record.get("reviewer"), str) or not record["reviewer"].strip():
            raise ValueError("invalid_reviewer")
        if not isinstance(record.get("reviewed_at"), str) or not record["reviewed_at"].strip() or not isinstance(record.get("reason", ""), str):
            raise ValueError("invalid_preference_review_record")
        for field in ("chosen", "rejected"):
            original, revised = pair[field][0], candidate[field][0]
            if set(original) != set(revised) or any(original[key] != revised[key] for key in original if key != "content"):
                raise ValueError("preference_message_metadata_is_immutable")
            if record.get("decision") != "approved" and original != revised:
                raise ValueError("only_approved_preference_pairs_may_be_revised")
        return record

    def _verified_review_state(self, pairs: list[dict], state: dict) -> dict:
        originals = {row["pair_id"]: row["pair"] for row in pairs}
        latest = {}
        for event in state["events"]:
            if not isinstance(event, dict) or event.get("pair_id") not in originals:
                raise ValueError("orphaned_preference_reviews_refresh_required")
            self._validated_record(originals[event["pair_id"]], event)
            latest[event["pair_id"]] = event
        if state["current"] != latest:
            raise ValueError("invalid_preference_review_history")
        return state

    def record_decision(self, run_id: str, expected_hash: str, record: dict) -> dict:
        self._run(run_id)
        if record.get("target") != self.target:
            raise ValueError("preference_review_target_mismatch")
        pair = self.pair(run_id, record.get("pair_id", ""))
        if pair_identity(pair) != expected_hash:
            raise ValueError("stale_preference_pair_refresh_required")
        self._validated_record(pair, record)
        review_path = self._review_path(run_id)
        review_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            with FileLock(str(review_path) + ".lock", timeout=10):
                state = self._verified_review_state(self._read_pairs(self._run(run_id)),
                                                    self._load_review(run_id))
                state["target"] = self.target
                state["events"].append(dict(record))
                state["current"][record["pair_id"]] = dict(record)
                atomic_json(review_path, state)
        except Timeout as error:
            raise ValueError("preference_review_busy_retry") from error
        return record

    def release(self, run_id: str) -> bytes:
        path = self._run(run_id)
        review_path = self._review_path(run_id)
        review_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            with FileLock(str(review_path) + ".lock", timeout=10):
                pairs = self._read_pairs(self._run(run_id))
                state = self._verified_review_state(pairs, self._load_review(run_id))
                pair_ids = {row["pair_id"] for row in pairs}
                current = state["current"]
                if set(current) - pair_ids:
                    raise ValueError("orphaned_preference_reviews_refresh_required")
                pending = [row["pair_id"] for row in pairs
                           if current.get(row["pair_id"], {}).get("decision") not in {"approved", "rejected"}]
                if pending:
                    raise ValueError(f"preference_review_incomplete:{len(pending)}")
                approved = [current[row["pair_id"]]["candidate"] for row in pairs
                            if current[row["pair_id"]]["decision"] == "approved"]
                if not approved:
                    raise ValueError("no_approved_preference_pairs")

                releases = path / "releases"
                releases.mkdir(parents=True, exist_ok=True)
                versions = []
                if releases.is_dir():
                    for item in releases.iterdir():
                        match = re.fullmatch(rf"\.?{self._release_prefix}-v(\d{{4}})(?:\.pending)?", item.name)
                        if match:
                            versions.append(int(match.group(1)))
                version = max(versions, default=0) + 1
                destination = releases / f"{self._release_prefix}-v{version:04d}"
                staging = releases / f".{self._release_prefix}-v{version:04d}.pending"
                staging.mkdir(parents=True, exist_ok=False)
                data_path = staging / self._artifact_name
                temporary_data = staging / f".{self.target}.pending"
                temporary_data.write_text("".join(json.dumps(row, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n"
                                                         for row in approved), encoding="utf-8")
                temporary_data.replace(data_path)
                evidence_path = staging / "review.json"
                temporary_evidence = staging / ".review.pending"
                temporary_evidence.write_text(json.dumps({"events": state["events"], "current": current},
                                                          ensure_ascii=False, sort_keys=True, indent=2), encoding="utf-8")
                temporary_evidence.replace(evidence_path)
                manifest = {
                    "status": "human_reviewed",
                    "target": self.target,
                    "run_id": run_id,
                    "version": version,
                    "created_at": datetime.now(timezone.utc).isoformat(),
                    "source_artifact_sha256": verify_artifacts(path)["sha256"].get(self._artifact_name),
                    "counts": {"candidate": len(pairs), "approved": len(approved),
                               "rejected": len(pairs) - len(approved)},
                    "sha256": {self._artifact_name: file_hash(data_path), "review.json": file_hash(evidence_path)},
                }
                atomic_json(staging / "manifest.json", manifest)
                staging.rename(destination)
                buffer = io.BytesIO()
                with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
                    for item in sorted(destination.iterdir()):
                        if item.is_file():
                            archive.write(item, item.name)
                return buffer.getvalue()
        except Timeout as error:
            raise ValueError("preference_review_busy_retry") from error
