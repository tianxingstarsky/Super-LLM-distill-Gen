"""Offline proofs of adaptive language design, real turn feedback and stopping."""
from copy import deepcopy
import json

import pytest

from lib.domain.workflow_qa_director import (
    DEFAULT_TYPE_WEIGHTS, validate_dialogue_design, validate_dialogue_step,
    validate_director_task, validate_qa_director,
)
from lib.infrastructure import training_workflow as engine
from lib.prompts import get, render


SOURCE = "检查设备前必须先断电。结束后记录检查结果。"
FIRST_MESSAGE = "我想把这份检查流程交给新同事，先给我一份操作卡。"
SECOND_MESSAGE = "第二步那项请用最短的措辞，记录字段按你刚才那份卡继续。"
FIRST_ANSWER = "操作卡：一、先断电；二、检查设备；三、记录检查结果。"
SECOND_ANSWER = "第二步写为“检查设备”。记录字段为检查结果。"


def recipe(**changes):
    return {"enabled": True, "planning_mode": "adaptive", "batch_size": 1,
            "history_limit": 2, "type_weights": {key: int(key == "closed_book")
                for key in DEFAULT_TYPE_WEIGHTS}, **changes}


def design(**changes):
    return {"interaction_goal": "共同完成适合新同事使用的设备操作卡。",
            "user_intent": "将流程变为简洁操作卡。", "context_links": ["首条消息给出操作卡用途。"],
            "success_criteria": ["操作顺序与资料一致，回应实际修改要求。"],
            "turn_guidance": ["根据实际卡片内容继续修改措辞。"],
            "stop_when": "修改要求得到回应且没有新的有价值活动。", **changes}


class Expert:
    model = "language-expert"

    def __init__(self, *, skip=None, invalid_step=False, false_state=False, stop_early=False,
                 fail_step_once=False, false_stop_state=False, confirmation=False, repeat_message=False):
        self.calls, self.usage = [], {"calls": 0}
        self.skip, self.invalid_step = skip, invalid_step
        self.false_state, self.stop_early, self.fail_step_once = false_state, stop_early, fail_step_once
        self.false_stop_state, self.confirmation = false_stop_state, confirmation
        self.repeat_message = repeat_message

    def chat(self, messages, **kwargs):
        self.calls.append(deepcopy(messages))
        self.usage["calls"] += 1
        data = json.loads(messages[1]["content"])
        if data.get("request_phase") == "batch_design":
            assert data["planning_mode"] == "adaptive"
            assert data["type_weights_are_not_quotas"] is True
            tasks = []
            for candidate in data["candidates"]:
                assert candidate["assigned_type"] is None
                if self.skip:
                    tasks.append({"id": candidate["id"], "skip_reason": self.skip,
                                  "guidance": "已无新的资料支持，不改写凑数。"})
                else:
                    tasks.append({"id": candidate["id"], "qa_type": "grounded",
                        "question": FIRST_MESSAGE, "visible_context": SOURCE, "answer_policy": "answer",
                        "guidance": "协作完成操作卡，不强行改成问答。", "evidence_quotes": [SOURCE],
                        "dialogue_design": design()})
            return json.dumps({"tasks": tasks}, ensure_ascii=False)
        assert data["request_phase"] == "next_turn"
        if self.fail_step_once:
            self.fail_step_once = False
            raise RuntimeError("offline interruption at dialogue replan")
        if self.invalid_step:
            return json.dumps({"continue": True, "user_message": "缺少状态与理由"})
        completed = data["completed_turns"]
        assert data["dialogue_messages"][1]["content"] == FIRST_ANSWER
        if (completed == 1 or self.repeat_message and completed == 2) and not self.stop_early:
            state = {"user_intent": "缩短第二步措辞并保留已有卡片的记录项。",
                "progress": "首版操作卡已给出，还未完成修改。",
                "shared_understanding": [FIRST_ANSWER], "open_issues": ["修改第二步措辞。"],
                "active_constraints": ["仍须先断电。"],
                "context_links": ["第二步与那份卡引用上轮实际操作卡。"]}
            if self.false_state:
                state["shared_understanding"].append("用户已说设备是999伏。")
            if self.repeat_message:
                state.update(shared_understanding=[data["dialogue_messages"][-1]["content"]],
                             user_intent="进一步缩短上轮实际卡片版本。")
            return json.dumps({"continue": True, "user_message": "再简短一点。" if self.repeat_message else
                "好，我知道如何使用这份卡了。" if self.confirmation else SECOND_MESSAGE,
                "reason": "上一轮已给出初稿，可据实际措辞继续修改。", "dialogue_state": state}, ensure_ascii=False)
        final_state = {"progress": "操作卡修改已完成。"}
        if self.false_stop_state:
            final_state["shared_understanding"] = ["用户已说设备是999伏。"]
        return json.dumps({"continue": False, "user_message": "", "reason": "实际修改已完成，没有新的必要活动。",
                           "dialogue_state": final_state}, ensure_ascii=False)


