"""Automatic repairs may improve an answer without switching the user task."""
from copy import deepcopy

import pytest

from lib.infrastructure.workflow_review_repair import WorkflowReviewRepair, _manual_patch


@pytest.fixture(scope="session", autouse=True)
def mock_llm_server():
    yield


class OfflineRepair(WorkflowReviewRepair):
    def __init__(self, candidate, *, marker=1):
        self.candidate = candidate
        self.recipe = {"qa_agent_memory_version": marker}
        self.state = {"stages": {"review": {}}}
        self.request_data = None
        self.request_rules = None
    def save(self, **kwargs):
        pass
    def ask(self, *args, **kwargs):
        self.request_data = deepcopy(args[3])
        self.request_rules = deepcopy(kwargs)
        return {"record": deepcopy(self.candidate), "quotes": [], "uncertain": False}


def cot_row():
    return {"id": "sample", "question": [{"role": "user", "content": "Explain equipment isolation."}],
            "reasoning": ["Isolation prevents unexpected activation."], "answer": "Isolate the power source.",
            "qa_contract": {"question": "Explain equipment isolation.", "visible_context": "",
                            "qa_type": "closed_book", "answer_policy": "answer"}}


def repair(target, original, candidate, *, marker=1):
    return OfflineRepair(candidate, marker=marker)._repair_content(
        target, deepcopy(original), {"source_kind": "brief"}, ["offline"], {}, "")


def test_cot_repair_cannot_change_question_and_silently_rewrite_linked_sft():
    original = cot_row()
    changed = {key: deepcopy(original[key]) for key in ("question", "reasoning", "answer")}
    changed["question"][0]["content"] = "Explain a different process."
    result, issue = repair("cot", original, changed)
    assert issue == "review_repair_learner_task_changed"
    assert result["question"] == original["question"]
    assert "messages" not in result


@pytest.mark.parametrize("target", ["sft", "multiturn"])
@pytest.mark.parametrize("change", ["user", "drop_turn", "add_turn"])
def test_conversation_repair_preserves_user_turns_and_full_role_sequence(target, change):
    messages = [{"role": "user", "content": "Explain equipment isolation."},
                {"role": "assistant", "content": "Disconnect the power source."},
                {"role": "user", "content": "What should I verify next?"},
                {"role": "assistant", "content": "Verify the isolation state."}]
    candidate = deepcopy(messages)
    if change == "user":
        candidate[2]["content"] = "Explain a different process."
    elif change == "drop_turn":
        candidate = candidate[:2]
    else:
        candidate += [{"role": "user", "content": "An invented next task?"},
                      {"role": "assistant", "content": "An invented response."}]
    result, issue = repair(target, {"id": "sample", "messages": messages}, {"messages": candidate})
    assert issue == "review_repair_learner_task_changed"
    assert result["messages"] == messages


@pytest.mark.parametrize("target", ["dpo", "orpo", "rlaif"])
def test_preference_repair_preserves_actual_prompt(target):
    original = {"id": "sample", "prompt": [{"role": "user", "content": "Explain equipment isolation."}],
                "chosen": [{"role": "assistant", "content": "Disconnect and verify the power source."}],
                "rejected": [{"role": "assistant", "content": "Keep the power source connected."}]}
    candidate = {key: deepcopy(original[key]) for key in ("prompt", "chosen", "rejected")}
    candidate["prompt"][0]["content"] = "An easier replacement task?"
    _, issue = repair(target, original, candidate)
    assert issue == "review_repair_learner_task_changed"


def test_answer_and_authored_reasoning_can_be_repaired_without_task_change():
    original = cot_row()
    candidate = {key: deepcopy(original[key]) for key in ("question", "reasoning", "answer")}
    candidate.update(reasoning=["Isolating the supply prevents unexpected activation; verify isolation before work."],
                     answer="Disconnect the supply and verify isolation before beginning work.")
    result, issue = repair("cot", original, candidate)
    assert issue is None
    assert result["question"] == original["question"]
    assert result["messages"][0] == original["question"][0]
    assert result["answer"] == candidate["answer"]


