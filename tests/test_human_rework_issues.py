"""Saved problem collections and bounded authentic review-only batches, offline."""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import json
from pathlib import Path
import sqlite3

import pytest

from lib.bootstrap.workflows import human_augmentation_application
from lib.domain.human_session import validate_issue_selection
from lib.infrastructure import training_workflow as engine
from lib.infrastructure.human_issue_store import HumanIssueStore
from test_human_sessions import application, generate, Writer, Reviewer


@pytest.fixture(scope="session", autouse=True)
def mock_llm_server():
    # All generator/reviewer instances are explicit offline fakes.
    yield None


def selection(round_row, candidate, **values):
    return {"round_id": round_row["id"], "target": candidate["target"],
            "candidate_id": candidate["candidate_id"], **values}


def configured(tmp_path, *, targets=("sft",), jev=False):
    models = {"review": {"generation": {"backend": "ollama", "model": "offline-writer"}}}
    if jev:
        models["review"]["jev"] = {"backend": "ollama", "model": "offline-reviewer"}
    return application(tmp_path, targets=targets, node_models=models, package_review={"enabled": jev})


def test_saved_issues_survive_reload_and_do_not_need_any_model_configuration(tmp_path, monkeypatch):
    app, sid, output = application(tmp_path)
    first, _, _ = generate(app, sid, output, tmp_path)
    candidate = app.results(sid)["rows"][0]
    before = (engine.run_path(output, first["run_id"]) / "artifacts/sft.records.json").read_bytes()
    import lib.infrastructure.human_sessions as sessions
    monkeypatch.setattr(sessions, "snapshot_backend_endpoint", lambda *args: pytest.fail("saved issue inspected a model"))
    saved = app.save_issues(sid, [selection(first, candidate)], expected_version=app.session(sid)["version"])
    assert saved["saved_count"] == 1
    reloaded = human_augmentation_application(tmp_path, output)
    page = reloaded.issues(sid)
    assert page["total"] == 1 and page["rows"][0]["issue_id"] == saved["issue_ids"][0]
    assert page["rows"][0]["status"] == "pending" and page["rows"][0]["instruction"] == ""
    assert "record" not in page["rows"][0] and "snapshot" not in page["rows"][0]
    assert app.session(sid)["feedback"] == [] and len(app.session(sid)["rounds"]) == 1
    assert (engine.run_path(output, first["run_id"]) / "artifacts/sft.records.json").read_bytes() == before
    with pytest.raises(ValueError, match="human_session_review_model_required"):
        app.revise_issues(sid, request_id="missing-model", expected_version=saved["version"], instruction="请改正错误。")


def test_rework_batch_is_only_review_preserves_original_and_tracks_true_success(tmp_path):
    app, sid, output = configured(tmp_path)
    first, _, _ = generate(app, sid, output, tmp_path)
    candidate = app.results(sid)["rows"][0]
    original = engine.run_path(output, first["run_id"]) / "artifacts/sft.records.json"
    before = original.read_bytes()
    saved = app.save_issues(sid, [selection(first, candidate, instruction="使用安全提醒的口吻。")],
                            expected_version=app.session(sid)["version"], request_id="save-issue")
    row = app.revise_issues(sid, request_id="batch-issue", expected_version=saved["version"],
                            selected_issue_ids=saved["issue_ids"], instruction="保留原问题与来源事实。")
    page = app.issues(sid, status="running")
    assert page["total"] == 1
    recipe = engine.read_json(engine.run_path(output, row["run_id"]) / "recipe.json")
    assert set(recipe["node_models"]) == {"review"} and set(recipe["node_models"]["review"]) == {"generation"}
    assert recipe["node_models"]["review"]["generation"]["backend"] == "ollama"
    assert recipe["node_models"]["review"]["generation"]["model"] == "offline-writer"
    assert recipe["repair_inputs"][0]["record"] == candidate["record"]
    assert recipe["repair_inputs"][0]["instruction"] == "使用安全提醒的口吻。\n保留原问题与来源事实。"
    writer = Writer()
    state = engine.Workflow(output, row["run_id"], tmp_path, generator=writer, judge=Reviewer()).execute()
    assert state["status"] == "completed", state.get("error")
    assert state["stages"]["sft"]["status"] == "skipped" and state["stages"]["review"]["status"] == "completed"
    assert all("candidate" in call for call in writer.calls if "review_feedback" in call)
    review_calls = [call for call in writer.calls if call.get("claim_review_protocol_version") == 4]
    assert [call["phase"] for call in review_calls] == ["claim_challenge", "claim_adjudication"]
    assert all(call["review_requirements"]["revision_instruction"] == recipe["repair_inputs"][0]["instruction"]
               for call in review_calls)
    page = app.issues(sid, status="completed")
    assert page["total"] == 1 and page["counts"]["pending"] == 0
    assert page["rows"][0]["result_run_id"] == row["run_id"]
    assert page["rows"][0]["outcome"]["review_status"] == "accepted"
    assert original.read_bytes() == before


