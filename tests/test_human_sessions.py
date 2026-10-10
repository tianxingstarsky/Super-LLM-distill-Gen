"""Offline end-to-end human workspaces and source-bound, reviewed revisions."""
from copy import deepcopy
import json
from pathlib import Path
import shutil

import pytest

from lib.bootstrap.workflows import human_augmentation_application
from lib.domain.human_augmentation import HUMAN_CHECK_FIELDS, validate_human_augmentation
from lib.infrastructure import training_workflow as engine
from lib.io_utils import atomic_json


Q = "检查设备前应该先做什么？"
A = "检查设备前必须先断电。检查结束后记录结果。"


@pytest.fixture(scope="session", autouse=True)
def mock_llm_server():
    yield


def design():
    return {"enabled": True, "seeds": [{"question": Q, "answer": A}],
            "question_requirements": "保持检查之前的条件。", "answer_requirements": "保持安全前提。"}


class Writer:
    model = "offline-session-writer"

    def __init__(self, *, apply_feedback=True, finish_early=False, target_turns=2):
        self.calls, self.usage = [], {"calls": 0}
        self.apply_feedback, self.finish_early = apply_feedback, finish_early
        self.target_turns = target_turns

    def chat(self, messages, **kwargs):
        data = json.loads(messages[1]["content"])
        self.calls.append(deepcopy(data))
        self.usage["calls"] += 1
        if data.get("request_phase") == "next_turn":
            complete = data["completed_turns"] >= (2 if self.finish_early else self.target_turns)
            question = "断电是在检查开始后才做吗？" if data["completed_turns"] >= 2 else "检查结束后该做什么？"
            return json.dumps({"continue": not complete, "user_message": question if not complete else "",
                "reason": "确认检查后的记录。" if not complete else "当前目标完成。",
                "dialogue_state": {"user_intent": "安全完成检查并记录", "progress": "已确认安全前提。"}})
        if "candidates" in data:
            tasks = []
            for candidate in data["candidates"]:
                seed = candidate["human_design"]["seed"]
                tasks.append({"id": candidate["id"], "qa_type": "closed_book", "question": seed["question"],
                    "visible_context": "", "answer_policy": "answer", "guidance": "保持安全前提与人工设计。",
                    "evidence_quotes": [candidate["teacher_evidence"]],
                    "dialogue_design": {"interaction_goal": "安全完成检查", "user_intent": "安全操作设备"}})
            return json.dumps({"tasks": tasks}, ensure_ascii=False)
        if "qa_contract" in data:
            seed = data["qa_contract"]["human_design"]["seed"]
            revision = seed.get("revision_context")
            if "messages" in data:
                later = len(data["messages"]) > 1
                answer = "检查结束后记录结果。" if later else "检查设备前必须先断电。"
                quote = answer
                if len(data["messages"]) >= 5:
                    answer, quote = "应该在检查开始前断电。", "检查设备前必须先断电。"
            else:
                answer = seed["answer"]
                quote = data["source_context"]["text"]
            if revision and self.apply_feedback:
                answer = "安全提醒：" + answer
            if "messages" in data:
                return json.dumps({"answer": answer, "quotes": [quote]}, ensure_ascii=False)
            return json.dumps({"question": data["qa_contract"]["question"], "answer": answer,
                "reasoning": "断电是开始检查前的安全条件。", "quotes": [quote]}, ensure_ascii=False)
        raise AssertionError(data)


