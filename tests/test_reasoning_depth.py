"""Depth is reproducible, public in student input, and absent from old runs."""
from copy import deepcopy

import pytest

from lib.domain.reasoning_depth import (
    REASONING_DEPTHS, RESOLVED_REASONING_DEPTHS, apply_reasoning_depth_control,
    is_reasoning_depth_control_message,
    reasoning_depth_control_message, reasoning_depth_instruction,
    resolve_reasoning_depth, validate_reasoning_depth,
)
from lib.domain.workflow_generation import style_for_sample, validate_node_generation


@pytest.mark.parametrize("depth", REASONING_DEPTHS)
@pytest.mark.parametrize("stage", ("sft", "cot", "multiturn"))
def test_node_depth_is_optional_literal_and_does_not_enable_authored_mode(depth, stage):
    config = {stage: {"enabled": False, "reasoning_depth": depth}}
    before = deepcopy(config)
    normalized = validate_node_generation(config)
    assert normalized[stage] == {
        "enabled": False, "style": "default", "instruction": "", "reasoning_depth": depth,
    }
    assert config == before
    assert "reasoning_depth" not in style_for_sample(
        config[stage], "sample", reasoning_depth_version=1)


@pytest.mark.parametrize("depth", [None, False, 1, [], {}, "", "high", "max", "Deep", " deep"])
def test_api_effort_invalid_values_and_untrimmed_names_are_not_depth(depth):
    with pytest.raises(ValueError, match="^invalid_reasoning_depth$"):
        validate_reasoning_depth(depth)
    with pytest.raises(ValueError, match="^invalid_node_generation$"):
        validate_node_generation({"sft": {"reasoning_depth": depth}})


def test_absent_version_keeps_exact_style_result_even_if_optional_depth_exists():
    config = {"style": "custom", "instruction": "Explain the real dependencies.",
              "reasoning_language": "en"}
    old = style_for_sample(config, "sample")
    assert style_for_sample({**config, "reasoning_depth": "deep"}, "sample") == old
    assert style_for_sample(config, "sample", reasoning_depth_version=1) == old


def test_depth_is_separate_from_pinned_style_and_language_instructions():
    base = {"style": "concise", "instruction": "Keep necessary evidence.", "reasoning_language": "zh_en"}
    old = style_for_sample(base, "sample", reasoning_language_version=3)
    controlled = style_for_sample({**base, "reasoning_depth": "deep"}, "sample",
                                  reasoning_language_version=3, reasoning_depth_version=1)
    assert {key: controlled[key] for key in old} == old
    assert controlled["reasoning_depth"] == "deep"
    assert controlled["reasoning_depth_instruction"] == reasoning_depth_instruction("deep")
    assert controlled["reasoning_depth_instruction"] not in controlled["instruction"]


def test_mixed_selection_is_stable_across_order_retries_and_unrelated_style_choices():
    identifiers = [f"family-{index}" for index in range(150)]
    first = {identifier: resolve_reasoning_depth("mixed", identifier) for identifier in identifiers}
    assert {identifier: resolve_reasoning_depth("mixed", identifier) for identifier in reversed(identifiers)} == first
    assert set(first.values()) == set(RESOLVED_REASONING_DEPTHS)
    for identifier, expected in first.items():
        for style in ("default", "mixed", "skeptical"):
            assert style_for_sample({"style": style, "reasoning_depth": "mixed"}, identifier,
                                    reasoning_depth_version=1)["reasoning_depth"] == expected
    pairs = {tuple(style_for_sample({"style": "mixed", "reasoning_depth": "mixed"}, identifier,
                                    reasoning_depth_version=1)[key]
                   for key in ("preset", "reasoning_depth")) for identifier in identifiers}
    assert len(pairs) == 12  # Style mixing does not dictate a corresponding depth.


@pytest.mark.parametrize("version", [None, False, True, -1, 2, "1"])
def test_unknown_or_boolean_contract_versions_do_not_silently_use_defaults(version):
    with pytest.raises(ValueError, match="^invalid_reasoning_depth_contract_version$"):
        style_for_sample({"reasoning_depth": "deep"}, "sample", reasoning_depth_version=version)
    with pytest.raises(ValueError, match="^invalid_reasoning_depth_contract_version$"):
        apply_reasoning_depth_control([{"role": "user", "content": "Why?"}], "deep", version=version)


@pytest.mark.parametrize("sample_id", [None, False, 4, ""])
def test_depth_resolution_requires_a_real_stable_identifier(sample_id):
    with pytest.raises(ValueError, match="^invalid_generation_sample_id$"):
        resolve_reasoning_depth("mixed", sample_id)


@pytest.mark.parametrize("operation", [reasoning_depth_instruction, reasoning_depth_control_message])
def test_public_input_and_generation_require_resolved_not_mixed_depth(operation):
    with pytest.raises(ValueError, match="^unresolved_reasoning_depth$"):
        operation("mixed")