class Worker:
    model = "dialogue-worker"

    def __init__(self, *, confirmation=False, repeat_message=False):
        self.calls, self.usage = [], {"calls": 0}
        self.confirmation = confirmation
        self.repeat_message = repeat_message

    def chat(self, messages, **kwargs):
        self.calls.append(deepcopy(messages))
        self.usage["calls"] += 1
        data = json.loads(messages[1]["content"])
        assert data["dialogue_design"]["interaction_goal"] == design()["interaction_goal"]
        assert data["dialogue_state"] is not None
        turn = len([message for message in data["messages"] if message["role"] == "user"])
        answer = FIRST_ANSWER if turn == 1 else "好的。" if self.confirmation else SECOND_ANSWER
        if self.repeat_message and turn > 1:
            answer = "先断电，检查设备，记录结果。" if turn == 2 else "断电→检查→记录。"
        return json.dumps({"answer": answer,
                           "quotes": [] if self.confirmation and turn == 2 else [SOURCE]}, ensure_ascii=False)


class Reviewer:
    model = "independent-reviewer"

    def __init__(self):
        self.calls, self.usage = [], {"calls": 0}

    def chat(self, messages, **kwargs):
        self.calls.append(deepcopy(messages))
        self.usage["calls"] += 1
        data = json.loads(messages[1]["content"])
        keep = "用户已说设备是999伏。" not in json.dumps(data, ensure_ascii=False)
        score = 5 if keep else 1
        if '"adherence"' in messages[0]["content"]:
            return json.dumps({"keep": keep, "adherence": score,
                               "reason": "依据真实消息核对状态。" if keep else "状态虚构了用户事实。"})
        return json.dumps({"keep": keep, "grounded": keep, "reasoning_valid": keep,
            "correctness": score, "scores": {key: score for key in (
                "correctness", "reasoning", "grounding", "instruction", "safety")},
            "reason": "来源与真实对话已核对。" if keep else "状态不能代替事实证据。"})


def make_run(tmp_path, monkeypatch, expert=None, *, conversation_turns=8):
    source = tmp_path / "source.txt"
    source.write_text(SOURCE, encoding="utf-8")
    output = tmp_path / "out"
    expert = expert or Expert()
    worker, reviewer = Worker(confirmation=expert.confirmation, repeat_message=expert.repeat_message), Reviewer()
    loaded = []

    def backend(root, *, model=None, **kwargs):
        loaded.append(model)
        return (expert if model == expert.model else worker), {}

    monkeypatch.setattr(engine, "load_backend", backend)
    run_id = engine.create_run(output, sources=[source], targets=["multiturn"], conversation_turns=conversation_turns,
        qa_director=recipe(), node_models={"director": {"generation": {"backend": "openai", "model": expert.model}},
            "multiturn": {"generation": {"backend": "openai", "model": worker.model}}})
    return output, run_id, engine.run_path(output, run_id), expert, worker, reviewer, loaded


