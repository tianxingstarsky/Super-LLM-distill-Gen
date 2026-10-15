from copy import deepcopy
import json
from pathlib import Path
import threading

import pytest

from lib.domain.knowledge_agent import KNOWLEDGE_AGENT_INSTRUCTION
from lib.domain.source_line_ranges import number_source_material
from lib.infrastructure.knowledge_agent_session import digest
from lib.infrastructure.production_checkpoints import ProductionCheckpoints
from lib.infrastructure.workflow_knowledge_agent import WorkflowKnowledgeAgent
from lib.model_request_reliability import ModelJSONError


@pytest.fixture(scope="session", autouse=True)
def mock_llm_server():
    yield


def material():
    text = "测试压力为45 kPa。\n检查前先关闭电源。"
    return number_source_material({"unit_id": "material-a", "source_id": "a" * 64,
        "source_name": "test.md", "text": text, "cleaning_status": "accepted", "cleaned_text_sha256": digest(text)})


class Client:
    model = "offline-agent"
    model_info = {}

    def __init__(self, replies):
        self.replies, self.requests = list(replies), []
        self.usage = {"calls": 0, "prompt_tokens": 0, "completion_tokens": 0}

    def tool_turn(self, messages, tools, **options):
        self.requests.append(deepcopy(messages))
        self.usage["calls"] += 1
        return deepcopy(self.replies.pop(0))

    def chat(self, messages, **options):
        self.requests.append(deepcopy(messages))
        self.usage["calls"] += 1
        return self.replies.pop(0)


def tool(name, arguments, identity="call-a"):
    return {"role": "assistant", "content": "", "reasoning_content": "actual private provider reasoning",
            "tool_calls": [{"id": identity, "type": "function", "function": {"name": name, "arguments": json.dumps(arguments)}}]}


def final(value=None):
    return {"role": "assistant", "content": json.dumps(value if value is not None else {"tasks": []}), "tool_calls": []}


class Worker(WorkflowKnowledgeAgent):
    def __init__(self, tmp_path, client):
        self.path = tmp_path / "output/workflows/run"
        self.path.mkdir(parents=True, exist_ok=True)
        self.stage = "director"
        self._lock = threading.RLock()
        self._client_locks = {id(client): threading.Lock()}
        self._client = client
        self._checkpoints = ProductionCheckpoints(self.path / "checkpoints.sqlite3")
        self.state = {"id": "run", "usage": {}, "qa_director": {"knowledge_context": {"tool_calls": 0}}}
        self.recipe = {"knowledge_agent_context_version": 1, "knowledge_agent_session_rules_version": 1,
            "knowledge_agent_instruction": KNOWLEDGE_AGENT_INSTRUCTION,
            "node_prompt_system": "Coordinate only.", "source_memory_protocol_version": 2,
            "qa_director": {"tools_mode": "auto"}, "node_models": {"director": {"generation": {
                "context_window_tokens": 131072, "max_output_tokens": 32768}}}}
        self.events = []

    def prompt_text(self, name):
        return "Only return a visible JSON task plan."

    def client(self, *args, **kwargs):
        return self._client

    def checkpoint(self, key, action):
        found, value = self._checkpoints.get(self.stage, digest(key))
        if found:
            return value
        result = action()
        self._checkpoints.put(self.stage, digest(key), result)
        return result

    def event(self, name, **fields):
        self.events.append((name, fields))

    def save(self, **kwargs):
        pass

    def check_cancel(self):
        pass


@pytest.fixture
def context(monkeypatch):
    # A normal already-verified source memory projection; the tools separately
    # authenticate its clean body/line addresses, not a stubbed tool response.
    def split(path, data, **kwargs):
        return {"source_memory_protocol_version": 2, "teacher_context_pack": [
            {"version": 1, "id": "pack", "materials": [material()]}], "task_payload": deepcopy(data)}
    monkeypatch.setattr("lib.infrastructure.model_context_prefix.split_context_request", split)
    return [material()], {"knowledge_id": "a" * 64, "session_id": "b" * 64}


def test_actual_read_only_tool_result_and_reasoning_are_replayed(tmp_path, context):
    client = Client([tool("source_read", {"material_id": "material-a", "start_line": 1, "end_line": 1}), final()])
    worker = Worker(tmp_path, client)
    assert worker.knowledge_agent_plan(["batch", 0], {"offset": 0}, context) == {"tasks": []}
    sent = client.requests[1]
    assert sent[-1]["role"] == "tool" and sent[-1]["tool_call_id"] == "call-a"
    assert "45 kPa" in sent[-1]["content"]
    assert sent[-2]["reasoning_content"] == "actual private provider reasoning"
    assert all(message["role"] == "system" for message in sent[:2])
    assert worker.state["usage"]["generation"]["calls"] == 2


