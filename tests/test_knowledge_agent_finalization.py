"""Exercise bounded finalization through the actual model/tool request path."""
from copy import deepcopy
import json

import httpx
import pytest

from lib.infrastructure.knowledge_agent_session import digest
from lib.infrastructure.workflow_knowledge_agent import KnowledgeAgentObservationLimit
from lib.model_request_reliability import ModelJSONError
from tests.test_workflow_knowledge_agent import Client, Worker, context, final, tool


@pytest.fixture(scope="session", autouse=True)
def mock_llm_server():
    # Every request in this module is a fake client or an SDK MockTransport.
    yield


class RecordingClient(Client):
    def __init__(self, replies):
        super().__init__(replies)
        self.transports = []

    def tool_turn(self, messages, tools, **options):
        self.transports.append({"mode": "tool", "schemas": deepcopy(tools), "options": deepcopy(options)})
        result = super().tool_turn(messages, tools, **options)
        return {"role": "assistant", "content": result, "tool_calls": []} if isinstance(result, str) else result

    def chat(self, messages, **options):
        self.transports.append({"mode": "chat", "options": deepcopy(options)})
        return super().chat(messages, **options)


class FinalizingWorker(Worker):
    def __init__(self, tmp_path, client):
        super().__init__(tmp_path, client)
        self.recipe["knowledge_agent_finalization_version"] = 1

    def invalidate_checkpoint(self, key, *, stage=None):
        self._checkpoints.delete(stage or self.stage, digest(key))


def observations():
    return [tool("source_read", {"material_id": "material-a", "start_line": 1,
                                "end_line": 1}, f"call-{index}") for index in range(4)]


def test_four_real_tool_rounds_then_one_native_turn_with_tools_disabled(tmp_path, context):
    client = RecordingClient([*observations(), '{"tasks": []}'])
    worker = FinalizingWorker(tmp_path, client)
    assert worker.knowledge_agent_plan(["batch", 0], {}, context) == {"tasks": []}
    assert [request["mode"] for request in client.transports] == ["tool"] * 5
    assert all(request["schemas"] for request in client.transports)
    assert all("tool_choice" not in request["options"] for request in client.transports[:4])
    assert client.transports[-1]["options"]["tool_choice"] == "none"
    assert len(client.requests) == 5
    closing = [message for message in client.requests[-1]
               if message["role"] == "system" and "Tools are now disabled" in message["content"]]
    assert len(closing) == 1
    assert "not evidence of source exhaustion" in closing[0]["content"]
    assert not any("Tools are now disabled" in message.get("content", "")
                   for message in client.requests[0])
    receipts = [message for message in client.requests[-1] if message["role"] == "tool"]
    assert len(receipts) == 4
    assert all("45 kPa" in receipt["content"] for receipt in receipts)
    assert worker.knowledge_store().history(context[1]["session_id"]) == []
    worker.commit_knowledge_plan(["batch", 0])
    history = worker.knowledge_store().history(context[1]["session_id"])
    assert sum(message["role"] == "tool" for message in history) == 4
    assert not any("Tools are now disabled" in message.get("content", "") for message in history)
    assert worker.state["usage"]["generation"]["calls"] == 5


def test_successful_finalization_replays_all_checkpoints_without_calls(tmp_path, context):
    first = FinalizingWorker(tmp_path, RecordingClient([*observations(), '{"tasks": []}']))
    first.knowledge_agent_plan(["batch", 0], {}, context)
    first.commit_knowledge_plan(["batch", 0])
    resumed = FinalizingWorker(tmp_path, RecordingClient([]))
    assert resumed.knowledge_agent_plan(["batch", 0], {}, context) == {"tasks": []}
    resumed.commit_knowledge_plan(["batch", 0])
    assert resumed._client.requests == []
    assert sum(message["role"] == "tool" for message in resumed.knowledge_store().history(
        context[1]["session_id"])) == 4


