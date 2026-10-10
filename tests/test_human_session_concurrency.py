"""Offline checks for durable human feedback loops across browser sessions."""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import multiprocessing
from pathlib import Path
from shutil import copyfile
import sys
import threading
import time
import types

from filelock import FileLock
import pytest

from lib.bootstrap.workflows import human_augmentation_application
from lib.infrastructure import training_workflow as engine
from lib.io_utils import atomic_json


@pytest.fixture(scope="session", autouse=True)
def mock_llm_server():
    # Preparing feedback rounds must never send a model request.
    yield None


@pytest.fixture
def standalone_spawn(monkeypatch):
    # AppTest leaves its temporary UI script installed as __main__. Windows
    # spawn would execute that UI again before unpickling our importable worker.
    # A fileless main keeps this test's children limited to the worker module.
    with monkeypatch.context() as isolated:
        isolated.setitem(sys.modules, "__main__", types.ModuleType("__main__"))
        yield multiprocessing.get_context("spawn")


def close_worker(worker):
    """Release only this test's process, including partially failed startups."""
    if worker.pid is None:
        worker.close()
        return
    if worker.is_alive():
        worker.terminate()
    worker.join(5)
    if worker.is_alive():
        worker.kill()
        worker.join(5)
    assert not worker.is_alive(), "Owned worker did not stop during test cleanup."
    worker.close()


def manual():
    return {"enabled": True, "seeds": [{"question": "检查设备前先做什么？", "answer": "先断电，再检查设备。"}],
            "question_requirements": "自然口语，保留操作前的条件。", "answer_requirements": "保留安全前提。"}


def prepared_app(root):
    settings = root / "configs"
    settings.mkdir(exist_ok=True)
    copyfile(Path(__file__).resolve().parents[1] / "configs" / "preferences.yaml",
             settings / "preferences.yaml")
    output = root / "out"
    app = human_augmentation_application(root, output)
    session_id = app.create_session(name="人工迭代测试", targets=["sft"], sources=[], brief="",
        sample_count=1, node_models={}, qa_director={"enabled": True, "planning_mode": "adaptive",
                                                  "human_augmentation": manual()})
    return app, session_id, output


def watch_creations(monkeypatch, *apps):
    lock, calls = threading.Lock(), []
    for app in apps:
        original = app._driver.workflow.create_run
        def create(*a, _original=original, **kw):
            with lock:
                calls.append(deepcopy(kw))
            time.sleep(.05)
            return _original(*a, **kw)
        monkeypatch.setattr(app._driver.workflow, "create_run", create)
    return calls


def child_state(output, run_id, status):
    path = engine.run_path(output, run_id) / "state.json"
    state = engine.read_json(path)
    state["status"] = status
    atomic_json(path, state)


def sealed_generation(root):
    app, session_id, output = prepared_app(root)
    row = app.generate_round(session_id, request_id="original-generation",
                             expected_version=app.session(session_id)["version"], sample_count=1)
    run = engine.run_path(output, row["run_id"])
    recipe = engine.read_json(run / "recipe.json")
    design = engine.human_design(recipe["qa_director"]["human_augmentation"])
    seed = design["seed"]
    record = {"id": "human-variant-1", "status": "eligible",
              "source_id": recipe["sources"][0]["sha256"], "evidence_level": "human_provided",
              "source_context": {"human_provided": True, "text": seed["answer"]},
              "messages": [{"role": "user", "content": seed["question"]},
                           {"role": "assistant", "content": seed["answer"]}],
              "qa_contract": {"human_design": design, "evidence_quotes": [seed["answer"]]}}
    artifacts = run / "artifacts"
    atomic_json(artifacts / "sft.records.json", [record])
    atomic_json(artifacts / "quality.json", {"targets": {"sft": {"total": 1}}})
    atomic_json(artifacts / "manifest.json", {"status": "complete", "sha256": {
        name: engine.file_hash(artifacts / name) for name in ("sft.records.json", "quality.json")}})
    child_state(output, row["run_id"], "completed")
    return app, session_id, output, row, record


