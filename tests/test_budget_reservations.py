"""Offline admission tests for the shared, priced model budget."""
from __future__ import annotations

from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
import json
from pathlib import Path
import subprocess
import sys
from threading import Barrier, Event

import pytest

from lib.llm_client import BudgetExceeded, BudgetGuard, ChatClient
from lib.infrastructure.backend_config_driver import FilesystemBackendConfigDriver


def client(root: Path) -> ChatClient:
    # Full 10-token input context at $0.05/token plus one output token at
    # $0.10/token gives a $0.60 maximum request reservation.
    return ChatClient(base_url="http://127.0.0.1:1/v1", api_key="test", model="offline",
                      budget=BudgetGuard(root, 1.0), context_window_tokens=10,
                      price_input_per_1m=50_000, price_output_per_1m=100_000)


def test_concurrent_workers_cannot_pass_a_one_request_budget(tmp_path):
    started = Barrier(3)
    release = Event()
    entered = []
    clients = [client(tmp_path), client(tmp_path)]

    def request(*args, **kwargs):
        entered.append(True)
        assert release.wait(5)
        return "ok", 1, 1

    for item in clients:
        item._request = request

    def invoke(item):
        started.wait(timeout=5)
        return item.chat([{"role": "user", "content": "x"}], max_tokens=1)

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(invoke, item) for item in clients]
        started.wait(timeout=5)
        done, _ = wait(futures, timeout=5, return_when=FIRST_COMPLETED)
        assert len(done) == 1
        with pytest.raises(BudgetExceeded):
            next(iter(done)).result()
        assert len(entered) == 1
        release.set()
        assert next(item for item in futures if item not in done).result(timeout=5) == "ok"

    state = json.loads((tmp_path / "data/output/budget.json").read_text(encoding="utf-8"))
    assert state["spent_usd"] == pytest.approx(.15)
    assert state["reservations"] == {}
    # Settlement releases the unused capacity for the next request.
    assert clients[0].chat([{"role": "user", "content": "x"}], max_tokens=1) == "ok"


def test_sufficient_budget_allows_two_requests_in_parallel(tmp_path):
    clients = [client(tmp_path), client(tmp_path)]
    for item in clients:
        item.budget.limit = 1.3
    simultaneous = Barrier(2)

    def request(*args, **kwargs):
        simultaneous.wait(timeout=5)
        return "ok", 1, 1

    for item in clients:
        item._request = request
    with ThreadPoolExecutor(max_workers=2) as pool:
        assert list(pool.map(lambda item: item.chat([{"role": "user", "content": "x"}], max_tokens=1),
                             clients)) == ["ok", "ok"]
    state = json.loads((tmp_path / "data/output/budget.json").read_text(encoding="utf-8"))
    assert state["spent_usd"] == pytest.approx(.3)
    assert state["reservations"] == {}


def test_other_process_reservation_blocks_and_crash_is_recovered(tmp_path):
    script = (
        "import os,sys\n"
        "from pathlib import Path\n"
        "from lib.llm_client import BudgetGuard\n"
        "guard=BudgetGuard(Path(sys.argv[1]),1.0)\n"
        "guard.reserve(.6)\n"
        "print('reserved',flush=True)\n"
        "sys.stdin.readline()\n"
        "os._exit(0)\n"
    )
    process = subprocess.Popen(
        [sys.executable, "-c", script, str(tmp_path)], stdin=subprocess.PIPE,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    try:
        assert process.stdout.readline().strip() == "reserved"
        guard = BudgetGuard(tmp_path, 1.0)
        with pytest.raises(BudgetExceeded):
            guard.reserve(.6)
        process.stdin.write("done\n")
        process.stdin.flush()
        assert process.wait(timeout=5) == 0
        with pytest.raises(BudgetExceeded):
            guard.reserve(.6)  # crashed calls are charged at their ceiling
        token = guard.reserve(.3)
        guard.settle(token, .1)
        state = json.loads(guard.path.read_text(encoding="utf-8"))
        assert state["reservations"] == {}
        assert state["spent_usd"] == pytest.approx(.7)
    finally:
        if process.poll() is None:
            process.terminate()
            process.wait(timeout=5)


def test_failed_request_books_unknown_usage_and_releases_reservation(tmp_path, monkeypatch):
    item = client(tmp_path)
    monkeypatch.setattr("lib.llm_client.time.sleep", lambda *_: None)
    item._request = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("offline failure"))
    with pytest.raises(RuntimeError, match="chat failed"):
        item.chat([{"role": "user", "content": "x"}], max_tokens=1, retries=1)
    state = json.loads(item.budget.path.read_text(encoding="utf-8"))
    assert state["spent_usd"] == pytest.approx(.6)
    assert state["reservations"] == {}


def test_provider_usage_over_bound_closes_future_admission(tmp_path):
    item = client(tmp_path)
    item.budget.limit = 2.0
    calls = []
    def request(*args, **kwargs):
        calls.append(True)
        return "ok", 20, 1
    item._request = request
    with pytest.raises(BudgetExceeded, match="estimate exceeded"):
        item.chat([{"role": "user", "content": "x"}], max_tokens=1)
    with pytest.raises(BudgetExceeded):
        item.chat([{"role": "user", "content": "x"}], max_tokens=1)
    assert len(calls) == 1
    state = json.loads(item.budget.path.read_text(encoding="utf-8"))
    assert state["budget_bound_exceeded"] is True
    assert state["spent_usd"] == pytest.approx(1.1)


def test_reset_and_legacy_save_reject_live_reservations(tmp_path):
    guard = BudgetGuard(tmp_path, 1.0)
    token = guard.reserve(.6)
    driver = FilesystemBackendConfigDriver(tmp_path)
    with pytest.raises(BudgetExceeded, match="budget_reset_in_flight"):
        driver.write_budget_reset("console", 1.0)
    guard.spent = 0.0
    with pytest.raises(BudgetExceeded, match="active requests"):
        guard.save()
    assert token in json.loads(guard.path.read_text(encoding="utf-8"))["reservations"]
    guard.settle(token, .1)
    assert driver.write_budget_reset("console", 1.0) == pytest.approx(.1)
    reset = json.loads(guard.path.read_text(encoding="utf-8"))
    assert reset["spent_usd"] == 0 and reset["reset_by"] == "console"


def test_legacy_add_detects_live_reservation_overrun(tmp_path):
    guard = BudgetGuard(tmp_path, 1.0)
    token = guard.reserve(.6)
    with pytest.raises(BudgetExceeded):
        guard.add_usd(.5)
    state = json.loads(guard.path.read_text(encoding="utf-8"))
    assert state["budget_bound_exceeded"] is True
    assert state["spent_usd"] == pytest.approx(.5)
    assert token in state["reservations"]
    with pytest.raises(BudgetExceeded):
        guard.settle(token, .1)
    with pytest.raises(BudgetExceeded):
        BudgetGuard(tmp_path, 1.0).reserve(.1)
