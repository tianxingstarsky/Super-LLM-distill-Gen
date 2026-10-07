"""Durable stream previews never replace completed per-item checkpoints."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path

from filelock import FileLock
import httpx
import pytest

from lib.application.workflow_service import WorkflowApplication
from lib.infrastructure import training_workflow as engine
from lib.infrastructure.workflow_driver import FilesystemWorkflowDriver
from lib.infrastructure.workflow_stream_journal import RECENT_LIMIT, TEXT_LIMIT, StreamJournal, read_streams
from lib.io_utils import atomic_json
from lib.model_streaming import ModelStreamError
import importlib.util

_spec = importlib.util.spec_from_file_location("model_streaming_fixtures", Path(__file__).with_name("model_streaming_fixtures.py"))
_helpers = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_helpers)
fixture, sdk_client, sse_response, unfinished = (_helpers.fixture, _helpers.sdk_client,
                                               _helpers.sse_response, _helpers.unfinished)


def workflow(tmp_path, client):
    output = tmp_path / "output"
    run_id = engine.create_run(output, brief="Write equipment maintenance examples", targets=["sft"],
                               tasks=2, batch_size=1, concurrency=1)
    run = engine.Workflow(output, run_id, tmp_path, generator=client)
    run.state.update(attempt=1, status="running")
    run.save()
    return output, run_id, run


def test_rebuilt_worker_reuses_finished_item_and_retries_only_interrupted_one(tmp_path):
    calls = []
    def first(request):
        calls.append(request)
        return sse_response(fixture("chat") if len(calls) == 1 else unfinished("chat"))
    client = sdk_client("chat", first)
    output, run_id, run = workflow(tmp_path, client)
    items = [{"id": "sample-1"}, {"id": "sample-2"}]
    def action(worker, item):
        return [{"id": item["id"], "status": "eligible",
                 **worker.ask([item["id"], "sft"], "generation", "workflow.sft", item)}]
    try:
        with pytest.raises(ModelStreamError, match="model_stream_incomplete"):
            run.stage_items("sft", items, lambda item: action(run, item))
        assert len(calls) == 2
        pending = (run.path / "stage-results/.sft.pending").read_text(encoding="utf-8")
        assert "sample-1" in pending and "sample-2" not in pending
        assert not (run.path / "stage-results/sft.jsonl").exists()
        checkpoints = run.path / "checkpoints/sft"
        assert (checkpoints / f"{engine.digest(['call', ['sample-1', 'sft']])}.json").is_file()
        assert not (checkpoints / f"{engine.digest(['call', ['sample-2', 'sft']])}.json").exists()
        application = WorkflowApplication(FilesystemWorkflowDriver(tmp_path, output))
        before = application.read_streams(run_id)
        assert {row["unit"]: row["status"] for row in before} == {"sample-1": "completed", "sample-2": "interrupted"}
        assert next(row for row in before if row["unit"] == "sample-2")["retryable"] is True
        retry_calls = []
        retry_client = sdk_client("chat", lambda request: (retry_calls.append(request), sse_response(fixture("chat")))[1])
        try:
            restored = engine.Workflow(output, run_id, tmp_path, generator=retry_client)
            restored.state["attempt"] = 2
            restored.save()
            rows = list(restored.stage_items("sft", items, lambda item: action(restored, item)))
            assert [row["id"] for row in rows] == ["sample-1", "sample-2"]
            assert len(retry_calls) == 1
            assert restored.state["stages"]["sft"]["cached"] == 1
            previews = application.read_streams(run_id)
            assert len({row["id"] for row in previews}) == 3
            assert sum(row["status"] == "completed" for row in previews) == 2
            assert all(row["text"] == '{"answer":"ok"}' for row in previews)
            assert "SUPER-SECRET" not in json.dumps(previews)
        finally:
            retry_client.client.close()
    finally:
        client.client.close()


def test_json_retry_uses_new_slot_instead_of_joining_invalid_output(tmp_path):
    calls = []
    invalid = fixture("chat").replace(b'{\\"answer\\":', b'not JSON').replace(b'\\"ok\\"}', b'still invalid')
    def handle(request):
        calls.append(request)
        return sse_response(invalid if len(calls) == 1 else fixture("chat"))
    client = sdk_client("chat", handle)
    _, _, run = workflow(tmp_path, client)
    run.stage = "sft"
    try:
        assert run.ask(["sample-1", "sft"], "generation", "workflow.sft", {}) == {"answer": "ok"}
        previews = read_streams(run.path, active=True, run_attempt=1)
        assert len(previews) == 2 and len({row["id"] for row in previews}) == 2
        assert {row["attempt"] for row in previews} == {1, 2}
        assert next(row for row in previews if row["status"] == "completed")["text"] == '{"answer":"ok"}'
        assert "invalid" in next(row for row in previews if row["status"] == "interrupted")["text"]
    finally:
        client.client.close()


def test_active_prefix_is_durable_and_dead_or_previous_worker_becomes_retryable(tmp_path):
    tmp_path.mkdir(exist_ok=True)
    journal = StreamJournal(tmp_path, stage="sft", unit="sample-1", role="generation", run_attempt=1)
    journal({"type": "start"})
    journal({"type": "delta", "channel": "text", "text": '{"answer":'})
    assert read_streams(tmp_path, active=True, run_attempt=1)[0]["status"] == "active"
    # No finalizer runs: simulate a process exit after receiving its first delta.
    for active, attempt in ((False, 1), (True, 2)):
        row = read_streams(tmp_path, active=active, run_attempt=attempt)[0]
        assert row["text"] == '{"answer":'
        assert row["status"] == "interrupted" and row["retryable"]
    assert not (tmp_path / "checkpoints").exists()


def test_checkpoint_commit_precedes_completed_preview_and_survives_exit(tmp_path, monkeypatch):
    client = sdk_client("chat", lambda request: sse_response(fixture("chat")))
    output, run_id, run = workflow(tmp_path, client)
    run.stage = "sft"
    checkpoint = run.path / "checkpoints/sft" / f"{engine.digest(['call', ['sample-1', 'sft']])}.json"
    class ProcessExit(BaseException):
        pass
    def exit_after_commit(journal):
        assert checkpoint.is_file()
        assert read_streams(run.path, active=True, run_attempt=1)[0]["status"] == "completed"
        raise ProcessExit()
    monkeypatch.setattr(StreamJournal, "completed", exit_after_commit)
    try:
        with pytest.raises(ProcessExit):
            run.ask(["sample-1", "sft"], "generation", "workflow.sft", {})
        row = read_streams(run.path, active=False, run_attempt=1)[0]
        assert row["status"] == "completed" and not row["retryable"]
        restored = engine.Workflow(output, run_id, tmp_path, generator=client)
        restored.stage = "sft"
        client.client.chat.completions.create = lambda **kwargs: pytest.fail("completed checkpoint must be reused")
        assert restored.ask(["sample-1", "sft"], "generation", "workflow.sft", {}) == {"answer": "ok"}
    finally:
        client.client.close()


def test_preview_failure_after_checkpoint_does_not_fail_completed_sample(tmp_path, monkeypatch):
    client = sdk_client("chat", lambda request: sse_response(fixture("chat")))
    _, _, run = workflow(tmp_path, client)
    run.stage = "sft"
    monkeypatch.setattr(StreamJournal, "completed", lambda self: (_ for _ in ()).throw(OSError("disk unavailable")))
    try:
        assert run.ask(["sample-1", "sft"], "generation", "workflow.sft", {}) == {"answer": "ok"}
        assert read_streams(run.path, active=False, run_attempt=1)[0]["status"] == "completed"
    finally:
        client.client.close()


def test_parallel_slots_are_isolated_and_preview_retention_is_bounded(tmp_path):
    def produce(index):
        journal = StreamJournal(tmp_path, stage="sft", unit=f"sample-{index}", role="generation", run_attempt=1)
        journal({"type": "start"})
        journal({"type": "delta", "channel": "text", "text": str(index) * (TEXT_LIMIT + 100)})
        journal({"type": "delta", "channel": "reasoning", "text": f"reason-{index}"})
        journal.completed()
        return journal.row["id"]
    with ThreadPoolExecutor(max_workers=8) as pool:
        ids = list(pool.map(produce, range(45)))
    assert len(set(ids)) == 45
    paths = list((tmp_path / "streams").glob("*.json"))
    assert len(paths) == RECENT_LIMIT
    rows = read_streams(tmp_path, active=False, run_attempt=1)
    assert len(rows) == RECENT_LIMIT
    for row in rows:
        index = row["unit"].split("-")[-1]
        assert row["text"] == (index * (TEXT_LIMIT + 100))[-TEXT_LIMIT:]
        assert row["reasoning"] == f"reason-{index}"
        assert row["truncated"] is True and row["status"] == "completed"
        assert len(row["text"]) == TEXT_LIMIT
    # A stream still in progress remains visible under rapid task completion.
    active = StreamJournal(tmp_path, stage="sft", unit="active", role="generation", run_attempt=1)
    active({"type": "start"})
    assert read_streams(tmp_path, active=True, run_attempt=1)[0]["unit"] == "active"


def test_application_status_uses_run_lock_and_validates_run_id(tmp_path):
    client = sdk_client("chat", lambda request: sse_response(fixture("chat")))
    output, run_id, run = workflow(tmp_path, client)
    journal = StreamJournal(run.path, stage="sft", unit="sample-1", role="generation", run_attempt=1)
    journal({"type": "start"})
    app = WorkflowApplication(FilesystemWorkflowDriver(tmp_path, output))
    try:
        with FileLock(str(run.path / ".run.lock"), timeout=0):
            assert app.read_streams(run_id)[0]["status"] == "active"
        assert app.read_streams(run_id)[0]["status"] == "interrupted"
        with pytest.raises(ValueError):
            app.read_streams("../outside")
        assert app.read_streams(run_id)[0]["text"] == ""
    finally:
        client.client.close()


def test_preview_reader_rejects_oversized_or_hardlinked_slot(tmp_path):
    journal = StreamJournal(tmp_path, stage="sft", unit="sample-1", role="generation", run_attempt=1)
    journal({"type": "start"})
    path = next((tmp_path / "streams").glob("*.json"))
    original = path.read_bytes()
    path.write_bytes(b"x" * 160001)
    with pytest.raises(ValueError, match="model_stream_storage_invalid"):
        read_streams(tmp_path, active=False, run_attempt=1)
    path.write_bytes(original)
    os.link(path, tmp_path / "alias.json")
    with pytest.raises(ValueError, match="model_stream_storage_invalid"):
        read_streams(tmp_path, active=False, run_attempt=1)


def test_old_attempt_active_slots_are_retired_on_next_completion(tmp_path):
    first = StreamJournal(tmp_path, stage="sft", unit="old", role="generation", run_attempt=1)
    first({"type": "start"})
    second = StreamJournal(tmp_path, stage="sft", unit="new", role="generation", run_attempt=2)
    second({"type": "start"})
    second.completed()
    rows = read_streams(tmp_path, active=True, run_attempt=2)
    assert {row["unit"]: row["status"] for row in rows} == {"old": "interrupted", "new": "completed"}
    assert not list((tmp_path / "streams").glob("*.active.json"))


def test_previous_attempt_committed_slot_stays_completed_after_later_pruning(tmp_path):
    key = engine.digest(["call", ["old", "sft"]])
    old = StreamJournal(tmp_path, stage="sft", unit="old", role="generation", run_attempt=1, checkpoint=key)
    old({"type": "start"})
    data = {"answer": "already complete"}
    atomic_json(tmp_path / "checkpoints/sft" / f"{key}.json", {"data": data, "sha256": engine.digest(data)})
    assert read_streams(tmp_path, active=False, run_attempt=1)[0]["status"] == "completed"
    new = StreamJournal(tmp_path, stage="sft", unit="new", role="generation", run_attempt=2)
    new({"type": "start"})
    new.completed()
    rows = read_streams(tmp_path, active=True, run_attempt=2)
    assert {row["unit"]: row["status"] for row in rows} == {"old": "completed", "new": "completed"}
    assert not list((tmp_path / "streams").glob("*.active.json"))


@pytest.mark.parametrize("field,bad", [("text_chars", "bad"), ("reasoning_chars", -1),
    ("run_attempt", True), ("attempt", 0), ("attempt", "1"), ("truncated", "false")])
def test_broken_preview_metadata_fails_with_static_code(tmp_path, field, bad):
    journal = StreamJournal(tmp_path, stage="sft", unit="sample-1", role="generation", run_attempt=1)
    journal({"type": "start"})
    path = next((tmp_path / "streams").glob("*.json"))
    row = json.loads(path.read_text(encoding="utf-8"))
    row[field] = bad
    atomic_json(path, row)
    with pytest.raises(ValueError, match="^model_stream_storage_invalid$"):
        read_streams(tmp_path, active=False, run_attempt=1)
    assert not (tmp_path / "checkpoints").exists()


def test_non_object_checkpoint_does_not_crash_preview_reader(tmp_path):
    key = engine.digest(["call", ["old", "sft"]])
    journal = StreamJournal(tmp_path, stage="sft", unit="old", role="generation", run_attempt=1, checkpoint=key)
    journal({"type": "start"})
    checkpoint = tmp_path / "checkpoints/sft" / f"{key}.json"
    atomic_json(checkpoint, [])
    row = read_streams(tmp_path, active=False, run_attempt=1)[0]
    assert row["status"] == "interrupted" and row["retryable"]
    assert json.loads(checkpoint.read_text(encoding="utf-8")) == []
