"""Review revisions cannot attach an original provider trace to a new answer."""
from copy import deepcopy

import pytest

from lib.domain.workflow_targets import TRAINING_FIELDS
from lib.infrastructure.workflow_review_repair import (
    WorkflowReviewRepair, _manual_patch, preserve_provider_reasoning,
)


@pytest.fixture(scope="session", autouse=True)
def mock_llm_server():
    """Every review and repair response is injected offline."""
    yield


NATIVE = "\n  The original teacher rationale supports answer one.\n"
SUMMARY = "A provider summary of the original response."
CALL = {"stage": "sft", "sha256": "c" * 64}
EVIDENCE = {"source_kind": "synthetic", "teacher_evidence": "The original task context."}


def native_row():
    return {"id": "sample", "source_id": "source", "status": "eligible",
            "messages": [{"role": "user", "content": "An original self-contained question."},
                         {"role": "assistant", "content": "Answer one.", "reasoning_content": NATIVE}],
            "reasoning_origin": "provider_reasoning", "reasoning_summary": SUMMARY,
            "reasoning_api_format": "chat", "reasoning_call": deepcopy(CALL)}


def score():
    return {"keep": True, "grounded": True, "reasoning_valid": True, "correctness": 5,
            "scores": {name: 5 for name in ("correctness", "reasoning", "grounding", "instruction", "safety")},
            "reason": "The candidate satisfies the task."}


class ReviewProbe(WorkflowReviewRepair):
    def __init__(self, repair=None, *, mode="auto"):
        self.recipe = {"package_review": {}, "generation_preferences": {},
                       "review_repair": {"mode": mode, "max_rounds": 0}}
        self.state = {"id": "review-run", "stages": {"review": {}}}
        self.calls, self.repair = [], repair

    def save(self, **options):
        pass

    def _review_payload(self, target, row):
        return {field: deepcopy(row[field]) for field in TRAINING_FIELDS[target]}

    def _review_evidence(self, row):
        return deepcopy(EVIDENCE)

    def ask(self, key, role, prompt, data, **options):
        self.calls.append({"prompt": prompt, "data": deepcopy(data)})
        return deepcopy(self.repair) if prompt == "workflow.review_repair" else score()


@pytest.mark.parametrize("mode", ["human", "auto"])
def test_scoring_without_edit_retains_native_provenance(mode):
    original = native_row()
    item = {"target": "sft", "record": original, "score": 1.0, "approved": True}
    reviewed, = ReviewProbe(mode=mode)._review_repair_item(item)
    assert reviewed["status"] == "eligible"
    assert reviewed["reasoning_origin"] == "provider_reasoning"
    assert reviewed["reasoning_summary"] == SUMMARY and reviewed["reasoning_call"] == CALL
    assert reviewed["messages"][-1]["reasoning_content"] == NATIVE
    assert "provider_reasoning_history" not in reviewed


def test_human_answer_edit_archives_exact_native_and_removes_stale_current_trace():
    original = native_row()
    unchanged = deepcopy(original)
    original["messages"][0]["content"] += " A very long source-free prompt." * 2_000
    updated = _manual_patch("sft", original, {"answer": "Answer two."})
    assert updated["messages"][-1] == {"role": "assistant", "content": "Answer two."}
    assert updated["reasoning_origin"] == "human_revision"
    assert not {"reasoning_summary", "reasoning_api_format", "reasoning_call"} & set(updated)
    archived, = updated["provider_reasoning_history"]
    assert archived["reasoning_content"] == NATIVE and archived["answer"] == "Answer one."
    assert archived["reasoning_summary"] == SUMMARY and archived["reasoning_call"] == CALL
    assert archived["source_id"] == "source" and archived["scope"] == "historical_provider_response_only"
    assert "record" not in archived and "messages" not in archived
    assert len(str(archived)) < 1_500
    assert original["messages"][-1] == unchanged["messages"][-1]
    assert "provider_reasoning_history" not in original


