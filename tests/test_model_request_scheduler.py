"""Offline admission tests across processes, streams, retries and cancellation."""
from concurrent.futures import ThreadPoolExecutor
import json
import multiprocessing
from pathlib import Path
import threading
import time
from types import SimpleNamespace

import pytest
import yaml

from lib.application.backend_service import BackendApplication
from lib.domain.model_scheduling import validate_model_scheduling
from lib.infrastructure.backend_config_driver import FilesystemBackendConfigDriver
from lib.llm_client import BudgetGuard, ChatClient, load_backend
from lib.model_request_reliability import ModelRequestError
from lib.model_request_scheduler import (ModelQueueTimeout, SharedModelScheduler,
                                         model_pool_identity)
from filelock import FileLock


@pytest.fixture(scope="session", autouse=True)
def mock_llm_server():
    # This suite never sends HTTP requests; do not claim the shared test port.
    yield None


IDENTITY = model_pool_identity("https://example.invalid/v1", "PRIVATE", "model")


def scheduler(root, limit=1, timeout=2):
    return SharedModelScheduler(root, IDENTITY, policy={"max_concurrency": limit,
                               "request_queue_timeout_seconds": timeout})


def eventually(check, timeout=6):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if check():
            return
        time.sleep(.025)
    assert check()


def _process_call(root, ready, gate, current, peak):
    item = scheduler(Path(root), limit=2)
    ready.put("ready")
    gate.wait(10)
    with item.acquire():
        with current.get_lock(), peak.get_lock():
            current.value += 1
            peak.value = max(peak.value, current.value)
        time.sleep(.25)
        with current.get_lock():
            current.value -= 1


def _process_hold(root, entered):
    with scheduler(Path(root)).acquire():
        entered.set()
        time.sleep(60)


def test_identity_shares_roles_aliases_protocols_but_not_account_or_model():
    assert model_pool_identity("https://EXAMPLE.invalid:443/v1/", "PRIVATE", "model") == IDENTITY
    assert model_pool_identity("https://example.invalid/v1", "other", "model") != IDENTITY
    assert model_pool_identity("https://example.invalid/v1", "PRIVATE", "other") != IDENTITY


@pytest.mark.parametrize("policy,code", [
    ({"max_concurrency": 0}, "invalid_model_max_concurrency"),
    ({"max_concurrency": 257}, "invalid_model_max_concurrency"),
    ({"max_concurrency": True}, "invalid_model_max_concurrency"),
    ({"request_queue_timeout_seconds": 0}, "invalid_model_queue_timeout"),
    ({"request_queue_timeout_seconds": 3601}, "invalid_model_queue_timeout"),
])
def test_limit_validation(policy, code):
    with pytest.raises(ValueError, match=code):
        validate_model_scheduling(policy)


def test_global_limit_across_subprocesses(tmp_path):
    context = multiprocessing.get_context("spawn")
    ready, gate = context.Queue(), context.Event()
    current, peak = context.Value("i", 0), context.Value("i", 0)
    workers = [context.Process(target=_process_call, args=(str(tmp_path), ready, gate, current, peak))
               for _ in range(4)]
    for worker in workers:
        worker.start()
    try:
        assert [ready.get(timeout=20) for _ in workers] == ["ready"] * 4
        gate.set()
        for worker in workers:
            worker.join(20)
            assert worker.exitcode == 0
        assert peak.value == 2 and current.value == 0
        assert scheduler(tmp_path).snapshot()["active"] == 0
    finally:
        for worker in workers:
            if worker.is_alive():
                worker.terminate()
                worker.join(5)


def test_fifo_and_exception_release(tmp_path):
    item, order = scheduler(tmp_path), []
    def request(index):
        with item.acquire():
            order.append(index)
            if index == 1:
                raise RuntimeError("provider failed")
    with ThreadPoolExecutor(max_workers=3) as workers:
        with item.acquire():
            futures = []
            for index in (1, 2, 3):
                futures.append(workers.submit(request, index))
                eventually(lambda: item.snapshot()["queued"] == index)
        with pytest.raises(RuntimeError):
            futures[0].result(5)
        for future in futures[1:]:
            future.result(5)
    assert order == [1, 2, 3]
    assert item.snapshot()["active"] == item.snapshot()["queued"] == 0


def test_killed_process_releases_slot_without_expiring_healthy_stream(tmp_path):
    context, item = multiprocessing.get_context("spawn"), scheduler(tmp_path)
    entered = context.Event()
    child = context.Process(target=_process_hold, args=(str(tmp_path), entered))
    child.start()
    try:
        assert entered.wait(20)
        assert item.snapshot()["active"] == 1
        child.terminate()
        child.join(5)
        with item.acquire():
            assert item.snapshot()["active"] == 1
        assert item.snapshot()["active"] == 0
    finally:
        if child.is_alive():
            child.terminate()
            child.join(5)