def _prepare_worker(root, output, session_id, version, gate, results):
    try:
        app = human_augmentation_application(Path(root), Path(output))
        gate.wait(15)
        row = app.generate_round(session_id, request_id="same-process-click", expected_version=version,
                                 sample_count=1)
        results.put(("ok", row["run_id"]))
    except Exception as error:
        results.put(("error", str(error)))


def _revision_crash_worker(root, output, session_id, version, feedback_id, created, errors):
    try:
        app = human_augmentation_application(Path(root), Path(output))
        original = app._driver.workflow.create_run
        def pause_after_create(**recipe):
            original(**recipe)
            created.set()
            # Parent terminates this owned process to reproduce an OS crash
            # between child creation and committing the ready ledger state.
            while True:
                time.sleep(.1)
        app._driver.workflow.create_run = pause_after_create
        app.revise_round(session_id, request_id="crash-revision-click", expected_version=version,
                         selected_feedback_ids=[feedback_id], sample_count=1)
    except Exception as error:
        errors.put(str(error))
        created.set()


def test_same_click_in_two_browsers_prepares_one_run_and_never_executes(tmp_path, monkeypatch):
    first, session_id, output = prepared_app(tmp_path)
    second = human_augmentation_application(tmp_path, output)
    calls = watch_creations(monkeypatch, first, second)
    version = first.session(session_id)["version"]
    with ThreadPoolExecutor(max_workers=2) as workers:
        futures = [workers.submit(app.generate_round, session_id, request_id="same-browser-click",
                                   expected_version=version, sample_count=1) for app in (first, second)]
        rounds = [future.result(15) for future in futures]
    assert len(calls) == 1
    assert rounds[0]["run_id"] == rounds[1]["run_id"]
    assert rounds[0]["round_id"] == rounds[1]["round_id"]
    state = engine.read_json(engine.run_path(output, rounds[0]["run_id"]) / "state.json")
    assert state["status"] == "queued" and state["attempt"] == 0 and state["usage"] == {}
    assert len(first.session(session_id)["rounds"]) == 1


def test_same_click_across_spawn_processes_is_durable(tmp_path, standalone_spawn):
    app, session_id, output = prepared_app(tmp_path)
    version = app.session(session_id)["version"]
    context = standalone_spawn
    gate, results = context.Event(), context.Queue()
    workers = [context.Process(target=_prepare_worker,
        args=(str(tmp_path), str(output), session_id, version, gate, results)) for _ in range(2)]
    try:
        for worker in workers:
            worker.start()
        gate.set()
        rows = [results.get(timeout=30) for _ in workers]
        for worker in workers:
            worker.join(10)
            assert worker.exitcode == 0
        assert all(row[0] == "ok" for row in rows), rows
        assert rows[0][1] == rows[1][1]
        assert len(list((output / "workflows").glob("*/recipe.json"))) == 1
    finally:
        for worker in workers:
            close_worker(worker)
        results.close()
        results.join_thread()


def test_two_different_clicks_cannot_create_parallel_rounds(tmp_path, monkeypatch):
    first, session_id, output = prepared_app(tmp_path)
    second = human_augmentation_application(tmp_path, output)
    calls = watch_creations(monkeypatch, first, second)
    version = first.session(session_id)["version"]
    with ThreadPoolExecutor(max_workers=2) as workers:
        futures = [workers.submit(app.generate_round, session_id, request_id=request,
            expected_version=version, sample_count=1) for app, request in ((first, "left"), (second, "right"))]
        successes, errors = [], []
        for future in futures:
            try:
                successes.append(future.result(15))
            except ValueError as error:
                errors.append(str(error))
    assert len(successes) == len(errors) == len(calls) == 1
    assert errors[0] in {"human_session_round_pending", "human_session_version_conflict"}
    assert len(first.session(session_id)["rounds"]) == 1


def test_concurrent_draft_edits_use_cas_without_silent_overwrite(tmp_path):
    first, session_id, output = prepared_app(tmp_path)
    second = human_augmentation_application(tmp_path, output)
    version = first.session(session_id)["version"]
    designs = [{**manual(), "question_requirements": text} for text in ("简洁正式。", "友善口语。")]
    with ThreadPoolExecutor(max_workers=2) as workers:
        futures = [workers.submit(app.save_draft, session_id, design, expected_version=version)
                   for app, design in zip((first, second), designs)]
        successes, errors = [], []
        for future in futures:
            try:
                successes.append(future.result(15))
            except ValueError as error:
                errors.append(str(error))
    assert len(successes) == len(errors) == 1
    assert errors == ["human_session_version_conflict"]
    assert first.session(session_id)["draft"] == successes[0]["draft"]