def test_human_replaces_reasoning_and_marks_it_as_human_text():
    original = native_row()
    messages = deepcopy(original["messages"])
    messages[-1].update(content="Answer two.", reasoning_content="The human's revised explanation.")
    updated = _manual_patch("sft", original, {"messages": messages})
    assert updated["reasoning_origin"] == "human_revision"
    assert updated["messages"][-1]["reasoning_content"] == "The human's revised explanation."
    assert updated["provider_reasoning_history"][0]["reasoning_content"] == NATIVE


def test_question_edit_invalidates_original_native_context():
    updated = _manual_patch("sft", native_row(), {"question": "A revised user question."})
    assert "reasoning_content" not in updated["messages"][-1]
    assert updated["reasoning_origin"] == "human_revision"


@pytest.mark.parametrize("replacement", [None, "The repair model's revised explanation."])
def test_automatic_revision_never_labels_new_answer_with_original_trace(replacement):
    original = native_row()
    messages = deepcopy(original["messages"])
    messages[-1]["content"] = "Answer two."
    if replacement:
        messages[-1]["reasoning_content"] = replacement
    worker = ReviewProbe({"record": {"messages": messages}, "quotes": [], "uncertain": False})
    updated, issue = worker._repair_content("sft", original, EVIDENCE, ["item"], score(), "Fix the answer.")
    assert issue is None and updated["reasoning_origin"] == "prompt_revision"
    assert updated["messages"][-1].get("reasoning_content") == replacement
    assert updated["provider_reasoning_history"][0]["answer"] == "Answer one."
    assert not {"reasoning_summary", "reasoning_api_format", "reasoning_call"} & set(updated)


def test_noop_automatic_repair_preserves_current_provider_evidence():
    original = native_row()
    worker = ReviewProbe({"record": {"messages": original["messages"]}, "quotes": [], "uncertain": False})
    updated, issue = worker._repair_content("sft", original, EVIDENCE, ["item"], score(), "Check the answer.")
    assert issue is None and updated["reasoning_origin"] == "provider_reasoning"
    assert updated["reasoning_summary"] == SUMMARY and updated["reasoning_call"] == CALL
    assert "provider_reasoning_history" not in updated


def test_invalid_automatic_repair_cannot_mutate_original_provenance():
    original = native_row()
    messages = deepcopy(original["messages"])
    messages[-1]["content"] = "Answer two."
    worker = ReviewProbe({"record": {"messages": messages}, "quotes": [], "uncertain": False})
    updated, issue = worker._repair_content("sft", original, {**EVIDENCE, "source_kind": "document"},
                                          ["item"], score(), "Fix the answer.")
    assert issue == "review_repair_source_quotes_missing" and updated is original
    assert updated["reasoning_origin"] == "provider_reasoning" and "provider_reasoning_history" not in updated


def cot_row():
    row = native_row()
    row.update(question=row["messages"][:-1], reasoning=[NATIVE], answer="Answer one.")
    return row


def test_cot_answer_only_human_edit_cannot_keep_old_native_steps():
    updated = _manual_patch("cot", cot_row(), {"answer": "Answer two."})
    assert updated["reasoning"] == [] and "reasoning_content" not in updated["messages"][-1]
    assert updated["provider_reasoning_history"][0]["reasoning_content"] == NATIVE


def test_cot_revision_preserves_new_authored_steps_without_native_claim():
    original = cot_row()
    updated = _manual_patch("cot", original, {"corrected_record": {
        "answer": "Answer two.", "reasoning": ["The new authored derivation."]}})
    assert updated["reasoning"] == ["The new authored derivation."]
    assert updated["messages"][-1]["reasoning_content"] == "The new authored derivation."
    assert updated["reasoning_origin"] == "human_revision"


def test_cot_automatic_answer_only_edit_is_rejected_without_replacement_reasoning():
    original = cot_row()
    worker = ReviewProbe({"record": {"question": original["question"], "answer": "Answer two.",
        "reasoning": original["reasoning"]}, "quotes": [], "uncertain": False})
    updated, issue = worker._repair_content("cot", original, EVIDENCE, ["item"], score(), "Fix it.")
    assert issue == "empty_text" and updated is original


