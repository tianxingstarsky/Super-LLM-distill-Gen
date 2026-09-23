"""Offline end-to-end contracts for complete multi-turn training examples."""
from __future__ import annotations

import json

from lib.application.workflow_service import WorkflowApplication
from lib.domain.multiturn import completed_turn_ends
from lib.infrastructure.training_workflow import Workflow, create_run, digest, read_json, resume, run_path, verify_artifacts
from lib.infrastructure.workflow_driver import FilesystemWorkflowDriver
from lib.io_utils import atomic_json


def _verdict(keep=True):
    score = 5 if keep else 2
    return {"keep": keep, "grounded": keep, "reasoning_valid": keep,
            "correctness": score, "scores": {key: score for key in
            ("correctness", "reasoning", "grounding", "instruction", "safety")},
            "reason": "轮次与已提供上下文一致" if keep else "上下文约束冲突"}


class Generator:
    model = "multiturn-generator-test"

    def __init__(self, *, source_text=""):
        self.calls = []
        self.source_text = source_text
        self.usage = {"calls": 0}

    def chat(self, messages, **kwargs):
        self.calls.append(messages)
        self.usage["calls"] += 1
        instruction = messages[0]["content"]
        data = json.loads(messages[1]["content"])
        if "规划互不重复" in instruction:
            return json.dumps({"tasks": [f"设备维护培训任务 {i}" for i in range(data["count"])]})
        if "自然且可回答的用户提问" in instruction:
            turn = data["turn"]
            return json.dumps({"message": ("如何安全检查设备？" if turn == 1 else
                                          "接着说明如何记录检查结果？" if turn == 2 else
                                          "如果复查仍发现故障，下一步如何处理？")})
        if "只回答最新用户消息" in instruction:
            turn = len([m for m in data["messages"] if m["role"] == "user"])
            answer = ("先断电，再检查线路。" if turn == 1 else
                      "记录故障、处理过程和复查结果。" if turn == 2 else
                      "继续保持断电，并请专业人员处理。")
            return json.dumps({"answer": answer,
                               "quotes": [self.source_text] if self.source_text else []}, ensure_ascii=False)
        raise AssertionError("unexpected generation prompt")


class Judge:
    model = "multiturn-judge-test"

    def __init__(self, *, reject_consistency=False, invalid_once=False):
        self.calls = []
        self.reject_consistency = reject_consistency
        self.invalid_once = invalid_once
        self.usage = {"calls": 0}

    def chat(self, messages, **kwargs):
        self.calls.append(messages)
        self.usage["calls"] += 1
        if self.invalid_once:
            self.invalid_once = False
            return json.dumps({"keep": "yes"})
        whole_dialogue = "独立检查完整对话" in messages[0]["content"]
        return json.dumps(_verdict(not (whole_dialogue and self.reject_consistency)), ensure_ascii=False)


def _run(tmp_path, *, brief="", sources=(), generator=None, judge=None, **options):
    output = tmp_path / "out"
    run_id = create_run(output, brief=brief, sources=sources, targets=["multiturn"], **options)
    state = Workflow(output, run_id, tmp_path, generator=generator or Generator(), jev=judge or Judge()).execute()
    return state, run_path(output, run_id), output, run_id


def test_open_brief_generates_full_three_turn_dialogue_with_separate_checks(tmp_path):
    gen, judge = Generator(), Judge()
    state, path, output, run_id = _run(tmp_path, brief="为新员工编写设备维护问答", tasks=1,
                             conversation_turns=3, generator=gen, judge=judge)
    assert state["status"] == "completed", state.get("error")
    assert len(gen.calls) == 7  # plan, then one user and one assistant call per turn
    assert len(judge.calls) == 4  # three turn checks plus whole-dialogue check
    manifest = verify_artifacts(path)
    assert manifest["counts"]["multiturn"] == 1
    payload = json.loads((path / "artifacts/multiturn.jsonl").read_text(encoding="utf-8"))
    assert [m["role"] for m in payload["messages"]] == ["user", "assistant"] * 3
    assert "为新员工" in payload["messages"][0]["content"]
    trainer = json.loads((path / "artifacts/trl_multiturn.jsonl").read_text(encoding="utf-8"))
    assert trainer["messages"] == payload["messages"]
    assert manifest["trainer_counts"]["multiturn"] == 1
    records = read_json(path / "artifacts/multiturn.records.json")
    assert records[0]["turn_count"] == 3
    assert len(records[0]["turn_reviews"]) == 3
    assert records[0]["synthetic"] is True
    assert records[0]["evidence_level"] == "model_assessed_synthetic"
    assert records[0]["source_name"] == "开放需求"
    assert records[0]["source_location"] == {"brief_task": 1}
    assert records[0]["source_verification"] == "no_external_source"
    assert records[0]["fact_verification"] == "not_independently_verified"
    assert records[0]["consistency"]["keep"] is True
    app = WorkflowApplication(FilesystemWorkflowDriver(tmp_path, output))
    assert app.artifact_preview(run_id, "multiturn") == [payload]