def test_refresh_recovers_prepared_round_without_creating_or_running_another(tmp_path, monkeypatch):
    app, session_id, output = prepared_app(tmp_path)
    calls = watch_creations(monkeypatch, app)
    first = app.generate_round(session_id, request_id="prepare-once",
                               expected_version=app.session(session_id)["version"], sample_count=1)
    refreshed = human_augmentation_application(tmp_path, output)
    recovered = refreshed.resume_round(session_id, first["round_id"])
    assert recovered["run_id"] == first["run_id"] and recovered["launchable"]
    assert refreshed.session(session_id)["current_run_id"] == first["run_id"]
    assert len(calls) == len(refreshed.session(session_id)["rounds"]) == 1
    assert engine.read_json(engine.run_path(output, first["run_id"]) / "state.json")["attempt"] == 0


def test_cancelled_prepared_click_is_retired_and_cannot_recreate_a_run(tmp_path):
    app, session_id, _ = prepared_app(tmp_path)
    row = app.generate_round(session_id, request_id="cancel-this-click",
                             expected_version=app.session(session_id)["version"], sample_count=1)
    stopped = app.cancel_round(session_id, row["round_id"])
    assert not stopped["launchable"]
    with pytest.raises(ValueError, match="human_session_round_cancelled"):
        app.generate_round(session_id, request_id="cancel-this-click",
                           expected_version=app.session(session_id)["version"], sample_count=1)
    assert len(app.session(session_id)["rounds"]) == 1


def test_editing_next_design_cannot_mutate_the_prepared_run_recipe(tmp_path):
    app, session_id, output = prepared_app(tmp_path)
    row = app.generate_round(session_id, request_id="freeze-first",
                             expected_version=app.session(session_id)["version"], sample_count=1)
    path = engine.run_path(output, row["run_id"]) / "recipe.json"
    original = path.read_bytes()
    app.save_draft(session_id, {**manual(), "answer_requirements": "用温和语气解释安全前提。"},
                   expected_version=app.session(session_id)["version"])
    assert path.read_bytes() == original
    with pytest.raises(ValueError, match="human_session_round_pending"):
        app.generate_round(session_id, request_id="next-design",
                           expected_version=app.session(session_id)["version"], sample_count=1)


def test_running_cancel_keeps_pending_guard_until_worker_has_stopped(tmp_path):
    app, session_id, output = prepared_app(tmp_path)
    row = app.generate_round(session_id, request_id="running-first",
                             expected_version=app.session(session_id)["version"], sample_count=1)
    run = engine.run_path(output, row["run_id"])
    child_state(output, row["run_id"], "running")
    with FileLock(str(run / ".run.lock"), timeout=0):
        stopped = app.cancel_round(session_id, row["round_id"])
        assert stopped["active"] and not stopped["launchable"]
        assert (run / "cancel.json").is_file()
        with pytest.raises(ValueError, match="human_session_round_pending"):
            app.generate_round(session_id, request_id="too-early",
                               expected_version=app.session(session_id)["version"], sample_count=1)
    child_state(output, row["run_id"], "cancelled")
    next_round = app.generate_round(session_id, request_id="after-stop",
                                    expected_version=app.session(session_id)["version"], sample_count=1)
    assert next_round["run_id"] != row["run_id"]


def test_resuming_old_failed_round_cannot_bypass_current_pending_round(tmp_path):
    app, session_id, output = prepared_app(tmp_path)
    first = app.generate_round(session_id, request_id="failed-first",
                               expected_version=app.session(session_id)["version"], sample_count=1)
    child_state(output, first["run_id"], "failed")
    second = app.generate_round(session_id, request_id="prepared-second",
                                expected_version=app.session(session_id)["version"], sample_count=1)
    assert first["run_id"] != second["run_id"]
    with pytest.raises(ValueError, match="human_session_round_pending"):
        app.resume_round(session_id, first["round_id"])


