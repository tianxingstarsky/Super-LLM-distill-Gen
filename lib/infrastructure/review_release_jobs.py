"""Durable release jobs, run by isolated local worker processes."""
from __future__ import annotations

from datetime import datetime, timezone
import os
from pathlib import Path
import subprocess
import sys
import time
import uuid

from filelock import FileLock, Timeout

from lib.infrastructure.training_workflow import read_json, run_path
from lib.io_utils import atomic_json, quiet_process
from lib.workspace import is_linked


ACTIVE = {"queued", "running"}


class ReleaseCancelled(Exception):
    pass


def _paths(output, run_id, target):
    if target not in {"sft", "cpt", "dpo", "orpo"}:
        raise ValueError("invalid_review_target")
    run = run_path(output, run_id)
    directory = run / "human-review" / ".jobs"
    if not (run / "state.json").is_file():
        raise ValueError("review_run_not_found")
    if any(is_linked(p) for p in (run, run / "human-review", directory) if p.exists() or p.is_symlink()):
        raise ValueError("linked_review_job")
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{target}.json"
    if (path.exists() or path.is_symlink()) and is_linked(path):
        raise ValueError("linked_review_job")
    return path


def _active(path):
    try:
        with FileLock(str(path) + ".active.lock", timeout=0):
            return False
    except Timeout:
        return True


def _read(path):
    if not path.exists():
        return None
    state = read_json(path)
    if state.get("status") == "running" and not _active(path):
        state = {**state, "status": "interrupted", "error": "review_release_worker_interrupted"}
        atomic_json(path, state)
    elif state.get("status") == "queued" and time.time() - state.get("submitted_epoch", 0) > 30 and not _active(path):
        state = {**state, "status": "interrupted", "error": "review_release_worker_not_started"}
        atomic_json(path, state)
    return state


def release_job(output, run_id, target):
    path = _paths(output, run_id, target)
    with FileLock(str(path) + ".submit.lock", timeout=2):
        return _read(path)


def cancel_release_job(output, run_id, target):
    path = _paths(output, run_id, target)
    with FileLock(str(path) + ".submit.lock", timeout=2):
        state = _read(path)
        if state and state["status"] in ACTIVE:
            state = {**state, "cancel_requested": True}
            if state["status"] == "queued":
                state["status"] = "cancelled"
            atomic_json(path, state)
        return state


def start_release_job(output, run_id, target, candidate_count):
    path = _paths(output, run_id, target)
    with FileLock(str(path) + ".submit.lock", timeout=2):
        previous = _read(path)
        if previous and previous["status"] in ACTIVE:
            return previous
        if _active(path):
            raise ValueError("review_release_busy")
        state = {"id": uuid.uuid4().hex, "run_id": run_id, "target": target,
                 "candidate_count": candidate_count,
                 "status": "queued", "phase": "queued", "done": 0, "total": 0,
                 "submitted_epoch": time.time(), "updated_at": datetime.now(timezone.utc).isoformat()}
        if previous:
            previous_result = previous.get("result") or previous.get("last_result")
            if previous_result:
                state["last_result"] = previous_result
        atomic_json(path, state)
        command = [sys.executable, "-m", "lib.infrastructure.review_release_worker",
                   "--output", str(Path(output).resolve()), "--run", run_id,
                   "--target", target, "--job", state["id"]]
        try:
            with path.with_suffix(".log").open("ab") as log:
                subprocess.Popen(command, cwd=Path(__file__).resolve().parents[2],
                    stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT,
                    env={**os.environ, "PYTHONUTF8": "1"}, **quiet_process())
        except OSError:
            state = {**state, "status": "failed", "error": "review_release_worker_launch_failed"}
            atomic_json(path, state)
            raise
        return state


def execute_release_job(output, run_id, target, job_id):
    """Only the worker touches release artifacts; UI polls small JSON status."""
    path = _paths(output, run_id, target)
    with FileLock(str(path) + ".active.lock", timeout=0):
        with FileLock(str(path) + ".submit.lock", timeout=2):
            state = read_json(path)
            if state.get("id") != job_id or state.get("status") != "queued":
                return
            state = {**state, "status": "running", "phase": "source", "done": 0, "total": 0}
            atomic_json(path, state)
        last = 0

        def progress(phase, done=0, total=0):
            nonlocal state, last
            now = time.monotonic()
            if phase == state["phase"] and done != total and now - last < 0.5:
                return
            last = now
            state = {**state, "phase": phase, "done": done, "total": total,
                     "updated_at": datetime.now(timezone.utc).isoformat()}
            with FileLock(str(path) + ".submit.lock", timeout=2):
                current = read_json(path)
                if current.get("cancel_requested"):
                    raise ReleaseCancelled()
                atomic_json(path, state)

        try:
            from lib.infrastructure.training_review_driver import FilesystemTrainingReviewDriver
            result = FilesystemTrainingReviewDriver(output, target).prepare_release(run_id, progress=progress)
            state = {**state, "status": "completed", "phase": "completed", "result": result}
        except ReleaseCancelled:
            state = {**state, "status": "cancelled"}
        except Exception as error:
            # Keep internal paths and tracebacks in local logs, outside the UI state.
            import traceback
            traceback.print_exc()
            state = {**state, "status": "failed", "error": str(error) if isinstance(error, ValueError)
                     else "review_release_failed"}
        state["updated_at"] = datetime.now(timezone.utc).isoformat()
        with FileLock(str(path) + ".submit.lock", timeout=2):
            atomic_json(path, state)
