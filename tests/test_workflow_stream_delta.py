"""Full selected output is paged as real deltas, separately from training data."""
import json
import os
from pathlib import Path

from filelock import FileLock
import pytest

from lib.application.workflow_service import WorkflowApplication
from lib.infrastructure import training_workflow as engine
from lib.infrastructure import workflow_stream_journal as journal
from lib.infrastructure.workflow_driver import FilesystemWorkflowDriver
from lib.io_utils import atomic_json
from lib.llm_client import ChatClient


def start(run, *, text="first", stage="sft", unit="sample", finish=True):
    run.mkdir(exist_ok=True, parents=True)
    writer = journal.StreamJournal(run, stage=stage, unit=unit, role="generation", run_attempt=1)
    writer({"type": "start"})
    writer({"type": "delta", "channel": "text", "text": text})
    if finish:
        writer.completed()
    return writer


def page(run, writer, *, offset=0, **options):
    return journal.read_stream_delta(run, writer.row["id"], active=True, run_attempt=1,
                                     offset=offset, stage=writer.row["stage"], **options)


def test_complete_large_unicode_output_is_recovered_without_replaying_prefix(tmp_path):
    text = ('数据生成 😀 <script>literal</script>\n"quoted"\x00 ' * 3000)
    reasoning = "先检查来源，再完成输出。" * 3000
    writer = start(tmp_path, text=text, finish=False)
    writer({"type": "delta", "channel": "reasoning", "text": reasoning})
    writer.completed()
    preview = journal.read_streams(tmp_path, active=False, run_attempt=1)[0]
    assert len(preview["text"]) == journal.TEXT_LIMIT and preview["truncated"]
    cursor, batches, text_parts, reason_parts = 0, [], [], []
    while True:
        batch = page(tmp_path, writer, offset=cursor)
        assert batch["offset"] == cursor and batch["next_offset"] > cursor
        assert not batch["legacy"] and not batch["truncated"]
        assert batch["text"] == batch["reasoning"] == ""
        for event in batch["events"]:
            (text_parts if event["channel"] == "text" else reason_parts).append(event["text"])
        batches.append(batch)
        cursor = batch["next_offset"]
        if batch["done"]:
            break
        assert len(batches) < 100
    assert len(batches) > 2 and not batches[0]["done"]
    assert "".join(text_parts) == text and "".join(reason_parts) == reasoning
    eof = page(tmp_path, writer, offset=cursor)
    assert eof["done"] and eof["events"] == [] and eof["next_offset"] == cursor


def test_active_eof_stays_open_and_later_original_delta_advances_cursor(tmp_path):
    writer = start(tmp_path, finish=False)
    first = page(tmp_path, writer)
    assert not first["done"] and first["events"] == [{"channel": "text", "text": "first"}]
    waiting = page(tmp_path, writer, offset=first["next_offset"])
    assert not waiting["done"] and waiting["events"] == []
    writer({"type": "delta", "channel": "text", "text": " next"})
    writer({"type": "response_completed"})
    received = page(tmp_path, writer, offset=first["next_offset"])
    assert received["events"] == [{"channel": "text", "text": " next"}] and not received["done"]
    writer.completed()
    finished = page(tmp_path, writer, offset=received["next_offset"])
    assert finished["done"] and finished["events"] == []


def test_legacy_snapshot_is_usable_and_honestly_reports_missing_prefix(tmp_path):
    writer = start(tmp_path, text="x" * (journal.TEXT_LIMIT + 20))
    path = writer._path()
    row = json.loads(path.read_text(encoding="utf-8"))
    row.pop("delta_version")
    row.pop("delta_truncated")
    atomic_json(path, row)
    journal._delta_path(writer.directory, row).unlink()
    result = page(tmp_path, writer)
    assert result["legacy"] and result["truncated"] and result["done"]
    assert result["events"] == [] and result["text"] == "x" * journal.TEXT_LIMIT
    assert result["next_offset"] == 0


