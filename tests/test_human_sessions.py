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
        if "candidate" in data and "source_evidence" in data:
            if "review_feedback" not in data:
                return json.dumps(review_verdict(data))
            payload = deepcopy(data["candidate"])
            instruction = data.get("instruction", "")
            requested = 3 if "第三轮" in instruction else 2 if "第二轮" in instruction else 1
            if self.apply_feedback:
                answers = [m for m in payload.get("messages", []) if m["role"] == "assistant"]
                if self.finish_early and requested > 1:
                    payload["messages"] = payload["messages"][:2]
                elif len(answers) >= requested:
                    answers[requested - 1]["content"] = "安全提醒：" + answers[requested - 1]["content"]
                elif "answer" in payload:
                    payload["answer"] = "安全提醒：" + payload["answer"]
            return json.dumps({"record": payload, "uncertain": False,
                               "quotes": [data["source_evidence"]["teacher_evidence"]]}, ensure_ascii=False)
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


def review_verdict(data):
    candidate = data.get("candidate", {})
    answers = [m.get("content", "") for m in candidate.get("messages", []) if m.get("role") == "assistant"]
    if "answer" in candidate:
        answers.append(candidate["answer"])
    instruction = data.get("instruction", "")
    requested = 3 if "第三轮" in instruction else 2 if "第二轮" in instruction else 1
    keep = (not instruction or len(answers) >= requested and "安全提醒：" in answers[requested - 1])
    return {"keep": keep, "grounded": True, "reasoning_valid": True, "correctness": 5 if keep else 2,
        "scores": {key: 5 if keep else 2 for key in ("correctness", "reasoning", "grounding", "instruction", "safety")},
        "reason": "来源和回答通过模型核对。" if keep else "选中结果的人工反馈未落实。"}


class Reviewer:
    model = "offline-session-reviewer"

    def __init__(self):
        self.calls, self.usage = [], {"calls": 0}

    def chat(self, messages, **kwargs):
        data, prompt = json.loads(messages[1]["content"]), messages[0]["content"]
        self.calls.append(deepcopy(data))
        self.usage["calls"] += 1
        if "candidate" in data and "source_evidence" in data:
            return json.dumps(review_verdict(data), ensure_ascii=False)
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


def application(tmp_path, *, sources=(), targets=("sft",), max_revision_depth=3, production=None, package_review=None, node_models=None):
    shutil.copytree(Path(__file__).resolve().parents[1] / "configs", tmp_path / "configs", dirs_exist_ok=True)
    app = human_augmentation_application(tmp_path, tmp_path / "out")
    session_id = app.create_session(name="安全设备人工增强", targets=list(targets), sources=list(sources),
        qa_director={"enabled": True, "planning_mode": "adaptive", "batch_size": 1},
        initial_draft=design(), node_models=node_models or {}, sample_count=1, conversation_turns=3,
        max_revision_depth=max_revision_depth, production=production, package_review=package_review)
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
    assert writer.calls[0]["candidate"] == {"messages": candidate["record"]["messages"]}
    child = engine.read_json(engine.run_path(output, second["run_id"]) / "recipe.json")
    assert child["repair_inputs"][0]["revision_context"] == context
    assert child["qa_director"].get("human_augmentation", {"enabled": False}) == {"enabled": False}
    assert state["stages"]["sft"]["status"] == "skipped"
    assert state["stages"]["review"]["status"] == "completed"
    assert all("candidate" in call and "source_evidence" in call for call in writer.calls)
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
    assert len(recipe["sources"]) == 1 and recipe["sources"][0].get("kind") != "human_design"
    assert writer.calls[0]["source_evidence"]["teacher_evidence"] == A
    assert writer.calls[-1]["source_evidence"]["source_kind"] == "document"
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
    final = [call for call in writer.calls if "candidate" in call and "review_feedback" not in call]
    assert len(final) == 1 and final[0]["candidate"]["messages"] == current["messages"]