def test_resume_reuses_model_and_tool_checkpoints_without_paid_calls(tmp_path, context):
    first = Client([tool("history_search", {"query": "pressure"}), final()])
    worker = Worker(tmp_path, first)
    worker.knowledge_agent_plan(["batch", 0], {"offset": 0}, context)
    worker.commit_knowledge_plan(["batch", 0])
    restarted = Worker(tmp_path, Client([]))
    assert restarted.knowledge_agent_plan(["batch", 0], {"offset": 0}, context) == {"tasks": []}
    restarted.commit_knowledge_plan(["batch", 0])
    assert restarted._client.requests == []
    assert len(restarted.knowledge_store().history(context[1]["session_id"])) == 4
    before = worker.state["qa_director"]["knowledge_context"]["tool_calls"]
    worker.knowledge_agent_plan(["batch", 0], {"offset": 0}, context)
    assert worker.state["qa_director"]["knowledge_context"]["tool_calls"] == before


def test_same_agent_next_batch_sees_completed_previous_turn(tmp_path, context):
    client = Client([final({"tasks": [{"id": "actual-first-plan"}]}), final()])
    worker = Worker(tmp_path, client)
    worker.knowledge_agent_plan(["batch", 0], {"offset": 0}, context)
    worker.commit_knowledge_plan(["batch", 0])
    worker.knowledge_agent_plan(["batch", 1], {"offset": 1}, context)
    assert any(message.get("role") == "assistant" and "actual-first-plan" in message.get("content", "") for message in client.requests[1])
    assert json.loads(client.requests[1][-1]["content"])["offset"] == 1


def test_invalid_tool_request_returns_error_not_invented_source(tmp_path, context):
    client = Client([tool("source_read", {"material_id": "outside-pack", "start_line": 1, "end_line": 1}), final()])
    worker = Worker(tmp_path, client)
    worker.knowledge_agent_plan(["batch", 0], {}, context)
    observation = json.loads(client.requests[1][-1]["content"])
    assert observation["ok"] is False and "45" not in str(observation)


def test_tool_budget_stops_without_committing_an_unfinished_plan(tmp_path, context):
    client = Client([tool("history_coverage", {}, str(index)) for index in range(5)])
    worker = Worker(tmp_path, client)
    with pytest.raises(ValueError, match="tool_limit"):
        worker.knowledge_agent_plan(["batch", 0], {}, context)
    assert worker.knowledge_store().history(context[1]["session_id"]) == []
    assert len(client.requests) == 5


def test_bad_visible_json_does_not_fall_back_to_private_reasoning(tmp_path, context):
    client = Client([{**final(), "content": "", "reasoning_content": '{"tasks": []}'}])
    with pytest.raises(ModelJSONError):
        Worker(tmp_path, client).knowledge_agent_plan(["batch", 0], {}, context)


def test_disabled_tools_keep_real_persistent_conversation(tmp_path, context):
    client = Client(['{"tasks": []}'])
    worker = Worker(tmp_path, client)
    worker.recipe["qa_director"]["tools_mode"] = "disabled"
    assert worker.knowledge_agent_plan(["batch", 0], {}, context) == {"tasks": []}
    worker.commit_knowledge_plan(["batch", 0])
    assert len(worker.knowledge_store().history(context[1]["session_id"])) == 2


def test_multiturn_same_opening_allows_different_real_designs():
    first = {"question": "怎么处理？", "visible_context": "", "dialogue_design": {"interaction_goal": "定位故障"}}
    second = {**first, "dialogue_design": {"interaction_goal": "解释操作结果"}}
    assert Worker._knowledge_task_identity(first, "multiturn") != Worker._knowledge_task_identity(second, "multiturn")
    assert Worker._knowledge_task_identity(first, "sft") == Worker._knowledge_task_identity(second, "sft")


def test_same_session_jobs_serialize_snapshot_and_other_sessions_remain_free(tmp_path, context):
    first = Worker(tmp_path, Client([]))
    second = Worker(tmp_path, Client([]))
    entered, finished, errors, observed = threading.Event(), threading.Event(), [], []

    def wait_for_previous_turn():
        entered.set()
        try:
            with second.knowledge_planning_lock(context):
                observed.extend(second.knowledge_store().history(context[1]["session_id"]))
        except BaseException as error:
            errors.append(error)
        finally:
            finished.set()

    with first.knowledge_planning_lock(context):
        thread = threading.Thread(target=wait_for_previous_turn)
        thread.start()
        assert entered.wait(2)
        assert not finished.wait(.35)
        other = (context[0], {**context[1], "session_id": "c" * 64})
        with second.knowledge_planning_lock(other):
            first.knowledge_store().remember_turn(context[1]["session_id"], "previous-job",
                [{"role": "user", "content": "planned task"}, final()])
    assert finished.wait(3)
    thread.join()
    assert not errors
    assert observed[0]["content"] == "planned task"


def test_session_planning_queue_timeout_does_not_call_model(tmp_path, context):
    first = Worker(tmp_path, Client([]))
    second = Worker(tmp_path, Client([]))
    second.recipe["node_models"]["director"]["generation"]["request_queue_timeout_seconds"] = .01
    with first.knowledge_planning_lock(context):
        with pytest.raises(ValueError, match="knowledge_agent_planning_timeout"):
            with second.knowledge_planning_lock(context):
                pytest.fail("a locked session must not plan concurrently")
    assert second._client.requests == []


