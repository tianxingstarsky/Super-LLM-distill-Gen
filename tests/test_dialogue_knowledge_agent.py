"""Real tool/checkpoint transport for opt-in adaptive dialogue continuations.

The model is an injected double. Source authentication, tool execution,
persistent storage, role projection and checkpoint replay are production code.
"""
from copy import deepcopy
import json
from types import SimpleNamespace

import pytest

from lib.domain.source_line_ranges import number_source_material
from lib.infrastructure.dialogue_role_messages import question_role_messages
from lib.infrastructure.knowledge_agent_session import digest, knowledge_identity
from lib.infrastructure.workflow_qa_director import DialoguePlanError, WorkflowQADirector
from lib.infrastructure.workflow_source_context import _pack_payload
from tests.test_workflow_knowledge_agent import Client, Worker as AgentWorker, final, tool


@pytest.fixture(scope="session", autouse=True)
def mock_llm_server():
    yield


SELECTED = "检查前先关闭电源。\n"
PRIVATE = "UNSELECTED_PRIVATE_CONTENT_86324"
MESSAGES = [{"role": "user", "content": "我想整理一下操作要求。"},
            {"role": "assistant", "content": "先关闭电源，再检查设备。"}]


def step(*, ongoing=True, **fields):
    return {"continue": ongoing, "user_message": "短一点。" if ongoing else "",
            "reason": "继续修改实际结果。" if ongoing else "实际需求已满足，不凑轮数。",
            "dialogue_state": {"progress": "已经给出首版。", "expression_design": {
                "register": "自然日常口语", "verbosity": "短句"}}, **fields}


class Worker(WorkflowQADirector, AgentWorker):
    def __init__(self, tmp_path, client):
        super().__init__(tmp_path, client)
        self.stage = "multiturn"
        self.recipe.update(qa_dialogue_agent_version=1, qa_expression_version=2,
            source_line_protocol_version=1, source_context_pack_version=1,
            qa_agent_memory_version=1, conversation_turns=6)
        self.recipe["qa_director"].update(question_rules="让用户直接表达当前意图。",
            answer_rules="回应真实问题，不猜未知条件。", question_separation=False)
        self.asks, self.bindings, self.blind_requests, self.invalidated = [], [], [], []
        self.prompt_bindings = []
        self.legacy_value = step()
        self.reject_brief = False

    def client(self, role, *, stage=None):
        self.bindings.append((role, stage))
        return super().client(role, stage=stage)

    def prompt_text(self, name, *, stage=None):
        assert name == "workflow.qa_director"
        self.prompt_bindings.append(stage)
        return "Only return the requested adaptive dialogue step."

    def shared_source_cleaning(self):
        # The fixture supplies an authenticated, already-clean context pack.
        return False

    def invalidate_checkpoint(self, key, *, stage=None):
        self.invalidated.append((deepcopy(key), stage or self.stage))
        self._checkpoints.delete(stage or self.stage, digest(key))

    def ask(self, key, role, prompt_id, data, **options):
        self.asks.append((deepcopy(key), role, prompt_id, deepcopy(data), options))
        if prompt_id == "workflow.qa_director":
            return deepcopy(self.legacy_value)
        if prompt_id == "workflow.question_brief_check":
            return {"keep": not self.reject_brief, "reason": "核对来源边界。"}
        if prompt_id == "workflow.question_user":
            self.blind_requests.append(question_role_messages("Speak directly as the user.", data,
                expression_guide_version=2))
            return {"message": "短一点。"}
        if prompt_id == "workflow.question_fit":
            return {"keep": True, "answer_policy": "answer", "reason": "自然修改请求。"}
        raise AssertionError(prompt_id)


def unit():
    text = PRIVATE + "\n" + SELECTED + "另一个未选中的内部条件。"
    material = number_source_material({"unit_id": "material-a", "source_id": "a" * 64,
        "source_name": "paper.md", "text": text, "cleaning_status": "accepted",
        "cleaned_text_sha256": digest(text)})
    pack = {**_pack_payload([material]), "source_line_protocol_version": 1}
    return {"id": "sample-a", "kind": "document", "source_id": "a" * 64,
        "text": text, "source_context_pack": pack, "source_line_material_id": "material-a",
        "knowledge_agent_context": {"version": 1, "knowledge_id": knowledge_identity([material]),
            "session_id": "b" * 64},
        "qa_contract": {"id": "sample-a", "qa_type": "grounded", "question": MESSAGES[0]["content"],
            "visible_context": SELECTED, "evidence_quotes": [SELECTED.strip()],
            "answer_policy": "answer", "source_line_ranges": [{"material_id": "material-a",
                "start_line": 2, "end_line": 2}],
            "dialogue_design": {"interaction_goal": "整理适合自己的操作说明。"}}}


