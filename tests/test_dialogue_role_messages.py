"""Protocol boundaries for actual dialogue, private memory and the questioner."""
from copy import deepcopy
import json

import pytest

from lib.infrastructure.dialogue_role_messages import answer_role_messages, question_role_messages
from lib.model_protocols import responses_input, anthropic_request


@pytest.fixture(scope="session", autouse=True)
def mock_llm_server():
    """These pure message projections do not need a shared HTTP fixture."""
    yield


BRIEF = {"topic": "设备检查", "communication_goal": "请求指导安全的检查准备步骤。", "behaviors": ["request_action"]}
PRIVATE = "SOURCE_PRIVATE_SENTINEL: 检查前应断电；完成后记录结果。"
CONTEXT = {"teacher_context_pack": [{"id": "pack-1", "materials": [{"text": PRIVATE}]}], "task_payload": {}}


def answer_data():
    return {"qa_contract": {"qa_type": "closed_book", "question": "DIRECTOR_PLACEHOLDER_NOT_ACTUAL",
            "answer_policy": "answer", "question_brief": BRIEF},
            "answer_rules": "ANSWER_RULE_SENTINEL",
            "learner_messages": [{"role": "user", "content": "检查设备前应做什么？"}]}


def test_actual_user_is_not_a_serialized_workflow_envelope():
    messages = answer_role_messages("system", CONTEXT, answer_data())
    assert messages[-1] == {"role": "user", "content": "检查设备前应做什么？"}
    assert [m["name"] for m in messages if m["role"] == "tool"] == ["source_memory", "dialogue_dispatch"]
    ordinary = "\n".join(m["content"] for m in messages if m["role"] == "user")
    assert PRIVATE not in ordinary and "DIRECTOR_PLACEHOLDER" not in ordinary
    assert "ANSWER_RULE_SENTINEL" in messages[0]["content"]
    assert BRIEF["communication_goal"] not in ordinary


def test_actual_multiturn_roles_are_preserved_without_private_native_reasoning():
    data = answer_data()
    data["learner_messages"] = [{"role": "user", "content": "如何准备检查？"},
        {"role": "assistant", "content": "先确认设备状态。", "reasoning_content": "HIDDEN_TRACE_SENTINEL"},
        {"role": "user", "content": "我确认了，下一步呢？"}]
    messages = answer_role_messages("system", CONTEXT, data)
    assert [m["role"] for m in messages[-3:]] == ["user", "assistant", "user"]
    assert "HIDDEN_TRACE_SENTINEL" not in json.dumps(messages, ensure_ascii=False)
    assert messages[-1]["content"] == "我确认了，下一步呢？"


@pytest.mark.parametrize("adapter", [responses_input, lambda messages: anthropic_request(messages, json_mode=True)])
def test_provider_adapters_keep_actual_question_and_both_tool_histories(adapter):
    converted = adapter(answer_role_messages("system", CONTEXT, answer_data()))
    body = json.dumps(converted, ensure_ascii=False)
    assert PRIVATE in body and "检查设备前应做什么？" in body
    if isinstance(converted, list):
        assert sum(item.get("type") == "function_call_output" for item in converted) == 2
        assert converted[-1] == {"role": "user", "content": "检查设备前应做什么？"}
    else:
        blocks = [part for m in converted["messages"] if isinstance(m["content"], list) for part in m["content"]]
        assert sum(part["type"] == "tool_result" for part in blocks) == 2
        assert blocks[-1] == {"type": "text", "text": "检查设备前应做什么？"}


def test_questioner_gets_no_source_result_reference_answer_or_placeholder():
    messages = question_role_messages("system", {"question_brief": BRIEF, "question_rules": "QUESTION_RULE_SENTINEL"})
    body = json.dumps(messages, ensure_ascii=False)
    assert PRIVATE not in body and "DIRECTOR_PLACEHOLDER" not in body
    assert [m.get("name") for m in messages if m["role"] == "tool"] == ["dialogue_dispatch"]
    assert "QUESTION_RULE_SENTINEL" in messages[0]["content"]
    assert json.loads(next(m["content"] for m in messages if m["role"] == "tool")) == {"question_brief": BRIEF}


@pytest.mark.parametrize("private_key", ["source_context", "source_context_pack", "teacher_evidence", "answer", "qa_contract", "feedback"])
def test_questioner_rejects_private_request_fields(private_key):
    with pytest.raises(ValueError, match="private_context_in_question_request"):
        question_role_messages("system", {"question_brief": BRIEF, private_key: PRIVATE})


