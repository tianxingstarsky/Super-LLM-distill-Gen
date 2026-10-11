"""Completed stage rows become readable before the rest of a batch finishes."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import json
import threading
import time

import pytest

from lib.infrastructure import training_workflow as engine


@pytest.fixture(scope="session", autouse=True)
def mock_llm_server():
    yield


def workflow(tmp_path, *, concurrency=2, batch_size=2):
    source = tmp_path / "source.txt"
    source.write_text("维护前先断电，再检查线路。", encoding="utf-8")
    output = tmp_path / "output"
    run_id = engine.create_run(output, sources=[source], targets=["sft"], concurrency=concurrency, batch_size=batch_size)
    return engine.Workflow(output, run_id, tmp_path)


def wait_rows(path, count):
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline:
        if path.exists():
            rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
            if len(rows) == count:
                return rows
        time.sleep(.01)
    raise AssertionError("The completed prefix was not flushed before the slow item finished.")


def test_stage_flushes_each_completed_prefix_without_waiting_for_whole_batch(tmp_path):
    work = workflow(tmp_path)
    release = threading.Event()
    items = [{"id": "first"}, {"id": "second"}]
    def action(item):
        if item["id"] == "second":
            assert release.wait(5)
        return [{**item, "status": "eligible", "text": item["id"]}]
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(work.stage_items, "sft", items, action)
        try:
            rows = wait_rows(work.path / "stage-results" / ".sft.pending", 1)
            assert [row["id"] for row in rows] == ["first"]
            assert not future.done()
        finally:
            release.set()
        assert [row["id"] for row in future.result()] == ["first", "second"]


def test_cancelled_stage_keeps_flushed_rows_and_resume_reuses_their_checkpoints(tmp_path):
    work = workflow(tmp_path)
    release = threading.Event()
    calls = []
    items = [{"id": "first"}, {"id": "second"}]
    def action(item):
        calls.append(item["id"])
        if item["id"] == "second":
            assert release.wait(5)
            work.check_cancel()
        return [{**item, "status": "eligible", "text": item["id"]}]
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(work.stage_items, "sft", items, action)
        try:
            wait_rows(work.path / "stage-results" / ".sft.pending", 1)
            engine.atomic_json(work.path / "cancel.json", {"cancelled": True})
        finally:
            release.set()
        with pytest.raises(engine.Cancelled):
            future.result()
    assert [row["id"] for row in wait_rows(work.path / "stage-results" / ".sft.pending", 1)] == ["first"]
    (work.path / "cancel.json").unlink()
    rows = work.stage_items("sft", items, action)
    assert [row["id"] for row in rows] == ["first", "second"]
    assert calls.count("first") == 1


def test_completion_journal_shows_later_items_while_first_item_is_still_running(tmp_path):
    work = workflow(tmp_path, concurrency=3, batch_size=3)
    release = threading.Event()
    items = [{"id": "first"}, {"id": "second"}, {"id": "third"}]
    def action(item):
        if item["id"] == "first":
            assert release.wait(5)
        return [{**item, "status": "eligible", "text": item["id"]}]
    journal = work.path / "stage-results" / ".sft.live.jsonl"
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(work.stage_items, "sft", items, action)
        try:
            rows = wait_rows(journal, 2)
            assert {row["item_index"] for row in rows} == {1, 2}
            assert {row["record"]["id"] for row in rows} == {"second", "third"}
            assert not future.done()
            assert (work.path / "stage-results" / ".sft.pending").read_text(encoding="utf-8") == ""
        finally:
            release.set()
        assert [row["id"] for row in future.result()] == ["first", "second", "third"]
    journal_rows = wait_rows(journal, 3)
    assert journal_rows[-1]["record"]["id"] == "first"
    assert all("item_index" not in row for row in engine.WorkflowRows(work.path / "stage-results" / "sft.jsonl", 3))


def test_cancelled_slow_first_preserves_completed_tail_preview_and_resumes_without_duplicates(tmp_path):
    work = workflow(tmp_path, concurrency=3, batch_size=3)
    release = threading.Event()
    calls = []
    items = [{"id": "first"}, {"id": "second"}, {"id": "third"}]
    def action(item):
        calls.append(item["id"])
        if item["id"] == "first":
            assert release.wait(5)
            work.check_cancel()
        return [{**item, "status": "eligible", "text": item["id"]}]
    journal = work.path / "stage-results" / ".sft.live.jsonl"
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(work.stage_items, "sft", items, action)
        try:
            wait_rows(journal, 2)
            engine.atomic_json(work.path / "cancel.json", {"cancelled": True})
        finally:
            release.set()
        with pytest.raises(engine.Cancelled):
            future.result()
    assert {row["record"]["id"] for row in wait_rows(journal, 2)} == {"second", "third"}
    assert (work.path / "stage-results" / ".sft.pending").read_text(encoding="utf-8") == ""
    (work.path / "cancel.json").unlink()
    assert [row["id"] for row in work.stage_items("sft", items, action)] == ["first", "second", "third"]
    assert calls.count("second") == calls.count("third") == 1
    # A stage restart replaces its preview journal, then replays committed
    # checkpoints once. Old previews are never appended as new samples.
    assert len(wait_rows(journal, 3)) == 3
