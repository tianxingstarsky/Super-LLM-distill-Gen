"""The real dialogue request has one coherent schema and private evidence."""
from copy import deepcopy
from hashlib import sha256
import json

import pytest

from lib.infrastructure import training_workflow as engine
from lib.prompts import get, render
from lib.prompts.workflow_multiturn import (
    MULTITURN_ASSISTANT_OUTPUT_V1_5, MULTITURN_PRIVATE_QUOTES_V1_5,
)
from tests.test_workflow_learning_integration import prepare, FIRST, SECOND
from tests.test_workflow_styled_generation import model_task_data
from tests.test_workflow_provider_reasoning import SDKProvider, NATIVE, Reviewer, new_run, execute, record


@pytest.fixture(scope="session", autouse=True)
def mock_llm_server():
    """Python model doubles and intercepted SDK HTTP only; no external API."""
    yield


def test_new_dialogue_prompt_replaces_the_exclusive_two_field_schema():
    spec = get("workflow.multiturn_assistant")
    body = render(spec)
    assert spec.version == "1.8.0"
    assert MULTITURN_ASSISTANT_OUTPUT_V1_5 in body
    assert MULTITURN_PRIVATE_QUOTES_V1_5 in body
    assert '仅返回 {"answer":"完整回答","quotes":["逐字来源片段"]}，开放需求' not in body
    assert '没有generation_style时，仅返回JSON：{"answer":"完整回答","quotes"' in body
    assert '存在generation_style时，仅返回JSON：{"answer":"完整回答","reasoning"' in body
    assert "closed_book或knowledge_application只是不把教师原文附加" in body
    assert "不能因为用户没看到原文，就把quotes清空" in body
    assert "即使出现在显式reasoning或知识运用建议中，仍需对应证据" in body
    assert "每条quotes须完整位于一个实际所选知识块" in body


def test_old_v1_4_prompt_remains_byte_identical_for_frozen_requests():
    old = get("workflow.multiturn_assistant", version="1.4.0")
    assert sha256(old.template.encode()).hexdigest() == "3983352c7f66c2c979031054afa41fcabc7c5e77f1363f14e876bf95f6c578c6"
    assert '仅返回 {"answer":"完整回答","quotes":["逐字来源片段"]}，开放需求' in render(old)
    assert "本阶段只有以下两种互斥" not in render(old)


def test_schema_following_styled_model_returns_both_turns_without_format_retry(tmp_path, monkeypatch):
    worker, client, unit = prepare(tmp_path, review=True)
    old_chat = client.chat
    responses = []

    def follow_schema(messages, **kwargs):
        response = json.loads(old_chat(messages, **kwargs))
        if messages[-1] in ({"role": "user", "content": FIRST}, {"role": "user", "content": SECOND}):
            data = model_task_data(messages)
            assert data["generation_style"]["reasoning_language"] == "zh_en"
            # This reproduces the real failure: obeying the old exclusive
            # two-field schema removed authored reasoning on every first call.
            if MULTITURN_ASSISTANT_OUTPUT_V1_5 not in messages[0]["content"]:
                response.pop("reasoning", None)
            responses.append(deepcopy(response))
        return json.dumps(response, ensure_ascii=False)

    monkeypatch.setattr(client, "chat", follow_schema)
    frozen_recipe = deepcopy(worker.recipe)
    row, = worker.multiturn(unit)
    assert row["status"] == "eligible", row
    assert [turn["repair_attempts"] for turn in row["turn_reviews"]] == [0, 0]
    assert len(responses) == client.answer_count == 2
    assert all(set(response) == {"answer", "reasoning", "quotes"} and response["quotes"] for response in responses)
    assert worker.recipe == frozen_recipe
    assert worker.recipe["prompts"]["workflow.multiturn_assistant"]["version"] == "1.8.0"


def test_closed_book_facts_with_empty_quotes_are_still_quarantined(tmp_path, monkeypatch):
    worker, client, unit = prepare(tmp_path, review=True)
    old_chat = client.chat

    def empty_quotes(messages, **kwargs):
        response = json.loads(old_chat(messages, **kwargs))
        if messages[-1] == {"role": "user", "content": FIRST}:
            assert MULTITURN_PRIVATE_QUOTES_V1_5 in messages[0]["content"]
            response["quotes"] = []
        return json.dumps(response, ensure_ascii=False)

    monkeypatch.setattr(client, "chat", empty_quotes)
    row, = worker.multiturn(unit)
    assert row["status"] == "quarantined" and row["failed_turn"] == 1
    assert row["feedback"] == "文档回答缺少可逐字匹配的原文依据"
    assert row["turn_reviews"] == [] and client.answer_count == 2


@pytest.mark.parametrize("streaming", [False, True])
def test_ordinary_answer_quotes_schema_keeps_each_turns_real_native_reasoning(tmp_path, streaming):
    output, rid, path = new_run(tmp_path, targets=["multiturn"], conversation_turns=2)
    provider = SDKProvider(streaming=streaming, multiturn=True, explanation=None)
    try:
        state = execute(output, rid, tmp_path, provider, reviewer=Reviewer())
        assert state["status"] == "completed", state.get("error")
        row = record(path, "multiturn")
        assert [m["reasoning_content"] for m in row["messages"] if m["role"] == "assistant"] == [
            NATIVE + " 第 1 轮。", NATIVE + " 第 2 轮。"]
        assert [turn["reasoning_origin"] for turn in row["turn_reviews"]] == ["provider_reasoning"] * 2
        recipe = engine.read_json(path / "recipe.json")
        assert recipe["prompts"]["workflow.multiturn_assistant"]["version"] == "1.8.0"
        assert MULTITURN_ASSISTANT_OUTPUT_V1_5 in recipe["node_prompt_templates"]["multiturn"]["workflow.multiturn_assistant"]
    finally:
        provider.close()