class Reviewer:
    model = "offline-session-reviewer"

    def __init__(self):
        self.calls, self.usage = [], {"calls": 0}

    def chat(self, messages, **kwargs):
        data, prompt = json.loads(messages[1]["content"]), messages[0]["content"]
        self.calls.append(deepcopy(data))
        self.usage["calls"] += 1
        if '"question_intent_preserved"' in prompt:
            revision = data["human_design"]["seed"].get("revision_context")
            content = [m["content"] for m in data["learner_messages"] if m["role"] == "assistant"]
            keep = not revision or any("安全提醒：" in item for item in content)
            requested_turn = (3 if revision and "第三轮" in revision["instruction"] else
                              2 if revision and "第二轮" in revision["instruction"] else 1)
            if revision and requested_turn > 1 and data.get("review_scope") == "complete":
                keep = keep and len(content) >= requested_turn and "安全提醒：" in content[requested_turn - 1]
            elif revision and requested_turn > 1 and data.get("review_scope") == "completed_prefix" and len(content) < requested_turn:
                keep = True
            return json.dumps({"keep": keep, **{key: keep for key in HUMAN_CHECK_FIELDS},
                               "reason": "反馈已落实。" if keep else "选中结果的人工反馈未落实。"}, ensure_ascii=False)
        if '"adherence"' in prompt:
            return json.dumps({"keep": True, "adherence": 5, "reason": "对话契约通过。"})
        return json.dumps({"keep": True, "grounded": True, "reasoning_valid": True, "correctness": 5,
            "scores": {key: 5 for key in ("correctness", "reasoning", "grounding", "instruction", "safety")},
            "reason": "来源和回答通过模型核对。"})


def application(tmp_path, *, sources=(), targets=("sft",), max_revision_depth=3, production=None):
    shutil.copytree(Path(__file__).resolve().parents[1] / "configs", tmp_path / "configs", dirs_exist_ok=True)
    app = human_augmentation_application(tmp_path, tmp_path / "out")
    session_id = app.create_session(name="安全设备人工增强", targets=list(targets), sources=list(sources),
        qa_director={"enabled": True, "planning_mode": "adaptive", "batch_size": 1},
        initial_draft=design(), node_models={}, sample_count=1, conversation_turns=3,
        max_revision_depth=max_revision_depth, production=production)
    return app, session_id, tmp_path / "out"


def generate(app, session_id, output, root, *, request_id="first", writer=None):
    row = app.generate_round(session_id, request_id=request_id,
        expected_version=app.session(session_id)["version"], sample_count=1)
    writer, reviewer = writer or Writer(), Reviewer()
    state = engine.Workflow(output, row["run_id"], root, generator=writer, judge=reviewer).execute()
    assert state["status"] in {"completed", "needs_attention"}, state.get("error")
    return row, writer, reviewer


def revise(app, session_id, parent, candidate, output, root, *, instruction="使用更自然的安全提醒口吻。", writer=None):
    feedback = app.save_feedback(session_id, parent["id"], candidate["target"], candidate["candidate_id"],
        instruction=instruction, expected_version=app.session(session_id)["version"])
    row = app.revise_round(session_id, request_id="revise:" + parent["id"],
        selected_feedback_ids=[feedback["feedback_id"]], expected_version=feedback["version"], sample_count=1)
    writer, reviewer = writer or Writer(), Reviewer()
    state = engine.Workflow(output, row["run_id"], root, generator=writer, judge=reviewer).execute()
    return row, state, writer, reviewer


def test_empty_working_window_and_incomplete_draft_are_durable_without_a_run(tmp_path):
    shutil.copytree(Path(__file__).resolve().parents[1] / "configs", tmp_path / "configs")
    app = human_augmentation_application(tmp_path, tmp_path / "out")
    session_id = app.create_session(targets=["sft"], qa_director={"enabled": True})
    state = app.session(session_id)
    assert state["draft"]["seeds"] == [] and state["rounds"] == []
    state = app.save_draft(session_id, {"enabled": True, "seeds": [{"question": Q, "answer": ""}]},
                           expected_version=state["version"])
    reloaded = human_augmentation_application(tmp_path, tmp_path / "out").session(session_id)
    assert reloaded["draft"] == state["draft"]
    with pytest.raises(ValueError, match="invalid_human_augmentation_text"):
        app.generate_round(session_id, request_id="incomplete", expected_version=state["version"])
    assert not list((tmp_path / "out/workflows").glob("*/recipe.json"))


