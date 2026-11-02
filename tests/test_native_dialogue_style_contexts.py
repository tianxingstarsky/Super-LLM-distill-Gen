"""Current-turn style notes use the real native request and training history."""
from copy import deepcopy

import pytest

from lib.bootstrap.dialogue_style_contexts import dialogue_style_context_catalog
from lib.contexts.workflows.application.generate_native_cot import GenerateNativeCot
from lib.contexts.workflows.application.native_generation import generate_native_turn
from lib.contexts.workflows.domain.dialogue_style_context import (
    authenticated_context_positions, authenticated_record_context_positions, context_message_provenance,
    context_system_message,
)
from lib.contexts.workflows.domain.public_experience import (
    public_experience_provenance, with_public_experience, without_injected_public_experience,
)
from lib.contexts.workflows.domain.targets import training_record
from test_generation_node_use_cases import MemoryGenerationPort, native_recipe


@pytest.fixture(scope="session", autouse=True)
def mock_llm_server():
    yield  # Memory ports never contact a provider or a listening service.


def recipe():
    return {**native_recipe("multiturn"), "dialogue_style_context_version": 1,
        "dialogue_style_context_catalog": dialogue_style_context_catalog(),
        "dialogue_context_scope_version": 1, "native_experience_version": 1,
        "agent_experience": {"scopes": {"reasoning": {
            "enabled": True, "text": "Keep actual conditions and reasoning intact."}}}}


def unit():
    return {"id": "dialogue", "source_id": "scene", "kind": "brief", "text": "A maintenance discussion.",
        "qa_contract": {"question": "What should I do before checking the circuit?",
            "answer_policy": "answer", "dialogue_contexts": ["direct"]}}


def turn(port, frozen, source, messages):
    return generate_native_turn(port, frozen, stage="multiturn", unit=source,
        messages=messages, style={}, source_text="", source_scope="prepared_source")


def test_selected_note_is_public_separate_from_unchanged_common_prefix_and_raw_channels():
    frozen, source = recipe(), unit()
    messages = [{"role": "user", "content": source["qa_contract"]["question"]}]
    before = deepcopy((frozen, source, messages))
    native = " \nThe circuit must be isolated before inspection.\n "
    port = MemoryGenerationPort([{"answer": "Isolate the circuit first.", "reasoning": native}])
    row = turn(port, frozen, source, messages)
    expected_note, _ = context_system_message(frozen, ["direct"])
    assert row["status"] == "eligible" and len(port.calls) == 1
    assert row["messages"][:1] == with_public_experience([], frozen["agent_experience"]["scopes"]["reasoning"]["text"])
    assert row["messages"][1:3] == [*messages, expected_note]
    assert port.calls[0][3]["prompt"] == row["messages"][:-1]
    assert row["messages"][-1]["reasoning_content"] == native
    assert authenticated_record_context_positions(row) == (2,)
    receipt, = row["native_generation"]["dialogue_style_contexts"]
    assert receipt["context_types"] == ["direct"] and receipt["message"] == expected_note
    assert receipt["content_quality"] == "not_assessed"
    for kind in ("narrative", "exploratory", "iterative", "emotional"):
        assert frozen["dialogue_style_context_catalog"]["contexts"][kind]["content"] not in expected_note["content"]
    assert training_record("sft", row)["messages"] == row["messages"]
    assert training_record("cot", row)["question"] == row["question"]
    assert (frozen, source, messages) == before