def test_replaying_old_request_cannot_launch_it_beside_current_round(tmp_path):
    app, session_id, output = prepared_app(tmp_path)
    original_version = app.session(session_id)["version"]
    first = app.generate_round(session_id, request_id="failed-click",
                               expected_version=original_version, sample_count=1)
    child_state(output, first["run_id"], "failed")
    second = app.generate_round(session_id, request_id="new-click",
                                expected_version=app.session(session_id)["version"], sample_count=1)
    assert first["run_id"] != second["run_id"]
    # Browser reconnects may replay an old click with the old CAS version.
    with pytest.raises(ValueError, match="human_session_round_pending"):
        app.generate_round(session_id, request_id="failed-click",
                           expected_version=original_version, sample_count=1)


def test_feedback_saved_by_two_windows_cannot_overwrite_the_winning_edit(tmp_path):
    first, session_id, output, row, record = sealed_generation(tmp_path)
    second = human_augmentation_application(tmp_path, output)
    version = first.session(session_id)["version"]
    with ThreadPoolExecutor(max_workers=2) as workers:
        futures = [workers.submit(app.save_feedback, session_id, row["round_id"], "sft", record["id"],
            instruction=instruction, expected_version=version)
            for app, instruction in ((first, "补充断电后等待设备停止。"), (second, "用更亲切的语气回答。"))]
        saved, errors = [], []
        for future in futures:
            try:
                saved.append(future.result(15))
            except ValueError as error:
                errors.append(str(error))
    assert len(saved) == len(errors) == 1
    assert errors == ["human_session_version_conflict"]
    state = first.session(session_id)
    assert len(state["feedback"]) == 1
    assert state["feedback"][0]["id"] == saved[0]["new_feedback_id"]
    assert len(list((output / "human-sessions" / session_id / "feedback").glob("*.json"))) == 1


def test_same_revision_click_prepares_one_child_with_exact_selected_result(tmp_path, monkeypatch):
    first, session_id, output, parent, record = sealed_generation(tmp_path)
    second = human_augmentation_application(tmp_path, output)
    feedback = first.save_feedback(session_id, parent["round_id"], "sft", record["id"],
        instruction="保持安全前提，用更亲切的语气回答。", expected_version=first.session(session_id)["version"])
    version = feedback["version"]
    original_recipe = (engine.run_path(output, parent["run_id"]) / "recipe.json").read_bytes()
    calls = watch_creations(monkeypatch, first, second)
    with ThreadPoolExecutor(max_workers=2) as workers:
        futures = [workers.submit(app.revise_round, session_id, request_id="same-revision-click",
            expected_version=version, selected_feedback_ids=[feedback["new_feedback_id"]], sample_count=1)
            for app in (first, second)]
        rows = [future.result(15) for future in futures]
    assert len(calls) == 1 and rows[0]["run_id"] == rows[1]["run_id"]
    assert rows[0]["run_id"] != parent["run_id"]
    revision_run = engine.run_path(output, rows[0]["run_id"])
    recipe = engine.read_json(revision_run / "recipe.json")
    context = recipe["repair_inputs"][0]["revision_context"]
    assert recipe["qa_director"].get("human_augmentation", {"enabled": False}) == {"enabled": False}
    assert context["parent_run_id"] == parent["run_id"]
    assert context["content_sha256"] == engine.digest(record) and context["messages"] == record["messages"]
    assert context["instruction"] == "保持安全前提，用更亲切的语气回答。"
    assert context["depth"] == 1 and context["source_kind"] == "human_provided"
    assert (engine.run_path(output, parent["run_id"]) / "recipe.json").read_bytes() == original_recipe
    state = first.session(session_id)
    assert state["feedback"][0]["applied_round_id"] == rows[0]["round_id"]
    assert len(state["rounds"]) == 2
    assert engine.read_json(revision_run / "state.json")["attempt"] == 0


