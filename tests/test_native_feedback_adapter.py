"""Original-node tool continuation, with a local model boundary and no API calls."""
from copy import deepcopy
import json
from threading import Lock
from types import SimpleNamespace

import pytest

from lib.contexts.workflows.domain.reasoning_checklist import checklist_digest
from lib.contexts.workflows.infrastructure.authored_edit_adapter import author_session
from lib.contexts.workflows.infrastructure.native_feedback_adapter import NativeFeedbackAdapter
from lib.contexts.workflows.infrastructure.reasoning_agent_adapter import CheckpointedReasoningAgent


TOOLS = [{"type": "function", "function": {"name": "read_review_feedback",
    "parameters": {"type": "object", "properties": {}}}}]
ORIGINAL = [{"role": "system", "content": "Frozen task and source."},
    {"role": "user", "content": "Can I demonstrate this tomorrow?"}]
HISTORY = [*ORIGINAL, {"role": "assistant", "content": "The original draft.",
    "reasoning_content": "Original native thought, preserved byte for byte.\n",
    "tool_calls": [{"id": "real-call", "type": "function", "function": {
        "name": "read_review_feedback", "arguments": "{}"}}]},
    {"role": "tool", "tool_call_id": "real-call", "content": "Saved review observation."}]


class NativeClient:
    def __init__(self, model):
        self.model, self.api_format = model, "chat"
        self.client = SimpleNamespace(base_url="https://api.deepseek.com/v1", api_key="test-only")
        self.reasoning = {"mode": "enabled", "effort": "high"}
        self.model_info = {"tools": True, "reasoning": True}
        self.calls, self.usage = [], {}

    def tool_turn(self, messages, tools, **options):
        self.calls.append((deepcopy(messages), deepcopy(tools), {
            key: value for key, value in options.items() if key not in {"on_stream", "response_details"}}))
        self.usage.update(calls=self.usage.get("calls", 0) + 1,
            prompt_tokens=self.usage.get("prompt_tokens", 0) + 100,
            completion_tokens=self.usage.get("completion_tokens", 0) + 20)
        assistant = {"role": "assistant", "reasoning_content": "New native correction.",
            "content": "The revised answer.", "tool_calls": []}
        if "response_details" in options:
            options["response_details"].update(reasoning_content=assistant["reasoning_content"], finish_reason="stop")
        return assistant


def runtime_for(tmp_path):
    clients = {stage: NativeClient(stage + "-author") for stage in ("sft", "multiturn", "cot")}
    cache, loaded, keys = {}, [], []

    def client(role, *, stage):
        loaded.append((role, stage))
        return clients[stage]

    def checkpoint(key, invoke):
        keys.append(deepcopy(key))
        identity = json.dumps(key)
        if identity not in cache:
            cache[identity] = invoke()
        return deepcopy(cache[identity])

    runtime = SimpleNamespace(client=client, _client_locks={id(c): Lock() for c in clients.values()},
        _lock=Lock(), check_cancel=lambda: None, event=lambda *a, **kw: None, state={},
        path=tmp_path, stage="review", checkpoint=checkpoint,
        recipe={"node_models": {stage: {"generation": {"model": c.model, "max_output_tokens": 32768}}
            for stage, c in clients.items()}})
    return runtime, clients, loaded, keys


def session_for(runtime, client, stage):
    return author_session(client, runtime.recipe["node_models"][stage]["generation"], ORIGINAL, [],
        stage=stage, role="generation", max_tokens=33000, temperature=0.2)


@pytest.mark.parametrize("stage", ["sft", "multiturn"])
def test_native_feedback_uses_original_node_preserves_history_and_replays_without_request(tmp_path, stage):
    runtime, clients, loaded, keys = runtime_for(tmp_path)
    session = session_for(runtime, clients[stage], stage)
    before = deepcopy((session, HISTORY, TOOLS))
    adapter = NativeFeedbackAdapter(runtime)
    first = adapter.turn(["candidate"], session, HISTORY, TOOLS, final=True)
    again = adapter.turn(["candidate"], session, HISTORY, TOOLS, final=True)
    assert first == again and (session, HISTORY, TOOLS) == before
    assert len(clients[stage].calls) == 1 and not clients["cot"].calls
    assert all(item == ("generation", stage) for item in loaded)
    assert runtime.stage == "review"
    sent_messages, sent_tools, options = clients[stage].calls[0]
    assert sent_messages == HISTORY and sent_tools == TOOLS
    assert options == {"max_tokens": 33000, "temperature": 0.2, "retries": 1, "tool_choice": "none"}
    assert clients[stage].reasoning == {"mode": "enabled", "effort": "high"}
    assert first["assistant"]["reasoning_content"] == "New native correction."
    assert first["author_continuation"]["tools_preserved"] is False
    assert first["author_continuation"]["message_prefix_preserved"] is True
    assert first["author_continuation"]["model_stage"] == stage
    assert runtime.state["usage"]["generation"] == {"calls": 1, "prompt_tokens": 100, "completion_tokens": 20}
    assert "model_stage_v1" in keys[0] and stage in keys[0]