def test_queue_timeout_does_not_leave_entry(tmp_path):
    item = scheduler(tmp_path, timeout=1)
    with item.acquire():
        before = time.monotonic()
        with pytest.raises(ModelQueueTimeout):
            with item.acquire():
                pytest.fail("No second slot")
        assert 1 <= time.monotonic() - before < 2
        assert item.snapshot()["queued"] == 0
    assert item.snapshot()["active"] == 0


class Cancelled(RuntimeError):
    pass


def offline_client(tmp_path, item, cancel_check=None):
    return ChatClient("https://example.invalid/v1", "PRIVATE", "model", scheduler=item,
                      cancel_check=cancel_check, context_window_tokens=100,
                      budget=BudgetGuard(tmp_path / "budget", 10),
                      price_input_per_1m=1, price_output_per_1m=1)


def test_waiting_cancel_preserves_original_exception_and_reserves_no_budget(tmp_path):
    item, stop = scheduler(tmp_path), threading.Event()
    def check():
        if stop.is_set():
            raise Cancelled("stop")
    client = offline_client(tmp_path, item, check)
    client._request = lambda *a, **kw: pytest.fail("Do not send after waiting cancellation")
    with ThreadPoolExecutor(max_workers=1) as workers:
        with item.acquire():
            future = workers.submit(client.chat, [{"role": "user", "content": "x"}], max_tokens=1)
            eventually(lambda: item.snapshot()["queued"] == 1)
            stop.set()
            with pytest.raises(Cancelled):
                future.result(2)
            assert item.snapshot()["queued"] == 0
            assert not client.budget.path.exists()


def test_queue_timeout_is_transient_without_paid_retry_or_budget_reservation(tmp_path):
    item = scheduler(tmp_path, timeout=1)
    client = offline_client(tmp_path, item)
    client._request = lambda *a, **kw: pytest.fail("Admission failed before API call")
    with item.acquire(), pytest.raises(ModelRequestError) as caught:
        client.chat([{"role": "user", "content": "x"}], max_tokens=1)
    assert caught.value.code == "model_request_queue_timeout"
    assert caught.value.retryable and caught.value.attempts == 0
    assert not client.budget.path.exists()


def test_stream_keeps_slot_until_last_delta_and_accounting(tmp_path):
    item, entered, finish = scheduler(tmp_path), threading.Event(), threading.Event()
    first, second = offline_client(tmp_path, item), offline_client(tmp_path, item)
    def stream(*a, **kw):
        kw["on_stream"]({"type": "delta", "channel": "text", "text": "x"})
        entered.set()
        assert finish.wait(5)
        kw["on_stream"]({"type": "response_completed", "prompt_tokens": 1, "completion_tokens": 1})
        return "first", 1, 1
    first._request = stream
    second._request = lambda *a, **kw: ("second", 1, 1)
    events = []
    with ThreadPoolExecutor(max_workers=2) as workers:
        one = workers.submit(first.chat, [{"role": "user", "content": "x"}], max_tokens=1,
                             on_stream=events.append)
        assert entered.wait(5)
        two = workers.submit(second.chat, [{"role": "user", "content": "x"}], max_tokens=1)
        eventually(lambda: item.snapshot()["queued"] == 1)
        assert not two.done() and item.snapshot()["active"] == 1
        finish.set()
        assert one.result(5) == "first" and two.result(5) == "second"
    assert json.loads(first.budget.path.read_text())["reservations"] == {}
    assert item.snapshot()["active"] == 0


def test_final_429_publishes_shared_cooldown_and_releases_budget(tmp_path):
    item = scheduler(tmp_path)
    client = offline_client(tmp_path, item)
    failure = RuntimeError("PRIVATE provider body")
    failure.status_code = 429
    failure.response = SimpleNamespace(headers={"Retry-After": ".4"})
    client._request = lambda *a, **kw: (_ for _ in ()).throw(failure)
    with pytest.raises(ModelRequestError) as caught:
        client.chat([{"role": "user", "content": "x"}], max_tokens=1, retries=1)
    assert caught.value.code == "service_rate_limited"
    peer = scheduler(tmp_path)
    state = peer.snapshot()
    assert state["active"] == state["queued"] == 0 and state["cooldown_seconds"] > 0
    before = time.monotonic()
    with peer.acquire():
        assert time.monotonic() - before >= .25
    assert json.loads(client.budget.path.read_text())["spent_usd"] == 0