def test_rejected_batch_stays_findable_and_is_never_marked_completed(tmp_path):
    app, sid, output = configured(tmp_path)
    first, _, _ = generate(app, sid, output, tmp_path)
    saved = app.save_issues(sid, [selection(first, app.results(sid)["rows"][0])], instruction="需要安全提醒。",
                            expected_version=app.session(sid)["version"])
    child = app.revise_issues(sid, request_id="bad-batch", expected_version=saved["version"])
    state = engine.Workflow(output, child["run_id"], tmp_path,
        generator=Writer(apply_feedback=False), judge=Reviewer()).execute()
    assert state["status"] == "needs_attention"
    page = app.issues(sid, status="needs_revision")
    assert page["total"] == 1 and page["counts"]["completed"] == 0
    assert page["rows"][0]["outcome"]["review_status"] == "rejected"
    again = app.revise_issues(sid, request_id="retry-batch", expected_version=page["version"])
    assert again["id"] != child["id"] and again["issue_ids"] == saved["issue_ids"]


def test_atomic_batch_validation_and_save_idempotency(tmp_path):
    app, sid, output = application(tmp_path)
    first, _, _ = generate(app, sid, output, tmp_path)
    candidate = app.results(sid)["rows"][0]
    version = app.session(sid)["version"]
    valid = selection(first, candidate)
    with pytest.raises(ValueError, match="human_session_candidate_not_found"):
        app.save_issues(sid, [valid, {**valid, "candidate_id": "not-in-sealed-result"}],
                        instruction="请保留安全前提。", expected_version=version)
    assert app.issues(sid)["total"] == 0 and app.session(sid)["version"] == version
    saved = app.save_issues(sid, [valid], instruction="请保留安全前提。", expected_version=version, request_id="stable-save")
    replay = app.save_issues(sid, [valid], instruction="请保留安全前提。", expected_version=version, request_id="stable-save")
    assert saved == replay and app.issues(sid)["total"] == 1
    with pytest.raises(ValueError, match="human_session_request_conflict"):
        app.save_issues(sid, [valid], instruction="改成另一条要求。", expected_version=version, request_id="stable-save")


def test_version_conflict_does_not_add_issue_and_rework_request_replay_is_exact(tmp_path):
    app, sid, output = configured(tmp_path)
    first, _, _ = generate(app, sid, output, tmp_path)
    candidate = app.results(sid)["rows"][0]
    version = app.session(sid)["version"]
    saved = app.save_issues(sid, [selection(first, candidate)], instruction="需要安全提醒。", expected_version=version)
    with pytest.raises(ValueError, match="human_session_version_conflict"):
        app.save_issues(sid, [selection(first, candidate)], instruction="另一条要求。", expected_version=version)
    child = app.revise_issues(sid, request_id="stable-child", expected_version=saved["version"], instruction="保留来源。")
    replay = app.revise_issues(sid, request_id="stable-child", expected_version=saved["version"], instruction="保留来源。")
    assert child["run_id"] == replay["run_id"] and len(app.session(sid)["rounds"]) == 2
    with pytest.raises(ValueError, match="human_session_request_conflict"):
        app.revise_issues(sid, request_id="stable-child", expected_version=saved["version"], instruction="修改共同要求。")