def test_legacy_recipe_keeps_pinned_repair_behavior():
    original = cot_row()
    candidate = {key: deepcopy(original[key]) for key in ("question", "reasoning", "answer")}
    candidate["question"][0]["content"] = "A changed legacy question?"
    result, issue = repair("cot", original, candidate, marker=0)
    assert issue is None and result["question"] == candidate["question"]


def test_human_explicit_question_revision_remains_allowed_before_automatic_repair():
    original = cot_row()
    patched = _manual_patch("cot", original, {"question": "A human-authored revised task?"})
    assert patched["question"] == "A human-authored revised task?"
    candidate = {key: deepcopy(patched[key]) for key in ("question", "reasoning", "answer")}
    candidate["answer"] = "A corrected answer for the explicitly revised task."
    result, issue = repair("cot", patched, candidate)
    assert issue is None and result["question"] == patched["question"]


def test_existing_tool_fact_violation_keeps_its_specific_error():
    original = {"id": "sample", "messages": [{"role": "system", "content": "Keep the task fixed."},
        {"role": "user", "content": "Explain equipment isolation."},
        {"role": "assistant", "content": "Disconnect the supply."}]}
    candidate = {"messages": deepcopy(original["messages"])}
    candidate["messages"][0]["content"] = "Changed system instruction."
    _, issue = repair("sft", original, candidate)
    assert issue == "review_repair_tool_facts_changed"


@pytest.mark.parametrize("question_list", [False, True])
def test_cot_delta_repair_keeps_long_article_and_literal_escapes_byte_for_byte(question_list):
    article = ("Visible article: preserve every condition.\n" * 900) + r"Formula: \frac{a}{b}\n is a literal sequence." + "\nFinal article condition."
    question = [{"role": "user", "content": article}] if question_list else article
    original = {**cot_row(), "question": question}
    candidate = {"reasoning": ["The preserved conditions justify the corrected conclusion."], "answer": "A corrected answer."}
    workflow = OfflineRepair(candidate)
    updated, issue = workflow._repair_content("cot", deepcopy(original), {"source_kind": "brief"}, ["offline"], {}, "")
    assert issue is None
    assert updated["question"] == original["question"]
    preserved = updated["question"][0]["content"] if question_list else updated["question"]
    assert preserved.encode("utf-8") == article.encode("utf-8")
    assert updated["messages"][0]["content"] == article
    assert "增量输出" in workflow.request_rules["instruction"]
    assert "增量输出" not in str(workflow.request_data), "Delta instructions belong to the system instruction argument."
    assert original["reasoning"] != updated["reasoning"] and updated["answer"] == candidate["answer"]


@pytest.mark.parametrize("candidate", [
    {"answer": "Only an answer."},
    {"reasoning": ["Only an explanation."]},
    {"reasoning": ["A corrected explanation."], "answer": "A corrected answer.", "extra": "Unexpected field"},
    {"reasoning": ["A corrected explanation."], "answer": "A corrected answer.", "messages": []},
])
def test_cot_delta_schema_rejects_missing_or_extra_fields(candidate):
    original = cot_row()
    updated, issue = repair("cot", original, candidate)
    assert issue == "review_repair_invalid_schema" and updated == original


def test_legacy_cot_repair_still_requires_the_complete_existing_payload():
    original = cot_row()
    updated, issue = repair("cot", original, {"reasoning": ["A corrected explanation."], "answer": "A corrected answer."}, marker=0)
    assert issue == "review_repair_invalid_schema" and updated == original


def test_delta_format_is_not_accepted_for_other_targets():
    original = {"id": "sample", "messages": [{"role": "user", "content": "Keep this task."},
                                               {"role": "assistant", "content": "An answer."}]}
    updated, issue = repair("sft", original, {"reasoning": ["A corrected explanation."], "answer": "A corrected answer."})
    assert issue == "review_repair_invalid_schema" and updated == original