def execute(tmp_path, monkeypatch, expert=None):
    output, run_id, path, expert, worker, reviewer, loaded = make_run(tmp_path, monkeypatch, expert)
    state = engine.Workflow(output, run_id, tmp_path, jev=reviewer).execute()
    return state, path, expert, worker, reviewer, loaded


def test_old_recipe_does_not_acquire_new_mode_or_change_schedule():
    assert "planning_mode" not in validate_qa_director({"enabled": True})
    assert validate_qa_director(recipe())["planning_mode"] == "adaptive"
    assert validate_qa_director(recipe(type_weights=dict.fromkeys(DEFAULT_TYPE_WEIGHTS, 0)))["planning_mode"] == "adaptive"
    with pytest.raises(ValueError, match="planning_mode"):
        validate_qa_director(recipe(planning_mode="rigid_dialogue_act_quota"))


def test_new_dialogue_assets_keep_original_version_lookups_and_render_safely():
    for name in ("plan", "qa_director", "sft_directed", "sft_directed_check", "multiturn_user",
                 "multiturn_assistant", "multiturn_consistency", "multiturn_directed_check"):
        prompt_id = "workflow." + name
        current = get(prompt_id)
        old = get(prompt_id, version="1.0.0")
        assert current.version == "1.1.0" and old.version == "1.0.0"
        assert render(current) != render(old)
    assert "request_phase=next_turn" in render(get("workflow.qa_director"))
    assert "request_phase=next_turn" not in render(get("workflow.qa_director", version="1.0.0"))


def test_design_has_open_language_but_cannot_transport_hidden_executable_fields():
    value = design(user_intent="协商、改口、反馈、叙述和共同完成任务均可。")
    normalized = validate_dialogue_design(value)
    normalized["context_links"].append("different")
    assert "different" not in value["context_links"]
    with pytest.raises(ValueError, match="invalid_dialogue_design"):
        validate_dialogue_design({**value, "tool_command": "execute"})
    with pytest.raises(ValueError, match="invalid_dialogue_step"):
        validate_dialogue_step({"continue": True, "user_message": "继续", "reason": "有价值",
            "dialogue_state": {"shared_understanding": ["x" * 1001]}}, completed_turns=1)


def test_adaptive_contract_still_checks_literal_source_and_visible_evidence():
    value = {"id": "candidate", "qa_type": "grounded", "question": FIRST_MESSAGE,
        "visible_context": "用户设备是999伏。", "answer_policy": "answer", "guidance": "协作。",
        "evidence_quotes": [SOURCE], "dialogue_design": design()}
    with pytest.raises(ValueError, match="visible_context_not_in_source"):
        validate_director_task(value, expected_type=None, expected_id="candidate", source_text=SOURCE)


def test_live_replanning_uses_director_binding_actual_answers_and_natural_two_turn_end(tmp_path, monkeypatch):
    state, path, expert, worker, reviewer, loaded = execute(tmp_path, monkeypatch)
    assert state["status"] == "completed", state.get("error")
    row = engine.read_json(path / "artifacts/multiturn.records.json")[0]
    assert row["turn_count"] == 2  # an upper bound of eight is not a quota
    assert row["dialogue_end_reason"] == "natural_completion"
    assert row["dialogue_state"] == {"progress": "操作卡修改已完成。"}
    assert row["dialogue_state_scope"] == "final_summary" and row["dialogue_state_after_turn"] == 2
    assert len(expert.calls) == 3 and len(worker.calls) == 2
    assert set(loaded) == {"language-expert", "dialogue-worker"}
    first = row["messages"][0]["content"]
    assert "用户消息：" in first and FIRST_MESSAGE in first and "问题：" not in first
    assert row["messages"][2]["content"] == SECOND_MESSAGE
    assert row["dialogue_steps"][1]["dialogue_state"]["shared_understanding"] == [FIRST_ANSWER]
    next_turn = json.loads(expert.calls[1][1]["content"])
    assert next_turn["dialogue_messages"][1]["content"] == FIRST_ANSWER
    reviewed = [json.loads(call[1]["content"]) for call in reviewer.calls]
    assert any(data.get("dialogue_state", {}).get("context_links") for data in reviewed)
    assert any(data.get("context", {}).get("whole_dialogue")
        and data.get("context", {}).get("dialogue_steps") for data in reviewed)
    trainer = json.loads((path / "artifacts/trl_multiturn.jsonl").read_text(encoding="utf-8"))
    assert "dialogue_state" not in trainer and "dialogue_design" not in trainer