def test_imported_dialogue_preserves_every_message_and_source_location(tmp_path):
    messages = [
        {"role": "system", "content": "用简洁的中文作答"},
        {"role": "user", "content": "检查前做什么？", "custom": {"source_span": 12}},
        {"role": "assistant", "content": "先断电。", "reasoning_content": "记录中的要求。"},
        {"role": "user", "content": "之后呢？"},
        {"role": "assistant", "content": "检查线路并记录结果。"},
    ]
    source = tmp_path / "conversations.jsonl"
    source.write_text(json.dumps({"messages": messages, "tools": [], "text": "ancillary text"},
                                 ensure_ascii=False) + "\n", encoding="utf-8")
    gen, judge = Generator(), Judge()
    state, path, _, _ = _run(tmp_path, sources=[source], generator=gen, judge=judge)
    assert state["status"] == "completed", state.get("error")
    assert not gen.calls
    assert len(judge.calls) == 3
    payload = json.loads((path / "artifacts/multiturn.jsonl").read_text(encoding="utf-8"))
    assert payload == {"messages": messages}
    trainer = json.loads((path / "artifacts/trl_multiturn.jsonl").read_text(encoding="utf-8"))
    assert [(m["role"], m["content"]) for m in trainer["messages"]] == [
        (m["role"], m["content"]) for m in messages]
    record = read_json(path / "artifacts/multiturn.records.json")[0]
    assert record["source_id"] == read_json(path / "artifacts/manifest.json")["sources"][0]["sha256"]
    assert record["source_name"] == "conversations.jsonl"
    assert record["location"] == 1
    assert record["synthetic"] is False
    assert record["evidence_level"] == "recorded_context_model_assessed"
    assert record["source_verification"] == "recorded_unverified"


def test_top_level_json_messages_array_is_one_complete_conversation(tmp_path):
    messages = [{"role": "user", "content": "先做什么？"},
                {"role": "assistant", "content": "先断电。"},
                {"role": "user", "content": "接下来？"},
                {"role": "assistant", "content": "检查线路。"}]
    source = tmp_path / "whole_dialogue.json"
    source.write_text(json.dumps(messages, ensure_ascii=False), encoding="utf-8")
    state, path, _, _ = _run(tmp_path, sources=[source])
    assert state["status"] == "completed", state.get("error")
    assert json.loads((path / "artifacts/multiturn.jsonl").read_text(encoding="utf-8")) == {"messages": messages}
    assert read_json(path / "artifacts/multiturn.records.json")[0]["location"] == 1


def test_incomplete_import_and_whole_dialogue_rejection_are_quarantined(tmp_path):
    source = tmp_path / "incomplete.json"
    source.write_text(json.dumps({"messages": [
        {"role": "user", "content": "提问"}, {"role": "assistant", "content": "回答"}]},
        ensure_ascii=False), encoding="utf-8")
    state, path, _, _ = _run(tmp_path, sources=[source])
    assert state["status"] == "needs_attention"
    assert read_json(path / "artifacts/multiturn.records.json")[0]["reason"] == "insufficient_completed_turns"
    assert not (path / "artifacts/multiturn.jsonl").read_text(encoding="utf-8")

    gen, judge = Generator(), Judge(reject_consistency=True)
    state2, path2, _, _ = _run(tmp_path / "second", brief="维护问答", tasks=1,
                               generator=gen, judge=judge)
    assert state2["status"] == "needs_attention"
    record = read_json(path2 / "artifacts/multiturn.records.json")[0]
    assert record["reason"] == "multiturn_consistency_rejected"
    assert len(record["turn_reviews"]) == 3
    assert not (path2 / "artifacts/multiturn.jsonl").read_text(encoding="utf-8")


