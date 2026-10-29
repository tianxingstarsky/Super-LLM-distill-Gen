"""User-owned import policy, immutable source actions and per-turn evidence scope."""
from copy import deepcopy

import pytest

from lib.contexts.workflows.application.prepare_conversation_reasoning import PrepareConversationReasoning
from lib.contexts.workflows.domain.conversation_reasoning import validate_conversation_reasoning
from lib.domain.reasoning_fields import sft_training_messages


class MemoryReasoningPort:
    def __init__(self, responses=()):
        self.responses = iter(responses)
        self.calls = []
        self.cancel_checks = 0

    def check_reasoning_cancel(self):
        self.cancel_checks += 1

    def request(self, key, role, prompt_id, payload, **options):
        self.calls.append((key, role, prompt_id, deepcopy(payload), options))
        response = next(self.responses)
        if isinstance(response, Exception):
            raise response
        return response


def conversation():
    return {"id": "imported-1", "source_id": "source-1", "kind": "conversation", "status": "ready",
            "messages": [
                {"role": "system", "content": "Check available evidence before answering."},
                {"role": "user", "content": "Can I use this file?"},
                {"role": "assistant", "content": "I need its contents.", "thinking": "The name alone is insufficient."},
                {"role": "user", "content": "Read it, please."},
                {"role": "assistant", "content": "", "tool_calls": [
                    {"id": "read-1", "type": "function", "function": {"name": "read_file", "arguments": '{"path":"note.txt"}'}}]},
                {"role": "tool", "tool_call_id": "read-1", "content": "The later observation contains 17 items."},
                {"role": "assistant", "content": "It contains 17 items.", "reasoning": "  "},
            ], "tools": [{"type": "function", "function": {"name": "read_file"}}],
            "tool_snapshots": {"after_last_turn": "MUST_NOT_ENTER_RECONSTRUCTION"}}


def policy(mode):
    return validate_conversation_reasoning({"version": 1, "mode": mode})


def test_old_recipe_is_exact_passthrough_and_preserve_marks_every_missing_assistant():
    original = conversation()
    before = deepcopy(original)
    port = MemoryReasoningPort()
    assert PrepareConversationReasoning(port, None).execute(original) is original
    prepared = PrepareConversationReasoning(port, policy("preserve")).execute(original)
    assert original == before and prepared["messages"] == before["messages"]
    receipt = prepared["conversation_reasoning"]
    assert (receipt["assistant_turns"], receipt["existing_turns"], receipt["missing_turns"]) == (3, 1, 2)
    assert [turn["message_index"] for turn in receipt["turns"] if turn["origin"] == "missing"] == [4, 6]
    assert receipt["semantic_status"] == "not_assessed" and prepared["status"] == "ready"
    assert port.calls == []


def test_exclude_skips_whole_incomplete_record_without_calling_or_rewriting():
    original = conversation()
    before = deepcopy(original)
    port = MemoryReasoningPort()
    prepared = PrepareConversationReasoning(port, policy("exclude")).execute(original)
    assert prepared["status"] == "skipped" and prepared["reason"] == "conversation_reasoning_missing"
    assert prepared["messages"] == before["messages"] and original == before
    assert prepared["conversation_reasoning"]["status"] == "excluded" and port.calls == []
    complete = deepcopy(original)
    for message in complete["messages"]:
        if message["role"] == "assistant":
            message.pop("reasoning", None)
            message["reasoning_content"] = message.get("thinking", "Recorded explanation.")
    assert PrepareConversationReasoning(port, policy("exclude")).execute(complete)["status"] == "ready"


def test_fill_uses_only_original_prefix_preserves_answers_and_marks_synthetic_per_turn():
    original = conversation()
    before = deepcopy(original)
    port = MemoryReasoningPort([{"reasoning_content": "Read the file before making claims about its contents."},
                               {"reasoning_content": "The observed count is 17; I can report that count."}])
    prepared = PrepareConversationReasoning(port, policy("fill_missing")).execute(original)
    assert original == before and len(port.calls) == 2 and port.cancel_checks == 2
    first, second = port.calls
    assert first[1:3] == ("generation", "workflow.conversation_reasoning")
    assert first[3]["history"] == before["messages"][:4]
    assert first[3]["assistant_response"] == before["messages"][4]
    assert "17 items" not in str(first[3]) and "MUST_NOT_ENTER_RECONSTRUCTION" not in str(first[3])
    assert second[3]["history"] == before["messages"][:6]
    assert "reasoning_content" not in second[3]["history"][4]
    assert first[4] == {"model_stage": "ingest", "prompt_stage": "ingest", "allow_reasoning_fallback": False,
                        "capture_response_details": True, "native_request_attempts": 1}
    assert prepared["messages"][2] == before["messages"][2]
    assert [message.get("content") for message in prepared["messages"]] == [message.get("content") for message in before["messages"]]
    assert prepared["messages"][4]["tool_calls"] == before["messages"][4]["tool_calls"]
    assert prepared["messages"][5] == before["messages"][5]
    assert "reasoning" not in prepared["messages"][6]
    assert prepared["reasoning_origin"] == "synthetic_explanation"
    receipt = prepared["conversation_reasoning"]
    assert [turn["origin"] for turn in receipt["turns"]] == ["source", "synthetic_explanation", "synthetic_explanation"]
    assert receipt["synthesized_turns"] == 2 and receipt["remaining_missing_turns"] == 0
    assert receipt["semantic_status"] == "not_assessed" and prepared["status"] == "ready"
    assert sft_training_messages(prepared["messages"])[2]["reasoning_content"] == before["messages"][2]["thinking"]


