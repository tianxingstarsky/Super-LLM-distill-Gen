"""End-to-end proof that Agent candidates depend on replayable tool evidence."""
from __future__ import annotations

import json

import pytest

from lib.application.workflow_service import WorkflowApplication
from lib.infrastructure.training_workflow import Workflow, create_run, read_json, run_path, verify_artifacts
from lib.infrastructure.workflow_driver import FilesystemWorkflowDriver
from lib.io_utils import atomic_json
from lib.infrastructure.training_workflow import digest


def _call(call_id: str, expression: str) -> dict:
    return {"role": "assistant", "content": "", "tool_calls": [
        {"id": call_id, "type": "function", "function": {
            "name": "calculator", "arguments": json.dumps({"expression": expression})}}]}


def _result(call_id: str, content: str, *, error: bool = False) -> dict:
    return {"role": "tool", "tool_call_id": call_id, "content": content, **({"isError": True} if error else {})}


def _pointer_call(call_id: str, snapshot_id: str, pointer: str) -> dict:
    return {"role": "assistant", "content": "", "tool_calls": [
        {"id": call_id, "type": "function", "function": {"name": "json_pointer", "arguments":
            json.dumps({"snapshot_id": snapshot_id, "pointer": pointer})}}]}


def _run(tmp_path, messages: list[dict], **source_fields):
    source = tmp_path / "trajectory.jsonl"
    source.write_text(json.dumps({"messages": messages, **source_fields}, ensure_ascii=False) + "\n", encoding="utf-8")
    output = tmp_path / "out"
    run_id = create_run(output, sources=[source], targets=["agent"])
    state = Workflow(output, run_id, tmp_path).execute()
    path = run_path(output, run_id)
    return state, path, WorkflowApplication(FilesystemWorkflowDriver(tmp_path, output)), run_id


def test_replayed_multi_turn_trajectory_is_pruned_and_exported_without_model(tmp_path):
    messages = [
        {"role": "user", "content": "先计算 1 + 1"},
        _call("first", "1 + 1"), _result("first", "2"),
        {"role": "assistant", "content": "2"},
        {"role": "user", "content": "计算 2 + 2，只回答结果"},
        _call("a", "2 + 2"), _result("a", "4"),
        _call("b", "2 + 2"), _result("b", '{"result":4}'),
        {"role": "assistant", "content": "4"},
    ]
    # Identical observations are necessary for a safe mechanical duplicate.
    messages[8]["content"] = "4"
    state, path, app, run_id = _run(tmp_path, messages)
    assert state["status"] == "completed"
    assert state["usage"] == {}
    manifest = verify_artifacts(path)
    assert manifest["counts"] == {"agent": 1}
    training = app.artifact_preview(run_id, "agent")[0]
    assert [m["role"] for m in training["messages"]] == [
        "user", "assistant", "tool", "assistant", "user", "assistant", "tool", "assistant"]
    assert [c["id"] for m in training["messages"] for c in m.get("tool_calls", [])] == ["first", "a"]
    record = read_json(path / "artifacts" / "agent.records.json")[0]
    assert record["verification"]["verified_call_ids"] == ["first", "a", "b"]
    assert record["verification"]["verified_turns"] == [1, 2]
    assert record["verification"]["pruned_call_ids"] == ["b"]
    assert record["original_messages_sha256"]
    assert app.artifact_preview(run_id, "agent_negative") == []
    trainer = read_json(path / "artifacts" / "quality.json")["trainer_exports"]["agent"]
    assert trainer["status"] == "incompatible"
    assert trainer["summary"]["reasons"] == {"trl_undefined_tool": 1}
    assert not (path / "artifacts" / "trl_agent.jsonl").exists()


def test_wrong_calculator_observation_becomes_separate_negative_not_sft(tmp_path):
    messages = [{"role": "user", "content": "2 + 2"}, _call("a", "2 + 2"),
                _result("a", "5"), {"role": "assistant", "content": "5"}]
    state, path, app, run_id = _run(tmp_path, messages)
    assert state["status"] == "needs_attention"
    assert verify_artifacts(path)["counts"] == {"agent": 0}
    assert app.artifact_preview(run_id, "agent") == []
    negative = app.artifact_preview(run_id, "agent_negative")[0]
    assert negative["failure"] == "tool_observation_mismatch"
    assert negative["failure_step"] == 2
    assert negative["evidence"]["expected"] == 4
    assert negative["evidence"]["observed"] == 5
    assert len(negative["messages"]) == 3
    assert read_json(path / "artifacts" / "quality.json")["targets"]["agent"]["negative"] == 1
    assert verify_artifacts(path)["negative_counts"] == {"agent": 1}
    with pytest.raises(ValueError, match="unknown_training_target"):
        app.artifact_preview(run_id, "../recipe")
    (path / "artifacts" / "agent.negative.jsonl").write_text("tampered", encoding="utf-8")
    with pytest.raises(ValueError, match="artifact_integrity_error"):
        app.artifact_preview(run_id, "agent_negative")