def test_same_result_latest_unsubmitted_issue_supersedes_without_losing_snapshot(tmp_path):
    app, sid, output = application(tmp_path)
    first, _, _ = generate(app, sid, output, tmp_path)
    candidate = app.results(sid)["rows"][0]
    saved = app.save_issues(sid, [selection(first, candidate)], instruction="第一版问题记录。", expected_version=app.session(sid)["version"])
    updated = app.save_issues(sid, [selection(first, candidate)], instruction="第二版问题记录。", expected_version=saved["version"])
    page = app.issues(sid)
    assert page["total"] == 1 and page["rows"][0]["instruction"] == "第二版问题记录。"
    db = sqlite3.connect(output / "human-sessions" / sid / "review-issues.sqlite3")
    assert db.execute("SELECT COUNT(*) FROM issues").fetchone()[0] == 2
    assert db.execute("SELECT superseded_by FROM issues WHERE id=?", (saved["issue_ids"][0],)).fetchone()[0] == updated["issue_ids"][0]
    db.close()


def test_review_session_reuses_same_imported_work_even_across_restarts_and_threads(tmp_path):
    app, sid, output = application(tmp_path)
    first, _, _ = generate(app, sid, output, tmp_path)
    with ThreadPoolExecutor(max_workers=2) as pool:
        ids = list(pool.map(lambda _: app.review_session(first["run_id"]), range(2)))
    assert ids[0] == ids[1]
    recreated = human_augmentation_application(tmp_path, output)
    assert recreated.review_session(first["run_id"]) == ids[0]
    assert app.session(ids[0])["review_only"]


def test_saved_full_revision_payload_and_question_answer_enter_only_repair_input(tmp_path):
    app, sid, output = configured(tmp_path)
    first, _, _ = generate(app, sid, output, tmp_path)
    candidate = app.results(sid)["rows"][0]
    messages = deepcopy(candidate["record"]["messages"])
    messages[-1]["content"] = "安全提醒：" + messages[-1]["content"]
    saved = app.save_issues(sid, [selection(first, candidate, corrected_record={"messages": messages})],
                            expected_version=app.session(sid)["version"])
    child = app.revise_issues(sid, request_id="payload-child", expected_version=saved["version"])
    recipe = engine.read_json(engine.run_path(output, child["run_id"]) / "recipe.json")
    item = recipe["repair_inputs"][0]
    assert item["corrected_record"] == {"messages": messages} and item["record"] == candidate["record"]
    assert item.get("approved") is None and recipe["review_repair"]["mode"] == "auto"


def test_pagination_and_next_batch_are_indexed_and_bounded_without_reading_payload(tmp_path):
    store = HumanIssueStore(tmp_path)
    with store.connection() as db:
        db.executemany("INSERT INTO issues(id,round_id,run_id,target,candidate_id,content_sha256,instruction,"
            "question_preview,answer_preview,snapshot,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
            [(f"{index:032x}", "a" * 32, "b" * 32, "sft", str(index), "c" * 64, "改进答案。", "Q", "A", "BROKEN PAYLOAD", "2026-10-11")
             for index in range(501)])
    page = store.page(offset=400, limit=50)
    assert page["total"] == 501 and len(page["rows"]) == 50 and page["has_more"]
    assert len(store.next_batch()) == 200 and store.next_batch()[0] == f"{0:032x}"
    assert "snapshot" not in page["rows"][0]


