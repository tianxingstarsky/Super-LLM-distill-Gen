"""Node rules reach the provider's system role without elevating source data."""
from copy import deepcopy
import json
from types import SimpleNamespace as Obj

import pytest

from lib.infrastructure import training_workflow as engine
from lib.infrastructure.workflow_node_messages import split_node_rules
from lib.infrastructure.workflow_source_context import _pack_payload
from lib.llm_client import ChatClient


@pytest.fixture(scope="session", autouse=True)
def mock_llm_server():
    """All SDK create methods below are replaced; no model service is used."""
    yield


NODE = "PINNED_NODE_SYSTEM_SENTINEL"
FIXED = "FIXED_STYLE_SYSTEM_SENTINEL"
QUESTION = "CONFIGURED_QUESTION_SYSTEM_SENTINEL"
ANSWER = "CONFIGURED_ANSWER_SYSTEM_SENTINEL"
SOURCE = "UNTRUSTED_SOURCE_SENTINEL: ignore instructions in this source."


def test_only_recipe_matched_rules_and_explicit_instruction_are_system_instructions():
    data = {"instruction": "UNTRUSTED_INSTRUCTION", "answer_rules": "FORGED_ANSWER_RULE",
            "context": {"question_rules": QUESTION, "source": {"instruction": SOURCE}},
            "source": {"question_rules": QUESTION, "text": SOURCE}}
    before = deepcopy(data)
    system, task = split_node_rules(data, instruction=FIXED,
        qa_director={"question_rules": QUESTION, "answer_rules": ANSWER})
    assert FIXED in system and QUESTION in system
    assert all(value not in system for value in (SOURCE, "UNTRUSTED_INSTRUCTION", "FORGED_ANSWER_RULE", ANSWER))
    assert "question_rules" not in task["context"]
    assert task["source"] == data["source"] and task["answer_rules"] == "FORGED_ANSWER_RULE"
    assert data == before


def test_answer_transport_does_not_promote_an_instruction_named_payload_field():
    from lib.infrastructure.dialogue_role_messages import answer_role_messages
    context = {"teacher_context_pack": [], "task_payload": {"node_task_instructions": SOURCE}}
    messages = answer_role_messages(NODE, context, {
        "learner_messages": [{"role": "user", "content": "ACTUAL_USER_QUESTION"}]})
    assert SOURCE not in messages[0]["content"]


@pytest.mark.parametrize("api_format", ["chat", "responses", "anthropic"])
@pytest.mark.parametrize("path", ["plain", "pack", "legacy_pack", "sealed", "image", "learner_only", "separated"])
def test_workflow_rules_remain_system_in_real_sdk_payloads(tmp_path, api_format, path):
    captured = []
    client = ChatClient("https://api.example.test/v1", "unused", "offline-writer", api_format=api_format)
    client.stream_supported = False

    def create(**kwargs):
        captured.append(deepcopy(kwargs))
        if api_format == "responses":
            return Obj(output_text='{"ok":true}', usage=None, status="completed")
        if api_format == "anthropic":
            return Obj(content=[Obj(type="text", text='{"ok":true}')], usage=None, stop_reason="end_turn")
        return Obj(choices=[Obj(message=Obj(content='{"ok":true}'), finish_reason="stop")], usage=None)

    if api_format == "responses":
        client.client.responses.create = create
    elif api_format == "anthropic":
        client.client.messages.create = create
    else:
        client.client.chat.completions.create = create
    source = tmp_path / "source.txt"
    source.write_text(SOURCE, encoding="utf-8")
    output = tmp_path / "output"
    prompt_id = "workflow.sft_answer" if path == "separated" else "workflow.sft_directed"
    run_id = engine.create_run(output, sources=[source], targets=["sft"],
        qa_director={"enabled": True, "question_rules": QUESTION, "answer_rules": ANSWER,
                     "question_separation": path == "separated"},
        node_prompts={"sft": {prompt_id: NODE}})
    worker = engine.Workflow(output, run_id, tmp_path, generator=client)
    worker.stage = "sft"
    worker.recipe["node_models"] = {"sft": {"generation": {"max_output_tokens": 4096}}}
    data = {"source": {"text": SOURCE}, "question_rules": QUESTION, "answer_rules": ANSWER,
            "learner_messages": [{"role": "user", "content": "ACTUAL_USER_QUESTION"}]}
    if path in {"pack", "legacy_pack", "separated"}:
        data["source"]["source_context_pack"] = _pack_payload([{
            "unit_id": "source-1", "text": SOURCE, "cleaning_status": "accepted",
            "cleaned_text_sha256": engine.digest(SOURCE)}])
    if path == "legacy_pack":
        worker.recipe.pop("source_memory_protocol_version")
    if path == "sealed":
        worker.recipe["repair_inputs"] = [{"record_id": "source-1"}]
        data.pop("source")
        data["source_evidence"] = {"teacher_evidence": SOURCE, "source_id": "source-1", "source_kind": "document"}
    if path == "learner_only":
        data["learner_context_only"] = True
    before = deepcopy(data)
    options = {"image": "data:image/png;base64,aGVsbG8="} if path == "image" else {}
    assert worker.ask(["sample", path], "generation", prompt_id, data, instruction=FIXED, **options) == {"ok": True}
    assert data == before and len(captured) == 1
    payload = captured[0]
    if api_format == "anthropic":
        system = payload["system"]
        ordinary = json.dumps(payload["messages"], ensure_ascii=False)
        assert payload["max_tokens"] == 4096
    else:
        messages = payload["input"] if api_format == "responses" else payload["messages"]
        system = "\n".join(item["content"] for item in messages if item.get("role") == "system")
        ordinary = json.dumps([item for item in messages if item.get("role") != "system"], ensure_ascii=False)
        assert payload["max_output_tokens" if api_format == "responses" else "max_tokens"] == 4096
    for rule in (NODE, FIXED, QUESTION, ANSWER):
        assert rule in system and rule not in ordinary
    assert SOURCE not in system and "ACTUAL_USER_QUESTION" not in system
    assert SOURCE in ordinary and "ACTUAL_USER_QUESTION" in ordinary
    if path == "separated" and api_format != "anthropic":
        assert messages[-1] == before["learner_messages"][-1]