def test_questioner_followup_receives_only_completed_visible_turns():
    actual = [{"role": "user", "content": "设备运行中能检查吗？"},
              {"role": "assistant", "content": "需要先确认允许的检查方式。", "reasoning_content": PRIVATE}]
    messages = question_role_messages("system", {"question_brief": BRIEF, "visible_dialogue": actual})
    payload = json.loads(next(m["content"] for m in messages if m["role"] == "tool"))
    assert payload["visible_dialogue"] == [{"role": "user", "content": actual[0]["content"]},
                                         {"role": "assistant", "content": actual[1]["content"]}]
    assert PRIVATE not in json.dumps(messages, ensure_ascii=False)


def test_compatibility_memory_remains_separate_from_actual_user():
    messages = answer_role_messages("system", CONTEXT, answer_data(), transport="system")
    assert all(m["role"] != "tool" for m in messages)
    assert PRIVATE in next(m["content"] for m in messages if "<untrusted_source_memory>" in m["content"])
    assert PRIVATE not in "\n".join(m["content"] for m in messages if m["role"] == "user")
    assert messages[-1]["content"] == "检查设备前应做什么？"


def test_memory_prefix_is_stable_when_editorial_feedback_changes():
    first, second = answer_data(), answer_data()
    first["feedback"], second["feedback"] = "clarify", "answer more specifically"
    left, right = answer_role_messages("system", CONTEXT, first), answer_role_messages("system", CONTEXT, second)
    index = next(i for i, m in enumerate(left) if m.get("name") == "source_memory" and m["role"] == "tool")
    assert left[:index + 1] == right[:index + 1]
    assert left[index + 1:] != right[index + 1:]
    assert CONTEXT["task_payload"] == {}


@pytest.mark.parametrize("visible", [[{"role": "assistant", "content": "first"}],
                                    [{"role": "user", "content": "one"}, {"role": "user", "content": "two"}],
                                    [{"role": "user", "content": ""}]])
def test_invalid_dialogue_roles_fail_before_provider_call(visible):
    data = answer_data()
    data["learner_messages"] = visible
    with pytest.raises(ValueError, match="invalid_visible_dialogue"):
        answer_role_messages("system", CONTEXT, data)


def test_manual_source_is_a_separate_tool_with_honest_verification_scope():
    data = answer_data()
    data["source_context"] = {"text": PRIVATE, "kind": "human_provided"}
    messages = answer_role_messages("system", None, data)
    memory = json.loads(next(m["content"] for m in messages if m.get("name") == "source_memory" and m["role"] == "tool"))
    pack = memory["teacher_context_pack"][0]
    assert pack["materials"][0] == {"text": PRIVATE, "source_kind": "human_provided"}
    assert pack["verification"] == "application_source_only_not_independent_fact_verification"
    assert PRIVATE not in messages[-1]["content"]


@pytest.mark.parametrize("transport", ["tool", "system"])
def test_multiturn_flat_manual_source_and_user_requirements_reach_answerer(transport):
    data = answer_data()
    data.update(task=PRIVATE, brief="用简短英文回答，保留实际步骤。", provenance={"kind": "human_provided"})
    original = deepcopy(data)
    messages = answer_role_messages("system", None, data, transport=transport)
    body = json.dumps(messages, ensure_ascii=False)
    assert PRIVATE in body
    assert data["brief"] in body
    assert messages[-1] == original["learner_messages"][-1]
    ordinary = "\n".join(item["content"] for item in messages if item["role"] == "user")
    assert PRIVATE not in ordinary and data["brief"] not in ordinary
    assert data == original
    if transport == "tool":
        memory = json.loads(next(item["content"] for item in messages if item.get("name") == "source_memory"
                                 and item["role"] == "tool"))
        assert memory["teacher_context_pack"][0]["materials"][0]["source_kind"] == "human_provided"
        dispatch = json.loads(next(item["content"] for item in messages if item.get("name") == "dialogue_dispatch"
                                   and item["role"] == "tool"))
        assert dispatch["requirement"] == data["brief"]


def test_packed_sft_preserves_user_requirement_in_dispatch_without_changing_source_prefix():
    data = answer_data()
    data["requirement"] = "Output a compact checklist in English."
    plain = answer_role_messages("system", CONTEXT, answer_data())
    messages = answer_role_messages("system", CONTEXT, data)
    source_end = next(index for index, item in enumerate(plain)
                      if item["role"] == "tool" and item.get("name") == "source_memory")
    assert messages[:source_end + 1] == plain[:source_end + 1]
    dispatch = json.loads(next(item["content"] for item in messages if item.get("name") == "dialogue_dispatch"
                               and item["role"] == "tool"))
    assert dispatch["requirement"] == data["requirement"]
    assert messages[-1] == data["learner_messages"][-1]