def write_config(root, endpoints):
    path = root / "configs" / "backends.local.yaml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump({"backends": endpoints}), encoding="utf-8")


def endpoint(limit=None):
    value = {"base_url": "https://example.invalid/v1", "api_key": "PRIVATE", "models": ["model"]}
    if limit is not None:
        value["model_capabilities"] = {"model": {"max_concurrency": limit}}
    return value


def test_runtime_configuration_is_shared_and_old_client_cannot_reset_new_limit(tmp_path):
    write_config(tmp_path, {"one": endpoint(2), "alias": endpoint()})
    one, _ = load_backend(tmp_path, backend="one", model="model", allow_global_endpoint_override=False)
    two, _ = load_backend(tmp_path, backend="alias", model="model", allow_global_endpoint_override=False)
    assert one.scheduler.directory == two.scheduler.directory
    assert two.scheduler.snapshot()["max_concurrency"] == 2
    app = BackendApplication(FilesystemBackendConfigDriver(tmp_path))
    app.save_model_scheduling("one", "model", max_concurrency=1, request_queue_timeout_seconds=9)
    assert one.scheduler.snapshot()["max_concurrency"] == 1
    assert one.scheduler.snapshot()["request_queue_timeout_seconds"] == 9
    # A client created before the edit only acquires; it never re-writes policy.
    with two.scheduler.acquire():
        assert one.scheduler.snapshot()["active"] == 1
    assert app.get_model_info("one", "model")["max_concurrency"] == 1
    with pytest.raises(ValueError, match="invalid_model_max_concurrency"):
        app.save_model_scheduling("one", "model", max_concurrency=0)


def test_explicit_alias_conflicts_use_conservative_limit_and_secrets_never_persist(tmp_path):
    write_config(tmp_path, {"one": endpoint(4), "alias": {**endpoint(2), "api_format": "responses"}})
    client, _ = load_backend(tmp_path, backend="one", model="model", allow_global_endpoint_override=False)
    assert client.scheduler.snapshot()["max_concurrency"] == 2
    state = client.scheduler.path.read_text(encoding="utf-8")
    assert all(text not in state for text in ("PRIVATE", "example.invalid", "model"))


def test_lowering_limit_keeps_live_slots_and_blocks_new_calls(tmp_path):
    write_config(tmp_path, {"one": endpoint(2)})
    client, _ = load_backend(tmp_path, backend="one", model="model", allow_global_endpoint_override=False)
    item = client.scheduler
    with item.acquire(), item.acquire():
        app = BackendApplication(FilesystemBackendConfigDriver(tmp_path))
        assert app.save_model_scheduling("one", "model", max_concurrency=1)["active"] == 2
        assert item.snapshot()["active"] == 2
        # No live stream is expired or forcibly cancelled to reduce the limit.
        assert item.snapshot()["max_concurrency"] == 1
    assert item.snapshot()["active"] == 0


def test_backoff_cancel_releases_slot_and_reservations_before_wait(tmp_path, monkeypatch):
    from lib import llm_client
    item, stop, in_backoff = scheduler(tmp_path), threading.Event(), threading.Event()
    def check():
        if stop.is_set():
            raise Cancelled("stop")
    client = offline_client(tmp_path, item, check)
    calls = []
    def fail(*a, **kw):
        calls.append(1)
        raise TimeoutError("PRIVATE")
    client._request = fail
    monkeypatch.setattr(llm_client, "retry_delay", lambda *a: 2.)
    from lib import model_request_reliability
    original = model_request_reliability.cancelable_wait
    def wait(seconds, cancel_check):
        assert item.snapshot()["active"] == 0
        assert json.loads(client.budget.path.read_text())["reservations"] == {}
        in_backoff.set()
        return original(seconds, cancel_check)
    monkeypatch.setattr(model_request_reliability, "cancelable_wait", wait)
    with ThreadPoolExecutor(max_workers=1) as workers:
        future = workers.submit(client.chat, [{"role": "user", "content": "x"}], max_tokens=1)
        assert in_backoff.wait(5)
        stop.set()
        with pytest.raises(Cancelled):
            future.result(2)
    assert len(calls) == 1


def test_corrupt_scheduler_state_fails_closed(tmp_path):
    item = scheduler(tmp_path)
    item.path.write_text('{"active": ["invented"]}', encoding="utf-8")
    with pytest.raises(ValueError, match="invalid_model_scheduler_state"):
        with item.acquire():
            pytest.fail("Do not reset or exceed a damaged capacity ledger")
    assert item.path.read_text() == '{"active": ["invented"]}'


