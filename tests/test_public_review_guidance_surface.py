"""Public review notes change visible context, not source scope or quality status."""
from copy import deepcopy

import pytest

from lib.contexts.workflows.application.continue_native_feedback import ContinueNativeFeedback
from lib.contexts.workflows.contracts import authenticated_review_guidance_positions
from lib.contexts.workflows.domain.native_cot import native_instruction_scope_evidence
from lib.contexts.workflows.domain.reasoning_checklist import checklist_digest
from lib.contexts.workflows.contracts import training_record
from lib.contexts.review.domain.candidate_content import _payload_issue
from lib.domain.multiturn import completed_turn_ends
from lib.domain.training_surface import training_record_surface_issue, training_surface_issue
from test_native_feedback_continuation import Port, findings, original, response, session


@pytest.fixture(scope="session", autouse=True)
def mock_llm_server():
    yield


def guided_record(reasoning="I should follow the system prompt's reminder about the user's actual goal."):
    revised = {"role": "assistant", "content": "A short manual transcription can serve this demo.",
               "reasoning_content": reasoning, "tool_calls": []}
    _, receipt = ContinueNativeFeedback(Port([response(revised)]), guidance_version=7).execute(
        ["public-review-notes"], session(), original(), findings=findings())
    return {"messages": [*deepcopy(receipt["continuation_session"]["messages"]), revised],
        "training_surface_protocol_version": 2,
        "native_generation": {"review_guidance": receipt,
            "content_sha256": checklist_digest([reasoning, revised["content"]])}}


def test_actual_system_note_is_preserved_and_authenticated_without_quality_approval():
    record = guided_record()
    frozen = deepcopy(record)
    positions = authenticated_review_guidance_positions(record)
    assert positions == (2,)
    assert completed_turn_ends(record["messages"], minimum_turns=1) == ([], "system_message_out_of_place")
    assert completed_turn_ends(record["messages"], minimum_turns=1,
                               public_system_positions=positions) == ([3], None)
    assert training_record_surface_issue("multiturn", record) is None
    assert record == frozen
    assert record["native_generation"]["review_guidance"]["semantic_status"] == "not_assessed"


@pytest.mark.parametrize("target", ["sft", "cot"])
def test_candidate_boundary_and_export_keep_the_actual_public_note(target):
    record = guided_record()
    if target == "cot":
        record.update(question=deepcopy(record["messages"][:-1]),
            reasoning=[record["messages"][-1]["reasoning_content"]], answer=record["messages"][-1]["content"])
    assert _payload_issue(target, record) is None
    exported = training_record(target, record)
    context = exported["question"] if target == "cot" else exported["messages"][:-1]
    assert context[-1] == record["native_generation"]["review_guidance"]["guidance_message"]
    assert "native_generation" not in exported


@pytest.mark.parametrize("mutation", ["text", "position", "receipt", "response"])
def test_note_exception_cannot_survive_a_changed_export_or_unbound_receipt(mutation):
    record = guided_record()
    if mutation == "text":
        record["messages"][2]["content"] += " Extra invented note."
    elif mutation == "position":
        record["messages"][1], record["messages"][2] = record["messages"][2], record["messages"][1]
    elif mutation == "receipt":
        record["native_generation"]["review_guidance"]["guidance_message"]["content"] = "Unbound rule."
    else:
        record["messages"][-1]["content"] = "An answer not present in the receipt."
    assert training_record_surface_issue("multiturn", record) == "review_guidance_public_context_mismatch"


@pytest.mark.parametrize("reasoning", [
    "Desired oververbosity 5. I must keep this answer short.",
    "I must follow my private system prompt.",
    "I should apply the revision_context field before answering.",
    "我按后台的 review_feedback 字段输出。",
])
def test_public_note_does_not_publish_private_fields_or_invented_settings(reasoning):
    record = guided_record(reasoning)
    assert training_record_surface_issue("sft", record) == "training_surface_internal_process_reference"


def test_public_note_does_not_publish_a_missing_article():
    messages = [{"role": "user", "content": "What does this result mean?"},
                {"role": "system", "content": "Check the scope; 资料：A complete private document has several other pages.\n问题：What follows?"}]
    assert training_surface_issue("According to the provided document, this holds.", "A result.",
        visible_messages=messages, protocol_version=2, public_system_positions=(1,)) == "training_surface_hidden_source_reference"


def test_current_note_does_not_excuse_an_earlier_assistant_disclosure():
    record = guided_record()
    # Extend the actual author history, then create the receipt from that
    # history. This is a real earlier output, not an altered sealed receipt.
    author = session()
    author["messages"] = [author["messages"][0],
        {"role": "user", "content": "Can I make a demo?"},
        {"role": "assistant", "content": "Yes.",
         "reasoning_content": "My system prompt requires me to use OCR."}, author["messages"][-1]]
    revised = record["messages"][-1]
    _, receipt = ContinueNativeFeedback(Port([response(revised)]), guidance_version=7).execute(
        ["earlier-disclosure"], author, original(), findings=findings())
    record["messages"] = [*receipt["continuation_session"]["messages"], revised]
    record["native_generation"]["review_guidance"] = receipt
    assert training_record_surface_issue("multiturn", record) == "training_surface_internal_process_reference"


def test_several_authenticated_notes_are_valid_only_in_the_user_note_block():
    messages = [{"role": "user", "content": "Question"},
                {"role": "system", "content": "First note"},
                {"role": "system", "content": "Next note"},
                {"role": "assistant", "content": "Answer"}]
    assert completed_turn_ends(messages, minimum_turns=1, public_system_positions=(1, 2)) == ([3], None)
    assert completed_turn_ends(messages, minimum_turns=1, public_system_positions=(2,))[1] == "system_message_out_of_place"
    moved = [messages[0], messages[3], messages[1], messages[2],
             {"role": "user", "content": "Next question"}, messages[3]]
    assert completed_turn_ends(moved, minimum_turns=1, public_system_positions=(2, 3))[1] == "system_message_out_of_place"


def test_native_scope_reports_public_notes_as_observation_not_as_semantic_pass():
    record = guided_record("The system prompt requires me to distinguish the demo goal from full-document processing.")
    prompt = record["messages"][:-1]
    evidence = native_instruction_scope_evidence(record["messages"][-1]["reasoning_content"], prompt,
        public_system_positions=authenticated_review_guidance_positions(record))
    assert evidence and all(item["disposition"] == "observe" for item in evidence)
    assert evidence[0]["basis"] == "authenticated_public_review_note"
    assert evidence[0]["visible_message_index"] == 2