def test_log_quota_never_truncates_successful_sample_checkpoint(tmp_path, monkeypatch):
    monkeypatch.setattr(journal, "DELTA_LIMIT", 2000)
    answer = "The original answer must remain complete. " * 1000
    response = json.dumps({"answer": answer})
    client = ChatClient("https://offline.invalid/v1", "offline", "writer")
    client.client.chat.completions.create = lambda **kwargs: iter([
        {"choices": [{"index": 0, "delta": {"content": response}, "finish_reason": "stop"}]},
        {"choices": [], "usage": {"prompt_tokens": 1, "completion_tokens": 1}}])
    output = tmp_path / "output"
    run_id = engine.create_run(output, brief="Write maintenance instructions", targets=["sft"])
    worker = engine.Workflow(output, run_id, tmp_path, generator=client)
    worker.stage = "sft"
    try:
        assert worker.ask(["sample", "sft"], "generation", "workflow.sft", {}) == {"answer": answer}
        checkpoint = worker.path / "checkpoints/sft" / f"{engine.digest(['call', ['sample', 'sft']])}.json"
        assert json.loads(checkpoint.read_text(encoding="utf-8"))["data"]["answer"] == answer
        row = journal.read_streams(worker.path, active=False, run_attempt=0)[0]
        result = journal.read_stream_delta(worker.path, row["id"], active=False, run_attempt=0, stage="sft")
        assert result["done"] and result["truncated"] and result["status"] == "completed"
        received = "".join(event["text"] for event in result["events"])
        assert response.startswith(received) and received != response
        assert journal._delta_path(worker.path / "streams", row).stat().st_size <= 2000
    finally:
        client.client.close()


def test_terminal_half_line_is_not_rendered_or_claimed_complete_output(tmp_path):
    writer = start(tmp_path, finish=False)
    writer._close_log()  # Simulate the process ending while its last record is written.
    path = journal._delta_path(writer.directory, writer.row)
    with path.open("ab") as handle:
        handle.write(b'{"channel":"text","text":"unfinished')
    result = journal.read_stream_delta(tmp_path, writer.row["id"], active=False, run_attempt=1, stage="sft")
    assert result["events"] == [{"channel": "text", "text": "first"}]
    assert result["done"] and result["truncated"] and result["status"] == "interrupted"
    assert result["next_offset"] < path.stat().st_size
    assert not (tmp_path / "checkpoints").exists()


def test_full_log_not_found_in_other_run_or_selected_stage(tmp_path):
    first, second = tmp_path / "first", tmp_path / "second"
    writer = start(first, text="confidential source")
    start(second, text="different source")
    with pytest.raises(ValueError, match="^model_stream_request_not_found$"):
        journal.read_stream_delta(second, writer.row["id"], active=False, run_attempt=1, stage="sft")
    with pytest.raises(ValueError, match="^model_stream_request_not_found$"):
        journal.read_stream_delta(first, writer.row["id"], active=False, run_attempt=1, stage="cot")


@pytest.mark.parametrize("options", [{"offset": -1}, {"offset": True}, {"offset": 1},
    {"offset": 10**50}, {"limit_bytes": 1}, {"limit_bytes": True}, {"limit_bytes": 65537}])
def test_invalid_cursor_cannot_force_prefix_scans_or_unbounded_reads(tmp_path, options):
    writer = start(tmp_path)
    with pytest.raises(ValueError, match="^invalid_model_stream_cursor$"):
        page(tmp_path, writer, **options)


@pytest.mark.parametrize("request_id", ["../outside", "a" * 31, ["a" * 32]])
def test_request_identifier_is_validated_before_file_access(tmp_path, request_id):
    with pytest.raises(ValueError, match="^invalid_model_stream_request$"):
        journal.read_stream_delta(tmp_path, request_id, active=False, run_attempt=1)


def test_log_hardlink_and_forged_header_are_rejected_after_warm_preview_cache(tmp_path):
    writer = start(tmp_path)
    page(tmp_path, writer)
    path = journal._delta_path(writer.directory, writer.row)
    alias = tmp_path / "alias.jsonl"
    os.link(path, alias)
    with pytest.raises(ValueError, match="^model_stream_storage_invalid$"):
        page(tmp_path, writer)
    alias.unlink()
    original = path.read_bytes()
    _, rest = original.split(b"\n", 1)
    path.write_bytes(journal._line({"version": 1, "id": "f" * 32, "stage": "sft"}) + rest)
    with pytest.raises(ValueError, match="^model_stream_storage_invalid$"):
        page(tmp_path, writer)