def test_bad_judge_schema_fails_then_resume_reuses_generated_turn(tmp_path):
    gen = Generator()
    state, path, output, run_id = _run(tmp_path, brief="维护问答", tasks=1,
                                       generator=gen, judge=Judge(invalid_once=True))
    assert state["status"] == "failed"
    assert state["error"] == "invalid_judge_boolean"
    calls_before_resume = len(gen.calls)
    assert calls_before_resume == 3  # plan, first user, first assistant
    resumed = resume(output, run_id, tmp_path, generator=gen, jev=Judge())
    assert resumed["status"] == "completed", resumed.get("error")
    assert len(gen.calls) == 7  # no repeat of the first generated turn
    assert resumed["attempt"] == 2
    assert verify_artifacts(path)["counts"]["multiturn"] == 1


def test_document_dialogue_includes_original_source_and_exact_quotes(tmp_path):
    text = "设备启动前必须检查电源连接。发现故障时先断电，再检查线路。维护结束后记录检查结果。"
    source = tmp_path / "guide.txt"
    source.write_text(text, encoding="utf-8")
    state, path, _, _ = _run(tmp_path, sources=[source],
                             generator=Generator(source_text=text), judge=Judge(), conversation_turns=2)
    assert state["status"] == "completed", state.get("error")
    payload = json.loads((path / "artifacts/multiturn.jsonl").read_text(encoding="utf-8"))
    assert text in payload["messages"][0]["content"]
    record = read_json(path / "artifacts/multiturn.records.json")[0]
    assert record["quotes_by_turn"] == [[text], [text]]
    assert record["evidence_level"] == "source_and_model_assessed"
    assert record["source_verification"] == "exact_quote_presence_only"


def test_turn_structure_requires_two_completed_user_assistant_turns():
    messages = [{"role": "user", "content": "a"}, {"role": "assistant", "content": "b"},
                {"role": "user", "content": "c"}, {"role": "assistant", "content": "d"}]
    assert completed_turn_ends(messages) == ([1, 3], None)
    assert completed_turn_ends(messages[:2])[1] == "insufficient_completed_turns"
    malformed = [messages[0], messages[2], messages[1], messages[3]]
    assert completed_turn_ends(malformed)[1] == "unanswered_user_turn"


def test_recorded_tool_cycle_stays_within_its_user_turn():
    messages = [
        {"role": "user", "content": "计算"},
        {"role": "assistant", "content": "", "tool_calls": [{"id": "call1", "type": "function",
            "function": {"name": "calculator", "arguments": '{"expression":"2+2"}'}}]},
        {"role": "tool", "tool_call_id": "call1", "content": "4"},
        {"role": "assistant", "content": "答案是 4"},
        {"role": "user", "content": "再说明方法"},
        {"role": "assistant", "content": "将 2 与 2 相加。"},
    ]
    assert completed_turn_ends(messages) == ([3, 5], None)


def test_generated_turn_rejection_is_quarantined_without_partial_training_row(tmp_path):
    class RejectJudge(Judge):
        def chat(self, messages, **kwargs):
            super().chat(messages, **kwargs)
            return json.dumps(_verdict(False), ensure_ascii=False)

    gen = Generator()
    state, path, _, _ = _run(tmp_path, brief="维护问答", tasks=1, generator=gen, judge=RejectJudge())
    assert state["status"] == "needs_attention"
    assert len(gen.calls) == 5  # plan, then two attempts for the first turn
    record = read_json(path / "artifacts/multiturn.records.json")[0]
    assert record["reason"] == "multiturn_turn_failed_after_repair"
    assert record["failed_turn"] == 1
    assert not (path / "artifacts/multiturn.jsonl").read_text(encoding="utf-8")


def test_unfinished_old_recipe_can_resume_after_new_prompts_are_added(tmp_path):
    source = tmp_path / "old_guide.txt"
    source.write_text("维护前先断电，检查完成后记录结果。", encoding="utf-8")
    output = tmp_path / "out"
    run_id = create_run(output, sources=[source], targets=["cpt"])
    path = run_path(output, run_id)
    recipe = read_json(path / "recipe.json")
    for key in list(recipe["prompts"]):
        if key.startswith("workflow.multiturn_"):
            del recipe["prompts"][key]
    recipe.pop("conversation_turns")
    atomic_json(path / "recipe.json", recipe)
    state = read_json(path / "state.json")
    state["recipe_hash"] = digest(recipe)
    del state["stages"]["multiturn"]
    atomic_json(path / "state.json", state)
    result = Workflow(output, run_id, tmp_path).execute()
    assert result["status"] == "completed", result.get("error")
    assert result["stages"]["multiturn"]["status"] == "skipped"