@pytest.mark.parametrize("state,selected", [
    ({"dialogue_contexts": ["emotional", "narrative"]}, ["narrative", "emotional"]),
    ({"progress": "The next user asks a new question."}, []),
])
def test_followup_selects_again_without_relabeling_old_note_or_native_history(state, selected):
    frozen, source = recipe(), unit()
    port = MemoryGenerationPort([
        {"answer": "Isolate it first.", "reasoning": "  First native channel.\n"},
        {"answer": "Record the observed result.", "reasoning": "\n Second native channel.  "},
    ])
    first = turn(port, frozen, source, [{"role": "user", "content": source["qa_contract"]["question"]}])
    history = deepcopy(first["messages"])
    messages = [*history, {"role": "user", "content": "What comes after the inspection?"}]
    second = turn(port, frozen, {**source, "dialogue_step": {"dialogue_state": state}}, messages)
    assert second["status"] == "eligible" and len(port.calls) == 2
    assert second["messages"][:len(history)] == history
    assert second["messages"][-2] == context_system_message(frozen, selected)[0]
    assert [receipt["context_types"] for receipt in second["native_generation"]["dialogue_style_contexts"]] == [
        ["direct"], selected]
    assert second["messages"][3]["reasoning_content"] == "  First native channel.\n"
    assert "reasoning_content" not in port.calls[1][3]["prompt"][3]
    assert authenticated_record_context_positions(second) == (2, 5)
    assert first["messages"] == history


def test_actor_projection_strips_only_exact_run_owned_notes_and_provenance_covers_all_notes():
    frozen = recipe()
    note, _ = context_system_message(frozen, ["exploratory"])
    actor = [{"role": "user", "content": "Which options can I consider?"},
        {"role": "assistant", "content": "What constraints matter?", "reasoning_content": "Actual old reasoning."}]
    messages = with_public_experience([actor[0], note, actor[1]],
        frozen["agent_experience"]["scopes"]["reasoning"]["text"])
    original = deepcopy(messages)
    assert without_injected_public_experience(messages, frozen) == actor
    provenance = public_experience_provenance(messages, frozen)
    receipt, = provenance["dialogue_contexts"]
    assert receipt["message_index"] == 2 and receipt["message"] == note
    forged = deepcopy(messages)
    forged[2]["content"] += "\nDifferent instructions."
    assert forged[2] in without_injected_public_experience(forged, frozen)
    assert authenticated_context_positions(forged, frozen) == ()
    assert messages == original


def test_existing_pending_note_is_not_duplicated_and_modified_suffix_is_rejected():
    frozen, source = recipe(), unit()
    note, _ = context_system_message(frozen, ["direct"])
    messages = [{"role": "user", "content": source["qa_contract"]["question"]}, note]
    port = MemoryGenerationPort([{"answer": "Isolate it first.", "reasoning": "The condition precedes inspection."}])
    row = turn(port, frozen, source, messages)
    assert row["status"] == "eligible" and authenticated_record_context_positions(row) == (2,)
    assert row["messages"][1:-1] == messages
    changed = deepcopy(messages)
    changed[-1]["content"] += " "
    rejected, = GenerateNativeCot(port, frozen, stage="multiturn").execute({**source, "messages": changed}, {})
    assert rejected["reason"] == "cot_missing_dialogue" and len(port.calls) == 1


def test_frozen_legacy_recipe_does_not_read_or_inject_new_selection():
    frozen = native_recipe("multiturn")
    source = {**unit(), "dialogue_contexts": "ignored historical field"}
    messages = [{"role": "user", "content": source["qa_contract"]["question"]}]
    port = MemoryGenerationPort([{"answer": "Isolate it first.", "reasoning": "The condition precedes inspection."}])
    row = turn(port, frozen, source, messages)
    assert row["status"] == "eligible" and row["messages"][:-1] == messages
    assert "dialogue_style_contexts" not in row["native_generation"]
    assert port.calls[0][3]["prompt"] == messages


