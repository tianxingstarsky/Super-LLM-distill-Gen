"""Fast node polling keeps the same file and run isolation checks as cold reads."""
from pathlib import Path
import json
import os
from types import SimpleNamespace

import pytest

from lib.infrastructure import workflow_stream_journal as journal
from lib.io_utils import atomic_json


@pytest.fixture(autouse=True)
def empty_preview_cache():
    with journal._CACHE_LOCK:
        journal._CACHE.clear()
        journal._CACHE_SIZE = 0
    yield
    with journal._CACHE_LOCK:
        journal._CACHE.clear()
        journal._CACHE_SIZE = 0


def start(run, stage="sft", unit="sample", text="output", *, finish=True):
    run.mkdir(exist_ok=True, parents=True)
    writer = journal.StreamJournal(run, stage=stage, unit=unit, role="generation", run_attempt=1)
    writer({"type": "start"})
    writer({"type": "delta", "channel": "text", "text": text})
    if finish:
        writer.completed()
    return writer


def test_per_node_retention_survives_large_later_node_and_global_remains_32(tmp_path):
    for i in range(37):
        start(tmp_path, "sft", str(i))
    for i in range(45):
        start(tmp_path, "preference", str(i))
    first = journal.read_streams(tmp_path, active=True, run_attempt=1, stage="sft")
    later = journal.read_streams(tmp_path, active=True, run_attempt=1, stage="preference")
    assert len(first) == len(later) == 32
    assert {int(row["unit"]) for row in first} == set(range(5, 37))
    assert {int(row["unit"]) for row in later} == set(range(13, 45))
    global_rows = journal.read_streams(tmp_path, active=True, run_attempt=1)
    assert len(global_rows) == 32 and {row["stage"] for row in global_rows} == {"preference"}
    active = start(tmp_path, "sft", "active", finish=False)
    for i in range(4):
        start(tmp_path, "preference", f"later-{i}")
    assert journal.read_streams(tmp_path, active=True, run_attempt=1, stage="sft")[0]["id"] == active.row["id"]
    assert active._path().is_file()


def test_warm_poll_reuses_body_without_reading_other_nodes(tmp_path, monkeypatch):
    for stage in ("sft", "preference", "cot"):
        for i in range(8):
            start(tmp_path, stage, str(i), "x" * journal.TEXT_LIMIT)
    # Flush the writer's own cache to measure a genuine browser cold read.
    journal._CACHE.clear()
    journal._CACHE_SIZE = 0
    checked, payloads = [], []
    original = journal._checked_read
    def measured(path, limit, known=None):
        result = original(path, limit, known)
        checked.append(path.name)
        if result[0] is not None:
            payloads.append(len(result[0]))
        return result
    monkeypatch.setattr(journal, "_checked_read", measured)
    rows = journal.read_streams(tmp_path, active=True, run_attempt=1, stage="sft")
    assert len(rows) == 8 and len(payloads) == 8
    assert all(name.startswith("sft.") for name in checked)
    checked.clear()
    payloads.clear()
    for _ in range(12):
        assert len(journal.read_streams(tmp_path, active=True, run_attempt=1, stage="sft")) == 8
    assert len(checked) == 96  # Every cache hit still verifies the open FD.
    assert not payloads
    assert all(name.startswith("sft.") for name in checked)


def test_legacy_filename_and_started_at_fallback_remain_readable(tmp_path):
    writer = start(tmp_path, text="legacy")
    path = writer._path()
    row = json.loads(path.read_text(encoding="utf-8"))
    row.pop("started_at")
    old_path = path.with_name(f"{row['id']}.{row['status']}.json")
    atomic_json(old_path, row)
    path.unlink()
    rows = journal.read_streams(tmp_path, active=False, run_attempt=1, stage="sft")
    assert rows[0]["text"] == "legacy" and rows[0]["started_at"] == rows[0]["updated_at"]


def test_fifty_ms_flush_coalesces_large_bursts_and_preserves_request_start(tmp_path, monkeypatch):
    clock = [0.0]
    monkeypatch.setattr(journal, "_clock", lambda: clock[0])
    class Timer:
        def __init__(self, delay, callback):
            self.delay, self.callback = delay, callback
        def start(self):
            pass
        def cancel(self):
            pass
    monkeypatch.setattr(journal.threading, "Timer", Timer)
    writer = start(tmp_path, text="first", finish=False)
    initial = journal.read_streams(tmp_path, active=True, run_attempt=1)[0]
    started_at = initial["started_at"]
    for i in range(40):
        clock[0] = i / 1000
        writer({"type": "delta", "channel": "text", "text": "x" * 2000})
    assert journal.read_streams(tmp_path, active=True, run_attempt=1)[0]["text_chars"] == 5
    clock[0] = 0.051
    writer({"type": "delta", "channel": "text", "text": "now"})
    row = journal.read_streams(tmp_path, active=True, run_attempt=1)[0]
    assert row["text_chars"] == 80008 and row["started_at"] == started_at
    # A terminal event flushes its remaining delta immediately, without waiting.
    clock[0] = 0.052
    writer({"type": "delta", "channel": "reasoning", "text": "pending reasoning"})
    writer.completed()
    row = journal.read_streams(tmp_path, active=True, run_attempt=1)[0]
    assert row["reasoning"] == "pending reasoning" and row["status"] == "completed"
    assert row["started_at"] == started_at


