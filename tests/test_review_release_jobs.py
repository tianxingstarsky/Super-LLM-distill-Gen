"""Publication workers survive session loss and reject duplicate starts."""
import time

import pytest
from filelock import FileLock

from lib.infrastructure import review_release_jobs as jobs
from lib.io_utils import atomic_json
from test_review_pagination import fixture_app


def wait_for_release(app, run_id, timeout=20):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        state = app.release_job(run_id)
        if state["status"] not in jobs.ACTIVE:
            return state
        time.sleep(0.05)
    raise AssertionError("Release worker did not finish")


@pytest.mark.parametrize("target", ["sft", "cpt", "dpo", "orpo"])
def test_real_worker_publishes_without_browser_session(tmp_path, target):
    app, run_id, _ = fixture_app(tmp_path, target, count=3)
    job = app.start_release(run_id)
    assert job["status"] == "queued"
    assert app.start_release(run_id)["id"] == job["id"]
    state = wait_for_release(app, run_id)
    assert state["status"] == "completed", state
    assert state["result"]["counts"]["approved"] == 3
    # A newly assembled service recovers completion from disk, without session memory.
    recovered = type(app)(type(app._driver)(tmp_path, **({"target": target} if target in {"dpo", "orpo"} else {})))
    assert recovered.release_job(run_id) == state
    with recovered.release_archive(run_id, state["result"]["id"], state["result"]["sha256"]) as handle:
        assert handle.read(2) == b"PK"


def test_live_worker_lock_keeps_state_running_and_dead_worker_can_retry(tmp_path, monkeypatch):
    app, run_id, _ = fixture_app(tmp_path, "sft", count=3)
    monkeypatch.setattr(jobs.subprocess, "Popen", lambda *args, **kwargs: None)
    state = app.start_release(run_id)
    path = jobs._paths(tmp_path, run_id, "sft")
    state["status"] = "running"
    atomic_json(path, state)
    with FileLock(str(path) + ".active.lock", timeout=0):
        assert app.release_job(run_id)["status"] == "running"
        # Listing runs must not wait on the audit/index locks during publication.
        with FileLock(str(path.parent.parent / ".indexes" / "sft.sqlite") + ".lock", timeout=0):
            assert app.reviewable_runs()[0]["sample_count"] == 3
    assert app.release_job(run_id)["status"] == "interrupted"
    replacement = app.start_release(run_id)
    assert replacement["id"] != state["id"]
    # An old delayed worker cannot publish or overwrite the new job.
    jobs.execute_release_job(tmp_path, run_id, "sft", state["id"])
    assert app.release_job(run_id)["id"] == replacement["id"]


def test_worker_failure_is_durable_without_visible_release(tmp_path):
    app, run_id, _ = fixture_app(tmp_path, "sft", count=25)
    app.start_release(run_id)
    state = wait_for_release(app, run_id)
    assert state["status"] == "failed"
    assert state["error"].startswith("sft_review_incomplete")
    assert not list((tmp_path / "workflows" / run_id / "releases").glob("sft-v*"))


def test_cancelled_queued_job_never_launches_publication(tmp_path, monkeypatch):
    app, run_id, _ = fixture_app(tmp_path, "sft", count=3)
    monkeypatch.setattr(jobs.subprocess, "Popen", lambda *args, **kwargs: None)
    state = app.start_release(run_id)
    assert app.cancel_release(run_id)["status"] == "cancelled"
    jobs.execute_release_job(tmp_path, run_id, "sft", state["id"])
    assert app.release_job(run_id)["status"] == "cancelled"
    assert not (tmp_path / "workflows" / run_id / "releases").exists()


def test_running_worker_observes_cancellation_at_progress_checkpoint(tmp_path, monkeypatch):
    from lib.infrastructure.training_review_driver import FilesystemTrainingReviewDriver
    app, run_id, _ = fixture_app(tmp_path, "sft", count=3)
    monkeypatch.setattr(jobs.subprocess, "Popen", lambda *args, **kwargs: None)
    state = app.start_release(run_id)

    def prepare(_driver, _run_id, *, progress):
        progress("audit", 0, 3)
        app.cancel_release(run_id)
        progress("audit", 3, 3)
        raise AssertionError("Cancellation was ignored")

    monkeypatch.setattr(FilesystemTrainingReviewDriver, "prepare_release", prepare)
    jobs.execute_release_job(tmp_path, run_id, "sft", state["id"])
    assert app.release_job(run_id)["status"] == "cancelled"
    assert not (tmp_path / "workflows" / run_id / "releases").exists()


def test_cancel_before_publication_keeps_previous_archive_unchanged(tmp_path):
    app, run_id, _ = fixture_app(tmp_path, "sft", count=3)
    previous = app.prepare_release(run_id)

    def cancel_at_commit(phase, done=0, total=0):
        if phase == "verify" and done == total == 1:
            raise jobs.ReleaseCancelled()

    with pytest.raises(jobs.ReleaseCancelled):
        app._driver.prepare_release(run_id, progress=cancel_at_commit)
    assert len(list((tmp_path / "workflows" / run_id / "releases").glob("sft-v*"))) == 1
    with app.release_archive(run_id, previous["id"], previous["sha256"]) as archive:
        assert archive.read(2) == b"PK"


def test_retry_keeps_last_complete_result_for_a_new_session(tmp_path, monkeypatch):
    app, run_id, _ = fixture_app(tmp_path, "sft", count=3)
    app.start_release(run_id)
    previous = wait_for_release(app, run_id)["result"]
    monkeypatch.setattr(jobs.subprocess, "Popen", lambda *args, **kwargs: None)
    next_job = app.start_release(run_id)
    assert next_job["last_result"] == previous
    app.cancel_release(run_id)
    assert app.release_job(run_id)["last_result"] == previous
