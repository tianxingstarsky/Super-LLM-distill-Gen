"""Ordinary dialogue limits do not force filler turns or bypass assessment."""
from copy import deepcopy
from hashlib import sha256
import json

import pytest

from lib.domain.multiturn import user_actor_action
from lib.infrastructure import training_workflow as engine
from lib.io_utils import atomic_json
from lib.prompts import get, render
from tests.test_multiturn_workflow import _verdict
from tests.test_workflow_production import model_request_data


@pytest.fixture(scope="session", autouse=True)
def mock_llm_server():
    yield


SOURCE = "检查设备前必须断电，断电后检查线路。完成检查后记录检查结果。"
END_REASON = "检查前提和结果记录已经说明，当前没有新的请求。"


class Writer:
    model = "ordinary-stop-offline-writer"

    def __init__(self, *, stop_after=2, flag="end", invalid=None, legacy=False):
        self.stop_after, self.flag, self.invalid, self.legacy = stop_after, flag, invalid, legacy
        self.calls, self.user_calls, self.answer_calls = [], [], []
        self.usage = {"calls": 0}

    def chat(self, messages, **kwargs):
        data = model_request_data(messages)
        self.calls.append(deepcopy(messages))
        self.usage["calls"] += 1
        if "previous_messages" in data:
            self.user_calls.append(deepcopy(data))
            turn = data["turn"]
            if self.invalid is not None:
                return json.dumps(self.invalid, ensure_ascii=False)
            if not self.legacy and turn > self.stop_after:
                return json.dumps({self.flag: True, "reason": END_REASON}, ensure_ascii=False)
            question = ("怎么安全检查设备？" if turn == 1 else "检查后怎么记录？" if turn == 2
                        else f"第{turn}次复查仍要保留哪些前提？")
            # Legacy snapshots did not assign any meaning to an end flag.
            return json.dumps({"message": question, **({"end": True} if self.legacy else {})}, ensure_ascii=False)
        self.answer_calls.append(deepcopy(data))
        answer = "先断电，再检查线路。" if len(self.answer_calls) == 1 else "检查后记录检查结果，复查时仍需先断电。"
        return json.dumps({"answer": answer, "quotes": [SOURCE]}, ensure_ascii=False)


class Judge:
    model = "ordinary-stop-offline-judge"

    def __init__(self, *, reject_stop=False, fail_whole_once=False):
        self.calls, self.turn_calls, self.whole_calls = [], [], []
        self.usage = {"calls": 0}
        self.reject_stop, self.fail_whole_once = reject_stop, fail_whole_once

    def chat(self, messages, **kwargs):
        data = model_request_data(messages)
        self.calls.append(deepcopy(messages))
        self.usage["calls"] += 1
        whole = data["context"].get("whole_dialogue")
        (self.whole_calls if whole else self.turn_calls).append(deepcopy(data))
        if whole and self.fail_whole_once:
            self.fail_whole_once = False
            return json.dumps({"keep": "invalid"})
        return json.dumps(_verdict(not (whole and self.reject_stop)), ensure_ascii=False)


def create(tmp_path, *, turns=6):
    source = tmp_path / "guide.txt"
    source.write_text(SOURCE, encoding="utf-8")
    output = tmp_path / "output"
    run_id = engine.create_run(output, sources=[source], targets=["multiturn"], conversation_turns=turns)
    return output, run_id, engine.run_path(output, run_id)


def record(path):
    return engine.read_json(path / "artifacts/multiturn.records.json")[0]


