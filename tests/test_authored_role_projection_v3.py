"""Exact reference projection has one public history and no answer backdoor."""
from copy import deepcopy
from hashlib import sha256
import json

import pytest

from lib.domain.workflow_quality import canonical
from lib.infrastructure.dialogue_role_messages import authored_role_messages
from lib.model_protocols import responses_input, anthropic_request


@pytest.fixture(scope="session", autouse=True)
def mock_llm_server():
    yield


def fixture_data():
    return {
        "prompt": [{"role": "system", "content": "RECORDED_SYSTEM"}, {"role": "user", "content": "CPT？"}],
        "source": {"kind": "conversation", "messages": [
            {"role": "system", "content": "RECORDED_SYSTEM"}, {"role": "user", "content": "CPT？"},
            {"role": "assistant", "content": "UNASSESSED_OLD_ANSWER"}]},
        "reference_answer_status": "unassessed_not_supplied", "generation_style": {"instruction": "明确自然"},
        "feedback": "EDITORIAL_ONLY",
    }


def memory():
    return {"teacher_context_pack": [{"id": "stable", "materials": [{"text": "FACT_MEMORY"}]}], "task_payload": {}}


def digest(value):
    return sha256(canonical(value).encode("utf-8")).hexdigest()


def dispatch(messages, transport="tool"):
    if transport == "tool":
        body = next(message["content"] for message in messages
                    if message["role"] == "tool" and message.get("name") == "dialogue_dispatch")
    else:
        wrapped = next(message["content"] for message in messages
                       if "<internal_dialogue_dispatch>" in message.get("content", ""))
        body = wrapped.split("\n", 1)[1].rsplit("\n", 1)[0]
    return json.loads(body)


@pytest.mark.parametrize("version,phase,transport,expected_sha", [
    (1, "combined", "tool", "7a9bb6d5141a801b94f6f50ccc286f988943a8af535a82ac50eb94964892f649"),
    (2, "rationale", "tool", "ac7478c6dbe326e667dc839a56d4b3df0097a823cadd05546f80986a68a30866"),
    (2, "native_answer", "system", "7dfa3d26bfee18ab4c72b69799ac066eb9aa622a0b53f0150bfd1df75e007e1c"),
    (2, "trim", "tool", "2e30c859eb55d0f7e284d6cb0df7ebf350864134b4061c9032037b3033068857"),
])
def test_v1_v2_requests_remain_byte_identical(version, phase, transport, expected_sha):
    assert digest(authored_role_messages("PRODUCTION", memory(), fixture_data(), transport=transport,
                                        role_projection_version=version, phase=phase)) == expected_sha


@pytest.mark.parametrize("phase,status", [
    ("rationale", "unassessed_not_supplied"), ("rationale", "model_assessed"),
    ("native_answer", "unassessed_not_supplied"), ("native_answer", "model_assessed"),
    ("combined", "unassessed_not_supplied"),
])
@pytest.mark.parametrize("transport", ["tool", "system"])
def test_matched_old_tail_cannot_reenter_independent_or_unassessed_phase(phase, status, transport):
    data = fixture_data()
    data["reference_answer_status"] = status
    data["reference_answer"] = "UNASSESSED_OLD_ANSWER"
    original = deepcopy(data)
    messages = authored_role_messages("rules", None, data, transport=transport,
                                     role_projection_version=3, phase=phase)
    reference = dispatch(messages, transport)["reference_dialogue"]
    assert reference["messages"] == []
    assert reference["shared_learner_prefix"]["message_count"] == len(data["prompt"])
    assert reference["shared_learner_prefix"]["sha256"] == digest(data["prompt"])
    assert reference["omitted_assistant_tail"]["source_message_index"] == len(data["prompt"])
    assert reference["omitted_assistant_tail"]["sha256"] == digest(data["source"]["messages"][-1])
    assert "UNASSESSED_OLD_ANSWER" not in canonical(messages)
    assert "reference_answer" not in dispatch(messages, transport)
    assert messages[-1] == data["prompt"][-1]
    assert data == original


@pytest.mark.parametrize("phase,status", [("combined", "model_assessed"), ("trim", "unassessed_not_supplied")])
def test_assessed_combined_and_editing_trim_keep_the_corresponding_old_tail(phase, status):
    data = {**fixture_data(), "reference_answer_status": status,
            "original_reasoning": ["EXPLANATION_TO_TRIM"], "final_answer": "IMMUTABLE_FINAL_ANSWER"}
    messages = authored_role_messages("rules", None, data, role_projection_version=3, phase=phase)
    work = dispatch(messages)
    assert work["reference_dialogue"]["messages"] == data["source"]["messages"][-1:]
    assert "omitted_assistant_tail" not in work["reference_dialogue"]
    assert work["original_reasoning"] == ["EXPLANATION_TO_TRIM"]
    assert work["final_answer"] == "IMMUTABLE_FINAL_ANSWER"