@pytest.mark.parametrize("change", [{"candidate_id": ""}, {"round_id": "../"}, {"target": "unknown"},
                                  {"corrected_record": {"status": "eligible"}}, {"instruction": "x" * 6001}])
def test_bad_problem_selection_cannot_smuggle_status_or_escape_filesystem(change):
    valid = {"round_id": "a" * 32, "target": "sft", "candidate_id": "candidate"}
    with pytest.raises(ValueError):
        validate_issue_selection([{**valid, **change}])


def test_problem_selection_retains_individual_and_common_instructions_and_edits():
    valid = {"round_id": "a" * 32, "target": "sft", "candidate_id": "candidate", "instruction": "核对数字。", "answer": "修正后的回答。"}
    normalized = validate_issue_selection([valid], instruction="保留上下文。")
    assert normalized[0]["instruction"] == "核对数字。\n保留上下文。"
    assert normalized[0]["answer"] == valid["answer"]


def test_one_rework_batch_can_mix_targets_and_saved_parent_rounds(tmp_path):
    app, sid, output = configured(tmp_path, targets=("sft", "multiturn"))
    first, _, _ = generate(app, sid, output, tmp_path)
    a = app.results(sid, round_id=first["id"], target="sft")["rows"][0]
    draft = deepcopy(app.session(sid)["draft"])
    draft["seeds"][0]["question"] = "准备开始检查设备时，先采取什么安全步骤？"
    app.save_draft(sid, draft, expected_version=app.session(sid)["version"])
    second, _, _ = generate(app, sid, output, tmp_path, request_id="second")
    b = app.results(sid, round_id=second["id"], target="multiturn")["rows"][0]
    saved = app.save_issues(sid, [selection(first, a, instruction="第一轮加安全提醒。"),
        selection(second, b, instruction="第二轮加安全提醒。")], expected_version=app.session(sid)["version"])
    child = app.revise_issues(sid, request_id="mixed-parents", expected_version=saved["version"])
    recipe = engine.read_json(engine.run_path(output, child["run_id"]) / "recipe.json")
    assert recipe["targets"] == ["sft", "multiturn"]
    assert {row["revision_context"]["parent_run_id"] for row in recipe["repair_inputs"]} == {first["run_id"], second["run_id"]}
    state = engine.Workflow(output, child["run_id"], tmp_path, generator=Writer(), judge=Reviewer()).execute()
    assert state["status"] == "completed", state.get("error")
    page = app.issues(sid, status="completed")
    assert page["total"] == 2 and {row["target"] for row in page["rows"]} == {"sft", "multiturn"}


def test_marking_sealed_old_results_is_allowed_while_another_round_is_pending(tmp_path):
    app, sid, output = configured(tmp_path)
    first, _, _ = generate(app, sid, output, tmp_path)
    candidate = app.results(sid, round_id=first["id"])["rows"][0]
    app.generate_round(sid, request_id="another-job", expected_version=app.session(sid)["version"])
    saved = app.save_issues(sid, [selection(first, candidate)], instruction="改正这个结果。", expected_version=app.session(sid)["version"])
    assert app.issues(sid)["total"] == 1
    with pytest.raises(ValueError, match="human_session_round_pending"):
        app.revise_issues(sid, request_id="wait-for-other-job", expected_version=saved["version"])


def test_reimporting_unchanged_completed_problem_reuses_the_finished_issue(tmp_path):
    app, sid, output = configured(tmp_path)
    first, _, _ = generate(app, sid, output, tmp_path)
    selected = selection(first, app.results(sid)["rows"][0])
    saved = app.save_issues(sid, [selected], instruction="加安全提醒。", expected_version=app.session(sid)["version"])
    child = app.revise_issues(sid, request_id="completed-problem", expected_version=saved["version"])
    engine.Workflow(output, child["run_id"], tmp_path, generator=Writer(), judge=Reviewer()).execute()
    assert app.issues(sid, status="completed")["total"] == 1
    imported = app.save_issues(sid, [selected], instruction="加安全提醒。", expected_version=app.session(sid)["version"], request_id="different-import-batch")
    assert imported["issue_ids"] == saved["issue_ids"]
    assert app.issues(sid)["total"] == 0 and app.issues(sid, status="completed")["total"] == 1