@pytest.mark.parametrize("flag", ["end", "stop"])
def test_goal_met_ends_without_fake_user_or_extra_answer_but_keeps_whole_review(tmp_path, flag):
    output, run_id, path = create(tmp_path)
    writer, judge = Writer(flag=flag), Judge()
    result = engine.Workflow(output, run_id, tmp_path, generator=writer, jev=judge).execute()
    assert result["status"] == "completed", result.get("error")
    row = record(path)
    assert row["turn_count"] == 2 and row["dialogue_end_reason"] == "natural_completion"
    assert row["dialogue_end_after_turn"] == 2 and row["dialogue_end_guidance"] == END_REASON
    assert len(writer.user_calls) == 3 and len(writer.answer_calls) == 2
    assert len(judge.turn_calls) == 2 and len(judge.whole_calls) == 1
    assert judge.whole_calls[0]["context"]["dialogue_end_scope"] == "user_actor_suggestion_not_message_or_evidence"
    assert "用户生成器提出结束建议" in judge.calls[-1][0]["content"]
    payload = json.loads((path / "artifacts/multiturn.jsonl").read_text("utf-8"))
    assert payload == {"messages": row["messages"]}
    assert [message["role"] for message in payload["messages"]] == ["user", "assistant"] * 2
    assert END_REASON not in json.dumps(payload, ensure_ascii=False)
    assert "断电" in payload["messages"][1]["content"] and "记录" in payload["messages"][3]["content"]
    assert row["quotes_by_turn"] == [[SOURCE], [SOURCE]]
    assert engine.verify_artifacts(path)["counts"] == {"multiturn": 1}


def test_natural_end_after_one_complete_turn_is_isolated_without_buying_filler(tmp_path):
    output, run_id, path = create(tmp_path)
    writer, judge = Writer(stop_after=1), Judge()
    result = engine.Workflow(output, run_id, tmp_path, generator=writer, jev=judge).execute()
    assert result["status"] == "needs_attention", result.get("error")
    row = record(path)
    assert row["reason"] == "multiturn_natural_end_insufficient_turns" and row["completed_turns"] == 1
    assert len(writer.user_calls) == 2 and len(writer.answer_calls) == len(judge.turn_calls) == 1
    assert not judge.whole_calls
    assert len(row["messages"]) == 2 and row["dialogue_end_reason"] == "natural_completion"
    assert (path / "artifacts/multiturn.jsonl").read_text("utf-8") == ""


@pytest.mark.parametrize("versioned", [False, True])
def test_terminal_generation_keeps_source_snapshot_only_for_the_new_protocol(tmp_path, versioned):
    from lib.infrastructure.human_revision import revision_evidence
    output, run_id, path = create(tmp_path)
    recipe = engine.read_json(path / "recipe.json")
    if versioned:
        recipe["multiturn_terminal_review_version"] = 1
    else:
        recipe.pop("multiturn_terminal_review_version", None)
    atomic_json(path / "recipe.json", recipe)
    state = engine.read_json(path / "state.json")
    state["recipe_hash"] = engine.digest(recipe)
    atomic_json(path / "state.json", state)
    writer, judge = Writer(stop_after=1), Judge()
    result = engine.Workflow(output, run_id, tmp_path, generator=writer, jev=judge).execute()
    assert result["status"] == "needs_attention", result.get("error")
    row = record(path)
    assert row["reason"] == "multiturn_natural_end_insufficient_turns"
    assert ("source_context" in row) is versioned
    if versioned:
        assert row["source_context"]["text"] == SOURCE
        assert row["source_context"]["source_context_pack"]
        assert row["evidence_level"] == "source_and_model_assessed"
        assert row["quotes_by_turn"] == [[SOURCE]]
        assert SOURCE in revision_evidence(row, recipe, path)["teacher_evidence"]
    assert not judge.whole_calls and len(writer.answer_calls) == 1


