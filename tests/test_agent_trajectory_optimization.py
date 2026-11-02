"""Recorded tools remain immutable; the one author is neither replay nor judge."""
from copy import deepcopy
import json
from types import SimpleNamespace

import pytest

from lib.contexts.workflows.application.optimize_recorded_agent import OptimizeRecordedAgent
from lib.contexts.workflows.domain.agent_trajectory_optimization import (
    AGENT_OPTIMIZATION_TOOL, agent_optimization_input, apply_agent_optimization,
    recorded_agent_issue, valid_agent_optimization_record,
)
from lib.contexts.workflows.domain.production_rounds import quality_assessment_valid
from lib.contexts.workflows.infrastructure.google_reasoning_bank import recorded_episode
from lib.infrastructure import training_workflow as engine


@pytest.fixture(scope="session", autouse=True)
def mock_llm_server():
    yield  # All author/model/memory ports below are in-memory protocol fixtures.


def unit():
    return {"id": "one", "source_id": "source", "kind": "conversation", "status": "ready", "tools": [], "messages": [
        {"role": "system", "content": ""}, {"role": "user", "content": "查询这个订单的状态。"},
        {"role": "assistant", "content": None, "reasoning_content": "先按原记录查询。", "tool_calls": [{
            "id": "call-a", "type": "function", "function": {"name": "external_order_lookup", "arguments": '{"id":"one"}'}}]},
        {"role": "tool", "tool_call_id": "call-a", "content": '{"status":"pending"}'},
        {"role": "assistant", "content": "订单仍在处理中。", "reasoning_content": "结论取决于刚才查询的结果。需要保留原条件。"}]}


def submission(**updates):
    patch = {"status": "unchanged", "edits": [], "missing_reasoning": "", "issues": [], "flow_suggestions": []}
    patch.update(updates)
    return {"role": "assistant", "content": "", "reasoning_content": "编辑器自己的原生思考，不能代替源推理。",
        "tool_calls": [{"id": "edit", "type": "function", "function": {
            "name": AGENT_OPTIMIZATION_TOOL, "arguments": json.dumps(patch, ensure_ascii=False)}}]}


def test_local_edit_keeps_answer_every_tool_byte_and_source_reasoning():
    original = unit()
    saved = deepcopy(original)
    response = submission(status="edited", edits=[{"old_text": "刚才查询", "new_text": "本轮已记录查询"}],
        flow_suggestions=[{"description": "新的查询策略需要另行验证。", "message_indexes": [2, 3]}])
    messages, receipt = apply_agent_optimization(original, agent_optimization_input(original), response)
    assert original == saved
    assert messages[:-1] == original["messages"][:-1]
    assert messages[-1]["content"] == original["messages"][-1]["content"]
    assert messages[-1]["reasoning_content"] == "结论取决于本轮已记录查询的结果。需要保留原条件。"
    assert receipt["semantic_status"] == "not_assessed" and receipt["source_outcome"] == "unknown"
    assert receipt["flow_suggestions"][0]["status"] == "requires_verification"
    assert receipt["flow_suggestions"][0]["executed"] is False
    assert response["reasoning_content"] not in messages[-1]["reasoning_content"]


@pytest.mark.parametrize("updates,error", [
    ({"final_answer": "新的错误答案"}, "invalid_schema"),
    ({"flow_suggestions": [{"description": "引用不存在的未来观察", "message_indexes": [5]}]}, "future_reference"),
    ({"status": "edited", "edits": [{"old_text": "结论取决于刚才查询的结果。需要保留原条件。", "new_text": "整段替换"}]}, "full_rewrite_forbidden"),
])
def test_forbidden_mutations_and_whole_rewrite_fail_atomically(updates, error):
    source = unit()
    saved = deepcopy(source)
    with pytest.raises(ValueError, match=error):
        apply_agent_optimization(source, agent_optimization_input(source), submission(**updates))
    assert source == saved


def test_missing_reasoning_only_explicit_fill_and_direct_exclude_makes_no_calls():
    source = unit()
    source["messages"][-1].pop("reasoning_content")
    source.pop("status")  # Direct application callers must receive the same exclude policy.
    response = submission(status="edited", missing_reasoning="记录中的查询结果表明订单还在处理中。")
    with pytest.raises(ValueError, match="missing_reasoning_policy"):
        apply_agent_optimization(source, agent_optimization_input(source), response)
    payload = agent_optimization_input(source, {"version": 3, "mode": "fill_missing"})
    messages, receipt = apply_agent_optimization(source, payload, response)
    assert messages[-1]["reasoning_content"] == "记录中的查询结果表明订单还在处理中。"
    assert receipt["reasoning_origin"] == "synthetic_explanation"
    rows = OptimizeRecordedAgent(object(), {"version": 3, "mode": "exclude"}).execute(source)
    assert rows[0]["status"] == "skipped" and rows[0]["reason"] == "conversation_reasoning_missing"