def test_rule_changes_isolate_conversation_but_keep_same_knowledge_memory(tmp_path):
    class EmptyPublished:
        def source_recent(self, *args, **kwargs):
            return []

    worker = Worker(tmp_path, Client([]))
    worker._director_evidence_materials = lambda unit: [material()]

    def descriptor():
        return worker.prepare_knowledge_agent([{}], EmptyPublished(), ["sft"])[1]

    original = descriptor()
    worker.recipe["qa_director"]["batch_size"] = 8
    assert descriptor() == original
    worker.recipe["qa_director"]["question_rules"] = "Use a real troubleshooting request."
    changed = descriptor()
    assert changed["knowledge_id"] == original["knowledge_id"]
    assert changed["session_id"] != original["session_id"]
    worker.recipe["node_prompt_system"] = "Another explicit user rule."
    assert descriptor()["session_id"] != changed["session_id"]


def test_tool_round_compaction_preserves_current_thought_and_complete_pairs(tmp_path, context):
    thought = "real-current-provider-thought " * 750
    reply = {**tool("source_read", {"material_id": "material-a", "start_line": 1, "end_line": 1}),
             "reasoning_content": thought}
    client = Client([reply, final()])
    worker = Worker(tmp_path, client)
    worker.recipe["node_models"]["director"]["generation"].update(
        context_window_tokens=40000, max_output_tokens=1024)
    worker.knowledge_store().remember_turn(context[1]["session_id"], "older-job",
        [{"role": "user", "content": "older-completed-task"},
         {"role": "assistant", "content": "old plan", "reasoning_content": "x" * 18000}])
    worker.knowledge_agent_plan(["batch", 0], {"offset": 0}, context)
    assert any(message.get("content") == "older-completed-task" for message in client.requests[0])
    assert not any(message.get("content") == "older-completed-task" for message in client.requests[1])
    assert client.requests[1][-2]["reasoning_content"] == thought
    assert client.requests[1][-1]["tool_call_id"] == "call-a"
    assert "45 kPa" in client.requests[1][-1]["content"]
    worker.commit_knowledge_plan(["batch", 0])
    current = worker.knowledge_store().history(context[1]["session_id"], max_bytes=100000)[-4:]
    assert current[0]["role"] == "user" and current[1]["reasoning_content"] == thought


def test_current_tool_chain_exhaustion_stops_before_another_paid_turn(tmp_path, context):
    reply = {**tool("history_coverage", {}), "reasoning_content": "x" * 80000}
    client = Client([reply, final()])
    worker = Worker(tmp_path, client)
    worker.recipe["node_models"]["director"]["generation"].update(
        context_window_tokens=40000, max_output_tokens=1024)
    with pytest.raises(ValueError, match="knowledge_agent_context_exhausted"):
        worker.knowledge_agent_plan(["batch", 0], {}, context)
    assert len(client.requests) == 1
    assert worker.knowledge_store().history(context[1]["session_id"]) == []


def test_normal_workflow_uses_tools_and_reuses_the_agent_between_batches(tmp_path):
    from test_separated_dialogue_generation import DirectorResponder, config, make_run, records
    from lib.infrastructure.training_workflow import verify_artifacts

    class ToolDirector(DirectorResponder):
        def __init__(self):
            super().__init__(qa_type="grounded")
            self.tool_requests = []

        def tool_turn(self, messages, tools, **options):
            self.tool_requests.append(deepcopy(messages))
            if messages[-1]["role"] == "user":
                return tool("history_search", {"query": "检查断电"}, "history-" + str(len(self.tool_requests)))
            value = json.loads(super().chat(messages))
            for task in value["tasks"]:
                task["question"] += "（请求 " + task["id"][:6] + "）"
            return final(value)

    director = ToolDirector()
    worker, *_ = make_run(tmp_path, writer=director, tasks=2, sample_count=2,
        qa_director=config("grounded", context_mode="persistent", tools_mode="auto"))
    state = worker.execute()
    assert state["status"] == "completed", state.get("error")
    assert verify_artifacts(worker.path)["counts"]["sft"] == 2
    assert len(records(worker.path)) == 2
    assert len(director.tool_requests) == 4
    # Batch two contains an actual completed prior plan and actual tool result.
    assert sum(message["role"] == "assistant" for message in director.tool_requests[2]) >= 2
    assert state["qa_director"]["knowledge_context"]["retained_messages"] == 4
    scope = state["qa_director"]["knowledge_context"]["knowledge_id"]
    assert worker.knowledge_store().coverage(scope)["counts"] == [
        {"qa_type": "grounded", "status": "final_eligible", "count": 2}]
    calls_before = len(director.tool_requests)
    worker.execute(resume_run=True)
    assert len(director.tool_requests) == calls_before