def test_batch_terminal_single_turn_bypasses_review_without_becoming_a_model_output_error(tmp_path, monkeypatch):
    source = tmp_path / "terminal-guide.txt"
    source.write_text(SOURCE, encoding="utf-8")
    output = tmp_path / "output"
    run_id = engine.create_run(output, sources=[source], targets=["multiturn"],
        conversation_turns=3, review_repair={"mode": "auto", "max_rounds": 2},
        production={"version": 2, "goals": {"multiturn": 1}, "max_attempts": 1,
                    "max_rounds": 1, "round_size": 1})
    path = engine.run_path(output, run_id)
    recipe = engine.read_json(path / "recipe.json")
    recipe["multiturn_terminal_review_version"] = 1
    atomic_json(path / "recipe.json", recipe)
    state = engine.read_json(path / "state.json")
    state["recipe_hash"] = engine.digest(recipe)
    atomic_json(path / "state.json", state)
    class NoCalls:
        model = "terminal-never-enters-review-model"
        usage = {}
        def chat(self, *_args, **_kwargs):
            pytest.fail("A terminal generation result must not request a model review.")
    def terminal(workflow, unit):
        return [{**workflow.rejected(unit, "multiturn_natural_end_insufficient_turns"),
            "messages": [{"role": "user", "content": "怎么检查？"},
                         {"role": "assistant", "content": "先断电。", "reasoning_content": "检查前提是断电。"}],
            "completed_turns": 1, "dialogue_end_reason": "natural_completion", "source_context": deepcopy(unit)}]
    monkeypatch.setattr(engine.Workflow, "multiturn", terminal)
    result = engine.Workflow(output, run_id, tmp_path, generator=NoCalls(), jev=NoCalls()).execute()
    assert result["status"] != "failed", result.get("error")
    row = record(path)
    assert row["reason"] == "multiturn_natural_end_insufficient_turns"
    assert row["review_repair"]["assessment"] == "not_performed_terminal_generation"
    assert row["review_repair"]["original_reason"] == row["reason"]
    assert not row.get("request_error") and not row["review_repair"].get("verdict")
    assert row["review_repair"]["attempts"] == 0 and row["source_context"]["text"] == SOURCE
    assert row["messages"][1]["reasoning_content"] == "检查前提是断电。"
    assert engine.verify_artifacts(path)["counts"] == {"multiturn": 0}


@pytest.mark.parametrize("invalid,reason", [
    ({"end": True, "reason": END_REASON}, "multiturn_stop_before_first_turn"),
    ({"stop": True, "reason": ""}, "invalid_multiturn_stop"),
    ({"end": True, "reason": END_REASON, "message": "不要写成用户消息"}, "invalid_multiturn_stop"),
    ({"end": "yes", "reason": END_REASON}, "invalid_multiturn_user_action"),
    ({"end": True, "stop": True, "reason": END_REASON}, "invalid_multiturn_user_action"),
    ({}, "invalid_multiturn_user_action"),
])
def test_bad_or_first_stop_is_bounded_and_cannot_publish_an_empty_dialogue(tmp_path, invalid, reason):
    output, run_id, path = create(tmp_path)
    writer, judge = Writer(invalid=invalid), Judge()
    result = engine.Workflow(output, run_id, tmp_path, generator=writer, jev=judge).execute()
    assert result["status"] == "needs_attention", result.get("error")
    assert record(path)["reason"] == reason
    assert len(writer.user_calls) == 2 and not writer.answer_calls and not judge.calls
    assert engine.verify_artifacts(path)["counts"] == {"multiturn": 0}


def test_stop_claim_cannot_bypass_unfinished_goal_or_real_dependency_review(tmp_path):
    output, run_id, path = create(tmp_path)
    writer, judge = Writer(), Judge(reject_stop=True)
    result = engine.Workflow(output, run_id, tmp_path, generator=writer, jev=judge).execute()
    assert result["status"] == "needs_attention", result.get("error")
    assert record(path)["reason"] == "multiturn_consistency_rejected"
    assert record(path)["consistency"]["keep"] is False
    assert "操作依赖与安全前提" in judge.calls[-1][0]["content"]
    assert len(writer.answer_calls) == 2 and len(judge.whole_calls) == 1
    assert engine.verify_artifacts(path)["counts"] == {"multiturn": 0}