def test_control_is_present_in_student_input_without_mutating_system_user_or_tools():
    messages = [
        {"role": "system", "content": "Original instructions."},
        {"role": "system", "content": "Preserve this second instruction."},
        {"role": "user", "content": [{"type": "text", "text": "What does this show?"},
                                     {"type": "image_url", "image_url": {"url": "test.png"}}]},
        {"role": "assistant", "content": "", "tool_calls": [{"id": "call-1"}]},
        {"role": "tool", "tool_call_id": "call-1", "content": "Recorded result."},
        {"role": "assistant", "content": "Answer.", "reasoning_content": "Existing explanation."},
    ]
    before = deepcopy(messages)
    result = apply_reasoning_depth_control(messages, "deep")
    assert result[:2] == messages[:2]
    assert result[2] == reasoning_depth_control_message("deep")
    assert result[3:] == messages[2:]
    assert messages == before
    assert apply_reasoning_depth_control(result, "deep") == result
    result[3]["content"][0]["text"] = "Mutation only in returned copy."
    assert messages == before


def test_control_is_idempotent_when_user_quotes_a_different_depth():
    user = {"role": "user", "content": reasoning_depth_control_message("quick")["content"]}
    result = apply_reasoning_depth_control([user], "deep")
    assert result == [reasoning_depth_control_message("deep"), user]
    assert apply_reasoning_depth_control(result, "deep") == result


@pytest.mark.parametrize("messages", [
    [reasoning_depth_control_message("quick"), {"role": "user", "content": "Why?"}],
    [reasoning_depth_control_message("deep"), reasoning_depth_control_message("quick"),
     {"role": "user", "content": "Why?"}],
])
def test_downstream_cot_can_replace_only_known_generated_controls(messages):
    before = deepcopy(messages)
    result = apply_reasoning_depth_control(messages, "deep")
    assert result == [reasoning_depth_control_message("deep"), messages[-1]]
    assert apply_reasoning_depth_control(result, "deep") == result
    assert messages == before


def test_only_exact_known_system_controls_are_replaced():
    lookalike = reasoning_depth_control_message("quick")["content"] + " Keep this user-authored addition."
    original = {"role": "system", "content": lookalike}
    user = {"role": "user", "content": "Why?"}
    result = apply_reasoning_depth_control([original, user], "deep")
    assert result == [original, reasoning_depth_control_message("deep"), user]


@pytest.mark.parametrize("depth", RESOLVED_REASONING_DEPTHS)
def test_control_recognition_does_not_promote_user_quotes_or_lookalike_instructions(depth):
    control = reasoning_depth_control_message(depth)
    assert is_reasoning_depth_control_message(control)
    assert not is_reasoning_depth_control_message({**control, "role": "user"})
    assert not is_reasoning_depth_control_message({**control, "content": control["content"] + " "})
    assert not is_reasoning_depth_control_message({**control, "content": control["content"].replace('version="1"', 'version="2"')})
    assert not is_reasoning_depth_control_message(None)


def test_dialogue_can_be_initialized_with_empty_or_system_only_prefix():
    assert apply_reasoning_depth_control([], "deep") == [reasoning_depth_control_message("deep")]
    original = {"role": "system", "content": "Preserve me."}
    prefix = apply_reasoning_depth_control([original], "quick")
    assert prefix == [original, reasoning_depth_control_message("quick")]
    assert apply_reasoning_depth_control(prefix, "deep") == [original, reasoning_depth_control_message("deep")]


@pytest.mark.parametrize("messages,error", [
    (None, "invalid_reasoning_depth_messages"),
    (["not a message"], "invalid_reasoning_depth_messages"),
    ([{"role": "assistant", "content": "Orphan response."}], "reasoning_depth_user_message_missing"),
    ([{"role": "user", "content": "Why?"}, reasoning_depth_control_message("deep")],
     "reasoning_depth_control_after_user"),
    ([{"role": "user", "content": "Why?"}, {"role": "system", "content": "Late instructions."}],
     "reasoning_depth_system_message_after_user"),
])
def test_control_does_not_repair_malformed_history_by_reordering_existing_messages(messages, error):
    with pytest.raises(ValueError, match=f"^{error}$"):
        apply_reasoning_depth_control(messages, "deep")


def test_public_control_survives_actual_chat_sharegpt_and_minimind_export_projection():
    from lib.exporters import _minimind_msg, to_chat_sample, to_sharegpt_sample

    messages = apply_reasoning_depth_control([
        {"role": "user", "content": "Why does the conclusion follow?"},
        {"role": "assistant", "content": "The conclusion.", "reasoning_content": "The necessary bridge."},
    ], "deep")
    sample = {"messages": messages}
    chat = to_chat_sample(sample)
    assert chat["messages"] == messages
    sharegpt = to_sharegpt_sample(sample)
    assert sharegpt["system"] == reasoning_depth_control_message("deep")["content"]
    assert "The necessary bridge." in sharegpt["conversations"][-1]["value"]
    assert _minimind_msg(messages[0]) == messages[0]
    assert _minimind_msg(messages[-1])["reasoning_content"] == "The necessary bridge."