def test_failed_child_preparation_leaves_the_saved_issue_pending(tmp_path, monkeypatch):
    app, sid, output = configured(tmp_path)
    first, _, _ = generate(app, sid, output, tmp_path)
    saved = app.save_issues(sid, [selection(first, app.results(sid)["rows"][0])], instruction="加安全提醒。", expected_version=app.session(sid)["version"])
    def fail_create(**kwargs):
        raise RuntimeError("disk unavailable")
    monkeypatch.setattr(app._driver.workflow, "create_run", fail_create)
    with pytest.raises(RuntimeError, match="disk unavailable"):
        app.revise_issues(sid, request_id="unprepared", expected_version=saved["version"])
    page = app.issues(sid)
    assert page["total"] == 1 and page["rows"][0]["applied_round_id"] is None
    assert app.session(sid)["rounds"][-1]["status"] == "preparation_failed"


def test_interruption_after_child_reservation_recovers_without_an_extra_run(tmp_path, monkeypatch):
    app, sid, output = configured(tmp_path)
    first, _, _ = generate(app, sid, output, tmp_path)
    saved = app.save_issues(sid, [selection(first, app.results(sid)["rows"][0])], instruction="加安全提醒。", expected_version=app.session(sid)["version"])
    real_assign = HumanIssueStore.assign
    def fail_assign(*args):
        raise RuntimeError("interrupted after durable child")
    monkeypatch.setattr(HumanIssueStore, "assign", fail_assign)
    with pytest.raises(RuntimeError, match="durable child"):
        app.revise_issues(sid, request_id="reserved", expected_version=saved["version"], instruction="保留事实。")
    monkeypatch.setattr(HumanIssueStore, "assign", real_assign)
    replay = app.revise_issues(sid, request_id="reserved", expected_version=saved["version"], instruction="保留事实。")
    assert len(app.session(sid)["rounds"]) == 2
    assert app.issues(sid, status="running")["rows"][0]["applied_round_id"] == replay["id"]


def test_jev_batch_has_two_explicit_review_roles_and_no_other_node_models(tmp_path):
    app, sid, output = configured(tmp_path, jev=True)
    first, _, _ = generate(app, sid, output, tmp_path)
    saved = app.save_issues(sid, [selection(first, app.results(sid)["rows"][0])], instruction="加安全提醒。", expected_version=app.session(sid)["version"])
    child = app.revise_issues(sid, request_id="two-model-roles", expected_version=saved["version"])
    recipe = engine.read_json(engine.run_path(output, child["run_id"]) / "recipe.json")
    assert set(recipe["node_models"]) == {"review"} and set(recipe["node_models"]["review"]) == {"generation", "jev"}
    assert recipe["node_models"]["review"]["jev"]["model"] == "offline-reviewer"


def test_revision_keeps_explicit_legacy_model_and_does_not_need_retired_other_nodes(tmp_path, monkeypatch):
    models = {"sft": {"generation": {"backend": "ollama", "model": "offline-writer"}},
              "director": {"generation": {"backend": "ollama", "model": "retired-director"}}}
    app, sid, output = application(tmp_path, node_models=models)
    first, _, _ = generate(app, sid, output, tmp_path)
    saved = app.save_issues(sid, [selection(first, app.results(sid)["rows"][0])], instruction="加安全提醒。", expected_version=app.session(sid)["version"])
    import lib.infrastructure.human_sessions as sessions
    real_snapshot = sessions.snapshot_backend_endpoint
    def snapshot(root, backend, model):
        assert model != "retired-director", "unused director service was inspected"
        return real_snapshot(root, backend, model)
    monkeypatch.setattr(sessions, "snapshot_backend_endpoint", snapshot)
    child = app.revise_issues(sid, request_id="migrate-legacy", expected_version=saved["version"])
    recipe = engine.read_json(engine.run_path(output, child["run_id"]) / "recipe.json")
    assert recipe["node_models"]["review"]["generation"]["model"] == "offline-writer"
    assert set(recipe["node_models"]) == {"review"}