@pytest.mark.parametrize("change", ["model", "endpoint", "credential", "binding"])
def test_native_feedback_refuses_changed_author_before_api(tmp_path, change):
    runtime, clients, _, _ = runtime_for(tmp_path)
    client = clients["multiturn"]
    session = session_for(runtime, client, "multiturn")
    if change == "model":
        client.model = "another-author"
    elif change == "endpoint":
        client.client.base_url = "https://another.example/v1"
    elif change == "credential":
        client.client.api_key = "different-test-only"
    else:
        runtime.recipe["node_models"]["multiturn"]["generation"]["max_output_tokens"] = 65536
    with pytest.raises(ValueError, match="^native_feedback_original_model_changed$"):
        NativeFeedbackAdapter(runtime).turn([], session, HISTORY, TOOLS)
    assert not client.calls


def test_native_feedback_refuses_changed_original_prefix_before_api(tmp_path):
    runtime, clients, _, _ = runtime_for(tmp_path)
    session = session_for(runtime, clients["multiturn"], "multiturn")
    changed = deepcopy(HISTORY)
    changed[0]["content"] = "Changed source and task."
    with pytest.raises(ValueError, match="^native_feedback_original_prefix_changed$"):
        NativeFeedbackAdapter(runtime).turn([], session, changed, TOOLS)
    assert not clients["multiturn"].calls


@pytest.mark.parametrize("change,issue", [
    ("responses", "native_tools_unsupported"), ("no_tools", "native_tools_unsupported"),
    ("no_reasoning", "native_tools_unsupported"), ("no_transport", "native_tools_unsupported"),
    ("auto", "thinking_not_enabled"), ("disabled", "thinking_not_enabled")])
def test_native_feedback_requires_native_chat_thinking_tools_before_api(tmp_path, change, issue):
    runtime, clients, _, _ = runtime_for(tmp_path)
    client = clients["sft"]
    if change == "responses":
        client.api_format = "responses"
    elif change == "no_tools":
        client.model_info["tools"] = False
    elif change == "no_reasoning":
        client.model_info["reasoning"] = False
    elif change == "no_transport":
        client.tool_turn = None
    else:
        client.reasoning["mode"] = change
    session = session_for(runtime, client, "sft")
    with pytest.raises(ValueError, match="^native_feedback_" + issue + "$"):
        NativeFeedbackAdapter(runtime).turn([], session, HISTORY, TOOLS)
    assert not client.calls


def test_default_cot_checkpoint_identity_remains_unchanged(tmp_path):
    runtime, clients, _, keys = runtime_for(tmp_path)
    adapter = CheckpointedReasoningAgent(runtime)
    first = adapter.prepared_tool_turn(["legacy"], "generation", ORIGINAL, TOOLS)
    again = adapter.prepared_tool_turn(["legacy"], "generation", ORIGINAL, TOOLS, model_stage="cot")
    assert first == again and len(clients["cot"].calls) == 1
    assert keys[0] == keys[1] == ["legacy", checklist_digest(ORIGINAL), checklist_digest(TOOLS)]


def test_non_cot_binding_changes_checkpoint_identity(tmp_path):
    runtime, clients, _, _ = runtime_for(tmp_path)
    adapter = CheckpointedReasoningAgent(runtime)
    adapter.prepared_tool_turn(["node"], "generation", ORIGINAL, TOOLS, model_stage="multiturn")
    runtime.recipe["node_models"]["multiturn"]["generation"]["max_output_tokens"] = 64000
    adapter.prepared_tool_turn(["node"], "generation", ORIGINAL, TOOLS, model_stage="multiturn")
    assert len(clients["multiturn"].calls) == 2
    assert [c[2]["max_tokens"] for c in clients["multiturn"].calls] == [32768, 64000]


@pytest.mark.parametrize("guidance_version", [5, 6])
def test_delivered_feedback_reuse_uses_original_author_checkpoint_and_no_second_read(tmp_path, guidance_version):
    from lib.contexts.workflows.application.continue_native_feedback import ContinueNativeFeedback
    runtime, clients, _, _ = runtime_for(tmp_path)
    client = clients["multiturn"]
    session = session_for(runtime, client, "multiturn")
    old = deepcopy(HISTORY[2])
    findings = [{"quote": "The original draft.", "issue": "Keep the proposed demo conditional."}]
    adapter = NativeFeedbackAdapter(runtime)
    _, parent = ContinueNativeFeedback(adapter, guidance_version=4).execute(
        ["parent"], session, old, findings=findings)
    usecase = ContinueNativeFeedback(adapter, guidance_version=guidance_version)
    value, receipt = usecase.execute_from_feedback(["reuse"], session, old,
        findings=findings, previous_receipt=parent)
    again = usecase.execute_from_feedback(["reuse"], session, old,
        findings=findings, previous_receipt=parent)
    assert again == (value, receipt) and len(client.calls) == 2
    assert receipt["model_turns"] == 1 and receipt["tool_feedback"] == parent["tool_feedback"]
    assert receipt["transcript"][0]["response"]["author_continuation"]["tools_preserved"] is True
    sent, tools, options = client.calls[-1]
    assert sent[:-1] == parent["continuation_session"]["messages"]
    assert tools == parent["continuation_session"]["tools"] and options["tool_choice"] == "none"
    assert value["reasoning"] == receipt["revised_assistant"]["reasoning_content"]
    assert runtime.state["usage"]["generation"]["calls"] == 2
    assert not clients["sft"].calls and not clients["cot"].calls
