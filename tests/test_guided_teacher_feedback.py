"""Regression cases from real local teacher disclosures and their next learner responses."""

import pytest

from lib.contexts.workflows.application.guided_teacher_feedback import (
    freeze_teacher_experience, observed_followup, transfer_findings,
)
from lib.contexts.workflows.domain.reasoning_bank import no_new_memory


def test_old_fenced_no_update_cannot_be_adopted_and_mentions_remain_valid_memory():
    signal = "```json\n<no-new-memory/>\n```"
    class Saved:
        def get_version(self, _id):
            return {"text": signal, "episodes": [{"guided_teacher_followup_projection_version": 1}]}
    assert no_new_memory(signal) and no_new_memory("<no-new-memory/>")
    assert not no_new_memory("When nothing changes the tool returns <no-new-memory/>.")
    assert not no_new_memory(signal + "\n# Memory Item 1\nA transferable observation")
    with pytest.raises(ValueError, match="no-update"):
        freeze_teacher_experience(None, Saved(), "old", trial=True)


def test_reviewer_method_and_correct_value_have_distinct_credit():
    before = {"student": {"content": "A vs F?"}, "verified_stage": {"answer_key_match": None}}
    after = {"student": {"content": "No.", "finish_reason": "stop"}, "check": {"format_ok": False},
             "verified_stage": {"new_method_segments": ["s2"], "unresolved_review_evidence": True,
                                "student_advanced": True, "complete_student_solution": False}}
    label, evidence = observed_followup(before, after, certified_value=None, exposed_answer=False)
    assert label == "method_requires_independent_check"
    assert evidence["new_method_segments_reviewer_only"] == ["s2"]
    after["verified_stage"].update(complete_student_solution=True, answer_key_match=True)
    label, evidence = observed_followup(before, after, certified_value=True, exposed_answer=False)
    assert label == "newly_delivered_key_match" and evidence["reviewer_supported_derivation"]
    assert observed_followup(before, after, certified_value=True, exposed_answer=True)[0] == "assisted_or_changed_solver"


def test_trial_cannot_bypass_source_leak_or_backend_role_confusion():
    source = [{"question": "He paints 3/5 of the rooms", "hint": "Each bedroom has 8 walls per room."}]
    assert "3/5" in transfer_findings("Calculate the complement of 3/5.", source)
    assert "action_properties" in transfer_findings("Treat action_properties as the teacher identity.", source)
    assert not transfer_findings("Ask for 2 independently verifiable intermediate values when needed.", source)
    class Library:
        def get_version(self, _id):
            return {"episodes": [{"guided_teacher_followup_projection_version": 1}], "text": "Each room uses 3/5."}
    class Store:
        def edition_events(self, _id):
            return source
    for trial in (True, False):
        with pytest.raises(ValueError, match="source-specific"):
            freeze_teacher_experience(Store(), Library(), "saved", trial=trial)


def test_routine_adoption_requires_task_appropriate_heldout_gains_and_complete_coverage():
    class Library:
        def get_version(self, _id):
            return {"id": "saved", "text": "Continue from the learner's last confirmed conclusion.",
                    "sha256": "sealed", "parent_id": None, "episodes": [{"guided_teacher_followup_projection_version": 1}]}
    class Store:
        pairs = [{"id": str(i), "comparable": True, "old_gain_supported": False,
                  "new_gain_supported": False} for i in range(2)]
        def edition_events(self, _id):
            return [{"case_id": "training-case", "hint": "Try the other room.", "question": "How many rooms?"}]
        def transfer_pairs(self, *_):
            return self.pairs
    store = Store()
    with pytest.raises(ValueError, match="coverage"):
        freeze_teacher_experience(store, Library(), "saved")
    for pair in store.pairs:
        pair["new_gain_supported"] = True
    store.pairs.append({"id": "no_teacher_request", "comparable": False})
    with pytest.raises(ValueError, match="coverage"):
        freeze_teacher_experience(store, Library(), "saved")
    store.pairs.pop()
    assert freeze_teacher_experience(store, Library(), "saved")["disclosure_teacher_adoption"]["scope"] == "paired_learner_evidence"