def test_actual_generation_feedback_and_same_question_revision_preserve_the_parent(tmp_path):
    app, sid, output = application(tmp_path)
    first, _, _ = generate(app, sid, output, tmp_path)
    original = engine.run_path(output, first["run_id"]) / "artifacts/sft.records.json"
    before = original.read_bytes()
    candidate = app.results(sid)["rows"][0]
    second, state, writer, reviewer = revise(app, sid, first, candidate, output, tmp_path)
    assert state["status"] == "completed", state.get("error")
    current = app.results(sid)["rows"][0]
    assert current["record"]["status"] == "eligible"
    assert current["question"] == candidate["question"]
    assert current["answer"].startswith("安全提醒：")
    assert original.read_bytes() == before
    context = current["lineage"]
    assert context["content_sha256"] == candidate["content_sha256"]
    assert context["messages"] == candidate["messages"]
    assert context["parent_run_id"] == first["run_id"] and context["depth"] == 1
    assert writer.calls[0]["candidates"][0]["human_design"]["seed"]["revision_context"] == context
    assert any(call.get("review_scope") == "complete" for call in reviewer.calls if "human_design" in call)
    assert first["run_id"] != second["run_id"] and len(app.list_sessions()) == 1


def test_feedback_must_actually_be_applied_before_a_revision_is_exported(tmp_path):
    app, sid, output = application(tmp_path)
    first, _, _ = generate(app, sid, output, tmp_path)
    second, state, _, _ = revise(app, sid, first, app.results(sid)["rows"][0], output, tmp_path,
                                writer=Writer(apply_feedback=False))
    assert state["status"] == "needs_attention", state.get("error")
    assert app.results(sid)["rows"][0]["record"]["status"] == "quarantined"
    assert not (engine.run_path(output, second["run_id"]) / "artifacts/sft.jsonl").read_text(encoding="utf-8").strip()


def test_revision_depth_bounds_are_per_lineage_and_cannot_overwrite_results(tmp_path):
    app, sid, output = application(tmp_path, max_revision_depth=1)
    first, _, _ = generate(app, sid, output, tmp_path)
    second, state, _, _ = revise(app, sid, first, app.results(sid)["rows"][0], output, tmp_path)
    assert state["status"] == "completed", state.get("error")
    selected = app.results(sid)["rows"][0]
    saved = app.save_feedback(sid, second["id"], "sft", selected["candidate_id"], instruction="进一步优化语气。",
                             expected_version=app.session(sid)["version"])
    with pytest.raises(ValueError, match="human_session_revision_limit"):
        app.revise_round(sid, request_id="too-deep", expected_version=saved["version"])
    assert len(app.session(sid)["rounds"]) == 2
    assert len(list((output / "workflows").glob("*/recipe.json"))) == 2


def test_revision_reuses_selected_document_evidence_instead_of_an_unrelated_chunk(tmp_path):
    source = tmp_path / "equipment.md"
    source.write_text(A + "\n", encoding="utf-8")
    app, sid, output = application(tmp_path, sources=[source])
    source.write_text("原路径已被编辑，新的内容不应进入会话。", encoding="utf-8")
    first, _, _ = generate(app, sid, output, tmp_path)
    parent = app.results(sid)["rows"][0]
    second, state, writer, reviewer = revise(app, sid, first, parent, output, tmp_path)
    assert state["status"] == "completed", state.get("error")
    recipe = engine.read_json(engine.run_path(output, second["run_id"]) / "recipe.json")
    assert len(recipe["sources"]) == 2 and recipe["sources"][-1]["kind"] == "human_design"
    assert writer.calls[0]["candidates"][0]["teacher_evidence"] == A
    check = next(call for call in reviewer.calls if "human_design" in call)
    assert check["source_kind"] == "document"
    assert parent["record"]["source_id"] == app.results(sid)["rows"][0]["record"]["source_id"]


