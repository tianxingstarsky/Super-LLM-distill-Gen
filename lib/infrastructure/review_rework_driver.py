"""Authenticated review-payload to sealed-candidate handoff, streamed in batches."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

from lib.infrastructure.rework_candidate_index import rework_candidate_index
from lib.infrastructure.training_review_driver import FilesystemTrainingReviewDriver


class FilesystemReviewReworkDriver:
    def __init__(self, output: Path):
        self.output = Path(output)

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