@pytest.mark.parametrize("reply, error", [
    ('{"agent_pause": {"reason": "Need the adjacent source lines to validate the condition."}}',
     KnowledgeAgentObservationLimit),
    ('not a JSON plan', ModelJSONError),
    ('{"agent_pause": {"reason": "", "extra": true}}', ModelJSONError),
])
def test_incomplete_final_response_resumes_only_the_last_native_turn(tmp_path, context, reply, error):
    first_client = RecordingClient([*observations(), reply])
    first = FinalizingWorker(tmp_path, first_client)
    with pytest.raises(error):
        first.knowledge_agent_plan(["batch", 0], {}, context)
    assert len(first_client.requests) == 5
    assert first.knowledge_store().history(context[1]["session_id"]) == []
    assert not getattr(first, "_knowledge_pending", {})
    if error is KnowledgeAgentObservationLimit:
        found, paused = first._checkpoints.get("director", digest(["knowledge_agent_pause", ["batch", 0]]))
        assert found and paused["code"] == "knowledge_agent_observation_limit_reached"
        assert sum(message["role"] == "tool" for message in paused["observations"]) == 4
        assert "source_exhausted" not in json.dumps(paused)
    resumed_client = RecordingClient(['{"tasks": []}'])
    resumed = FinalizingWorker(tmp_path, resumed_client)
    assert resumed.knowledge_agent_plan(["batch", 0], {}, context) == {"tasks": []}
    assert [request["mode"] for request in resumed_client.transports] == ["tool"]
    assert resumed_client.transports[0]["options"]["tool_choice"] == "none"
    assert len([message for message in resumed_client.requests[0] if message["role"] == "tool"]) == 4
    resumed.commit_knowledge_plan(["batch", 0])
    assert resumed.knowledge_store().history(context[1]["session_id"])[-1]["role"] == "assistant"


def test_early_valid_plan_never_forces_additional_calls(tmp_path, context):
    client = RecordingClient([final()])
    worker = FinalizingWorker(tmp_path, client)
    assert worker.knowledge_agent_plan(["batch", 0], {}, context) == {"tasks": []}
    assert [request["mode"] for request in client.transports] == ["tool"]


def test_rejected_semantic_plan_invalidates_only_its_final_reply(tmp_path, context):
    first_client = RecordingClient([*observations(), '{"tasks": [{"id": "rejected-plan"}]}'])
    worker = FinalizingWorker(tmp_path, first_client)
    assert worker.knowledge_agent_plan(["batch", 0], {}, context)["tasks"]
    worker.invalidate_knowledge_plan(["batch", 0])
    worker.commit_knowledge_plan(["batch", 0])
    assert worker.knowledge_store().history(context[1]["session_id"]) == []
    resumed_client = RecordingClient(['{"tasks": []}'])
    resumed = FinalizingWorker(tmp_path, resumed_client)
    assert resumed.knowledge_agent_plan(["batch", 0], {}, context) == {"tasks": []}
    assert [request["mode"] for request in resumed_client.transports] == ["tool"]
    assert resumed_client.transports[0]["options"]["tool_choice"] == "none"
    assert sum(message["role"] == "tool" for message in resumed_client.requests[0]) == 4


def test_new_finalization_rule_isolates_session_without_changing_knowledge(tmp_path):
    from tests.test_workflow_knowledge_agent import material

    class NoHistory:
        def source_recent(self, *args, **kwargs):
            return []

    worker = Worker(tmp_path, RecordingClient([]))
    worker._director_evidence_materials = lambda unit: [material()]
    before = worker.prepare_knowledge_agent([{}], NoHistory(), ["sft"])[1]
    worker.recipe["knowledge_agent_finalization_version"] = 1
    after = worker.prepare_knowledge_agent([{}], NoHistory(), ["sft"])[1]
    assert before["knowledge_id"] == after["knowledge_id"]
    assert before["session_id"] != after["session_id"]


def native_reply(api, index, *, calls=True):
    from tests.test_model_tool_calls import reply

    response = reply(api, calls=calls, content='{"tasks": []}')
    if not calls:
        return response
    if api == "chat":
        call = response["choices"][0]["message"]["tool_calls"][0]
        call.update(id=f"real-{index}")
        call["function"].update(name="history_coverage", arguments="{}")
    elif api == "responses":
        call = response["output"][-1]
        call.update(id=f"fc-{index}", call_id=f"real-{index}", name="history_coverage", arguments="{}")
    else:
        response["content"][-1].update(id=f"real-{index}", name="history_coverage", input={})
    return response


