"""TRL export contracts for actual Agent traces and explicit preference pairs."""
from __future__ import annotations

from copy import deepcopy
import json

import pytest

from lib.domain.trainer_formats import to_trl_preference, to_trl_sft


CALCULATOR = {"type": "function", "function": {
    "name": "calculator", "description": "计算表达式",
    "parameters": {"type": "object", "properties": {"expression": {"type": "string"}},
                   "required": ["expression"], "additionalProperties": False},
}}


def test_internal_agent_trace_becomes_tool_aware_trl_row_without_mutation():
    sample = {"messages": [
        {"role": "user", "content": "计算 2+2"},
        {"role": "assistant", "content": "", "reasoning_content": "先调用计算器", "toolCalls": [
            {"id": "call-1", "name": "calculator", "input": {"expression": "2+2"}},
        ]},
        {"role": "tool", "toolCallId": "call-1", "content": "4"},
        {"role": "assistant", "content": "答案是 4"},
    ], "tools": [CALCULATOR], "id": "sample-1"}
    original = deepcopy(sample)

    result = to_trl_sft(sample)

    assert result == {"messages": [
        {"role": "user", "content": "计算 2+2"},
        {"role": "assistant", "thinking": "先调用计算器", "tool_calls": [
            {"type": "function", "function": {"name": "calculator", "arguments": {"expression": "2+2"}},
             "id": "call-1"},
        ]},
        {"role": "tool", "name": "calculator", "content": "4", "tool_call_id": "call-1"},
        {"role": "assistant", "content": "答案是 4"},
    ], "tools": [CALCULATOR]}
    assert sample == original


def test_openai_calls_and_out_of_order_results_match_by_id():
    sample = {"tools": [
        {"name": "read_power"},
        {"name": "calculator", "input_schema": CALCULATOR["function"]["parameters"]},
    ], "messages": [
        {"role": "user", "content": "检查并计算"},
        {"role": "assistant", "content": None, "tool_calls": [
            {"id": "a", "type": "function", "function": {"name": "calculator", "arguments": '{"expression":"2+2"}'}},
            {"id": "b", "type": "function", "function": {"name": "read_power", "arguments": "{}"}},
        ]},
        {"role": "tool", "tool_call_id": "b", "content": "已断电"},
        {"role": "tool", "tool_call_id": "a", "content": "4"},
        {"role": "assistant", "content": "电源已断开，结果为 4"},
    ]}
    result = to_trl_sft(sample)
    assert [tool["function"]["name"] for tool in result["tools"]] == ["calculator", "read_power"]
    assert result["tools"][1]["function"]["parameters"]["additionalProperties"] is False
    assert [message["name"] for message in result["messages"] if message["role"] == "tool"] == [
        "read_power", "calculator",
    ]
    assert result["messages"][1]["tool_calls"][0]["function"]["arguments"] == {"expression": "2+2"}
    assert json.dumps(result, ensure_ascii=False, sort_keys=True)


@pytest.mark.parametrize("messages,tools,reason", [
    ([{"role": "user", "content": "q"},
      {"role": "assistant", "toolCalls": [{"id": "a", "name": "read_power", "input": {}}]}],
     [{"name": "read_power"}], "trl_unclosed_tool_call"),
    ([{"role": "user", "content": "q"}, {"role": "tool", "toolCallId": "a", "content": "x"},
      {"role": "assistant", "content": "a"}], [], "trl_orphan_tool_result"),
    ([{"role": "user", "content": "q"},
      {"role": "assistant", "toolCalls": [{"id": "a", "name": "read_power", "input": "{"}]}],
     [{"name": "read_power"}], "trl_invalid_tool_arguments"),
    ([{"role": "user", "content": "q"},
      {"role": "assistant", "toolCalls": [{"id": "a", "name": "missing", "input": {}}]}],
     [{"name": "read_power"}], "trl_undefined_tool"),
    ([{"role": "user", "content": [{"type": "image_url", "image_url": "https://example.com/a.png"}]},
      {"role": "assistant", "content": "a"}], [], "trl_multimodal_content_unsupported"),
    ([{"role": "user", "content": "q"},
      {"role": "assistant", "toolCalls": [{"id": "a", "name": "read_power", "input": {"mode": "on"}}]}],
     [{"name": "read_power"}], "trl_tool_arguments_outside_schema"),
])
def test_sft_rejects_ambiguous_or_untrainable_records(messages, tools, reason):
    with pytest.raises(ValueError, match=reason):
        to_trl_sft({"messages": messages, "tools": tools})