def test_old_workspace_can_confirm_review_node_model_without_changing_the_parent(tmp_path):
    app, sid, output = application(tmp_path)
    first, _, _ = generate(app, sid, output, tmp_path)
    candidate = app.results(sid)["rows"][0]
    parent_path = engine.run_path(output, first["run_id"]) / "recipe.json"
    original_recipe = parent_path.read_bytes()
    saved = app.save_issues(sid, [selection(first, candidate)], instruction="安全提醒。", expected_version=app.session(sid)["version"])
    configured_session = app.configure_review_models(sid,
        {"generation": {"backend": "ollama", "model": "confirmed-review-model", "max_output_tokens": 32768}},
        expected_version=saved["version"])
    assert parent_path.read_bytes() == original_recipe
    assert configured_session["blueprint"]["node_models"]["review"]["generation"]["model"] == "confirmed-review-model"
    child = app.revise_issues(sid, request_id="configured-old-work", expected_version=configured_session["version"])
    recipe = engine.read_json(engine.run_path(output, child["run_id"]) / "recipe.json")
    assert recipe["node_models"]["review"]["generation"]["model"] == "confirmed-review-model"
    with pytest.raises(ValueError, match="human_session_round_pending"):
        app.configure_review_models(sid, {"generation": {"backend": "ollama", "model": "different-model"}},
                                    expected_version=app.session(sid)["version"])


def test_confirmed_model_configuration_recovers_the_blueprint_version_after_interruption(tmp_path, monkeypatch):
    app, sid, output = application(tmp_path)
    import lib.infrastructure.human_sessions as sessions
    original_atomic = sessions.atomic_json
    def interrupted(path, value):
        if Path(path).name == "session.json":
            raise RuntimeError("interrupted between blueprint and session")
        original_atomic(path, value)
    monkeypatch.setattr(sessions, "atomic_json", interrupted)
    with pytest.raises(RuntimeError, match="between blueprint"):
        app.configure_review_models(sid, {"generation": {"backend": "ollama", "model": "confirmed-model"}},
                                    expected_version=1)
    monkeypatch.setattr(sessions, "atomic_json", original_atomic)
    recovered = human_augmentation_application(tmp_path, output).session(sid)
    assert recovered["version"] == 2
    assert recovered["blueprint"]["node_models"]["review"]["generation"]["model"] == "confirmed-model"
    assert not (output / "human-sessions" / sid / ".review-model-update.json").exists()


def test_changed_issue_note_after_a_rejected_batch_supersedes_stale_pending_identity(tmp_path):
    app, sid, output = configured(tmp_path)
    first, _, _ = generate(app, sid, output, tmp_path)
    selected = selection(first, app.results(sid)["rows"][0])
    saved = app.save_issues(sid, [selected], instruction="加安全提醒。", expected_version=app.session(sid)["version"])
    child = app.revise_issues(sid, request_id="reject-first-note", expected_version=saved["version"])
    engine.Workflow(output, child["run_id"], tmp_path, generator=Writer(apply_feedback=False), judge=Reviewer()).execute()
    assert app.issues(sid, status="needs_revision")["total"] == 1
    latest = app.save_issues(sid, [selected], instruction="第二版具体安全提醒。", expected_version=app.session(sid)["version"])
    page = app.issues(sid, status="all")
    assert page["total"] == 1 and page["rows"][0]["issue_id"] == latest["issue_ids"][0]
    next_child = app.revise_issues(sid, request_id="next-current-note", expected_version=latest["version"])
    assert next_child["issue_ids"] == latest["issue_ids"]