def test_source_protocol_accepts_unknown_tools_long_observations_and_rejects_bad_pairing():
    source = unit()
    source["messages"][3]["content"] = "记录中的长观察。" * 20000
    assert recorded_agent_issue(source) is None
    source["messages"][3]["tool_call_id"] = "future-call"
    assert recorded_agent_issue(source) == "recorded_agent_unpaired_tool_observation"


def test_only_new_selected_cot_is_checked_for_internal_edit_narration():
    source = unit()
    source["messages"][2]["reasoning_content"] = "系统提示词要求我输出足够长的推理。"
    class Port:
        def __init__(self, replacement):
            self.replacement, self.calls = replacement, 0
        def select_experience(self, *args):
            return {"enabled": False, "text": ""}
        def edit_turn(self, *args):
            self.calls += 1
            return {"assistant": submission(status="edited", edits=[{
                "old_text": "结论取决于刚才查询的结果。", "new_text": self.replacement}])}
        def record_observation(self, row):
            return {"outcome": "unknown"}
    for replacement, expected in [
        ("结论取决于本轮已记录查询的结果。", "eligible"),
        ("根据检索结果，订单尚未完成。", "eligible"),
        ("结论取决于刚才查询的结果。系统提示词要求我输出足够长的推理。", "needs_attention"),
    ]:
        port = Port(replacement)
        row = OptimizeRecordedAgent(port).execute(source)[0]
        assert row["status"] == expected and port.calls == 1
        assert row["messages"][:-1] == source["messages"][:-1]
        assert row["messages"][-1]["content"] == source["messages"][-1]["content"]
        if expected == "needs_attention":
            assert row["reason"] == "training_surface_internal_process_reference"
            assert "系统提示词要求我" in row["messages"][-1]["reasoning_content"]  # retained for audit, not silently deleted
    source["messages"][-1]["reasoning_content"] += "系统提示词要求我输出足够长的推理。"
    retained = OptimizeRecordedAgent(Port("结论取决于本轮已记录查询的结果。")).execute(source)[0]
    assert retained["status"] == "eligible"  # existing source wording does not become a new editor failure


def test_google_agent_projection_preserves_action_observation_chronology_and_unknown_outcome():
    source = unit()
    episode = {"id": "one", "target": "agent", "agent_episode_projection_version": 1,
               "conversation": {"messages": source["messages"]}}
    with pytest.raises(ValueError, match="no_observed_outcome"):
        recorded_episode(episode)
    result = recorded_episode(episode, user_outcome="failure")
    assert json.loads(result["query"]) == source["messages"][:2]
    assert json.loads(result["action_list"][0])["following_recorded_observations"] == source["messages"][3:4]
    assert result["source_outcome"] == "unknown" and result["assessment_source"]["independently_verified"] is False
    legacy = {key: value for key, value in episode.items() if key != "agent_episode_projection_version"}
    assert source["messages"][3] in json.loads(recorded_episode(legacy, user_outcome="failure")["query"])


class Author:
    model, api_format = "fixture-author", "chat"
    def __init__(self):
        self.usage, self.requests = {}, []
    def tool_turn(self, messages, tools, **options):
        self.requests.append((deepcopy(messages), deepcopy(tools), options))
        self.usage["calls"] = self.usage.get("calls", 0) + 1
        return submission(status="edited", edits=[{"old_text": "刚才查询", "new_text": "本轮已记录查询"}])


