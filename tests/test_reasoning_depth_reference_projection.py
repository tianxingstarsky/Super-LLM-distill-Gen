"""Generated depth controls cannot expose the old answer to independent CoT."""
from copy import deepcopy
import hashlib
import json

import pytest

from lib.domain.reasoning_depth import reasoning_depth_control_message
from lib.domain.workflow_quality import canonical
from lib.infrastructure.dialogue_role_messages import _reference_dialogue_v3, authored_role_messages


@pytest.fixture(scope="session", autouse=True)
def mock_llm_server():
    yield


USER = {"role": "user", "content": "Which setting is appropriate?"}
OLD = {"role": "assistant", "content": "UNASSESSED_OLD_ANSWER_771"}


def digest(value):
    return hashlib.sha256(canonical(value).encode("utf-8")).hexdigest()


def source(messages=None):
    return {"kind": "conversation", "source_id": "source-identity",
            "verification_status": "recorded_not_independently_verified",
            "messages": deepcopy([USER, OLD] if messages is None else messages)}


@pytest.mark.parametrize("depth", ["quick", "standard", "deep"])
@pytest.mark.parametrize("phase,status", [
    ("rationale", None), ("native_answer", None), ("combined", "unassessed_not_supplied"),
])
def test_new_learner_control_preserves_exact_shared_turns_and_omits_old_answer(depth, phase, status):
    original = source()
    actual = [reasoning_depth_control_message(depth), deepcopy(USER)]
    frozen = deepcopy((original, actual))
    reference = _reference_dialogue_v3(original, actual, phase=phase, reference_answer_status=status)
    assert reference["messages"] == []
    shared = reference["shared_learner_prefix"]
    assert shared["source_message_indices"] == [0] and shared["learner_message_indices"] == [1]
    assert shared["message_count"] == 1 and shared["sha256"] == digest([USER])
    assert "source_start_index" not in shared and "learner_start_index" not in shared
    omitted = reference["omitted_assistant_tail"]
    assert omitted["source_message_index"] == 1 and omitted["sha256"] == digest(OLD)
    assert "UNASSESSED_OLD_ANSWER_771" not in canonical(reference)
    controls = reference["reasoning_depth_control_projection"]
    assert controls["source_message_indices"] == [] and controls["learner_message_indices"] == [0]
    assert controls["source_controls_sha256"] == digest([])
    assert controls["learner_controls_sha256"] == digest(actual[:1])
    assert (original, actual) == frozen


@pytest.mark.parametrize("source_depth", ["quick", "standard", "deep"])
@pytest.mark.parametrize("learner_depth", ["quick", "standard", "deep"])
def test_all_known_control_replacements_keep_real_index_bindings(source_depth, learner_depth):
    original = source([reasoning_depth_control_message(source_depth), USER, OLD])
    actual = [reasoning_depth_control_message(learner_depth), deepcopy(USER)]
    reference = _reference_dialogue_v3(original, actual, phase="rationale")
    shared = reference["shared_learner_prefix"]
    assert shared["source_message_indices"] == shared["learner_message_indices"] == [1]
    assert reference["omitted_assistant_tail"]["source_message_index"] == 2
    assert reference["omitted_assistant_tail"]["sha256"] == digest(OLD)
    assert reference["messages"] == [] and reference["source_message_count"] == 3
    controls = reference["reasoning_depth_control_projection"]
    assert controls["source_controls_sha256"] == digest(original["messages"][:1])
    assert controls["learner_controls_sha256"] == digest(actual[:1])


def test_earlier_real_assistant_history_and_user_system_remain_literal_and_mapped():
    system = {"role": "system", "content": "Preserve this original task instruction exactly."}
    previous = {"role": "assistant", "content": "A previously visible answer."}
    followup = {"role": "user", "content": "What if the limit changes?"}
    original = source([system, USER, {**previous, "reasoning_content": "SOURCE_NATIVE_REASONING_STAYS_PRIVATE"}, followup, OLD])
    actual = [deepcopy(system), reasoning_depth_control_message("deep"), deepcopy(USER), previous, followup]
    frozen = deepcopy((original, actual))
    reference = _reference_dialogue_v3(original, actual, phase="native_answer")
    shared = reference["shared_learner_prefix"]
    assert shared["source_message_indices"] == [0, 1, 2, 3]
    assert shared["learner_message_indices"] == [0, 2, 3, 4]
    assert shared["sha256"] == digest([system, USER, previous, followup])
    assert reference["omitted_assistant_tail"]["source_message_index"] == 4
    assert reference["messages"] == []
    assert (original, actual) == frozen
    assert original["messages"][2]["reasoning_content"] == "SOURCE_NATIVE_REASONING_STAYS_PRIVATE"