@pytest.mark.parametrize("api", ["chat", "responses", "anthropic"])
def test_finalization_preserves_native_thinking_and_tool_pairs_in_real_sdk_wire(tmp_path, context, api):
    from tests.test_model_tool_calls import sdk_client

    captured = []

    def handle(request):
        captured.append(json.loads(request.content))
        return httpx.Response(200, json=native_reply(api, len(captured), calls=len(captured) <= 4))

    client = sdk_client(api, handle)
    worker = FinalizingWorker(tmp_path, client)
    # The fixture SDK uses the SDK default timeout, unlike production's
    # explicitly configured timeout. This small wire test needs no long output.
    worker.recipe["node_models"]["director"]["generation"]["max_output_tokens"] = 1024
    try:
        assert worker.knowledge_agent_plan(["batch", 0], {}, context) == {"tasks": []}
        assert len(captured) == 5
        choices = [request["tool_choice"] for request in captured]
        assert choices == ([{"type": "auto"}] * 4 + [{"type": "none"}]
                           if api == "anthropic" else ["auto"] * 4 + ["none"])
        assert all(request["tools"] for request in captured)
        if api == "chat":
            assistants = [message for message in captured[-1]["messages"] if message.get("tool_calls")]
            assert len(assistants) == 4
            assert all(message["reasoning_content"].strip() == "Check the original lines." for message in assistants)
            assert "provider_state" not in json.dumps(captured)
        elif api == "responses":
            states = [item for item in captured[-1]["input"] if item.get("type") == "reasoning"]
            assert len(states) == 4
            assert all(item["encrypted_content"] == "opaque-ciphertext" for item in states)
            results = [item for item in captured[-1]["input"] if item.get("type") == "function_call_output"]
            assert [item["call_id"] for item in results] == [f"real-{index}" for index in range(1, 5)]
        else:
            blocks = [part for message in captured[-1]["messages"]
                      if isinstance(message["content"], list) for part in message["content"]]
            assert sum(part.get("signature") == "untouched-signature" for part in blocks) == 4
            assert sum(part.get("data") == "opaque-redacted-data" for part in blocks) == 4
            assert [part["tool_use_id"] for part in blocks if part.get("type") == "tool_result"] == [
                f"real-{index}" for index in range(1, 5)]
        worker.commit_knowledge_plan(["batch", 0])
        history = worker.knowledge_store().history(context[1]["session_id"])
        assert not any("Tools are now disabled" in message.get("content", "") for message in history)
        assert sum(message["role"] == "tool" for message in history) == 4
    finally:
        client.client.close()


@pytest.mark.parametrize("api", ["chat", "responses", "anthropic"])
def test_upstream_cannot_execute_tools_despite_final_none_choice(api):
    from tests.test_model_tool_calls import MESSAGES, TOOLS, reply, sdk_client

    captured, events = [], []

    def handle(request):
        captured.append(json.loads(request.content))
        return httpx.Response(200, json=reply(api, calls=True))

    client = sdk_client(api, handle)
    try:
        with pytest.raises(ValueError, match="model_unexpected_tool_call"):
            client.tool_turn(MESSAGES, TOOLS, tool_choice="none", on_stream=events.append)
        assert len(captured) == 1
        assert client.usage["calls"] == 1
        assert [event["type"] for event in events] == ["start"]
    finally:
        client.client.close()


@pytest.mark.parametrize("choice", ["required", {}, False, None])
def test_invalid_tool_choice_fails_before_any_sdk_request(choice):
    from tests.test_model_tool_calls import MESSAGES, TOOLS, sdk_client

    captured = []
    client = sdk_client("chat", lambda request: captured.append(request))
    try:
        with pytest.raises(ValueError, match="invalid_model_tool_choice"):
            client.tool_turn(MESSAGES, TOOLS, tool_choice=choice)
        assert captured == []
    finally:
        client.client.close()
