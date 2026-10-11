"""Stage concurrency replenishes promptly with bounded inputs and ordered disk output."""
from concurrent.futures import ThreadPoolExecutor
import io
import json
import threading

import pytest

from lib.infrastructure import training_workflow as engine
from lib.infrastructure.workflow_stage_order import StageResultOrder
from lib.model_request_reliability import ModelRequestError


@pytest.fixture(scope="session", autouse=True)
def mock_llm_server():
    # These checks exercise only local actions and never start a provider server.
    yield


def workflow(tmp_path, **options):
    output = tmp_path / "output"
    run_id = engine.create_run(output, brief="Generate maintenance examples", targets=["sft"], **options)
    return engine.Workflow(output, run_id, tmp_path)


class LazyItems:
    def __init__(self, count):
        self.count = count
        self.consumed = 0
        self.closed = False

    def __len__(self):
        return self.count

    def __iter__(self):
        return self

    def __next__(self):
        if self.consumed == self.count:
            raise StopIteration
        item = self.consumed
        self.consumed += 1
        return item

    def close(self):
        self.closed = True


def test_completed_requests_replenish_across_batch_while_first_item_is_slow(tmp_path, monkeypatch):
    work = workflow(tmp_path, concurrency=2, batch_size=2)
    release = threading.Event()
    tail_done = threading.Event()
    append = StageResultOrder.append

    def appended(order, index, rows, preview):
        append(order, index, rows, preview)
        if index == 4:
            tail_done.set()

    monkeypatch.setattr(StageResultOrder, "append", appended)

    def action(item):
        if item == 0:
            assert release.wait(5)
        return [{"status": "eligible", "index": item}]

    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(work.stage_items, "sft", list(range(5)), action)
        try:
            assert tail_done.wait(3), "Every free slot must advance without waiting for the slow first item."
            assert not future.done()
            assert (work.path / "stage-results/.sft.pending").read_text(encoding="utf-8") == ""
        finally:
            release.set()
        assert [row["index"] for row in future.result()] == list(range(5))
    previews = [json.loads(line) for line in
                (work.path / "stage-results/.sft.live.jsonl").read_text(encoding="utf-8").splitlines()]
    assert [row["item_index"] for row in previews] == [1, 2, 3, 4, 0]
    metrics = work.state["stages"]["sft"]
    assert metrics["batches_done"] == metrics["batches_total"] == 3
    assert not list((work.path / "stage-results").glob("*.sqlite3"))


def test_million_item_source_consumes_only_running_window_and_closes_on_cancel(tmp_path):
    work = workflow(tmp_path, concurrency=2, batch_size=500)
    items = LazyItems(1_000_000)
    started = [threading.Event(), threading.Event()]
    release = threading.Event()

    def action(item):
        started[item].set()
        assert release.wait(5)
        work.check_cancel()
        return []

    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(work.stage_items, "sft", items, action)
        try:
            assert all(event.wait(3) for event in started)
            assert items.consumed == 2
            engine.atomic_json(work.path / "cancel.json", {"cancelled": True})
        finally:
            release.set()
        with pytest.raises(engine.Cancelled):
            future.result()
    assert items.consumed == 2 and items.closed
    assert not (work.path / "stage-results/sft.jsonl").exists()
    assert not list((work.path / "stage-results").glob("*.sqlite3"))


def test_model_failure_stops_replenishment_and_preserves_original_error(tmp_path):
    work = workflow(tmp_path, concurrency=2, batch_size=500)
    items = LazyItems(1_000_000)
    first_started = threading.Event()
    failure = ModelRequestError("model_http_error", "fatal", 1)

    def action(item):
        if item == 0:
            first_started.set()
            assert work._abort.wait(3)
            work.check_cancel()
        assert first_started.wait(3)
        raise failure

    with pytest.raises(ModelRequestError) as raised:
        work.stage_items("sft", items, action)
    assert raised.value is failure
    assert items.consumed == 2 and items.closed
    assert not (work.path / "stage-results/sft.jsonl").exists()
    assert not list((work.path / "stage-results").glob("*.sqlite3"))


def test_old_item_checkpoints_are_reused_and_empty_outputs_advance_order(tmp_path):
    work = workflow(tmp_path, concurrency=3, batch_size=2)
    work.stage = "sft"
    key = ["item", 2, engine.digest(2)]
    work.checkpoint(key, lambda: [{"status": "eligible", "index": 2}])
    calls = []

    def action(item):
        calls.append(item)
        return [] if item == 0 else [{"status": "eligible", "index": item}]

    assert [row["index"] for row in work.stage_items("sft", list(range(5)), action)] == [1, 2, 3, 4]
    assert 2 not in calls and len(calls) == 4
    assert work.state["stages"]["sft"]["cached"] == 1


def test_completed_tail_is_kept_on_disk_until_prefix_can_be_published(tmp_path):
    preview, output = io.StringIO(), io.StringIO()
    with StageResultOrder(tmp_path, "sft") as order:
        spool_path = order.path
        for index in range(1, 301):
            order.append(index, [{"index": index}], preview)
            assert order.flush(output) is False
        assert output.getvalue() == ""
        assert order.connection.execute("SELECT COUNT(*) FROM rows").fetchone()[0] == 600
        assert spool_path.stat().st_size > 0
        order.append(0, [], preview)
        assert order.flush(output) is True
        assert [json.loads(line)["index"] for line in output.getvalue().splitlines()] == list(range(1, 301))
        assert order.connection.execute("SELECT COUNT(*) FROM rows").fetchone()[0] == 0
    assert not spool_path.exists()