def test_ending_multiturn_early_cannot_bypass_feedback_on_a_later_turn(tmp_path):
    app, sid, output = application(tmp_path, targets=["multiturn"])
    first, _, _ = generate(app, sid, output, tmp_path, writer=Writer(target_turns=3))
    candidate = app.results(sid, target="multiturn")["rows"][0]
    _, state, _, _ = revise(app, sid, first, candidate, output, tmp_path,
        instruction="第三轮回答也改成安全提醒的自然口吻。", writer=Writer(finish_early=True))
    assert state["status"] == "needs_attention", state.get("error")
    current = app.results(sid, target="multiturn")["rows"][0]
    assert current["record"]["status"] == "quarantined"
    assert current["record"]["reason"] in {"review_repair_exhausted", "review_repair_invalid_schema"}


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
    assert session["blueprint"]["targets"] == ["cpt", "sft"] and session["rounds"][0]["kind"] == "imported"
    assert session["review_only"]
    candidate = app.results(imported)["rows"][0]
    _, state, _, _ = revise(app, imported, session["rounds"][0], candidate, output, tmp_path)
    assert state["status"] == "completed", state.get("error")


def test_forged_revision_context_without_session_round_authorization_never_calls_models(tmp_path):
    app, sid, output = application(tmp_path)
    first, _, _ = generate(app, sid, output, tmp_path)
    _, _, _, _ = revise(app, sid, first, app.results(sid)["rows"][0], output, tmp_path)
    recipe = engine.read_json(engine.run_path(output, app.session(sid)["current_run_id"]) / "recipe.json")
    forged = engine.create_run(output, targets=["sft"], settings_root=tmp_path,
        review_repair=recipe["review_repair"], repair_inputs=recipe["repair_inputs"])
    writer, reviewer = Writer(), Reviewer()
    state = engine.Workflow(output, forged, tmp_path, generator=writer, judge=reviewer).execute()
    assert state["status"] == "failed"
    assert "human_session_revision_not_authorized" in state["error"]
    assert writer.calls == reviewer.calls == []


def test_real_feedback_revision_returns_to_jev_and_keeps_rejected_and_passed_versions(tmp_path, monkeypatch):
    class FinalReviewer(Reviewer):
        def __init__(self, reject):
            super().__init__()
            self.reject = reject

        def chat(self, messages, **kwargs):
            data = json.loads(messages[1]["content"])
            response = super().chat(messages, **kwargs)
            if self.reject and data.get("target") == "sft" and "candidate" in data:
                value = json.loads(response)
                value.update(keep=False, correctness=2, reason="请在回答中明确检查开始前断电。")
                value["scores"] = {key: 2 for key in value["scores"]}
                return json.dumps(value, ensure_ascii=False)
            return response

    app, sid, output = application(tmp_path, package_review={"enabled": True, "node": "jev", "mode": "all"})
    first, _, _ = generate(app, sid, output, tmp_path)
    original = engine.run_path(output, first["run_id"]) / "artifacts/sft.records.json"
    original_bytes = original.read_bytes()
    parent = app.results(sid)["rows"][0]
    saved = app.save_feedback(sid, first["id"], "sft", parent["candidate_id"],
        instruction="使用自然的安全提醒口吻。", expected_version=app.session(sid)["version"])
    waiting = app.branch_projection(sid, result=app.results(sid)["rows"][0])
    assert waiting["phase"] == "editing" and waiting["next_action"] == "submit_revision"
    second = app.revise_round(sid, request_id="reject-then-correct", expected_version=saved["version"])
    state = engine.Workflow(output, second["run_id"], tmp_path, generator=Writer(), judge=FinalReviewer(True)).execute()
    assert state["status"] == "needs_attention", state.get("error")
    selected = app.results(sid)["rows"][0]
    projection = app.branch_projection(sid, result=selected)
    assert projection["phase"] == "needs_revision" and projection["counts"]["rejected"] == 1
    assert projection["generation_node"] == "sft" and projection["review_node"] == "review"
    assert projection["generation_nodes"] == []
    assert projection["stages"]["self_check"]["status"] == "completed"
    assert projection["stages"]["scoring"]["rejected"] == 1
    assert projection["reviewer_feedback"] == "请在回答中明确检查开始前断电。"
    assert len(app.session(sid)["rounds"]) == 2 and original.read_bytes() == original_bytes
    rejected_bytes = (engine.run_path(output, second["run_id"]) / "artifacts/sft.records.json").read_bytes()
    saved = app.save_feedback(sid, second["id"], "sft", selected["candidate_id"],
        instruction=projection["reviewer_feedback"], expected_version=app.session(sid)["version"])
    third = app.revise_round(sid, request_id="correct-after-score", expected_version=saved["version"])
    state = engine.Workflow(output, third["run_id"], tmp_path, generator=Writer(), judge=FinalReviewer(False)).execute()
    assert state["status"] == "completed", state.get("error")
    selected = app.results(sid)["rows"][0]
    # A live display refresh reuses this record and never scans/hashes artifacts.
    monkeypatch.setattr(app._driver, "_records", lambda *args, **kwargs: pytest.fail("unexpected record scan"))
    monkeypatch.setattr("lib.infrastructure.human_sessions.verify_artifacts", lambda *args: pytest.fail("unexpected full hash"))
    projection = app.branch_projection(sid, result=selected)
    assert projection["phase"] == "approved" and projection["counts"]["accepted"] == 1
    assert projection["selected_result"]["optional_score_status"] == "accepted"
    assert projection["next_action"] == "feedback_optional" and projection["actionable"]
    assert projection["auto_rebuild"] is False and projection["merge_versions"] is False
    assert len(app.session(sid)["rounds"]) == 3
    assert original.read_bytes() == original_bytes
    assert (engine.run_path(output, second["run_id"]) / "artifacts/sft.records.json").read_bytes() == rejected_bytes


