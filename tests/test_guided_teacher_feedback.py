"""Regression cases from real local teacher disclosures and their next learner responses."""

import hashlib
import json

import pytest

from lib.contexts.workflows.application.guided_teacher_feedback import (
    MIXED_INTERMEDIATE, freeze_teacher_experience, observed_followup, teacher_batch_episode, transfer_findings,
)
from lib.contexts.workflows.domain.guided_student_calculations import attributable_mixed_calculations, recovered_student_calculations
from lib.contexts.workflows.domain.reasoning_bank import no_new_memory
from lib.contexts.workflows.domain.experience_evolution import digest
from lib.contexts.workflows.infrastructure import guided_teacher_feedback as feedback
from lib.contexts.workflows.infrastructure.google_reasoning_bank import recorded_episode


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


@pytest.mark.parametrize("wording,earlier,allowed", [
    ("My first step was calculating Volume: $EQUATION$.\n</think>\nFinal answer: 45,000", "695", True),
    ("My first step was calculating Volume: $EQUATION$.", "That slab already occupies 5,000 cubic feet.", False),
    ("My first step was calculating Volume: $EQUATION$.", "I tried 100*100*.5 but got stuck.", False),
    ('The tutor said “EQUATION”.', "695", False),
    ('The tutor said:\nEQUATION', "695", False),
    ('The tutor said:\n“Start here\nEQUATION\nEnd here”', "695", False),
    ("> EQUATION", "695", False),
    ("```text\nEQUATION\n```", "695", False),
    ("<think>\nEQUATION\n</think>", "695", False),
    ("EQUATION </think>", "695", False),
    ("Suppose that EQUATION.", "695", False),
    ("Suppose instead:\nEQUATION", "695", False),
])
def test_mixed_feedback_requires_a_new_source_matched_assertion(wording, earlier, allowed):
    equation = r"100 \times 100 \times 0.5 = 5000"
    text = wording.replace("EQUATION", equation)
    case = {"question": "What is the cost of the foundation?", "source_reasoning": "Volume is <<100*100*.5=5000>>.",
            "gold": {"kind": "number", "value": "45000"}}
    pieces, offset = [], 0
    for i, line in enumerate(text.splitlines(keepends=True)):
        pieces.append(dict(id=str(i), author="student", text=line, start=offset, end=offset+len(line)))
        offset += len(line)
    recovered = list(recovered_student_calculations(case, pieces).values())
    archived = [{**f, "source_attempt": 2} for f in recovered]
    facts = attributable_mixed_calculations(text, recovered, archived, ["Use the volume formula.", earlier], 2)
    assert bool(facts) is allowed
    if allowed:
        fact, = facts
        assert text[slice(*fact["source_span"])] == equation
        assert case["source_reasoning"][slice(*fact["source_calculation_span"])] == "<<100*100*.5=5000>>"
        assert not attributable_mixed_calculations(text, recovered, [{**archived[0], "source_span": [0, 1]}], [], 2)


def test_mixed_followup_projects_disclosure_feedback_and_scopes_the_binary_upstream_label():
    event = {"id": "one", "outcome": "insufficient_evidence", "author": "teacher_authored", "learning_scope": MIXED_INTERMEDIATE,
             "evidence": {"output_issue": "private_or_template_markers"},
             "action_properties": {"grounded_in_quoted_premise": False, "continues_a_previous_hint": False,
                                   "cites_learner_result": False, "linked_to_verifiable_operation": True}}
    episode = recorded_episode(teacher_batch_episode([event]))
    assert episode["status"] == "fail"
    assert episode["assessment_source"]["scope"] == "whole_delivery_unconfirmed_with_checked_intermediate"
    assert "newly worked intermediate calculation" in episode["feedback"] and "overall success remain unconfirmed" in episode["feedback"]
    assert "private or template marker" in episode["feedback"] and "5,000" not in episode["feedback"]


def test_reobserving_same_mixed_receipts_preserves_consumption_and_edition(tmp_path, monkeypatch):
    source_path = tmp_path / "input.jsonl"
    source_path.write_text(json.dumps({"id": "slab", "question": "What does the foundation cost?",
                                      "gold": {"kind": "number", "value": "45000"}}), encoding="utf8")
    manifest = {"template_version": "4.10.4", "experience": {"version": "manual"},
                "input_sha256": hashlib.sha256(source_path.read_bytes()).hexdigest(),
                "endpoints": {"student": {"model": "learner"}}}
    (tmp_path / "manifest.json").write_text(json.dumps(manifest), encoding="utf8")
    source, = feedback.inputs(source_path, "4.10.4")
    folder = feedback.location(tmp_path, "slab")
    folder.mkdir(parents=True)
    case = {**source, "binding": digest(manifest), "source_sha256": digest(source), "status": "quarantine", "attempts": [{}, {}]}
    (folder / "case.json").write_text(json.dumps(case), encoding="utf8")
    event = {"id": "original-receipt-fingerprint", "round": 1, "case_id": "slab", "action_properties": {"grounded": True},
             "receipts": {"previous_student": "previous", "next_student": "next"}, "eligible": False,
             "evidence": {"replayed_new_calculations": ["volume"]}, "category": "insufficient_evidence"}
    monkeypatch.setattr(feedback, "teaching_events", lambda *a, **kw: [dict(event)])
    store = feedback.TeacherFeedbackStore(tmp_path / "library")
    assert store.observe(tmp_path, source_path)["eligible_unconsumed"] == 0
    event.update(eligible=True, learning_scope=MIXED_INTERMEDIATE)
    observed = store.observe(tmp_path, source_path)
    assert observed["eligible_unconsumed"] == 1 and not observed["events"][0]["new"]
    assert observed["by_category"] == {"insufficient_evidence": 1}
    learned, = store.batch(2)
    assert not learned["transfer_gain_supported"]
    store.consumed([learned], "edition")
    assert store.observe(tmp_path, source_path)["eligible_unconsumed"] == 0
    assert [r["id"] for r in store.edition_events("edition")] == [learned["id"]]


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