def test_bad_final_verdict_resume_reuses_paid_stop_and_all_completed_turns(tmp_path):
    output, run_id, path = create(tmp_path)
    writer, judge = Writer(), Judge(fail_whole_once=True)
    workflow = engine.Workflow(output, run_id, tmp_path, generator=writer, jev=judge)
    failed = workflow.execute()
    assert failed["status"] == "failed" and failed["error"] == "invalid_judge_boolean"
    before = deepcopy(writer.calls)
    restored = engine.resume(output, run_id, tmp_path, generator=writer, jev=judge)
    assert restored["status"] == "completed", restored.get("error")
    assert writer.calls == before and len(judge.whole_calls) == 2
    assert record(path)["turn_count"] == 2 and engine.verify_artifacts(path)["counts"] == {"multiturn": 1}


def test_old_node_snapshot_without_stop_marker_keeps_v13_literal_and_fixed_loop(tmp_path):
    output, run_id, path = create(tmp_path, turns=3)
    recipe = engine.read_json(path / "recipe.json")
    recipe.pop("multiturn_stop_protocol_version")
    old = get("workflow.multiturn_user", version="1.3.0")
    assert sha256(old.template.encode("utf-8")).hexdigest() == "a1e97ca79a4b7b2939346818267360d22e87d6c4ef7aa97d1e74df847e2105eb"
    recipe["node_prompt_templates"]["multiturn"][old.id] = render(old)
    atomic_json(path / "recipe.json", recipe)
    state = engine.read_json(path / "state.json")
    state.update(recipe_hash=engine.digest(recipe), status="cancelled")
    atomic_json(path / "state.json", state)
    writer, judge = Writer(legacy=True), Judge()
    result = engine.Workflow(output, run_id, tmp_path, generator=writer, jev=judge).execute(resume_run=True)
    assert result["status"] == "completed", result.get("error")
    assert len(writer.user_calls) == len(writer.answer_calls) == 3 and len(judge.calls) == 4
    assert record(path)["turn_count"] == 3 and "dialogue_end_reason" not in record(path)
    assert render(old) in writer.calls[0][0]["content"]
    assert all("用户生成器提出结束建议" not in message[0]["content"] for message in judge.calls)


@pytest.mark.parametrize("action,expected", [
    ({"message": "继续这个版本"}, {"end": False, "message": "继续这个版本"}),
    ({"message": "再短点", "end": False}, {"end": False, "message": "再短点"}),
    ({"message": "已完成", "stop": False}, {"end": False, "message": "已完成"}),
    ({"end": True, "reason": END_REASON}, {"end": True, "reason": END_REASON}),
    ({"stop": True, "reason": END_REASON}, {"end": True, "reason": END_REASON}),
])
def test_user_actor_action_preserves_literals_and_only_treats_boolean_stop_as_control(action, expected):
    original = deepcopy(action)
    assert user_actor_action(action) == expected and action == original


def test_new_user_contract_has_one_schema_and_does_not_force_new_conditions_or_turns():
    current = render(get("workflow.multiturn_user"))
    assert get("workflow.multiturn_user").version == "1.5.0"
    assert "轮数上限" in current and "不必每轮加新条件" in current
    assert "不等于必须补全全部信息或排除真实歧义" in current
    assert "只完成一个轮次可合理停下" in current
    assert '"end":true' in current and '"message":"完整真实用户消息"' in current
    assert "后续轮次必须承接之前的回答并引入有价值的新约束" not in current
    assert "操作有已核验的先决条件" in current


def test_stop_isolation_reasons_are_localized_in_node_and_package_views():
    from lib.presentation.streamlit.workflow_quality_page import REASON_LABELS
    from lib.presentation.streamlit.i18n import translate
    for reason in ("invalid_multiturn_user_action", "invalid_multiturn_stop", "multiturn_stop_before_first_turn",
                   "multiturn_natural_end_insufficient_turns"):
        chinese = REASON_LABELS[reason]
        assert translate(chinese, "en") != chinese
