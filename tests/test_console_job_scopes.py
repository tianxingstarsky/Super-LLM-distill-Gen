"""Console process admission keeps independent workflow runs isolated."""
from __future__ import annotations

import threading

import pytest
from filelock import Timeout

from lib.console_jobs import Job, workflow_run_id


RUN_A = "a" * 32
RUN_B = "b" * 32


def _resume(run_id: str) -> list[str]:
    return ["workflow", "--action", "resume", "--run-id", run_id]


def _output_at(tmp_path, monkeypatch):
    output = tmp_path / "output"
    monkeypatch.setattr("lib.workspace.out", lambda _ws, _root: output)
    return output


def _recorded_run(output, run_id: str):
    run = output / "workflows" / run_id
    run.mkdir(parents=True)
    (run / "state.json").write_text("{}", encoding="utf-8")


def test_only_exact_existing_run_resume_is_eligible_for_run_scope(tmp_path, monkeypatch):
    output = _output_at(tmp_path, monkeypatch)
    assert workflow_run_id(_resume(RUN_A)) == RUN_A
    assert workflow_run_id(["workflow", "--action", "start", "--run-id", RUN_A]) is None
    assert workflow_run_id(["workflow", "--action", "resume", "--run-id", "../bad"]) is None
    assert workflow_run_id(_resume(RUN_A) + ["--extra"]) is None
    assert workflow_run_id(["export", "--bulk"]) is None

    job = Job(_resume(RUN_A), "default", tmp_path)
    with pytest.raises(ValueError, match="运行不存在"):
        job.start()
    assert not (output / "workflows" / RUN_A).exists()


def test_independent_runs_execute_concurrently_without_mixing_output(tmp_path, monkeypatch):
    output = _output_at(tmp_path, monkeypatch)
    for run_id in (RUN_A, RUN_B):
        _recorded_run(output, run_id)
    started = {run_id: threading.Event() for run_id in (RUN_A, RUN_B)}
    finish = {run_id: threading.Event() for run_id in (RUN_A, RUN_B)}

    class HeldProcess:
        def __init__(self, argv):
            self.run_id = argv[argv.index("--run-id") + 1]

        def __enter__(self):
            started[self.run_id].set()
            return self

        def __exit__(self, *_args):
            return False

        @property
        def stdout(self):
            yield f"{self.run_id} started\n"
            if not finish[self.run_id].wait(5):
                raise RuntimeError("test worker was not released")
            yield f"{self.run_id} finished\n"

        def wait(self):
            return 0

    monkeypatch.setattr("lib.console_jobs.subprocess.Popen", lambda argv, **_kw: HeldProcess(argv))
    first = Job(_resume(RUN_A), "default", tmp_path)
    second = Job(_resume(RUN_B), "default", tmp_path)
    try:
        first.start()
        second.start()
        assert started[RUN_A].wait(5) and started[RUN_B].wait(5)
        with pytest.raises(Timeout):
            Job(_resume(RUN_A), "default", tmp_path).start()
        assert first.snapshot()[0] is None and second.snapshot()[0] is None
    finally:
        for event in finish.values():
            event.set()
        for job in (first, second):
            if job._thread:
                job._thread.join(5)
    assert first.snapshot() == (0, [f"{RUN_A} started", f"{RUN_A} finished"])
    assert second.snapshot() == (0, [f"{RUN_B} started", f"{RUN_B} finished"])


def test_shared_output_commands_remain_serial_per_workspace(tmp_path, monkeypatch):
    monkeypatch.setattr("lib.workspace.out", lambda ws, _root: tmp_path / ws / "output")
    started = {ws: threading.Event() for ws in ("alpha", "beta")}
    finish = {ws: threading.Event() for ws in ("alpha", "beta")}

    class HeldProcess:
        def __init__(self, argv):
            self.workspace = argv[argv.index("--ws") + 1]

        def __enter__(self):
            started[self.workspace].set()
            return self

        def __exit__(self, *_args):
            return False

        @property
        def stdout(self):
            yield f"{self.workspace} started\n"
            if not finish[self.workspace].wait(5):
                raise RuntimeError("test worker was not released")

        def wait(self):
            return 0

    monkeypatch.setattr("lib.console_jobs.subprocess.Popen", lambda argv, **_kw: HeldProcess(argv))
    alpha = Job(["export", "--bulk"], "alpha", tmp_path)
    beta = Job(["export", "--bulk"], "beta", tmp_path)
    try:
        alpha.start()
        assert started["alpha"].wait(5)
        with pytest.raises(Timeout):
            Job(["doc2data"], "alpha", tmp_path).start()
        beta.start()
        assert started["beta"].wait(5)
    finally:
        for event in finish.values():
            event.set()
        for job in (alpha, beta):
            if job._thread:
                job._thread.join(5)
    assert alpha.snapshot() == (0, ["alpha started"])
    assert beta.snapshot() == (0, ["beta started"])


@pytest.mark.parametrize("command", [["doc2data"], _resume(RUN_A)])
def test_failed_thread_start_releases_admission_lock(tmp_path, monkeypatch, command):
    output = _output_at(tmp_path, monkeypatch)
    if workflow_run_id(command):
        _recorded_run(output, RUN_A)

    class FailedThread:
        def __init__(self, **_kwargs):
            pass

        def start(self):
            raise RuntimeError("thread unavailable")

    real_thread = threading.Thread
    monkeypatch.setattr("lib.console_jobs.threading.Thread", FailedThread)
    failed = Job(command, "default", tmp_path)
    with pytest.raises(RuntimeError, match="thread unavailable"):
        failed.start()
    assert failed._thread is None and failed._ws_lock is None

    monkeypatch.setattr("lib.console_jobs.threading.Thread", real_thread)

    class QuickProcess:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        @property
        def stdout(self):
            return iter(())

        def wait(self):
            return 0

    monkeypatch.setattr("lib.console_jobs.subprocess.Popen", lambda *_a, **_kw: QuickProcess())
    retry = Job(command, "default", tmp_path)
    retry.start()
    retry._thread.join(5)
    assert retry.snapshot() == (0, [])