def test_preference_export_uses_explicit_context_and_distinct_completions():
    pair = {"id": "p1", "prompt": [
        {"role": "system", "content": "简洁回答"},
        {"role": "user", "content": "2+2=?"},
    ], "chosen": [{"role": "assistant", "content": "4"}],
        "rejected": [{"role": "assistant", "content": "5"}]}
    result = to_trl_preference(pair)
    assert list(result) == ["prompt", "chosen", "rejected"]
    assert result["prompt"][-1] == {"role": "user", "content": "2+2=?"}
    assert result["chosen"] == [{"role": "assistant", "content": "4"}]
    assert result["rejected"] == [{"role": "assistant", "content": "5"}]


def test_standard_preference_format_and_equal_answer_rejection():
    row = to_trl_preference({"prompt": "2+2=?", "chosen": "4", "rejected": "5"})
    assert row == {"prompt": "2+2=?", "chosen": "4", "rejected": "5"}
    with pytest.raises(ValueError, match="trl_preference_answers_must_differ"):
        to_trl_preference({"prompt": "2+2=?", "chosen": "4", "rejected": " 4 "})
    with pytest.raises(ValueError, match="trl_preference_answers_must_differ"):
        to_trl_preference({"prompt": [{"role": "user", "content": "q"}],
                           "chosen": [{"role": "assistant", "content": "same", "reasoning_content": "good"}],
                           "rejected": [{"role": "assistant", "content": "same", "reasoning_content": "bad"}]})


def test_preference_rejects_multimodal_candidates_and_implicit_prompt_duplication():
    prompt = [{"role": "user", "content": "描述图片"}]
    with pytest.raises(ValueError, match="trl_multimodal_content_unsupported"):
        to_trl_preference({"prompt": prompt,
                           "chosen": [{"role": "assistant", "content": [{"type": "text", "text": "cat"}]}],
                           "rejected": [{"role": "assistant", "content": "dog"}]})
    with pytest.raises(ValueError, match="trl_preference_chosen_must_start_with_assistant"):
        to_trl_preference({"prompt": prompt,
                           "chosen": prompt + [{"role": "assistant", "content": "cat"}],
                           "rejected": [{"role": "assistant", "content": "dog"}]})


def test_schema_and_reasoning_conflicts_are_rejected_without_silent_loss():
    sample = {"messages": [{"role": "user", "content": "q"},
                           {"role": "assistant", "content": "a",
                            "reasoning_content": "first", "thinking": "second"}]}
    with pytest.raises(ValueError, match="trl_conflicting_reasoning_fields"):
        to_trl_sft(sample)
    with pytest.raises(ValueError, match="trl_multimodal_content_unsupported"):
        to_trl_sft({**sample, "image_url": "https://example.invalid/a.png"})
    with pytest.raises(ValueError, match="trl_invalid_tool_schema"):
        to_trl_sft({"messages": sample["messages"][:1] + [{"role": "assistant", "content": "a"}],
                    "tools": [{"type": "non_function", "name": "x"}]})


def test_empty_legacy_calls_do_not_hide_populated_openai_calls():
    sample = {"tools": [{"name": "read_power"}], "messages": [
        {"role": "user", "content": "电源状态？"},
        {"role": "assistant", "toolCalls": [], "tool_calls": [
            {"id": "a", "function": {"name": "read_power", "arguments": "{}"}},
        ]},
        {"role": "tool", "tool_call_id": "a", "content": "已断电"},
        {"role": "assistant", "content": "电源已断开"},
    ]}
    result = to_trl_sft(sample)
    assert result["messages"][1]["tool_calls"][0]["function"]["name"] == "read_power"
