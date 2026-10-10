"""Persistent human-guided rounds; callers launch the returned ordinary workflow run."""
from __future__ import annotations


class HumanAugmentationApplication:
    def __init__(self, driver):
        self._driver = driver

    def create_session(self, *, from_run_id=None, max_revision_depth=3, initial_draft=None, review_only=False, **recipe):
        return self._driver.create_session(from_run_id=from_run_id,
            max_revision_depth=max_revision_depth, initial_draft=initial_draft, review_only=review_only, **recipe)

    def session(self, session_id):
        return self._driver.session(session_id)

    def list_sessions(self):
        return self._driver.list_sessions()

    def save_draft(self, session_id, human_augmentation, *, expected_version):
        return self._driver.save_draft(session_id, human_augmentation, expected_version=expected_version)

    def generate_round(self, session_id, *, request_id, expected_version, sample_count=None):
        return self._driver.generate_round(session_id, request_id=request_id,
            expected_version=expected_version, sample_count=sample_count)

    def results(self, session_id, *, round_id=None, target="sft", offset=0, limit=20):
        return self._driver.results(session_id, round_id=round_id, target=target, offset=offset, limit=limit)

    def branch_projection(self, session_id, *, round_id=None, target=None, candidate_id=None, result=None):
        """Read the current version's real feedback/review routes without launching it."""
        return self._driver.branch_projection(session_id, round_id=round_id, target=target,
            candidate_id=candidate_id, result=result)

    def save_feedback(self, session_id, round_id, target, candidate_id, *, instruction="",
                      question=None, answer=None, decision="revise", expected_version):
        return self._driver.save_feedback(session_id, round_id, target, candidate_id,
            instruction=instruction, question=question, answer=answer, decision=decision,
            expected_version=expected_version)

    def revise_round(self, session_id, *, request_id, expected_version,
                     selected_feedback_ids=None, sample_count=None):
        return self._driver.revise_round(session_id, request_id=request_id,
            expected_version=expected_version, selected_feedback_ids=selected_feedback_ids,
            sample_count=sample_count)

    def submit_manual_review(self, session_id, round_id, target, candidate_id, *, request_id,
                             score, decision="approve", instruction="", messages=None,
                             question=None, answer=None, corrected_record=None, expected_version):
        return self._driver.submit_manual_review(session_id, round_id, target, candidate_id,
            request_id=request_id, score=score, decision=decision, instruction=instruction,
            messages=messages, question=question, answer=answer, corrected_record=corrected_record,
            expected_version=expected_version)

    def resume_round(self, session_id, round_id):
        return self._driver.resume_round(session_id, round_id)

    def cancel_round(self, session_id, round_id):
        return self._driver.cancel_round(session_id, round_id)