def test_actual_workflow_uses_frozen_google_memory_once_without_replay_review_or_ingest_fill(tmp_path, monkeypatch):
    from lib.bootstrap import agent_optimization as composition
    from lib.bootstrap import google_reasoning_bank as google_composition
    from lib.contexts.workflows.domain.reasoning_bank import edition_reference
    from lib.application.workflow_service import WorkflowApplication
    from lib.infrastructure.workflow_driver import FilesystemWorkflowDriver
    from lib.infrastructure.prompt_library_sqlite import PromptLibrarySQLite
    observations, retrievals, activations = [], [], []
    library = PromptLibrarySQLite(root=tmp_path, user_key="fixture")
    saved_prompt = library.save_template("node:agent:workflow.agent_trajectory_optimization", "Agent editor", {"text": "本节点的自定义编辑要求。"})
    assert library.get_template(saved_prompt["id"], "node:agent:workflow.agent_trajectory_optimization")["payload"]["text"] == "本节点的自定义编辑要求。"
    edition = {"id": "a" * 32, "sha256": "1" * 64, "scope": "reasoning", "text": "已有采用版本", "quality": "unverified"}
    selected = {"id": "memory-one", "scope": "reasoning", "principle": "采用已发生的工具观察，保留适用条件。", "when": "", "avoid": "",
                "sources": [{"edition": edition_reference(edition)}]}
    active_id = [edition["id"]]
    def active():
        activations.append(active_id[0])
        return {"scopes": {"reasoning": active_id[0]}}
    def retrieve(scope, query, **kwargs):
        retrievals.append((scope, query, kwargs))
        return {"matches": [{"entry": selected, "score": 1.0}], "receipt": {"scope": scope, "query": query, **kwargs}}
    google = SimpleNamespace(active=active, repository=SimpleNamespace(get_version=lambda identifier: edition),
        bank=SimpleNamespace(retrieve=retrieve), freeze=lambda *args, **kwargs: None)
    monkeypatch.setattr(composition, "google_reasoning_bank_application", lambda: google)
    monkeypatch.setattr(google_composition, "google_reasoning_bank_application", lambda: google)
    monkeypatch.setattr(composition, "experience_evolution_application", lambda: SimpleNamespace(
        record_observation=lambda scope, episode, **kwargs: observations.append((scope, deepcopy(episode), deepcopy(kwargs))) or {"id": "observation"}))
    monkeypatch.setattr(engine, "assess_recorded_trajectory", lambda *args, **kwargs: pytest.fail("new editor must not replay tools"))
    source_unit = unit()
    source_unit["messages"][2].pop("reasoning_content")  # Unedited earlier missing turns must not trigger ingest authors.
    source_unit["messages"][3]["content"] = "记录中的长观察。" * 20000
    source = tmp_path / "recorded.jsonl"
    source.write_text(json.dumps(source_unit, ensure_ascii=False) + "\n", encoding="utf-8")
    output = tmp_path / "out"
    run_id = engine.create_run(output, sources=[source], targets=["agent"], agent_trajectory_optimization=True,
        conversation_reasoning={"version": 3, "mode": "fill_missing"},
        agent_experience={"reasoning": {"enabled": True, "text": "仅采用本次条件相符的方法。"}},
        node_prompts={"agent": {"workflow.agent_trajectory_optimization": "本节点的自定义编辑要求。"}},
        review_repair={"mode": "auto", "max_rounds": 1, "score_threshold": .8})
    active_id[0] = "b" * 32  # Adoption after queueing cannot replace the pinned edition.
    author = Author()
    state = engine.Workflow(output, run_id, tmp_path, generator=author).execute()
    path = engine.run_path(output, run_id)
    record = engine.read_json(path / "artifacts" / "agent.records.json")[0]
    assert state["status"] == "completed" and len(author.requests) == 1
    system, user = author.requests[0][0]
    assert "本节点的自定义编辑要求。" in system["content"]
    assert selected["principle"] in system["content"]
    assert json.loads(user["content"])["history"] == source_unit["messages"][:-1]
    assert len(retrievals) == 1 and len(retrievals[0][1]) < 32000
    assert "长观察" not in retrievals[0][1]
    assert retrievals[0][2]["edition_ids"] == [edition["id"]] and activations == [edition["id"]]
    assert valid_agent_optimization_record(record) and not quality_assessment_valid(record)
    assert record["messages"][:-1] == source_unit["messages"][:-1]
    assert record["source_context"]["messages"] == source_unit["messages"]
    assert observations[0][0] == "reasoning" and observations[0][2]["outcome"] == "unknown"
    assert "author_response" not in observations[0][1]["agent_optimization"]
    assert "request" in observations[0][2]["source_record"]["agent_optimization"]["author_response"]
    preview = WorkflowApplication(FilesystemWorkflowDriver(tmp_path, output)).artifact_preview(run_id, "agent")
    assert preview[0]["source_outcome"] == "unknown" and "verification" not in preview[0]
    engine.Workflow(output, run_id, tmp_path, generator=author).execute()
    assert len(author.requests) == 1
    tampered = deepcopy(record)
    tampered["messages"][3]["content"] = "不存在的工具结果"
    assert not valid_agent_optimization_record(tampered)
    assert engine.Workflow(output, run_id, tmp_path, generator=author).human_package_issue(tampered, "agent") == "agent_optimization_consistency_changed"