def test_opt_in_continuation_uses_director_tools_in_current_multiturn_checkpoints(tmp_path):
    candidate = unit()
    client = Client([])
    worker = Worker(tmp_path, client)
    runtime = worker._dialogue_knowledge_runtime(candidate)
    local_id = runtime[0][0]["material_id"]
    client.replies.extend([tool("source_read", {"material_id": local_id,
        "start_line": 1, "end_line": 1}), final(step())])
    result = worker.next_dialogue_step(candidate, MESSAGES, {})
    assert result["user_message"] == "短一点。"
    assert worker.stage == "multiturn" and not worker.asks
    assert worker.bindings and set(worker.bindings) == {("generation", "director")}
    assert worker.prompt_bindings == ["director"]
    assert SELECTED.strip() in client.requests[1][-1]["content"]
    assert PRIVATE not in json.dumps(client.requests, ensure_ascii=False)
    assert worker.knowledge_store().history(runtime[1]["session_id"])
    key = ["sample-a", "dialogue_director", 1, 0, digest(MESSAGES)]
    assert worker._checkpoints.get("multiturn", digest(["knowledge_agent_inputs", key]))[0]
    assert not worker._checkpoints.get("director", digest(["knowledge_agent_inputs", key]))[0]


def test_tool_reads_cannot_expand_first_turn_scope_or_replay_full_batch_history(tmp_path):
    client, candidate = Client([]), unit()
    worker = Worker(tmp_path, client)
    runtime = worker._dialogue_knowledge_runtime(candidate)
    original_session = candidate["knowledge_agent_context"]["session_id"]
    worker.knowledge_store().remember_turn(original_session, "batch-plan", [
        {"role": "user", "content": PRIVATE}, final({"tasks": []})])
    local_id = runtime[0][0]["material_id"]
    client.replies.extend([
        tool("source_read", {"material_id": "material-a", "start_line": 1, "end_line": 1}, "outside"),
        tool("source_read", {"material_id": local_id, "start_line": 2, "end_line": 2}, "outside-local"),
        final(step(ongoing=False))])
    assert worker.next_dialogue_step(candidate, MESSAGES, {})["continue"] is False
    assert runtime[1]["session_id"] != original_session
    for request in client.requests[1:]:
        assert json.loads(request[-1]["content"])["ok"] is False
    assert PRIVATE not in json.dumps(client.requests, ensure_ascii=False)
    payload = json.loads(client.requests[0][-1]["content"])
    assert payload["dialogue_source_tools_scope"]["source_expansion_available"] is False


def test_same_knowledge_ledger_and_validated_continuation_are_reused_without_answers_as_facts(tmp_path):
    candidate, client = unit(), Client([])
    worker = Worker(tmp_path, client)
    knowledge = candidate["knowledge_agent_context"]["knowledge_id"]
    worker.knowledge_store().claim(knowledge, "existing", run_id="previous", sample_id="accepted",
        question="怎么写操作卡？", qa_type="closed_book", expression_design={"register": "轻松口语"})
    worker.knowledge_store().outcome(knowledge, "existing", run_id="previous", sample_id="accepted",
        stage="multiturn", status="final_eligible")
    client.replies.extend([tool("history_search", {"query": "操作卡", "limit": 2}),
                          final(step()), final(step(ongoing=False))])
    worker.next_dialogue_step(candidate, MESSAGES, {})
    found = json.loads(client.requests[1][-1]["content"])
    assert "怎么写操作卡？" in json.dumps(found, ensure_ascii=False)
    assert "answer" not in json.dumps(found, ensure_ascii=False)
    changed = [*MESSAGES, {"role": "user", "content": "短一点。"},
               {"role": "assistant", "content": "先断电，再检查。"}]
    assert worker.next_dialogue_step(candidate, changed, {})["continue"] is False
    assert any(message.get("role") == "assistant" and '"continue": true' in message.get("content", "")
               for message in client.requests[-1][:-1])
    assert worker.state["qa_director"]["knowledge_context"]["tool_calls"] == 1


def test_restart_reuses_a_validated_step_without_rebuying_model_or_tool_calls(tmp_path):
    candidate = unit()
    first = Worker(tmp_path, Client([tool("history_coverage", {}), final(step())]))
    expected = first.next_dialogue_step(candidate, MESSAGES, {})
    runtime = first._dialogue_knowledge_runtime(candidate)
    before = first.knowledge_store().history(runtime[1]["session_id"])
    restarted = Worker(tmp_path, Client([]))
    assert restarted.next_dialogue_step(candidate, MESSAGES, {}) == expected
    assert restarted._client.requests == [] and restarted.stage == "multiturn"
    assert restarted.knowledge_store().history(runtime[1]["session_id"]) == before