def test_public_style_and_review_notes_coexist_in_exact_native_repair_and_next_parent():
    from lib.contexts.workflows.application.continue_native_dialogue import ContinueNativeDialogue
    from lib.contexts.workflows.domain.dialogue import dialogue_digest
    from lib.contexts.workflows.domain.native_dialogue_continuation import native_dialogue_continuation_input
    from lib.contexts.workflows.domain.review_guidance import authenticated_review_guidance_positions
    from test_native_dialogue_continuation import NativeToolRepairPort, tool_repair_parent_and_recipe

    item, frozen = tool_repair_parent_and_recipe()
    frozen.update(dialogue_style_context_version=1, dialogue_style_context_catalog=dialogue_style_context_catalog(),
        native_feedback_guidance_version=7, native_instruction_scope_version=1)
    parent = item["record"]
    parent["source_context"]["qa_contract"]["dialogue_contexts"] = ["direct"]
    note, _ = context_system_message(frozen, ["direct"])
    parent["question"].append(deepcopy(note))
    parent["messages"].insert(-1, deepcopy(note))
    parent["native_generation"]["author_session"]["messages"].append(deepcopy(note))
    parent["native_generation"]["dialogue_style_contexts"] = context_message_provenance(parent["question"], frozen)
    item["revision_context"]["content_sha256"] = dialogue_digest(parent)
    before = deepcopy(parent)
    author = NativeToolRepairPort()
    author.guidance_version = 7
    row, = ContinueNativeDialogue(author, frozen, "new-draft").execute(item)
    assert len(author.tool_requests) == 1 and not author.requests and not author.assessments
    assert author.tool_requests[0]["messages"][:2] == parent["question"]
    assert row["messages"][1] == note
    assert authenticated_record_context_positions(row) == (1,)
    assert authenticated_review_guidance_positions(row) == (2,)
    assert native_dialogue_continuation_input(row, "Keep the actual conditions.", guidance_version=7)
    assert row["native_generation"]["previous_candidate"] == before["messages"][-1]
    assert parent == before


def test_real_multiturn_use_case_passes_current_director_context_and_keeps_exported_notes():
    from lib.contexts.workflows.application.generate_dialogue import GenerateMultiturnDialogue
    from lib.contexts.workflows.domain.dialogue import DialogueSource

    class DialoguePort(MemoryGenerationPort):
        def learner_prompt(self, contract):
            return contract["question"]

        def prepare_source(self, source, style):
            return DialogueSource({"task": source["text"]})

        def next_dialogue_step(self, source, messages, state, **options):
            assert state["dialogue_contexts"] == ["direct"]
            return {"continue": True, "user_message": "Help me revise the inspection record.",
                "response_policy": "answer", "reason": "The task now asks for a revision.",
                "dialogue_state": {**state, "dialogue_contexts": ["iterative"]}}

        def assess(self, key, context, candidate, **options):
            self.review_contexts = [*getattr(self, "review_contexts", []), deepcopy(context)]
            return super().assess(key, context, candidate, **options)

    frozen, source = recipe(), {**unit(), "family_id": "family"}
    frozen["conversation_turns"] = 2
    port = DialoguePort([
        {"answer": "Isolate it before inspection.", "reasoning": "  Keep the safety precondition.\n"},
        {"answer": "Use the actual inspection result in the revised record.", "reasoning": " Revise only the requested record. "},
    ])
    row, = GenerateMultiturnDialogue(port, frozen).execute(source)
    assert row["status"] == "eligible" and row["turn_count"] == 2
    assert authenticated_record_context_positions(row) == (2, 5)
    assert row["dialogue_steps"][0]["dialogue_state"]["dialogue_contexts"] == ["direct"]
    assert row["dialogue_steps"][1]["dialogue_state"]["dialogue_contexts"] == ["iterative"]
    assert row["messages"][5] == context_system_message(frozen, ["iterative"])[0]
    assert row["messages"][3]["reasoning_content"] == "  Keep the safety precondition.\n"
    assert len([call for call in port.calls if len(call) > 2 and call[2] == "workflow.cot_native_joint"]) == 2
    assert port.review_contexts[0]["dialogue_before_answer"] == row["messages"][:3]
    assert port.review_contexts[1]["dialogue_before_answer"] == row["messages"][:-1]
    assert port.review_contexts[0]["learner_system_provenance"]["dialogue_contexts"][0]["message_index"] == 2