@pytest.mark.parametrize("reason", ["source_exhausted", "no_new_grounded_scenario"])
def test_source_or_activity_exhaustion_skips_worker_without_fabricating_sample(tmp_path, monkeypatch, reason):
    state, path, expert, worker, reviewer, _ = execute(tmp_path, monkeypatch, Expert(skip=reason))
    assert state["status"] == "needs_attention", state.get("error")
    assert len(expert.calls) == 1 and not worker.calls and not reviewer.calls
    assert state["qa_director"]["feedback"][reason] == 1
    assert state["qa_director"]["skipped"] == 1
    row = engine.read_json(path / "artifacts/multiturn.records.json")[0]
    assert row["status"] == "quarantined" and row["reason"] == reason
    assert not (path / "artifacts/multiturn.jsonl").read_text(encoding="utf-8")


def test_inferred_dialogue_state_cannot_bypass_source_or_real_message_review(tmp_path, monkeypatch):
    state, path, _, _, reviewer, _ = execute(tmp_path, monkeypatch, Expert(false_state=True))
    assert state["status"] == "needs_attention", state.get("error")
    row = engine.read_json(path / "artifacts/multiturn.records.json")[0]
    assert row["reason"] == "multiturn_turn_failed_after_repair"
    assert row["failed_turn"] == 2
    assert reviewer.calls and not (path / "artifacts/multiturn.jsonl").read_text(encoding="utf-8")


def test_natural_end_state_is_adopted_but_fabricated_shared_fact_is_rejected(tmp_path, monkeypatch):
    state, path, _, _, reviewer, _ = execute(tmp_path, monkeypatch, Expert(false_stop_state=True))
    assert state["status"] == "needs_attention", state.get("error")
    row = engine.read_json(path / "artifacts/multiturn.records.json")[0]
    assert row["reason"] == "multiturn_consistency_rejected"
    final_review = json.loads(reviewer.calls[-1][1]["content"])["context"]
    assert final_review["dialogue_state"]["shared_understanding"] == ["用户已说设备是999伏。"]
    assert not (path / "artifacts/multiturn.jsonl").read_text(encoding="utf-8")


def test_nonfactual_confirmation_turn_does_not_need_forced_document_quote(tmp_path, monkeypatch):
    state, path, _, worker, _, _ = execute(tmp_path, monkeypatch, Expert(confirmation=True))
    assert state["status"] == "completed", state.get("error")
    row = engine.read_json(path / "artifacts/multiturn.records.json")[0]
    assert row["messages"][-1]["content"] == "好的。"
    assert row["quotes_by_turn"][-1] == [] and len(worker.calls) == 2


def test_same_user_words_can_advance_different_versions_in_adaptive_dialogue(tmp_path, monkeypatch):
    state, path, _, _, reviewer, _ = execute(tmp_path, monkeypatch, Expert(repeat_message=True))
    assert state["status"] == "completed", state.get("error")
    row = engine.read_json(path / "artifacts/multiturn.records.json")[0]
    assert row["turn_count"] == 3
    assert row["messages"][2]["content"] == row["messages"][4]["content"] == "再简短一点。"
    assert row["messages"][3]["content"] != row["messages"][5]["content"]
    assert row["turn_reviews"][2]["surface_repetition"] is True
    reviewed = [json.loads(call[1]["content"]) for call in reviewer.calls]
    assert any(data.get("context", {}).get("surface_repetition") for data in reviewed)