def test_multiturn_revision_receives_full_selected_dialogue_and_final_feedback_check(tmp_path):
    app, sid, output = application(tmp_path, targets=["multiturn"])
    first, _, _ = generate(app, sid, output, tmp_path)
    candidate = app.results(sid, target="multiturn")["rows"][0]
    assert len(candidate["messages"]) == 4
    second, state, writer, reviewer = revise(app, sid, first, candidate, output, tmp_path,
        instruction="第二轮回答也改成安全提醒的自然口吻。")
    assert state["status"] == "completed", state.get("error")
    current = app.results(sid, target="multiturn")["rows"][0]
    assert current["lineage"]["messages"] == candidate["messages"]
    assert len(current["messages"]) == 4 and current["messages"][3]["content"].startswith("安全提醒：")
    final = [call for call in reviewer.calls if call.get("review_scope") == "complete" and "human_design" in call]
    assert len(final) == 1 and final[0]["learner_messages"] == current["messages"]


def test_ending_multiturn_early_cannot_bypass_feedback_on_a_later_turn(tmp_path):
    app, sid, output = application(tmp_path, targets=["multiturn"])
    first, _, _ = generate(app, sid, output, tmp_path, writer=Writer(target_turns=3))
    candidate = app.results(sid, target="multiturn")["rows"][0]
    _, state, _, _ = revise(app, sid, first, candidate, output, tmp_path,
        instruction="第三轮回答也改成安全提醒的自然口吻。", writer=Writer(finish_early=True))
    assert state["status"] == "needs_attention", state.get("error")
    current = app.results(sid, target="multiturn")["rows"][0]
    assert current["record"]["status"] == "quarantined"
    assert current["record"]["reason"] == "human_revision_feedback_not_applied"


def test_from_automatic_mixed_cpt_run_has_a_real_imported_round_and_qa_only_reflow(tmp_path):
    source = tmp_path / "equipment.md"
    source.write_text(A, encoding="utf-8")
    app, sid, output = application(tmp_path, sources=[source])
    run_id = engine.create_run(output, sources=[source], targets=["cpt", "sft"], sample_count=1,
        settings_root=tmp_path, qa_director={"enabled": True, "planning_mode": "adaptive",
                                           "human_augmentation": design(), "batch_size": 1})
    state = engine.Workflow(output, run_id, tmp_path, generator=Writer(), judge=Reviewer()).execute()
    assert state["status"] == "completed", state.get("error")
    imported = app.create_session(from_run_id=run_id)
    session = app.session(imported)
    assert session["blueprint"]["targets"] == ["sft"] and session["rounds"][0]["kind"] == "imported"
    candidate = app.results(imported)["rows"][0]
    _, state, _, _ = revise(app, imported, session["rounds"][0], candidate, output, tmp_path)
    assert state["status"] == "completed", state.get("error")


def test_forged_revision_context_without_session_round_authorization_never_calls_models(tmp_path):
    app, sid, output = application(tmp_path)
    first, _, _ = generate(app, sid, output, tmp_path)
    _, _, _, _ = revise(app, sid, first, app.results(sid)["rows"][0], output, tmp_path)
    recipe = engine.read_json(engine.run_path(output, app.session(sid)["current_run_id"]) / "recipe.json")
    seed = deepcopy(recipe["qa_director"]["human_augmentation"]["seeds"][0])
    seed.pop("id")
    forged_human = validate_human_augmentation({"enabled": True, "seeds": [seed]})
    forged = engine.create_run(output, targets=["sft"], settings_root=tmp_path,
        qa_director={"enabled": True, "planning_mode": "adaptive", "human_augmentation": forged_human})
    writer, reviewer = Writer(), Reviewer()
    state = engine.Workflow(output, forged, tmp_path, generator=writer, judge=reviewer).execute()
    assert state["status"] == "failed"
    assert "human_session_revision_not_authorized" in state["error"]
    assert writer.calls == reviewer.calls == []