def test_projection_exposes_blocked_feedback_without_creating_a_loop(tmp_path):
    app, sid, output = application(tmp_path, max_revision_depth=1)
    first, _, _ = generate(app, sid, output, tmp_path)
    second, _, _, _ = revise(app, sid, first, app.results(sid)["rows"][0], output, tmp_path)
    selected = app.results(sid)["rows"][0]
    saved = app.save_feedback(sid, second["id"], "sft", selected["candidate_id"], instruction="继续改善语言。",
        expected_version=app.session(sid)["version"])
    projection = app.branch_projection(sid, result=app.results(sid)["rows"][0])
    assert projection["phase"] == "revision_limit" and projection["feedback"]["blocked"] == 1
    assert projection["routes"]["rebuild_waiting"]["count"] == 0 and not projection["actionable"]
    assert app.session(sid)["version"] == saved["version"] and len(app.session(sid)["rounds"]) == 2


@pytest.mark.parametrize("score,decision,expected", [(95, "approve", "eligible"), (40, "approve", "quarantined"), (95, "reject", "quarantined")])
def test_manual_service_scores_and_edits_one_candidate_without_any_model_request(tmp_path, score, decision, expected):
    app, sid, output = application(tmp_path)
    first, _, _ = generate(app, sid, output, tmp_path)
    candidate = app.results(sid)["rows"][0]
    original_file = engine.run_path(output, first["run_id"]) / "artifacts/sft.records.json"
    original = original_file.read_bytes()
    payload = {"messages": deepcopy(candidate["messages"])}
    payload["messages"][-1]["content"] = "安全提醒：" + A
    request = {"request_id": "manual-score-edit", "score": score, "decision": decision,
               "instruction": "已按来源检查安全前提。", "corrected_record": payload,
               "expected_version": app.session(sid)["version"]}
    row = app.submit_manual_review(sid, first["id"], "sft", candidate["candidate_id"], **request)
    repeated = app.submit_manual_review(sid, first["id"], "sft", candidate["candidate_id"], **request)
    assert repeated["run_id"] == row["run_id"]
    class NeverCalled:
        usage = {}
        def chat(self, *args, **kwargs):
            pytest.fail("Manual scoring and correction must not call a model")
    state = engine.Workflow(output, row["run_id"], tmp_path, generator=NeverCalled(), judge=NeverCalled()).execute()
    assert state["status"] in {"completed", "needs_attention"}, state.get("error")
    result = app.results(sid)["rows"][0]
    assert result["record"]["status"] == expected
    assert result["record"]["messages"] == payload["messages"]
    assert result["record"]["review_repair"]["score"] == score / 100
    assert state["stages"]["sft"]["status"] == "skipped" and not state["usage"]
    assert len(app.session(sid)["rounds"]) == 2
    assert original_file.read_bytes() == original
    projection = app.branch_projection(sid, result=result)
    assert projection["review_node"] == "review" and projection["generation_nodes"] == []
    assert projection["selected_result"]["checks"][0]["source"] == "human_review"