def test_json_pointer_replays_only_a_pinned_source_snapshot(tmp_path):
    snapshots = {"inventory": {"a/b": {"~counts": [{"qty": 3}, {"qty": 4}]}}}
    tools = [{"type": "function", "function": {"name": "json_pointer", "parameters": {
        "type": "object", "properties": {"snapshot_id": {"type": "string"},
                                         "pointer": {"type": "string"}},
        "required": ["snapshot_id", "pointer"], "additionalProperties": False}}}]
    messages = [
        {"role": "user", "content": "第一项有多少？"},
        _pointer_call("lookup", "inventory", "/a~1b/~0counts/0/qty"),
        _result("lookup", '{"result":3}'),
        {"role": "assistant", "content": "3"},
    ]
    state, path, app, run_id = _run(tmp_path, messages, tool_snapshots=snapshots, tools=tools)
    assert state["status"] == "completed"
    assert len(app.artifact_preview(run_id, "agent")) == 1
    record = read_json(path / "artifacts" / "agent.records.json")[0]
    evidence = record["verification"]
    assert evidence["method"] == "snapshot_json_pointer_replay"
    assert evidence["source_snapshot_sha256"] == read_json(path / "recipe.json")["sources"][0]["sha256"]
    assert evidence["call_evidence"][0]["snapshot_sha256"]
    assert evidence["call_evidence"][0]["snapshot_id"] == "inventory"
    assert "tool_snapshots" not in app.artifact_preview(run_id, "agent")[0]
    assert read_json(path / "artifacts" / "quality.json")["trainer_exports"]["agent"]["status"] == "ready"
    assert (path / "artifacts" / "trl_agent.jsonl").exists()


def test_json_pointer_string_result_requires_exact_or_explicit_comparison(tmp_path):
    snapshots = {"record": {"status": "ready"}}
    prefix = [{"role": "user", "content": "状态？"},
              _pointer_call("lookup", "record", "/status"), _result("lookup", '{"result":"ready"}')]
    state, _, app, run_id = _run(tmp_path, [*prefix, {"role": "assistant", "content": "ready"}],
                                 tool_snapshots=snapshots)
    assert state["status"] == "completed"
    assert app.artifact_preview(run_id, "agent_negative") == []

    other = tmp_path / "prose"
    other.mkdir()
    state, path, app, run_id = _run(other, [*prefix, {"role": "assistant", "content": "状态为 pending"}],
                                    tool_snapshots=snapshots)
    assert state["status"] == "needs_attention"
    assert app.artifact_preview(run_id, "agent_negative") == []
    assert read_json(path / "artifacts" / "agent.records.json")[0]["reason"] == \
           "final_answer_not_deterministically_verifiable"


def test_missing_or_oversized_tool_snapshot_is_quarantined_without_negative(tmp_path):
    messages = [{"role": "user", "content": "数量？"},
                _pointer_call("lookup", "missing", "/qty"), _result("lookup", "1"),
                {"role": "assistant", "content": "1"}]
    state, path, app, run_id = _run(tmp_path, messages)
    assert state["status"] == "needs_attention"
    assert app.artifact_preview(run_id, "agent_negative") == []
    assert read_json(path / "artifacts" / "agent.records.json")[0]["reason"] == "missing_tool_snapshot"

    forged = tmp_path / "argument_document"
    forged.mkdir()
    forged_call = {"role": "assistant", "content": "", "tool_calls": [{"id": "lookup", "type": "function",
        "function": {"name": "json_pointer", "arguments": json.dumps({
            "snapshot_id": "missing", "pointer": "/qty", "document": {"qty": 1}})}}]}
    _, path, app, run_id = _run(forged, [messages[0], forged_call, *messages[2:]])
    assert app.artifact_preview(run_id, "agent_negative") == []
    assert read_json(path / "artifacts" / "agent.records.json")[0]["reason"] == \
           "invalid_json_pointer_arguments"

    oversized = tmp_path / "oversized"
    oversized.mkdir()
    state, path, app, run_id = _run(oversized, messages, tool_snapshots={"missing": {"text": "x" * 5000}})
    assert state["input_summary"]["quarantined"] == 1
    assert app.artifact_preview(run_id, "agent_negative") == []
    assert read_json(path / "input_records.json")[0]["reason"] == "tool_snapshot_limit_exceeded"