def test_long_history_is_transmitted_once_and_exact_prefix_receipt_can_be_checked():
    history = [{"role": "system", "content": "RECORDED_SYSTEM"},
               {"role": "developer", "content": "RECORDED_DEVELOPER"}]
    history += [{"role": "user", "content": f"QUESTION_{index}" + "k" * 1200}
                if index % 2 == 0 else {"role": "assistant", "content": f"ANSWER_{index}" + "m" * 1200}
                for index in range(21)]
    data = {"learner_messages": history, "source": {"kind": "conversation", "messages": [
        *deepcopy(history), {"role": "assistant", "content": "CURRENT_OLD_ANSWER"}]}}
    v2 = authored_role_messages("rules", None, data, role_projection_version=2, phase="rationale")
    v3 = authored_role_messages("rules", None, data, role_projection_version=3, phase="rationale")
    reference = dispatch(v3)["reference_dialogue"]
    assert reference["shared_learner_prefix"]["sha256"] == digest(history)
    assert reference["shared_learner_prefix"]["message_count"] == 23
    assert len(canonical(v2).encode()) - len(canonical(v3).encode()) > 24000
    assert canonical(v3).count("QUESTION_0") == 1
    assert canonical(v3).count("ANSWER_1m") == 1
    assert canonical(v3).count("RECORDED_SYSTEM") == 1
    assert canonical(v3).count("RECORDED_DEVELOPER") == 1
    assert "CURRENT_OLD_ANSWER" not in canonical(v3)
    assert v3[-21:] == history[2:]


@pytest.mark.parametrize("changed", [
    {"role": "user", "content": "CPT?"},
    {"role": "user", "content": "CPT？ "},
    {"role": "user", "content": "CPT？\\n"},
])
def test_similar_or_different_source_question_cannot_trigger_tail_deletion(changed):
    data = fixture_data()
    data["source"]["messages"][1] = changed
    messages = authored_role_messages("rules", None, data, role_projection_version=3, phase="rationale")
    reference = dispatch(messages)["reference_dialogue"]
    assert reference["messages"] == data["source"]["messages"][1:]
    assert reference["shared_learner_prefix"]["message_count"] == 1
    assert "omitted_assistant_tail" not in reference
    assert "UNASSESSED_OLD_ANSWER" in canonical(messages)


def test_unrelated_private_dialogue_is_not_removed_or_reclassified_as_live_turns():
    data = fixture_data()
    data["source"]["messages"] = [{"role": "user", "content": "UNRELATED_PRIVATE_QUESTION\\n"},
                                    {"role": "assistant", "content": "UNRELATED_PRIVATE_ANSWER"}]
    messages = authored_role_messages("rules", None, data, role_projection_version=3, phase="native_answer")
    reference = dispatch(messages)["reference_dialogue"]
    assert reference["messages"] == data["source"]["messages"]
    assert "shared_learner_prefix" not in reference and "omitted_assistant_tail" not in reference
    assert [message for message in messages if message["role"] == "user"] == data["prompt"][-1:]


def test_only_corresponding_single_tail_is_isolated_not_a_private_later_transcript():
    data = fixture_data()
    data["source"]["messages"].append({"role": "user", "content": "A_PRIVATE_FUTURE_TURN"})
    messages = authored_role_messages("rules", None, data, role_projection_version=3, phase="rationale")
    reference = dispatch(messages)["reference_dialogue"]
    assert reference["messages"] == data["source"]["messages"][2:]
    assert "omitted_assistant_tail" not in reference


@pytest.mark.parametrize("adapter", [responses_input, lambda messages: anthropic_request(messages, json_mode=True)])
def test_structured_tools_and_multimodal_content_keep_exact_literal_identity(adapter):
    image = {"type": "image_url", "image_url": {"url": "data:image/png;base64,aGVsbG8=", "detail": "high"}}
    actual = [{"role": "system", "content": "RECORDED_RULE"},
              {"role": "user", "content": [{"type": "text", "text": "IMAGE_REQUEST\\n"}, image]},
              {"role": "assistant", "content": "", "tool_calls": [{"id": "real-tool", "type": "function",
                    "function": {"name": "inspect_image", "arguments": '{"literal":"line\\\\n"}'}}]},
              {"role": "tool", "name": "inspect_image", "tool_call_id": "real-tool", "content": "TOOL_RESULT\\n"}]
    source_tail = {"role": "assistant", "content": "OLD_VISION_ANSWER"}
    data = {"learner_messages": actual, "source": {"kind": "conversation", "messages": [*deepcopy(actual), source_tail]}}
    messages = authored_role_messages("rules", None, data, role_projection_version=3, phase="rationale")
    assert messages[-3:] == actual[1:]
    reference = dispatch(messages)["reference_dialogue"]
    assert reference["messages"] == []
    assert reference["shared_learner_prefix"]["sha256"] == digest(actual)
    assert canonical(messages).count("IMAGE_REQUEST") == 1
    assert canonical(messages).count("TOOL_RESULT") == 1
    assert "OLD_VISION_ANSWER" not in canonical(messages)
    # The base64 bytes intentionally are not a real PNG; role conversion is
    # structural, and does not perform image decoding or a model call.
    converted = adapter(messages)
    assert "TOOL_RESULT" in canonical(converted)


def test_changed_tool_identity_does_not_collapse_seemingly_equal_reference_results():
    actual = [{"role": "user", "content": "Query."},
              {"role": "assistant", "content": "", "tool_calls": [{"id": "original-call", "type": "function",
                "function": {"name": "query", "arguments": "{}"}}]},
              {"role": "tool", "tool_call_id": "original-call", "content": "same result"}]
    source = deepcopy(actual)
    source[1]["tool_calls"][0]["id"] = "other-call"
    source[2]["tool_call_id"] = "other-call"
    source.append({"role": "assistant", "content": "OTHER_TASK_ANSWER"})
    messages = authored_role_messages("rules", None, {"learner_messages": actual, "source": {"messages": source}},
                                     role_projection_version=3, phase="rationale")
    reference = dispatch(messages)["reference_dialogue"]
    assert reference["shared_learner_prefix"]["message_count"] == 1
    assert reference["messages"] == source[1:]
    assert "omitted_assistant_tail" not in reference
