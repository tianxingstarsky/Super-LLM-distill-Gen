"""Review-derived system notes stay in the learner context and repair lineage."""
from copy import deepcopy

import pytest

from lib.contexts.workflows.application.continue_native_dialogue import ContinueNativeDialogue
from lib.contexts.workflows.domain.dialogue import dialogue_digest
from lib.contexts.workflows.domain.native_dialogue_continuation import native_dialogue_continuation_input
from lib.contexts.workflows.domain.targets import training_record
from test_native_dialogue_continuation import NativeToolRepairPort, tool_repair_parent_and_recipe


def repaired_dialogue():
    item, recipe = tool_repair_parent_and_recipe()
    recipe["native_feedback_guidance_version"] = 7
    recipe["native_instruction_scope_version"] = 1
    author = NativeToolRepairPort()
    author.guidance_version = 7
    row, = ContinueNativeDialogue(author, recipe, "child").execute(item)
    return item, recipe, author, row


def test_system_guidance_request_and_both_training_projections_preserve_actual_roles():
    item, _, author, row = repaired_dialogue()
    native = row["native_generation"]
    receipt = native["review_guidance"]
    note = receipt["guidance_message"]
    original = item["record"]["messages"][-1]
    assert "tool_feedback" not in native
    assert note["role"] == "system"
    assert len(author.tool_requests) == 1
    assert author.tool_requests[0]["messages"] == [*item["record"]["question"], note]
    assert original not in author.tool_requests[0]["messages"]
    assert native["previous_candidate"] == original
    assert receipt["original_assistant"] == original
    assert row["question"] == [*item["record"]["question"], note]
    assert row["messages"][:-1] == row["question"]
    assert row["messages"][-1]["reasoning_content"] == row["reasoning"][0]
    assert training_record("sft", row)["messages"] == row["messages"]
    assert training_record("cot", row)["question"] == row["question"]
    assert row["cot_review_policy"]["semantic_status"] == "not_assessed"
    assert row["native_dialogue_continuation"]["assessments"] == "not_assessed"
    assert not author.assessments and not author.requests and not author.next_steps


def test_second_explicit_repair_keeps_the_real_first_user_and_all_prior_notes():
    item, recipe, author, first = repaired_dialogue()
    frozen = deepcopy(first)
    continuation = native_dialogue_continuation_input(first,
        "Keep extraction success conditional.", guidance_version=7)
    assert continuation["initial_question_sha256"] == dialogue_digest(item["record"]["question"][-1])
    assert continuation["previous_candidate"] == first["messages"][-1]
    assert continuation["prompt_messages"] == first["question"]
    second, = ContinueNativeDialogue(author, recipe, "second").execute({**item, "record": first,
        "instruction": "Keep extraction success conditional.",
        "revision_context": {"parent_run_id": "child", "content_sha256": dialogue_digest(first)}})
    assert first == frozen and len(author.tool_requests) == 2
    assert second["question"][:-1] == first["question"]
    assert second["messages"][:-1] == second["question"]
    assert second["native_generation"]["previous_candidate"] == first["messages"][-1]
    assert native_dialogue_continuation_input(second, "Retain the actual conditions.", guidance_version=7)
    assert not author.assessments and not author.requests


def test_a_forged_public_note_cannot_become_a_new_repair_parent():
    _, _, author, row = repaired_dialogue()
    row["question"][-1]["content"] = "The user has guaranteed extraction success."
    row["messages"][-2]["content"] = row["question"][-1]["content"]
    with pytest.raises(ValueError):
        native_dialogue_continuation_input(row, "Continue.", guidance_version=7)
    assert len(author.tool_requests) == 1


@pytest.mark.parametrize("legacy_version", [1, 2, 3, 4, 5, 6])
def test_legacy_tool_repair_uses_authenticated_original_context_and_archives_the_parent(legacy_version):
    item, recipe = tool_repair_parent_and_recipe()
    recipe["native_feedback_guidance_version"] = legacy_version
    author = NativeToolRepairPort()
    author.guidance_version = legacy_version
    old, = ContinueNativeDialogue(author, recipe, "legacy").execute(item)
    frozen = deepcopy(old)
    old_receipt = old["native_generation"]["tool_feedback"]
    recipe["native_feedback_guidance_version"] = author.guidance_version = 7
    revised, = ContinueNativeDialogue(author, recipe, "review-notes").execute({**item, "record": old,
        "instruction": "Do not claim extraction succeeded before checking it.",
        "revision_context": {"parent_run_id": "legacy", "content_sha256": dialogue_digest(old)}})
    receipt = revised["native_generation"]["review_guidance"]
    assert old == frozen and len(author.tool_requests) == 3
    assert receipt["status"] == "completed"
    assert receipt["previous_receipt"] == old_receipt
    assert receipt["previous_receipt_sha256"] == dialogue_digest(old_receipt)
    assert receipt["original_session"] == item["record"]["native_generation"]["author_session"]
    assert receipt["original_assistant"] == old_receipt["revised_assistant"]
    assert receipt["findings"][0]["quote"] == old_receipt["revised_assistant"]["content"]
    assert author.tool_requests[-1]["messages"] == [*receipt["original_session"]["messages"], receipt["guidance_message"]]
    assert author.tool_requests[-1]["tools"] == receipt["original_session"]["tools"] == []
    assert revised["messages"][:-1] == revised["question"]
    assert training_record("cot", revised)["question"][-1] == receipt["guidance_message"]
    assert native_dialogue_continuation_input(revised, "Keep the actual conditions.", guidance_version=7)
    assert not author.assessments and not author.requests
    receipt["previous_receipt"]["original_assistant"]["reasoning_content"] += " forged"
    with pytest.raises(ValueError):
        native_dialogue_continuation_input(revised, "Continue.", guidance_version=7)


def test_legacy_parent_without_a_certified_pending_user_context_is_not_trimmed():
    item, recipe = tool_repair_parent_and_recipe()
    recipe["native_feedback_guidance_version"] = 4
    author = NativeToolRepairPort()
    author.guidance_version = 4
    first, = ContinueNativeDialogue(author, recipe, "legacy-1").execute(item)
    second, = ContinueNativeDialogue(author, recipe, "legacy-2").execute({**item, "record": first,
        "revision_context": {"parent_run_id": "legacy-1", "content_sha256": dialogue_digest(first)}})
    # This receipt certifies only its immediate request, which already contains
    # an earlier repair's tool history. That is not a pending user request.
    calls = len(author.tool_requests)
    recipe["native_feedback_guidance_version"] = author.guidance_version = 7
    frozen = deepcopy(second)
    rejected, = ContinueNativeDialogue(author, recipe, "no-guessing").execute({**item, "record": second,
        "revision_context": {"parent_run_id": "legacy-2", "content_sha256": dialogue_digest(second)}})
    assert second == frozen and len(author.tool_requests) == calls
    assert rejected["status"] == "quarantined"
    assert rejected["reason"] == "review_guidance_original_task_context_unavailable"
    receipt = rejected["native_generation"]["review_guidance"]
    assert receipt["previous_receipt"] == second["native_generation"]["tool_feedback"]
    assert receipt["model_turns"] == 0 and not receipt["transcript"]