def test_agent_source_snapshot_tampering_stops_replay(tmp_path):
    messages = [{"role": "user", "content": "数量？"},
                _pointer_call("lookup", "record", "/qty"), _result("lookup", "1"),
                {"role": "assistant", "content": "1"}]
    source = tmp_path / "trajectory.jsonl"
    source.write_text(json.dumps({"messages": messages, "tool_snapshots": {"record": {"qty": 1}}}),
                      encoding="utf-8")
    output = tmp_path / "out"
    run_id = create_run(output, sources=[source], targets=["agent"])
    path = run_path(output, run_id)
    copied = next((path / "inputs").iterdir())
    copied.write_text(copied.read_text(encoding="utf-8").replace('"qty": 1', '"qty": 2'),
                      encoding="utf-8")
    state = Workflow(output, run_id, tmp_path).execute()
    assert state["status"] == "failed"
    assert state["error"] == "source_snapshot_changed"


def test_mixed_agent_batch_reports_attention_for_quarantined_trace(tmp_path):
    good = [{"role": "user", "content": "2+2"}, _call("a", "2+2"), _result("a", "4"),
            {"role": "assistant", "content": "4"}]
    unknown = [{"role": "user", "content": "查询"},
               {"role": "assistant", "content": "", "tool_calls": [
                   {"id": "w", "name": "web_search", "input": {"query": "example"}}]},
               _result("w", "recorded summary"), {"role": "assistant", "content": "1"}]
    source = tmp_path / "mixed.jsonl"
    source.write_text("".join(json.dumps({"messages": messages}, ensure_ascii=False) + "\n"
                              for messages in (good, unknown)), encoding="utf-8")
    output = tmp_path / "out"
    run_id = create_run(output, sources=[source], targets=["agent"])
    state = Workflow(output, run_id, tmp_path).execute()
    path = run_path(output, run_id)
    assert state["status"] == "needs_attention"
    assert verify_artifacts(path)["counts"] == {"agent": 1}
    assert read_json(path / "artifacts" / "quality.json")["targets"]["agent"]["total"] == 2


def test_unparseable_observation_and_supported_recorded_error_are_distinct(tmp_path):
    base = [{"role": "user", "content": "2+2"}, _call("a", "2+2")]
    state, path, app, run_id = _run(tmp_path, [*base, _result("a", "4.0"),
                                              {"role": "assistant", "content": "4"}])
    assert state["status"] == "needs_attention"
    assert app.artifact_preview(run_id, "agent_negative") == []
    assert read_json(path / "artifacts" / "agent.records.json")[0]["reason"] == \
           "unparseable_tool_observation"

    other = tmp_path / "error"
    other.mkdir()
    state, _, app, run_id = _run(other, [*base, _result("a", "failed", error=True),
                                         {"role": "assistant", "content": "4"}])
    assert state["status"] == "needs_attention"
    negative = app.artifact_preview(run_id, "agent_negative")[0]
    assert negative["failure"] == "recorded_tool_error"
    assert negative["evidence"]["basis"] == "bounded_arithmetic_replay"
    assert negative["evidence"]["expected"] == 4


def test_distinct_results_in_one_turn_do_not_create_a_false_negative(tmp_path):
    messages = [{"role": "user", "content": "先分别计算两个数，再回答它们的和"},
                _call("a", "2+2"), _result("a", "4"),
                _call("b", "1+1"), _result("b", "2"),
                {"role": "assistant", "content": "6"}]
    state, path, app, run_id = _run(tmp_path, messages)
    assert state["status"] == "needs_attention"
    assert app.artifact_preview(run_id, "agent_negative") == []
    assert read_json(path / "artifacts" / "agent.records.json")[0]["reason"] == "ambiguous_turn_result"