def test_turn_limit_marks_last_planning_state_scope_instead_of_claiming_final_summary(tmp_path, monkeypatch):
    output, run_id, path, _, _, reviewer, _ = make_run(tmp_path, monkeypatch, conversation_turns=2)
    state = engine.Workflow(output, run_id, tmp_path, jev=reviewer).execute()
    assert state["status"] == "completed", state.get("error")
    row = engine.read_json(path / "artifacts/multiturn.records.json")[0]
    assert row["dialogue_end_reason"] == "turn_limit"
    assert row["dialogue_state_scope"] == "before_last_turn" and row["dialogue_state_after_turn"] == 1
    final_review = json.loads(reviewer.calls[-1][1]["content"])["context"]
    assert final_review["dialogue_state_scope"] == "before_last_turn"


def test_repeated_words_without_actual_revision_are_quarantined_by_progress_review(tmp_path, monkeypatch):
    output, run_id, path, _, worker, reviewer, _ = make_run(
        tmp_path, monkeypatch, Expert(repeat_message=True))
    worker.repeat_message = False  # the third answer merely repeats the second
    original = reviewer.chat

    def check_progress(messages, **kwargs):
        data = json.loads(messages[1]["content"])
        context = data.get("context", {})
        previous_answers = [message["content"] for message in context.get("dialogue_before_answer", [])
                            if message["role"] == "assistant"]
        if (context.get("surface_repetition") and previous_answers
                and isinstance(data.get("answer"), dict)
                and data["answer"].get("content") == previous_answers[-1]):
            return json.dumps({"keep": False, "grounded": True, "reasoning_valid": True,
                "correctness": 2, "scores": {key: 2 for key in (
                    "correctness", "reasoning", "grounding", "instruction", "safety")},
                "reason": "修改请求得到同一版本回复，没有实际推进。"})
        return original(messages, **kwargs)

    monkeypatch.setattr(reviewer, "chat", check_progress)
    state = engine.Workflow(output, run_id, tmp_path, jev=reviewer).execute()
    assert state["status"] == "needs_attention", state.get("error")
    row = engine.read_json(path / "artifacts/multiturn.records.json")[0]
    assert row["reason"] == "multiturn_turn_failed_after_repair" and row["failed_turn"] == 3
    assert not (path / "artifacts/multiturn.jsonl").read_text(encoding="utf-8")


@pytest.mark.parametrize("options", [{"invalid_step": True}, {"stop_early": True}])
def test_invalid_or_single_turn_director_stop_is_bounded_and_not_silently_replaced(tmp_path, monkeypatch, options):
    state, path, expert, worker, _, _ = execute(tmp_path, monkeypatch, Expert(**options))
    assert state["status"] == "needs_attention", state.get("error")
    assert len(expert.calls) == 3 and len(worker.calls) == 1
    row = engine.read_json(path / "artifacts/multiturn.records.json")[0]
    assert row["reason"] == "multiturn_turn_failed_after_repair"


def test_resume_keeps_completed_answer_and_replans_from_identical_actual_history(tmp_path, monkeypatch):
    output, run_id, path, expert, worker, reviewer, _ = make_run(
        tmp_path, monkeypatch, Expert(fail_step_once=True))
    state = engine.Workflow(output, run_id, tmp_path, jev=reviewer).execute()
    assert state["status"] == "failed"
    before = json.loads(expert.calls[-1][1]["content"])
    state = engine.resume(output, run_id, tmp_path, jev=reviewer)
    assert state["status"] == "completed", state.get("error")
    assert json.loads(expert.calls[-2][1]["content"]) == before
    assert len(worker.calls) == 2  # the accepted first answer is reused
    assert engine.read_json(path / "artifacts/multiturn.records.json")[0]["turn_count"] == 2