@pytest.mark.parametrize("protocol", ["chat", "responses", "anthropic"])
def test_sdk_has_explicit_connect_and_read_inactivity_timeout(protocol):
    client = ChatClient("https://example.invalid/v1", "PRIVATE", "model", api_format=protocol)
    assert client.client.max_retries == 0
    assert client.client.timeout.connect == 15
    assert client.client.timeout.read == 600
    assert client.client.timeout.write == 60
    assert client.client.timeout.pool == 15


def test_housekeeping_removes_crash_orphans_and_preserves_live_unregistered_lease(tmp_path):
    item = scheduler(tmp_path)
    stale, live_token = "a" * 32, "b" * 32
    item._lease_path(stale).touch()
    temporary = item.directory / ".pending-crash.json"
    temporary.write_text("{}")
    unrelated = item.directory / "operator-notes.txt"
    unrelated.write_text("keep")
    live_lock = FileLock(str(item._lease_path(live_token)), timeout=0)
    with live_lock, item.acquire():
        assert not item._lease_path(stale).exists()
        assert not temporary.exists()
        assert item._lease_path(live_token).exists()
        assert unrelated.exists()
    item._next_housekeeping = 0
    item.snapshot()
    assert not item._lease_path(live_token).exists()


def test_finished_calls_do_not_accumulate_files_or_state(tmp_path):
    item = scheduler(tmp_path)
    for _ in range(100):
        with item.acquire():
            pass
    files = {path.name for path in item.directory.iterdir()}
    assert files <= {"state.json", "state.json.lock"} and "state.json" in files
    assert item.path.stat().st_size < 512
    assert item.snapshot()["active"] == item.snapshot()["queued"] == 0


def test_cooldown_uses_shared_monotonic_time_during_wall_clock_jumps(tmp_path, monkeypatch):
    from lib import model_request_scheduler as module
    clocks = {"mono": 1000., "wall": 100_000.}
    monkeypatch.setattr(module.time, "monotonic", lambda: clocks["mono"])
    monkeypatch.setattr(module.time, "time", lambda: clocks["wall"])
    item = scheduler(tmp_path)
    item.defer(30)
    clocks.update(mono=1005., wall=1_000_000.)
    assert item.snapshot()["cooldown_seconds"] == 25
    clocks.update(mono=1010., wall=1.)
    assert scheduler(tmp_path).snapshot()["cooldown_seconds"] == 20
    clocks["mono"] = 1031.
    assert item.snapshot()["cooldown_seconds"] == 0


def test_cooldown_clock_reset_rebases_once_and_bounds_future_wall_deadline(tmp_path, monkeypatch):
    from lib import model_request_scheduler as module
    clocks = {"mono": 1000., "wall": 100_000.}
    monkeypatch.setattr(module.time, "monotonic", lambda: clocks["mono"])
    monkeypatch.setattr(module.time, "time", lambda: clocks["wall"])
    item = scheduler(tmp_path)
    item.defer(30)
    clocks.update(mono=1., wall=1.)
    assert item.snapshot()["cooldown_seconds"] == 60
    clocks["mono"] = 11.
    assert scheduler(tmp_path).snapshot()["cooldown_seconds"] == 50


def test_legacy_wall_only_cooldown_migrates_without_resetting_active_ledger(tmp_path, monkeypatch):
    from lib import model_request_scheduler as module
    item = scheduler(tmp_path)
    item.path.write_text(json.dumps({"version": 1, "max_concurrency": 1,
        "request_queue_timeout_seconds": 1, "active": [], "waiting": [], "cooldown_until": 1_000_000.}))
    clocks = {"mono": 10., "wall": 10.}
    monkeypatch.setattr(module.time, "monotonic", lambda: clocks["mono"])
    monkeypatch.setattr(module.time, "time", lambda: clocks["wall"])
    assert item.snapshot()["cooldown_seconds"] == 60
    clocks["mono"] = 20.
    assert item.snapshot()["cooldown_seconds"] == 50


def test_expired_waiter_does_not_start_when_capacity_later_becomes_free(tmp_path):
    item, waiting, resume = scheduler(tmp_path, timeout=1), threading.Event(), threading.Event()
    def on_wait(info):
        waiting.set()
        assert resume.wait(5)
    def request():
        with item.acquire(on_wait=on_wait):
            pytest.fail("Queue deadline expired before provider admission")
    with ThreadPoolExecutor(max_workers=1) as workers:
        with item.acquire():
            future = workers.submit(request)
            assert waiting.wait(5)
            time.sleep(1.1)
        resume.set()
        with pytest.raises(ModelQueueTimeout):
            future.result(5)
    assert item.snapshot()["active"] == item.snapshot()["queued"] == 0