@pytest.mark.parametrize("change", [
    lambda messages: messages[0].update(content="Changed user system instruction."),
    lambda messages: messages[-1].update(content="A different user question."),
    lambda messages: messages[0].update(role="developer"),
])
def test_arbitrary_system_user_or_role_changes_are_not_collapsed_into_a_shared_prefix(change):
    system = {"role": "system", "content": "A literal original system."}
    original = source([system, USER, OLD])
    actual = [deepcopy(system), reasoning_depth_control_message("deep"), deepcopy(USER)]
    change(actual)
    reference = _reference_dialogue_v3(original, actual, phase="rationale")
    assert "omitted_assistant_tail" not in reference
    assert "UNASSESSED_OLD_ANSWER_771" in canonical(reference["messages"])


def test_only_exact_known_control_messages_can_be_mapped_away():
    original = source()
    altered = reasoning_depth_control_message("deep")
    altered["content"] += " Do something else."
    actual = [altered, deepcopy(USER)]
    reference = _reference_dialogue_v3(original, actual, phase="rationale")
    assert "reasoning_depth_control_projection" not in reference
    assert "shared_learner_prefix" not in reference
    assert "omitted_assistant_tail" not in reference
    assert reference["messages"] == [USER, OLD]


def test_tool_identifiers_and_structured_visible_content_still_compare_exactly():
    call = {"role": "assistant", "content": "", "tool_calls": [{"id": "lookup-1", "type": "function",
        "function": {"name": "lookup", "arguments": "{}"}}]}
    tool = {"role": "tool", "tool_call_id": "lookup-1", "content": "Observed value: 7"}
    original = source([USER, call, tool, OLD])
    actual = [reasoning_depth_control_message("standard"), deepcopy(USER), deepcopy(call), deepcopy(tool)]
    reference = _reference_dialogue_v3(original, actual, phase="native_answer")
    assert reference["shared_learner_prefix"]["source_message_indices"] == [0, 1, 2]
    assert reference["shared_learner_prefix"]["learner_message_indices"] == [1, 2, 3]
    assert reference["omitted_assistant_tail"]["source_message_index"] == 3
    actual[-1]["tool_call_id"] = "other-observation"
    assert "omitted_assistant_tail" not in _reference_dialogue_v3(original, actual, phase="native_answer")


def test_assessed_reference_and_trim_keep_the_editable_old_tail_instead_of_deleting_it():
    original = source()
    actual = [reasoning_depth_control_message("deep"), deepcopy(USER)]
    for phase in ("combined", "trim"):
        reference = _reference_dialogue_v3(original, actual, phase=phase)
        assert "omitted_assistant_tail" not in reference
        assert reference["messages"] == [OLD]


def test_no_control_version_three_receipt_is_byte_identical_to_its_existing_shape():
    system = {"role": "system", "content": "An original instruction."}
    original = source([system, USER, OLD])
    actual = [system, USER]
    reference = _reference_dialogue_v3(original, actual, phase="rationale")
    expected = {"scope": "reference_only_not_learner_history_and_not_verified_facts",
        "source_kind": "conversation", "messages": [], "source_id": "source-identity",
        "verification_status": "recorded_not_independently_verified", "projection_version": 3,
        "source_message_count": 3,
        "shared_learner_prefix": {"source_start_index": 0, "learner_start_index": 0, "message_count": 2,
            "comparison_scope": "public_input_messages_before_instruction_demotion", "sha256": digest(actual)},
        "omitted_assistant_tail": {"source_message_index": 2, "sha256": digest(OLD),
            "scope": "independent_reasoning_and_answer_without_old_answer"}}
    assert canonical(reference) == canonical(expected)


@pytest.mark.parametrize("phase", ["rationale", "native_answer"])
def test_actual_authored_role_wire_keeps_old_answer_out_of_independent_dispatch(phase):
    original = source()
    actual = [reasoning_depth_control_message("deep"), deepcopy(USER)]
    data = {"prompt": actual, "source": original, "reference_answer_status": "unassessed_not_supplied"}
    frozen = deepcopy(data)
    wire = authored_role_messages("PRODUCTION_RULE", None, data, transport="system",
                                  role_projection_version=3, phase=phase)
    assert wire is not None and wire[-1] == USER
    assert "UNASSESSED_OLD_ANSWER_771" not in canonical(wire)
    dispatch_message = next(message for message in wire if message["content"].startswith("<internal_dialogue_dispatch>"))
    dispatch = json.loads(dispatch_message["content"].split("\n", 1)[1].rsplit("\n", 1)[0])
    reference = dispatch["reference_dialogue"]
    assert reference["shared_learner_prefix"]["source_message_indices"] == [0]
    assert reference["shared_learner_prefix"]["learner_message_indices"] == [1]
    assert reference["omitted_assistant_tail"]["source_message_index"] == 1
    assert dispatch["learner_instruction_context"] == actual[:1]
    assert data == frozen