def test_large_rework_round_details_are_outside_session_head_and_tampering_never_calls_model(tmp_path):
    app, sid, output = configured(tmp_path)
    first, _, _ = generate(app, sid, output, tmp_path)
    saved = app.save_issues(sid, [selection(first, app.results(sid)["rows"][0])], instruction="安全提醒。", expected_version=app.session(sid)["version"])
    child = app.revise_issues(sid, request_id="sidecar-auth", expected_version=saved["version"])
    folder = output / "human-sessions" / sid
    persisted = engine.read_json(folder / "session.json")["rounds"][-1]
    assert "parent_results" not in persisted and "issue_ids" not in persisted
    assert persisted["repair_inputs_sha256"] and persisted["issue_count"] == 1
    assert app.session(sid)["rounds"][-1]["parent_results"] == child["parent_results"]
    details = folder / persisted["details_ref"]["file"]
    value = engine.read_json(details)
    value["parent_results"][0]["content_sha256"] = "f" * 64
    from lib.io_utils import atomic_json
    atomic_json(details, value)
    with pytest.raises(ValueError, match="human_session_round_details_changed"):
        app.resume_round(sid, child["id"])
    writer = Writer()
    state = engine.Workflow(output, child["run_id"], tmp_path, generator=writer, judge=Reviewer()).execute()
    assert state["status"] == "failed" and "human_session_round_details_changed" in state["error"]
    assert writer.calls == []


def test_rework_can_continue_past_history_limit_and_inspect_an_older_round_exactly(tmp_path):
    app, sid, output = configured(tmp_path)
    first, _, _ = generate(app, sid, output, tmp_path)
    candidate = app.results(sid)["rows"][0]
    folder = output / "human-sessions" / sid
    state = engine.read_json(folder / "session.json")
    state["rounds"][0]["status"] = "completed"
    # Compact terminal summaries exercise the bound without running 199
    # duplicate generations or placing fake rows in a real product workspace.
    for index in range(1, 200):
        state["rounds"].append({"id": f"{index:032x}", "round_id": f"{index:032x}",
            "run_id": f"{index + 1000:032x}", "kind": "revision", "request_id": f"history-{index}",
            "status": "completed", "parent_results": [], "feedback_ids": []})
    from lib.io_utils import atomic_json
    atomic_json(folder / "session.json", state)
    saved = app.save_issues(sid, [selection(first, candidate)], instruction="安全提醒。", expected_version=state["version"])
    child = app.revise_issues(sid, request_id="past-two-hundred", expected_version=saved["version"])
    current = app.session(sid)
    assert current["round_total"] == 201 and len(current["rounds"]) == 200 and current["history_truncated"]
    assert app.results(sid, round_id=first["id"])["rows"][0]["candidate_id"] == candidate["candidate_id"]
    assert app.branch_projection(sid, round_id=first["id"], target="sft")["target"] == "sft"
    assert child["parent_results"][0]["round_id"] == first["id"]
    with pytest.raises(ValueError, match="human_session_round_limit"):
        app.generate_round(sid, request_id="generation-limit-kept", expected_version=current["version"])


def test_historical_review_workspace_can_be_created_and_collect_issues_after_model_disconnect(tmp_path, monkeypatch):
    app, sid, output = configured(tmp_path)
    first, _, _ = generate(app, sid, output, tmp_path)
    import lib.infrastructure.human_sessions as sessions
    monkeypatch.setattr(sessions, "snapshot_backend_endpoint", lambda *args: pytest.fail("historical issue creation inspected a disconnected model"))
    imported = app.review_session(first["run_id"])
    current = app.session(imported)
    candidate = app.results(imported)["rows"][0]
    saved = app.save_issues(imported, [selection(current["rounds"][0], candidate)], instruction="下次统一修正。", expected_version=current["version"])
    assert app.issues(imported)["total"] == 1 and saved["saved_count"] == 1