def test_changed_selected_artifact_cannot_create_a_revision(tmp_path, monkeypatch):
    app, session_id, output, row, record = sealed_generation(tmp_path)
    feedback = app.save_feedback(session_id, row["round_id"], "sft", record["id"],
        instruction="用更自然的话术回答。", expected_version=app.session(session_id)["version"])
    artifact = engine.run_path(output, row["run_id"]) / "artifacts" / "sft.records.json"
    changed = deepcopy(record)
    changed["messages"][1]["content"] = "先通电，再检查设备。"
    atomic_json(artifact, [changed])
    calls = watch_creations(monkeypatch, app)
    with pytest.raises(ValueError, match="artifact_integrity_error|human_session_result_changed"):
        app.revise_round(session_id, request_id="tampered-parent",
            expected_version=feedback["version"], selected_feedback_ids=[feedback["new_feedback_id"]])
    assert calls == [] and len(app.session(session_id)["rounds"]) == 1


def test_resume_reserves_the_pending_round_before_browser_launch(tmp_path):
    app, session_id, output = prepared_app(tmp_path)
    first = app.generate_round(session_id, request_id="first-then-failed",
                               expected_version=app.session(session_id)["version"], sample_count=1)
    child_state(output, first["run_id"], "failed")
    resumed = app.resume_round(session_id, first["round_id"])
    assert resumed["run_id"] == first["run_id"] and resumed["launchable"]
    assert not resumed["active"]
    with pytest.raises(ValueError, match="human_session_round_pending"):
        app.generate_round(session_id, request_id="racing-browser-click",
                           expected_version=app.session(session_id)["version"], sample_count=1)
    app.cancel_round(session_id, first["round_id"])
    next_round = app.generate_round(session_id, request_id="after-cancelled-resume",
                                    expected_version=app.session(session_id)["version"], sample_count=1)
    assert next_round["run_id"] != first["run_id"]


def test_started_console_job_blocks_next_round_before_child_execution_lock(tmp_path):
    app, session_id, output = prepared_app(tmp_path)
    first = app.generate_round(session_id, request_id="job-starting",
                               expected_version=app.session(session_id)["version"], sample_count=1)
    run = engine.run_path(output, first["run_id"])
    child_state(output, first["run_id"], "failed")
    with FileLock(str(run / ".console-job.lock"), timeout=0):
        assert app.session(session_id)["rounds"][0]["active"]
        with pytest.raises(ValueError, match="human_session_round_pending"):
            app.generate_round(session_id, request_id="while-job-launching",
                               expected_version=app.session(session_id)["version"], sample_count=1)


def test_os_crash_after_revision_creation_recovers_run_and_consumed_feedback(tmp_path, monkeypatch, standalone_spawn):
    app, session_id, output, parent, record = sealed_generation(tmp_path)
    feedback = app.save_feedback(session_id, parent["round_id"], "sft", record["id"],
        instruction="保持安全前提，回答更自然。", expected_version=app.session(session_id)["version"])
    context = standalone_spawn
    created, errors = context.Event(), context.Queue()
    worker = context.Process(target=_revision_crash_worker, args=(str(tmp_path), str(output), session_id,
        feedback["version"], feedback["new_feedback_id"], created, errors))
    try:
        worker.start()
        assert created.wait(20), "Owned worker failed to reach the crash point."
        if not errors.empty():
            pytest.fail(errors.get(timeout=1))
        worker.terminate()
        worker.join(5)
        assert not worker.is_alive()
        refreshed = human_augmentation_application(tmp_path, output)
        state = refreshed.session(session_id)
        assert len(state["rounds"]) == 2
        recovered = state["rounds"][-1]
        assert recovered["status"] == "prepared" and recovered["launchable"]
        assert state["feedback"][0]["applied_round_id"] == recovered["round_id"]
        calls = watch_creations(monkeypatch, refreshed)
        replay = refreshed.revise_round(session_id, request_id="crash-revision-click",
            expected_version=feedback["version"], selected_feedback_ids=[feedback["new_feedback_id"]], sample_count=1)
        assert replay["run_id"] == recovered["run_id"] and calls == []
        child_state(output, recovered["run_id"], "completed")
        with pytest.raises(ValueError, match="human_session_feedback_not_available"):
            refreshed.revise_round(session_id, request_id="reusing-consumed-feedback",
                expected_version=refreshed.session(session_id)["version"],
                selected_feedback_ids=[feedback["new_feedback_id"]])
    finally:
        close_worker(worker)
        errors.close()
        errors.join_thread()
