"""Automatic package workers persist progress and cooperate with cancellation."""
import time
from pathlib import Path

from filelock import FileLock

from lib.application.workflow_service import WorkflowApplication
from lib.infrastructure.workflow_driver import FilesystemWorkflowDriver
from lib.infrastructure import review_release_jobs as jobs, workflow_archive as archives
from lib.infrastructure.training_workflow import Workflow, create_run, run_path


def app_fixture(tmp_path):
    source = tmp_path / "guide.txt"
    source.write_text("Disconnect power before checking the wiring.", encoding="utf-8")
    run_id = create_run(tmp_path, sources=[source], targets=["cpt"])
    assert Workflow(tmp_path, run_id, Path.cwd()).execute()["status"] == "completed"
    return WorkflowApplication(FilesystemWorkflowDriver(Path.cwd(), tmp_path)), run_id


def wait(app, run_id):
    deadline = time.monotonic() + 20
    while time.monotonic() < deadline:
        state = app.bundle_job(run_id)
        if state["status"] not in jobs.ACTIVE:
            return state
        time.sleep(0.05)
    raise AssertionError("Archive worker did not finish")


def test_real_worker_recovers_after_browser_session_loss(tmp_path):
    app, run_id = app_fixture(tmp_path)
    job = app.start_bundle(run_id)
    assert app.start_bundle(run_id)["id"] == job["id"]
    state = wait(app, run_id)
    assert state["status"] == "completed", state
    other = WorkflowApplication(FilesystemWorkflowDriver(Path.cwd(), tmp_path))
    assert other.bundle_job(run_id) == state
    assert other.prepared_bundle(run_id) == state["result"]
    assert other.bundle(run_id).startswith(b"PK")


def test_archive_cancel_checkpoint_keeps_existing_package(tmp_path, monkeypatch):
    app, run_id = app_fixture(tmp_path)
    previous = app.prepare_bundle(run_id)
    monkeypatch.setattr(jobs.subprocess, "Popen", lambda *args, **kwargs: None)
    job = app.start_bundle(run_id)
    original = archives.prepare_bundle

    def cancelled(run, *, progress):
        app.cancel_bundle(run_id)
        return original(run, progress=progress)

    monkeypatch.setattr(archives, "prepare_bundle", cancelled)
    jobs.execute_release_job(tmp_path, run_id, "workflow", job["id"])
    assert app.bundle_job(run_id)["status"] == "cancelled"
    assert app.prepared_bundle(run_id) == previous


def test_cancel_during_zip_write_removes_partial_archive(tmp_path, monkeypatch):
    app, run_id = app_fixture(tmp_path)
    monkeypatch.setattr(jobs.subprocess, "Popen", lambda *args, **kwargs: None)
    job = app.start_bundle(run_id)
    original = archives.prepare_bundle

    def prepare(run, *, progress):
        def monitored(phase, done=0, total=0):
            if phase == "archive" and done == total:
                app.cancel_bundle(run_id)
            progress(phase, done, total)
        return original(run, progress=monitored)

    monkeypatch.setattr(archives, "prepare_bundle", prepare)
    jobs.execute_release_job(tmp_path, run_id, "workflow", job["id"])
    assert app.bundle_job(run_id)["status"] == "cancelled"
    assert app.prepared_bundle(run_id) is None
    assert not list((run_path(tmp_path, run_id) / "delivery").glob("*.pending"))


def test_worker_liveness_stale_identity_and_retry(tmp_path, monkeypatch):
    app, run_id = app_fixture(tmp_path)
    monkeypatch.setattr(jobs.subprocess, "Popen", lambda *args, **kwargs: None)
    job = app.start_bundle(run_id)
    path = jobs._paths(tmp_path, run_id, "workflow")
    jobs.atomic_json(path, {**job, "status": "running"})
    with FileLock(str(path) + ".active.lock", timeout=0):
        assert app.bundle_job(run_id)["status"] == "running"
    assert app.bundle_job(run_id)["status"] == "interrupted"
    fresh = app.start_bundle(run_id)
    jobs.execute_release_job(tmp_path, run_id, "workflow", job["id"])
    assert app.bundle_job(run_id)["id"] == fresh["id"]
    jobs.execute_release_job(tmp_path, run_id, "workflow", fresh["id"])
    assert app.bundle_job(run_id)["status"] == "completed"