@pytest.mark.parametrize("bad", ["not JSON", {"continue": True, "user_message": "缺少状态"}])
def test_rejected_final_reply_is_invalidated_but_valid_tool_work_is_replayed(tmp_path, bad):
    candidate, client = unit(), Client([])
    worker = Worker(tmp_path, client)
    runtime = worker._dialogue_knowledge_runtime(candidate)
    client.replies.extend([tool("history_coverage", {}),
        {"role": "assistant", "content": bad, "tool_calls": []} if isinstance(bad, str) else final(bad),
        final(step())])
    with pytest.raises(DialoguePlanError):
        worker.next_dialogue_step(candidate, MESSAGES, {})
    assert not worker.knowledge_store().history(runtime[1]["session_id"])
    assert worker.invalidated[-1][0][0] == "knowledge_agent_turn"
    assert worker.invalidated[-1][1] == "multiturn"
    assert worker.next_dialogue_step(candidate, MESSAGES, {})["continue"]
    assert len(client.requests) == 3  # The same tool call was not purchased again.
    assert worker.state["qa_director"]["knowledge_context"]["tool_calls"] == 1
    assert worker.knowledge_store().history(runtime[1]["session_id"])


def test_blind_user_actor_never_receives_private_source_or_agent_tools_and_commit_follows_boundary(tmp_path):
    candidate, client = unit(), Client([])
    candidate["qa_contract"].update(qa_type="closed_book", visible_context="",
        question_brief={"topic": "操作说明", "communication_goal": "整理当前的操作要求"})
    worker = Worker(tmp_path, client)
    worker.recipe["qa_director"]["question_separation"] = True
    runtime = worker._dialogue_knowledge_runtime(candidate)
    response = step(question_brief={"topic": "操作说明", "communication_goal": "希望已有结果更简短"})
    revised = {**response, "question_brief": {"topic": "操作说明", "communication_goal": "只修改已有结果的措辞"}}
    client.replies.extend([tool("history_coverage", {}), final(response), final(revised)])
    worker.reject_brief = True
    with pytest.raises(DialoguePlanError):
        worker.next_dialogue_step(candidate, MESSAGES, {})
    assert not worker.blind_requests and not worker.knowledge_store().history(runtime[1]["session_id"])
    worker.reject_brief = False
    assert worker.next_dialogue_step(candidate, MESSAGES, {})["user_message"] == "短一点。"
    assert len(worker.blind_requests) == 1
    blind = json.dumps(worker.blind_requests[0], ensure_ascii=False)
    assert not any(value in blind for value in (PRIVATE, "knowledge_agent_memory", "history_coverage",
        "source_read", SELECTED.strip(), "dialogue_source_tools_scope"))
    assert all(message.get("name") in {None, "dialogue_dispatch"}
               for message in worker.blind_requests[0])
    assert worker.knowledge_store().history(runtime[1]["session_id"])
    boundary_keys = [key for key, _, prompt_id, _, _ in worker.asks
                     if prompt_id == "workflow.question_brief_check"]
    assert len(boundary_keys) == 2 and boundary_keys[0] != boundary_keys[1]


def test_legacy_recipe_keeps_original_director_ask_and_prompt_binding(tmp_path):
    worker = Worker(tmp_path, Client([]))
    worker.recipe.pop("qa_dialogue_agent_version")
    assert worker.next_dialogue_step(unit(), MESSAGES, {})["continue"]
    _, role, prompt_id, data, options = worker.asks[0]
    assert role == "generation" and prompt_id == "workflow.qa_director"
    assert options["model_stage"] == options["prompt_stage"] == "director"
    assert "dialogue_source_tools_scope" not in data and not worker.bindings
    assert worker.stage == "multiturn" and not hasattr(worker, "_knowledge_store")


def test_open_brief_never_gets_an_invented_authenticated_knowledge_scope(tmp_path):
    worker, candidate = Worker(tmp_path, Client([])), unit()
    candidate.update(kind="brief")
    assert worker.next_dialogue_step(candidate, MESSAGES, {})["continue"]
    assert worker.asks and not worker.bindings and not hasattr(worker, "_knowledge_store")