def multiturn_row():
    return {"id": "dialogue", "source_id": "source", "messages": [
        {"role": "user", "content": "First question."},
        {"role": "assistant", "content": "First answer.", "reasoning_content": "First native reasoning."},
        {"role": "user", "content": "Second question."},
        {"role": "assistant", "content": "Second answer.", "reasoning_content": "Second native reasoning."}],
        "turn_reviews": [
            {"turn": 1, "end_message_index": 1, "reasoning_origin": "provider_reasoning",
             "reasoning_summary": "First summary.", "reasoning_api_format": "chat", "reasoning_call": deepcopy(CALL)},
            {"turn": 2, "end_message_index": 3, "reasoning_origin": "provider_reasoning",
             "reasoning_summary": "Second summary.", "reasoning_api_format": "chat", "reasoning_call": deepcopy(CALL)}]}


@pytest.mark.parametrize("changed_turn", [0, 1])
def test_multiturn_revision_preserves_other_unmodified_native_turn(changed_turn):
    original = multiturn_row()
    messages = deepcopy(original["messages"])
    index = 2 * changed_turn + 1
    messages[index]["content"] = "The revised answer."
    updated = _manual_patch("multiturn", original, {"messages": messages})
    other = 1 - changed_turn
    assert "reasoning_content" not in updated["messages"][index]
    assert updated["turn_reviews"][changed_turn]["reasoning_origin"] == "human_revision"
    assert not {"reasoning_summary", "reasoning_api_format", "reasoning_call"} & set(updated["turn_reviews"][changed_turn])
    assert updated["messages"][2 * other + 1] == original["messages"][2 * other + 1]
    assert updated["turn_reviews"][other] == original["turn_reviews"][other]
    archived, = updated["provider_reasoning_history"]
    assert archived["end_message_index"] == index
    assert archived["reasoning_content"] == original["messages"][index]["reasoning_content"]
    assert "reasoning_origin" not in updated


def test_multiturn_changed_question_invalidates_only_its_native_turn():
    original = multiturn_row()
    messages = deepcopy(original["messages"])
    messages[2]["content"] = "Revised second question."
    updated = _manual_patch("multiturn", original, {"messages": messages})
    assert updated["messages"][1] == original["messages"][1]
    assert "reasoning_content" not in updated["messages"][3]
    assert updated["turn_reviews"][0] == original["turn_reviews"][0]


def test_summary_only_provenance_is_retired_after_edit_without_inventing_native_text():
    original = native_row()
    original["reasoning_origin"] = "none"
    original["messages"][-1].pop("reasoning_content")
    updated = _manual_patch("sft", original, {"answer": "Answer two."})
    archived, = updated["provider_reasoning_history"]
    assert archived["reasoning_summary"] == SUMMARY and "reasoning_content" not in archived
    assert "reasoning_summary" not in updated and "reasoning_content" not in updated["messages"][-1]


def test_prompt_trim_can_reuse_archive_helper_and_keeps_modified_chain():
    original = native_row()
    updated = deepcopy(original)
    updated["messages"][-1]["reasoning_content"] = "Trimmed explanation."
    preserve_provider_reasoning("sft", original, updated, origin="prompt_trim")
    assert updated["messages"][-1]["reasoning_content"] == "Trimmed explanation."
    assert updated["reasoning_origin"] == "prompt_trim"
    assert updated["provider_reasoning_history"][0]["reasoning_call"] == CALL


def test_legacy_nonprovider_rows_keep_existing_schema():
    original = native_row()
    original["reasoning_origin"] = "synthetic_explanation"
    for field in ("reasoning_summary", "reasoning_api_format", "reasoning_call"):
        original.pop(field)
    updated = _manual_patch("sft", original, {"answer": "Answer two."})
    assert updated["reasoning_origin"] == "synthetic_explanation"
    assert updated["messages"][-1]["reasoning_content"] == NATIVE
    assert "provider_reasoning_history" not in updated