def test_simulated_agent_trace_is_not_promoted_by_matching_calculation(tmp_path):
    messages = [{"role": "user", "content": "2+2"}, _call("a", "2+2"), _result("a", "4"),
                {"role": "assistant", "content": "4"}]
    state, path, app, run_id = _run(tmp_path, messages, verification_status="synthetic_unverified")
    assert state["status"] == "needs_attention"
    assert app.artifact_preview(run_id, "agent") == []
    assert app.artifact_preview(run_id, "agent_negative") == []
    assert read_json(path / "artifacts" / "agent.records.json")[0]["reason"] == \
           "simulated_tool_observation_not_verified_agent"


@pytest.mark.parametrize("bad_name", [[], {}, 3, ""])
def test_invalid_tool_name_is_quarantined_without_crashing(tmp_path, bad_name):
    call = _call("a", "2+2")
    call["tool_calls"][0]["function"]["name"] = bad_name
    messages = [{"role": "user", "content": "2+2"}, call, _result("a", "4"),
                {"role": "assistant", "content": "4"}]
    state, path, app, run_id = _run(tmp_path, messages)
    assert state["status"] == "needs_attention"
    assert app.artifact_preview(run_id, "agent_negative") == []
    if state["input_summary"]["quarantined"]:
        assert read_json(path / "input_records.json")[0]["status"] == "quarantined"
    else:
        assert read_json(path / "artifacts" / "agent.records.json")[0]["status"] == "quarantined"


def test_invalid_tool_calls_shape_is_quarantined(tmp_path):
    messages = [{"role": "user", "content": "2+2"},
                {"role": "assistant", "content": "", "tool_calls": {"id": "a", "name": "calculator"}},
                _result("a", "4"), {"role": "assistant", "content": "4"}]
    state, path, app, run_id = _run(tmp_path, messages)
    assert state["status"] == "needs_attention"
    assert app.artifact_preview(run_id, "agent_negative") == []
    assert read_json(path / "input_records.json")[0]["reason"] == "invalid_tool_calls"


def test_unknown_tool_and_its_reported_error_have_no_replay_evidence(tmp_path):
    unknown = [{"role": "user", "content": "查找资料"},
               {"role": "assistant", "content": "", "toolCalls": [
                   {"id": "w", "name": "web_search", "input": {"query": "example"}}]},
               {"role": "tool", "content": "网页摘要", "toolCallId": "w"},
               {"role": "assistant", "content": "3"}]
    state, path, app, run_id = _run(tmp_path, unknown)
    assert state["status"] == "needs_attention"
    assert app.artifact_preview(run_id, "agent_negative") == []
    assert read_json(path / "artifacts" / "agent.records.json")[0]["reason"] == "tool_replay_unavailable"

    errored = [*unknown[:2], {"role": "tool", "content": "请求超时", "toolCallId": "w", "isError": True}, unknown[-1]]
    other = tmp_path / "error_case"
    other.mkdir()
    state, path, app, run_id = _run(other, errored)
    assert state["status"] == "needs_attention"
    assert app.artifact_preview(run_id, "agent_negative") == []
    assert read_json(path / "artifacts" / "agent.records.json")[0]["reason"] == "tool_replay_unavailable"


def test_calculator_cannot_execute_arbitrary_code_and_final_answer_must_match(tmp_path):
    malicious = [{"role": "user", "content": "计算"},
                 _call("a", "__import__('os').system('whoami')"),
                 _result("a", "0"), {"role": "assistant", "content": "0"}]
    state, path, app, run_id = _run(tmp_path, malicious)
    assert state["status"] == "needs_attention"
    assert read_json(path / "artifacts" / "agent.records.json")[0]["reason"] == "unsupported_calculator_expression"
    assert app.artifact_preview(run_id, "agent_negative") == []

    wrong_final = [{"role": "user", "content": "2 + 2"}, _call("a", "2 + 2"),
                   _result("a", "4"), {"role": "assistant", "content": "5"}]
    other = tmp_path / "wrong_final"
    other.mkdir()
    _, _, app, run_id = _run(other, wrong_final)
    assert app.artifact_preview(run_id, "agent_negative")[0]["failure"] == "final_answer_mismatch"