def test_atomic_replacement_and_same_inode_edit_invalidate_warm_cache(tmp_path):
    writer = start(tmp_path, text="original")
    row = journal.read_streams(tmp_path, active=False, run_attempt=1)[0]
    path = writer._path()
    data = json.loads(path.read_text(encoding="utf-8"))
    data["text"] = "replaced"
    old_time = path.stat().st_mtime_ns
    atomic_json(path, data)
    os.utime(path, ns=(old_time, old_time))
    assert journal.read_streams(tmp_path, active=False, run_attempt=1)[0]["text"] == "replaced"
    data["text"] = "in-place"
    path.write_text(json.dumps(data), encoding="utf-8")
    os.utime(path, ns=(old_time + 1000000, old_time + 1000000))
    assert journal.read_streams(tmp_path, active=False, run_attempt=1)[0]["text"] == "in-place"
    assert row["text"] == "original"


def test_warm_cache_never_masks_hardlink_or_missing_file(tmp_path):
    writer = start(tmp_path, text="private")
    journal.read_streams(tmp_path, active=False, run_attempt=1)
    path = writer._path()
    alias = tmp_path / "alias.json"
    os.link(path, alias)
    with pytest.raises(ValueError, match="model_stream_storage_invalid"):
        journal.read_streams(tmp_path, active=False, run_attempt=1)
    alias.unlink()
    path.unlink()
    assert journal.read_streams(tmp_path, active=False, run_attempt=1) == []


def test_same_request_id_in_different_runs_and_caller_mutation_are_isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(journal.uuid, "uuid4", lambda: SimpleNamespace(hex="a" * 32))
    first, second = tmp_path / "run-one", tmp_path / "run-two"
    start(first, text="first confidential source")
    start(second, text="second source")
    row = journal.read_streams(first, active=False, run_attempt=1)[0]
    row["text"] = "changed by presentation"
    row["text_chars"] = 0
    assert journal.read_streams(first, active=False, run_attempt=1)[0]["text"] == "first confidential source"
    assert journal.read_streams(second, active=False, run_attempt=1)[0]["text"] == "second source"
    assert journal.read_streams(first, active=False, run_attempt=1, stage="cot") == []


def test_identity_race_during_open_reloads_current_file_instead_of_stale_cache(tmp_path, monkeypatch):
    writer = start(tmp_path, text="old")
    journal.read_streams(tmp_path, active=False, run_attempt=1)
    path, swapped = writer._path(), []
    data = json.loads(path.read_text(encoding="utf-8"))
    data["text"] = "new"
    original = journal.os.open
    def replace_before_open(name, *args, **kwargs):
        if Path(name) == path and not swapped:
            swapped.append(True)
            atomic_json(path, data)
        return original(name, *args, **kwargs)
    monkeypatch.setattr(journal.os, "open", replace_before_open)
    assert journal.read_streams(tmp_path, active=False, run_attempt=1)[0]["text"] == "new"
    assert swapped


def test_cache_budget_is_bounded_across_many_runs(tmp_path, monkeypatch):
    monkeypatch.setattr(journal, "CACHE_ENTRIES", 3)
    monkeypatch.setattr(journal, "CACHE_BYTES", 4000)
    for i in range(12):
        path = tmp_path / f"run-{i}"
        start(path, text="x" * 1000)
        assert len(journal.read_streams(path, active=False, run_attempt=1)) == 1
        assert len(journal._CACHE) <= 3 and journal._CACHE_SIZE <= 4000


def test_nested_checkpoint_cannot_understate_cache_memory_budget(tmp_path):
    writer = start(tmp_path)
    path = writer._path()
    row = json.loads(path.read_text(encoding="utf-8"))
    row["checkpoint"] = [[] for _ in range(20000)]
    atomic_json(path, row)
    with pytest.raises(ValueError, match="^model_stream_storage_invalid$"):
        journal.read_streams(tmp_path, active=False, run_attempt=1)


def test_last_delta_is_flushed_at_timer_even_if_provider_pauses(tmp_path, monkeypatch):
    clock, timers = [0.0], []
    monkeypatch.setattr(journal, "_clock", lambda: clock[0])
    class Timer:
        def __init__(self, delay, callback):
            self.delay, self.callback, self.cancelled = delay, callback, False
            timers.append(self)
        def start(self):
            pass
        def cancel(self):
            self.cancelled = True
    monkeypatch.setattr(journal.threading, "Timer", Timer)
    writer = start(tmp_path, text="first", finish=False)
    clock[0] = 0.02
    writer({"type": "delta", "channel": "text", "text": " last before pause"})
    assert len(timers) == 1 and timers[0].delay == pytest.approx(0.03)
    assert journal.read_streams(tmp_path, active=True, run_attempt=1)[0]["text"] == "first"
    clock[0] = 0.051
    timers[0].callback()
    assert journal.read_streams(tmp_path, active=True, run_attempt=1)[0]["text"] == "first last before pause"
    writer.interrupted()


@pytest.mark.parametrize("stage", ["../sft", "not-a-stage", ["sft"]])
def test_stage_filter_rejects_invalid_values(tmp_path, stage):
    with pytest.raises(ValueError, match="^invalid_workflow_stream_stage$"):
        journal.read_streams(tmp_path, active=False, run_attempt=1, stage=stage)