def test_manual_cpt_service_imports_and_reviews_corpus_without_a_qa_design(tmp_path, monkeypatch):
    shutil.copytree(Path(__file__).resolve().parents[1] / "configs", tmp_path / "configs")
    source = tmp_path / "manual.txt"
    source.write_text(A, encoding="utf-8")
    output = tmp_path / "out"
    run_id = engine.create_run(output, sources=[source], targets=["cpt"], settings_root=tmp_path,
                               review_repair={"mode": "human"})
    monkeypatch.setattr(engine.Workflow, "cpt", lambda self, unit: [{**unit, "status": "eligible", "source_context": unit}])
    class NeverCalled:
        usage = {}
        def chat(self, *args, **kwargs):
            pytest.fail("Human corpus review must not call a model")
    initial = engine.Workflow(output, run_id, tmp_path, generator=NeverCalled()).execute()
    assert initial["status"] == "needs_attention", initial.get("error")
    app = human_augmentation_application(tmp_path, output)
    sid = app.create_session(from_run_id=run_id)
    session = app.session(sid)
    assert session["review_only"] and session["blueprint"]["targets"] == ["cpt"]
    candidate = app.results(sid, target="cpt")["rows"][0]
    projection = app.branch_projection(sid, target="cpt", result=candidate)
    assert projection["selected_result"]["route"] == "waiting_manual_review"
    assert projection["actionable"]
    row = app.submit_manual_review(sid, session["rounds"][0]["id"], "cpt", candidate["candidate_id"],
        request_id="corpus-review", score=90, decision="approve", corrected_record={"text": A},
        expected_version=session["version"])
    state = engine.Workflow(output, row["run_id"], tmp_path, generator=NeverCalled()).execute()
    assert state["status"] == "completed", state.get("error")
    assert app.results(sid, target="cpt")["rows"][0]["record"]["status"] == "eligible"
    assert state["stages"]["cpt"]["status"] == "skipped"


def test_manual_review_does_not_depend_on_unused_model_endpoint_configuration(tmp_path, monkeypatch):
    binding = {"sft": {"generation": {"backend": "offline-service", "model": "offline-model"}}}
    shutil.copytree(Path(__file__).resolve().parents[1] / "configs", tmp_path / "configs")
    (tmp_path / "configs/backends.local.yaml").write_text(json.dumps({"budget": {"max_total_usd": 0, "hard_stop": False}, "backends": {
        "offline-service": {"base_url": "http://127.0.0.1:9/v1", "models": ["offline-model"]}}}), encoding="utf-8")
    output = tmp_path / "out"
    app = human_augmentation_application(tmp_path, output)
    sid = app.create_session(targets=["sft"], node_models=binding, initial_draft=design(), sample_count=1,
        qa_director={"enabled": True, "planning_mode": "adaptive", "batch_size": 1})
    first, _, _ = generate(app, sid, output, tmp_path)
    candidate = app.results(sid)["rows"][0]
    monkeypatch.setattr("lib.infrastructure.human_sessions.snapshot_backend_endpoint",
        lambda *args: pytest.fail("Manual correction must not inspect unused model endpoint pins"))
    child = app.submit_manual_review(sid, first["id"], "sft", candidate["candidate_id"],
        request_id="manual-with-stale-model", score=95, decision="approve",
        corrected_record={"messages": candidate["messages"]}, expected_version=app.session(sid)["version"])
    recipe = engine.read_json(engine.run_path(output, child["run_id"]) / "recipe.json")
    assert recipe["node_models"] == {} and recipe.get("endpoint_pins", {}) == {}
    class NeverCalled:
        usage = {}
        def chat(self, *args, **kwargs):
            pytest.fail("Manual review must not call a model")
    state = engine.Workflow(output, child["run_id"], tmp_path, generator=NeverCalled(), judge=NeverCalled()).execute()
    assert state["status"] == "completed", state.get("error")