def test_invalid_original_receipt_is_rejected_before_any_model_call(tmp_path):
    worker, candidate = Worker(tmp_path, Client([])), unit()
    candidate["source_context_pack"]["materials"][0]["text"] += "tampered"
    with pytest.raises(ValueError, match="source_context_pack_invalid"):
        worker.next_dialogue_step(candidate, MESSAGES, {})
    assert not worker._client.requests and not worker.asks


def test_new_batch_retry_after_all_bad_plans_reuses_tools_but_not_rejected_final_replies(tmp_path):
    from lib.domain.workflow_qa_director import QA_TYPES, validate_qa_director
    from lib.infrastructure.workflow_qa_director import DirectorPlanError

    config = validate_qa_director({"enabled": True, "context_mode": "persistent", "tools_mode": "auto",
        "batch_size": 1, "history_limit": 0,
        "type_weights": {name: int(name == "grounded") for name in QA_TYPES}})
    empty_history = SimpleNamespace(source_recent=lambda *a, **kw: [])

    def configured(client):
        worker = Worker(tmp_path, client)
        worker.recipe.update(version=12, production={}, knowledge_agent_finalization_version=1)
        worker.recipe["qa_director"] = deepcopy(config)
        worker.state["stages"] = {"director": dict.fromkeys(
            ("done", "outputs", "quarantined", "eligible", "batches_done"), 0)}
        worker.state["qa_director"].update(coverage={name: {"planned": 0, "assigned": 0}
            for name in QA_TYPES}, feedback={}, batches_planned=0)
        return worker

    first = configured(Client([reply for index in range(3) for reply in (
        tool("history_coverage", {}, "coverage-" + str(index)), final({"tasks": []}))]))
    candidate = unit()
    failed = first._director_batch_plan([deepcopy(candidate)], 0, config, empty_history, empty_history, ["multiturn"])
    assert failed[0]["director_plan_failure"] == DirectorPlanError.code
    assert len(first._client.requests) == 6
    assert len(first.invalidated) == 3
    assert all(key[0] == "knowledge_agent_turn" for key, _ in first.invalidated)
    assert not first.knowledge_store().history(first.state["qa_director"]["knowledge_context"]["session_id"])

    valid = deepcopy(candidate["qa_contract"])
    valid["guidance"] = "根据已清洗来源回应实际请求。"
    # Batch planning authenticates its own evidence; a resumed model is not
    # allowed to change candidate ids or bypass the original selected range.
    restarted = configured(Client([final({"tasks": [valid]})]))
    result = restarted._director_batch_plan([deepcopy(candidate)], 0, config,
        empty_history, empty_history, ["multiturn"])
    assert result[0]["qa_contract"]["id"] == "sample-a"
    assert len(restarted._client.requests) == 1
    assert restarted._client.requests[0][-1]["role"] == "tool"
    assert restarted.state["qa_director"]["knowledge_context"]["tool_calls"] == 0


def test_actual_workflow_runs_multiturn_agent_tools_at_director_and_exports_only_real_dialogue(tmp_path):
    from tests.test_separated_dialogue_generation import (
        DirectorResponder, FOLLOWUP_QUESTION, config, make_run, records, tool_payload,
    )
    from lib.infrastructure.training_workflow import verify_artifacts

    class ToolDirector(DirectorResponder):
        def __init__(self):
            super().__init__()
            self.tool_requests = []

        def tool_turn(self, messages, tools, **options):
            self.tool_requests.append(deepcopy(messages))
            if messages[-1]["role"] == "user":
                return tool("history_coverage", {}, "coverage-" + str(len(self.tool_requests)))
            return final(json.loads(super().chat(messages, **options)))

    director = ToolDirector()
    workflow, _, actor, _ = make_run(tmp_path, writer=director, targets=["multiturn"],
        conversation_turns=5, qa_director=config(context_mode="persistent", tools_mode="auto"))
    assert workflow.recipe["qa_dialogue_agent_version"] == 1
    state = workflow.execute()
    assert state["status"] == "completed", state.get("error")
    assert verify_artifacts(workflow.path)["counts"]["multiturn"] == 1
    record, = records(workflow.path, "multiturn")
    assert record["messages"][2]["content"] == FOLLOWUP_QUESTION
    assert record["dialogue_end_reason"] == "natural_completion"
    assert len(director.tool_requests) == 6  # Initial design, follow-up, natural stop.
    assert len(actor.calls) == 2
    assert all(tool_payload(call, "source_memory") is None for call in actor.calls)
    assert "history_coverage" not in (workflow.path / "artifacts/trl_multiturn.jsonl").read_text(encoding="utf-8")
    before = len(director.tool_requests)
    workflow.execute(resume_run=True)
    assert len(director.tool_requests) == before
