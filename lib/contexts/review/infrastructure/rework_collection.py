"""Authenticated review-payload to sealed-candidate handoff, streamed in batches."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import sqlite3

from lib.domain.human_session import identifier

from lib.infrastructure.rework_candidate_index import rework_candidate_index
from lib.infrastructure.training_review_driver import FilesystemTrainingReviewDriver


class FilesystemReviewReworkDriver:
    def __init__(self, output: Path):
        self.output = Path(output)

    def existing_collection(self, run_id: str, target: str) -> dict | None:
        """Read only the saved collection index, without initializing any store.

        This is navigation metadata, not permission to repair. The existing
        studio still authenticates every parent version before generation.
        """
        identifier(run_id, "invalid_workflow_id")
        folder = self.output / "human-sessions"
        for file in folder.glob("*/session.json"):
            try:
                state = json.loads(file.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                # A different broken historical workspace must not conceal
                # this run's saved work. A matched store is checked below.
                continue
            if not isinstance(state, dict) or not state.get("review_only"):
                continue
            rounds = state.get("rounds", [])
            parent = next((row for row in rounds if isinstance(row, dict)
                and row.get("run_id") == run_id and row.get("kind") == "imported"), None) if isinstance(rounds, list) else None
            if parent is None:
                continue
            session_id = identifier(state["id"], "invalid_human_session_id")
            if session_id != file.parent.name:
                raise ValueError("invalid_human_session_id")
            database = file.parent / "review-issues.sqlite3"
            if not database.is_file():
                return None
            try:
                db = sqlite3.connect(database.resolve().as_uri() + "?mode=ro", uri=True, timeout=5)
                try:
                    saved_count = db.execute("SELECT COUNT(*) FROM issues WHERE round_id=? AND target=? "
                        "AND superseded_by IS NULL", (parent["id"], target)).fetchone()[0]
                finally:
                    db.close()
            except sqlite3.Error as exc:
                raise ValueError("review_rework_collection_unavailable") from exc
            if saved_count:
                return {"session_id": session_id, "round_id": parent["id"],
                        "version": state["version"], "saved_count": saved_count}
        return None

    @staticmethod
    def request_id(run_id, target, selections, instruction):
        digest = hashlib.sha256(json.dumps({"run": run_id, "target": target,
            "selections": selections, "instruction": instruction}, ensure_ascii=False,
            sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        return "review-rework:" + digest

    def rejected_batches(self, run_id: str, target: str):
        """The current decision index supplies only rejected rows, not all rows.

        Opening this generator verifies native artifacts and the review audit.
        The nested candidate context lets save_issues authenticate selected
        records by ID without rescanning the whole record array for each batch.
        """
        review = FilesystemTrainingReviewDriver(self.output, target)
        with review._context(run_id) as (path, _review_index, store):
            with rework_candidate_index(path, target) as candidates:
                rows = store.db.execute("SELECT c.sample_id "
                    "FROM current c JOIN events e ON c.sequence=e.sequence "
                    "WHERE e.decision='rejected' ORDER BY c.sample_id")
                while page := rows.fetchmany(100):
                    selections = []
                    for row in page:
                        # get() verifies this current head against the latest
                        # append-only event and its immutable review payload.
                        decision = store.get(row[0])
                        if decision is None or decision.get("decision") != "rejected":
                            raise ValueError("review_rework_decision_changed")
                        identity = decision[review.id_key]
                        candidate_id = candidates.reviewed_candidate(identity)
                        selections.append({"candidate_id": candidate_id, "instruction": decision.get("reason", "")})
                    yield {"rows": selections}
