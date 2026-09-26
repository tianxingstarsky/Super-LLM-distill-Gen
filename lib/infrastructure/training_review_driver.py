"""One filesystem adapter for typed training-data review and publication."""
from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
import re

from lib.domain.corpus_review import corpus_identity, validate_corpus_row
from lib.domain.preference_review import pair_identity, validate_pair
from lib.domain.review_audit import validate_review_event
from lib.domain.sft_review import sft_identity, validate_sft_record
from lib.infrastructure.review_index import review_index
from lib.infrastructure.review_release import prepare_review_release, review_archive
from lib.infrastructure.review_store import review_store
from lib.infrastructure.training_workflow import list_runs, read_json, run_path, verify_artifacts


class FilesystemTrainingReviewDriver:
    def __init__(self, output: Path, target: str):
        if target not in {"sft", "cpt", "dpo", "orpo"}:
            raise ValueError("invalid_review_target")
        self.output, self.target = Path(output), target
        self.preference = target in {"dpo", "orpo"}
        self.family = "preference" if self.preference else target
        self.id_key = "pair_id" if self.preference else "sample_id"
        self.validate, self.identity = ((validate_pair, pair_identity) if self.preference else
            (validate_sft_record, sft_identity) if target == "sft" else (validate_corpus_row, corpus_identity))
        self.prefix = {"sft": "sft", "cpt": "cpt", "dpo": "preference", "orpo": "orpo-preference"}[target]

    def _run(self, run_id):
        if not re.fullmatch(r"[a-f0-9]{32}", run_id or ""):
            raise ValueError("invalid_run_id")
        path = run_path(self.output, run_id)
        state = read_json(path / "state.json")
        if state.get("status") not in {"completed", "needs_attention"} or self.target not in state.get("targets", []):
            raise ValueError(f"{self.family}_run_not_ready")
        verify_artifacts(path)
        if not (path / "artifacts" / f"{self.target}.jsonl").is_file():
            raise ValueError(f"{self.family}_artifact_missing")
        return path

    def _review_path(self, run_id):
        name = {"sft": "sft", "cpt": "cpt", "dpo": "preferences", "orpo": "preferences-orpo"}[self.target]
        return run_path(self.output, run_id) / "human-review" / f"{name}.json"

    @contextmanager
    def _context(self, run_id):
        path = self._run(run_id)
        with review_index(path, self.target, self.validate, self.identity) as index:
            with review_store(self._review_path(run_id), self.target, index.row) as store:
                yield path, index, store

    def reviewable_runs(self):
        result = []
        for state in list_runs(self.output):
            if state.get("status") not in {"completed", "needs_attention"} or self.target not in state.get("targets", []):
                continue
            try:
                path = self._run(state["id"])
                job = self.release_job(state["id"])
                if job and job["status"] in {"queued", "running"}:
                    count = job["candidate_count"]
                else:
                    with review_index(path, self.target, self.validate, self.identity) as index:
                        count = index.count
            except (OSError, ValueError, KeyError, TypeError):
                continue
            result.append({"id": state["id"], "name": state.get("name", f"{self.target.upper()} 工作流"),
                           "status": state["status"], "updated_at": state.get("updated_at", ""),
                           "pair_count" if self.preference else "sample_count": count, "target": self.target})
        return result

    def queue(self, run_id, *, offset=0, limit=20, decision=None):
        with self._context(run_id) as (_, index, store):
            return index.page(store, offset=offset, limit=limit, decision=decision)

    def row(self, run_id, sample_id):
        noun = "preference_pair" if self.preference else f"{self.target}_sample"
        if not isinstance(sample_id, str) or not re.fullmatch(r"[a-f0-9]{64}", sample_id):
            raise ValueError(f"invalid_{noun}_id")
        with review_index(self._run(run_id), self.target, self.validate, self.identity) as index:
            row = index.row(sample_id)
            if row is not None:
                return row
        raise ValueError(f"{noun}_not_found")

    def pair(self, run_id, pair_id):
        return self.row(run_id, pair_id)

    def record_decision(self, run_id, expected_hash, record):
        with self._context(run_id) as (_, index, store):
            if not isinstance(record, dict):
                raise ValueError(f"invalid_{self.family}_review_record")
            source = index.row(record.get(self.id_key, ""))
            noun = "preference_pair" if self.preference else f"{self.target}_sample"
            if source is None or self.identity(source) != expected_hash:
                raise ValueError(f"stale_{noun}_refresh_required")
            validate_review_event(self.target, source, record)
            return store.append(record)

    def prepare_release(self, run_id, *, progress=None):
        if progress:
            progress("source")
        with self._context(run_id) as (path, index, store):
            return prepare_review_release(path, self.target, self.prefix, index, store, progress=progress)

    def start_release(self, run_id):
        from lib.infrastructure.review_release_jobs import start_release_job
        job = self.release_job(run_id)
        if job and job["status"] in {"queued", "running"}:
            return job
        with review_index(self._run(run_id), self.target, self.validate, self.identity) as index:
            count = index.count
        return start_release_job(self.output, run_id, self.target, count)

    def release_job(self, run_id):
        from lib.infrastructure.review_release_jobs import release_job
        return release_job(self.output, run_id, self.target)

    def cancel_release(self, run_id):
        from lib.infrastructure.review_release_jobs import cancel_release_job
        return cancel_release_job(self.output, run_id, self.target)

    def release_archive(self, run_id, release_id, expected_hash):
        return review_archive(self._run(run_id), self.prefix, release_id, expected_hash)

    def release(self, run_id):
        """Compatibility API for callers that explicitly request ZIP bytes."""
        result = self.prepare_release(run_id)
        with self.release_archive(run_id, result["id"], result["sha256"]) as handle:
            return handle.read()