def test_earlier_wrong_answer_cannot_be_hidden_by_correct_final_turn(tmp_path):
    messages = [
        {"role": "user", "content": "2 + 2"},
        _call("first", "2 + 2"), _result("first", "4"),
        {"role": "assistant", "content": "5"},
        {"role": "user", "content": "3 + 3"},
        _call("second", "3 + 3"), _result("second", "6"),
        {"role": "assistant", "content": "6"},
    ]
    state, path, app, run_id = _run(tmp_path, messages)
    assert state["status"] == "needs_attention"
    assert verify_artifacts(path)["counts"] == {"agent": 0}
    assert app.artifact_preview(run_id, "agent") == []
    negative = app.artifact_preview(run_id, "agent_negative")[0]
    assert negative["failure"] == "intermediate_answer_mismatch"
    assert negative["failure_step"] == 3
    assert negative["evidence"]["turn"] == 1
    assert negative["evidence"]["expected"] == 4
    assert negative["evidence"]["observed"] == 5
    assert len(negative["messages"]) == 4


def test_unreplayed_earlier_assistant_turn_is_quarantined_without_false_negative(tmp_path):
    messages = [
        {"role": "user", "content": "先打个招呼"},
        {"role": "assistant", "content": "你好。"},
        {"role": "user", "content": "2 + 2"},
        _call("calc", "2 + 2"), _result("calc", "4"),
        {"role": "assistant", "content": "4"},
    ]
    state, path, app, run_id = _run(tmp_path, messages)
    assert state["status"] == "needs_attention"
    assert verify_artifacts(path)["counts"] == {"agent": 0}
    assert app.artifact_preview(run_id, "agent_negative") == []
    record = read_json(path / "artifacts" / "agent.records.json")[0]
    assert record["reason"] == "unverified_assistant_turn"
    assert record["unverified_turn"] == 1


def test_agent_only_open_brief_needs_recorded_trajectory(tmp_path):
    with pytest.raises(ValueError, match="JSON/JSONL"):
        create_run(tmp_path, brief="查找资料", targets=["agent"])


def test_simulated_agent_observations_do_not_enter_sft_training(tmp_path):
    source = tmp_path / "simulated.jsonl"
    messages = [{"role": "user", "content": "计算"}, _call("a", "2 + 2"),
                _result("a", "4"), {"role": "assistant", "content": "4"}]
    source.write_text(json.dumps({"messages": messages, "verification_status": "synthetic_unverified"},
                                 ensure_ascii=False), encoding="utf-8")
    output = tmp_path / "out"
    run_id = create_run(output, sources=[source], targets=["sft"])
    state = Workflow(output, run_id, tmp_path).execute()
    assert state["status"] == "needs_attention"
    assert state["usage"] == {}
    assert read_json(run_path(output, run_id) / "artifacts" / "sft.records.json")[0]["reason"] == \
           "simulated_tool_observation_not_verified_sft"


def test_sensitive_tool_arguments_never_enter_positive_or_negative_exports(tmp_path):
    messages = [{"role": "user", "content": "计算"},
                _call("a", "2 + 2"), _result("a", "5"),
                {"role": "assistant", "content": "5"}]
    messages[1]["tool_calls"][0]["function"]["arguments"] = json.dumps({
        "expression": "2 + 2", "contact": "test@example.com"})
    state, path, app, run_id = _run(tmp_path, messages)
    assert state["status"] == "needs_attention"
    assert state["input_summary"]["quarantined"] == 1
    assert app.artifact_preview(run_id, "agent") == []
    assert app.artifact_preview(run_id, "agent_negative") == []
    assert read_json(path / "input_records.json")[0]["reason"] == "potential_personal_data"


def test_version_two_unfinished_runs_resume_without_agent_stage_key(tmp_path):
    source = tmp_path / "guide.txt"
    source.write_text("维护前先断电，检查完成后记录结果。", encoding="utf-8")
    output = tmp_path / "out"
    run_id = create_run(output, sources=[source], targets=["cpt"])
    path = run_path(output, run_id)
    recipe = read_json(path / "recipe.json")
    recipe["version"] = 2
    atomic_json(path / "recipe.json", recipe)
    state = read_json(path / "state.json")
    state["recipe_hash"] = digest(recipe)
    del state["stages"]["agent"]
    atomic_json(path / "state.json", state)

    completed = Workflow(output, run_id, tmp_path).execute()
    assert completed["status"] == "completed"
    assert completed["stages"]["agent"]["status"] == "skipped"
