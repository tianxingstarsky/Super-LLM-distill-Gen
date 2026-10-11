"""Move reviewed problem samples into the existing persistent rework studio."""
from __future__ import annotations

from lib.domain.human_session import bounded_text


class ReviewReworkApplication:
    def __init__(self, driver, human_application):
        self._driver, self._human = driver, human_application

    def collection(self, run_id: str) -> dict:
        session_id = self._human.review_session(run_id)
        session = self._human.session(session_id)
        parent = next((row for row in session["rounds"] if row["run_id"] == run_id), None)
        if parent is None:
            raise ValueError("human_session_round_not_found")
        return {"session_id": session_id, "round_id": parent["id"], "version": session["version"]}

    def save_rejected(self, run_id: str, target: str, *, instruction: str = "") -> dict:
        """Persist every current rejection in bounded batches; never launch work.

        The driver holds a read snapshot and an authenticated candidate index
        for this action. Each saved batch retains its own existing review note.
        Reopening/repeating uses the same studio and stable save request IDs.
        """
        if target not in {"sft", "cpt", "dpo", "orpo", "rlaif"}:
            raise ValueError("unsupported_review_target")
        # A review note may contain 2,000 characters; leave space for the
        # separator within the studio's 6,000-character instruction limit.
        instruction = bounded_text(instruction, limit=3999)
        collection = self.collection(run_id)
        saved, batches, counts = 0, 0, {}
        for batch in self._driver.rejected_batches(run_id, target):
            selections = [{"round_id": collection["round_id"], "target": target,
                           "candidate_id": row["candidate_id"], "instruction": row["instruction"]}
                          for row in batch["rows"]]
            if not selections:
                continue
            # Reviewer notes are part of this identity. An unchanged import is
            # idempotent; deliberately changed notes create a new issue version.
            request_id = self._driver.request_id(run_id, target, selections, instruction)
            response = self._human.save_issues(collection["session_id"], selections,
                instruction=instruction, expected_version=collection["version"], request_id=request_id)
            collection["version"] = response["version"]
            saved += response["saved_count"]
            batches += 1
            counts = response.get("issue_counts", {})
        return {**collection, "saved_count": saved, "batch_count": batches, "issue_counts": counts}