def test_log_symlink_is_rejected_without_reading_external_body(tmp_path):
    writer = start(tmp_path)
    path = journal._delta_path(writer.directory, writer.row)
    outside = tmp_path / "outside.jsonl"
    outside.write_text("private outside body", encoding="utf-8")
    path.unlink()
    try:
        path.symlink_to(outside)
    except OSError:
        pytest.skip("This host does not grant file symlink creation")
    with pytest.raises(ValueError, match="^model_stream_storage_invalid$"):
        page(tmp_path, writer)
    assert outside.read_text(encoding="utf-8") == "private outside body"


def test_retention_deletes_only_expired_node_logs(tmp_path):
    first = start(tmp_path, stage="sft", unit="keep")
    stale = start(tmp_path, stage="preference", unit="expire")
    for i in range(32):
        start(tmp_path, stage="preference", unit=str(i))
    assert journal._delta_path(first.directory, first.row).is_file()
    assert not journal._delta_path(stale.directory, stale.row).exists()
    assert page(tmp_path, first)["events"] == [{"channel": "text", "text": "first"}]
    with pytest.raises(ValueError, match="^model_stream_request_not_found$"):
        page(tmp_path, stale)


def test_application_delegates_selected_delta_and_honors_actual_worker_lock(tmp_path):
    output = tmp_path / "output"
    run_id = engine.create_run(output, brief="Write maintenance examples", targets=["sft"])
    run = engine.run_path(output, run_id)
    state = engine.read_json(run / "state.json")
    state["attempt"] = 1
    atomic_json(run / "state.json", state)
    writer = start(run, finish=False)
    app = WorkflowApplication(FilesystemWorkflowDriver(tmp_path, output))
    try:
        with FileLock(str(run / ".run.lock"), timeout=0):
            result = app.read_stream_delta(run_id, writer.row["id"], stage="sft")
            assert result["status"] == "active" and not result["done"]
        result = app.read_stream_delta(run_id, writer.row["id"], stage="sft")
        assert result["status"] == "interrupted" and result["done"]
        with pytest.raises(ValueError):
            app.read_stream_delta("../outside", writer.row["id"])
    finally:
        writer.interrupted()


def test_partial_write_failure_retries_only_unwritten_suffix(tmp_path):
    writer = start(tmp_path, finish=False)
    original = writer._log
    class InterruptedFile:
        calls = 0
        def __getattr__(self, name):
            return getattr(original, name)
        def write(self, data):
            self.calls += 1
            if self.calls == 1:
                return original.write(data[:7])
            if self.calls == 2:
                raise OSError("offline write interruption")
            return original.write(data)
    writer._log = InterruptedFile()
    writer._append_delta("text", " suffix")
    with pytest.raises(OSError, match="offline write interruption"):
        writer({"type": "response_completed"})
    writer.interrupted()
    result = page(tmp_path, writer)
    assert "".join(event["text"] for event in result["events"]) == "first suffix"
    assert result["status"] == "interrupted" and result["done"]


def test_fsync_failure_does_not_append_same_tokens_again(tmp_path, monkeypatch):
    writer = start(tmp_path, finish=False)
    writer._append_delta("text", " suffix")
    original, calls = journal.os.fsync, []
    def fail_once(fd):
        calls.append(fd)
        if len(calls) == 1:
            raise OSError("offline fsync interruption")
        return original(fd)
    monkeypatch.setattr(journal.os, "fsync", fail_once)
    with pytest.raises(OSError, match="offline fsync interruption"):
        writer({"type": "response_completed"})
    writer.interrupted()
    result = page(tmp_path, writer)
    assert "".join(event["text"] for event in result["events"]) == "first suffix"
    assert result["done"]