@pytest.mark.parametrize("response,reason", [
    ({"reasoning_content": ""}, "conversation_reasoning_empty_response"),
    ({"reasoning_content": "<INSUFFICIENT_EVIDENCE>"}, "conversation_reasoning_insufficient_evidence"),
    ({"reasoning_content": "replacement", "answer": "rewritten"}, "conversation_reasoning_invalid_response"),
    ({"reasoning_content": "bad\x00text"}, "conversation_reasoning_invalid_encoding"),
])
def test_failed_completion_quarantines_instead_of_dropping_missing_or_rewriting_answer(response, reason):
    original = conversation()
    before = deepcopy(original)
    port = MemoryReasoningPort([response])
    prepared = PrepareConversationReasoning(port, policy("fill_missing")).execute(original)
    assert prepared["status"] == "quarantined" and prepared["reason"] == reason
    assert prepared["messages"] == before["messages"] and original == before
    receipt = prepared["conversation_reasoning"]
    assert receipt["remaining_missing_turns"] == 2 and receipt["turns"][1]["raw_response"] == response
    assert receipt["turns"][1]["status"] == "failed" and len(port.calls) == 1


def test_partly_completed_record_stays_quarantined_with_completed_explanation_retained():
    port = MemoryReasoningPort([{"reasoning_content": "Only the current action is justified."},
                               {"reasoning_content": "<INSUFFICIENT_EVIDENCE>"}])
    prepared = PrepareConversationReasoning(port, policy("fill_missing")).execute(conversation())
    assert prepared["status"] == "quarantined"
    assert prepared["messages"][4]["reasoning_content"] == "Only the current action is justified."
    assert prepared["conversation_reasoning"]["synthesized_turns"] == 1
    assert prepared["conversation_reasoning"]["remaining_missing_turns"] == 1
    assert prepared["reasoning_origin"] == "synthetic_explanation"


def test_model_failure_and_cancellation_propagate_without_source_mutation():
    original = conversation()
    before = deepcopy(original)
    with pytest.raises(RuntimeError, match="upstream unavailable"):
        PrepareConversationReasoning(MemoryReasoningPort([RuntimeError("upstream unavailable")]),
                                     policy("fill_missing")).execute(original)
    class CancelledPort(MemoryReasoningPort):
        def check_reasoning_cancel(self):
            raise InterruptedError("cancelled")
    cancelled = CancelledPort()
    with pytest.raises(InterruptedError, match="cancelled"):
        PrepareConversationReasoning(cancelled, policy("fill_missing")).execute(original)
    assert original == before and cancelled.calls == []


def test_streaming_is_lazy_and_documents_are_unchanged():
    seen = []
    document = {"id": "document", "kind": "document", "status": "ready", "text": "A source."}
    def units():
        try:
            seen.append(1)
            yield document
            seen.append(2)
            yield conversation()
        finally:
            seen.append("closed")
    stream = PrepareConversationReasoning(MemoryReasoningPort(), policy("preserve")).execute_many(units())
    assert seen == []
    assert next(stream) is document and seen == [1]
    assert next(stream)["conversation_reasoning"]["missing_turns"] == 2 and seen == [1, 2]
    stream.close()
    assert seen == [1, 2, "closed"]


@pytest.mark.parametrize("value", [{}, {"version": True, "mode": "preserve"}, {"version": 2, "mode": "preserve"},
    {"version": 1, "mode": "overwrite"}, {"version": 1, "mode": "preserve", "hidden": True}])
def test_creation_rejects_unsupported_policy_contracts(value):
    with pytest.raises(ValueError, match="invalid_conversation_reasoning"):
        validate_conversation_reasoning(value)


def test_absent_creation_policy_stays_absent():
    assert validate_conversation_reasoning(None) is None
