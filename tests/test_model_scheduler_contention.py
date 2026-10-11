"""Real local file-lock contention, fairness, cancellation and cadence checks."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack
import json
import multiprocessing
from pathlib import Path
import queue
import threading
import time

from filelock import FileLock
import pytest

from lib import model_request_scheduler as module
from lib.model_request_scheduler import SharedModelScheduler, model_pool_identity


@pytest.fixture(scope="session", autouse=True)
def mock_llm_server():
    # Every workload uses temp-directory locks and synthetic local work only.
    yield None


IDENTITY = model_pool_identity("https://contention.invalid/v1", "offline", "model")
POLICY = {"max_concurrency": 4, "request_queue_timeout_seconds": 20}


class Cancelled(RuntimeError):
    pass


class FixedCadenceScheduler(SharedModelScheduler):
    """Compare cadence using the same metadata mutex in both workloads."""

    def _prune(self, state, *, full=False, active=True):
        return super()._prune(state, full=full, active=True)

    @staticmethod
    def _poll_interval(position, limit, cooldown):
        return module.POLL_SECONDS

    def _wait(self, seconds, *, revision, cancel_check=None):
        time.sleep(min(seconds, module.POLL_SECONDS))


class Counters:
    def __init__(self):
        self.lock = threading.Lock()
        self.values = {"metadata_lock_attempts": 0, "state_reads": 0, "lease_probes": 0}

    def add(self, name):
        with self.lock:
            self.values[name] += 1

    def reset(self):
        with self.lock:
            for name in self.values:
                self.values[name] = 0

    def snapshot(self):
        with self.lock:
            return dict(self.values)


def _waiting_workload(root, base, monkeypatch, *, waiters=24, duration=1.5):
    counts, ready = Counters(), queue.Queue()
    begin, stop = threading.Event(), threading.Event()

    class MeasuredLock(FileLock):
        def acquire(self, *args, **kwargs):
            if str(self.lock_file).endswith("state.json.lock"):
                counts.add("metadata_lock_attempts")
            return super().acquire(*args, **kwargs)

    class MeasuredScheduler(base):
        def _state(self):
            counts.add("state_reads")
            return super()._state()

        def _alive(self, token):
            counts.add("lease_probes")
            return super()._alive(token)

    def check():
        if stop.is_set():
            raise Cancelled("quiet workload ended")

    def request(index):
        item = MeasuredScheduler(root, IDENTITY, policy=POLICY)
        def on_wait(info):
            ready.put(index)
            assert begin.wait(10)
        with pytest.raises(Cancelled):
            with item.acquire(cancel_check=check, on_wait=on_wait):
                pytest.fail("The four synthetic streams still own all capacity")

    # The scheduler really opens/locks files; instrumentation counts attempts
    # at the OS-lock boundary instead of inferring them from a formula.
    with monkeypatch.context() as patch:
        patch.setattr(module, "FileLock", MeasuredLock)
        holder = SharedModelScheduler(root, IDENTITY, policy=POLICY)
        with ExitStack() as stack:
            for _ in range(POLICY["max_concurrency"]):
                stack.enter_context(holder.acquire())
            with ThreadPoolExecutor(max_workers=waiters) as workers:
                futures = [workers.submit(request, index) for index in range(waiters)]
                try:
                    assert {ready.get(timeout=10) for _ in futures} == set(range(waiters))
                    counts.reset()
                    before = time.monotonic()
                    begin.set()
                    time.sleep(duration)
                    result = {**counts.snapshot(), "duration_seconds": time.monotonic() - before}
                finally:
                    begin.set()
                    stop.set()
                for future in futures:
                    future.result(5)
        assert holder.snapshot()["active"] == holder.snapshot()["queued"] == 0
    return result


def test_adaptive_waiting_reduces_actual_disk_lock_and_probe_work(tmp_path, monkeypatch):
    old = _waiting_workload(tmp_path / "fixed", FixedCadenceScheduler, monkeypatch)
    new = _waiting_workload(tmp_path / "adaptive", SharedModelScheduler, monkeypatch)
    print("scheduler_waiting_measurement=" + json.dumps({"fixed": old, "adaptive": new}))
    assert new["state_reads"] < old["state_reads"] * .75
    assert new["metadata_lock_attempts"] < old["metadata_lock_attempts"] * .75
    assert new["lease_probes"] < old["lease_probes"] * .5


def _process_fifo(root, index, gate, events, current, peak, cadence):
    cls = FixedCadenceScheduler if cadence == "fixed" else SharedModelScheduler
    item = cls(Path(root), IDENTITY, policy={**POLICY, "max_concurrency": 1})
    events.put(("ready", index))
    if not gate.wait(20):
        raise RuntimeError("test gate timeout")
    with item.acquire(on_wait=lambda info: events.put(("queued", index))):
        with current.get_lock(), peak.get_lock():
            current.value += 1
            peak.value = max(peak.value, current.value)
        events.put(("entered", index, time.monotonic()))
        time.sleep(.04)
        with current.get_lock():
            current.value -= 1


@pytest.mark.parametrize("cadence", ["fixed", "adaptive"])
def test_real_process_fifo_fairness_and_bounded_refill_gap(tmp_path, cadence):
    ctx = multiprocessing.get_context("spawn")
    events = ctx.Queue()
    gates = [ctx.Event() for _ in range(6)]
    current, peak = ctx.Value("i", 0), ctx.Value("i", 0)
    children = [ctx.Process(target=_process_fifo,
                            args=(str(tmp_path), index, gate, events, current, peak, cadence))
                for index, gate in enumerate(gates)]
    holder = SharedModelScheduler(tmp_path, IDENTITY, policy={**POLICY, "max_concurrency": 1})
    with holder.acquire():
        for child in children:
            child.start()
        try:
            assert {events.get(timeout=20) for _ in children} == {
                ("ready", index) for index in range(len(children))}
            for index, gate in enumerate(gates):
                gate.set()
                assert events.get(timeout=10) == ("queued", index)
            released = time.monotonic()
        except BaseException:
            for child in children:
                if child.is_alive():
                    child.terminate()
                child.join(5)
            raise
    try:
        entered = [events.get(timeout=10) for _ in children]
        assert [value[1] for value in entered] == list(range(len(children)))
        assert all(value[0] == "entered" for value in entered)
        gaps = [entered[0][2] - released] + [b[2] - a[2] - .04 for a, b in zip(entered, entered[1:])]
        for child in children:
            child.join(10)
            assert child.exitcode == 0
        assert peak.value == 1 and current.value == 0
        assert max(gaps) < module.MAX_POLL_SECONDS + .8
        assert holder.snapshot()["active"] == holder.snapshot()["queued"] == 0
        print("scheduler_process_measurement=" + json.dumps({
            "cadence": cadence, "processes": len(children), "fifo": True,
            "peak": peak.value, "max_refill_gap_seconds": max(gaps),
            "drain_seconds": entered[-1][2] - released + .04}))
    finally:
        for child in children:
            if child.is_alive():
                child.terminate()
                child.join(5)


def test_cancellation_during_metadata_contention_preserves_exception_and_releases_lease(tmp_path):
    item = SharedModelScheduler(tmp_path, IDENTITY, policy=POLICY)
    ready, stop = threading.Event(), threading.Event()
    def check():
        if stop.is_set():
            raise Cancelled("original cancellation")
    def request():
        with item.acquire(cancel_check=check):
            ready.set()
            assert stop.wait(5)
            check()
    with ThreadPoolExecutor(max_workers=1) as workers:
        future = workers.submit(request)
        assert ready.wait(5)
        token = json.loads(item.path.read_text(encoding="utf-8"))["active"][0]
        with FileLock(str(item.path) + ".lock", timeout=0):
            before = time.monotonic()
            stop.set()
            with pytest.raises(Cancelled, match="original cancellation"):
                future.result(1)
            elapsed = time.monotonic() - before
            with FileLock(str(item._lease_path(token)), timeout=0):
                pass  # Capacity is already released while metadata is locked.
        assert elapsed < .75
    assert item.snapshot()["active"] == item.snapshot()["queued"] == 0
    assert not item._lease_path(token).exists()
    print("scheduler_cancel_contention_seconds=" + str(elapsed))


def test_deep_queue_backoff_remains_promptly_cancelable(tmp_path):
    policy = {**POLICY, "max_concurrency": 1}
    item = SharedModelScheduler(tmp_path, IDENTITY, policy=policy)
    stop, target_stop, ready = threading.Event(), threading.Event(), queue.Queue()
    def request(target=False):
        def check():
            if stop.is_set() or (target and target_stop.is_set()):
                raise Cancelled("deep queue cancelled")
        with pytest.raises(Cancelled, match="deep queue cancelled"):
            with SharedModelScheduler(tmp_path, IDENTITY, policy=policy).acquire(
                    cancel_check=check, on_wait=lambda info: ready.put(info["position"])):
                pytest.fail("The synthetic stream still owns all capacity")
    with item.acquire(), ThreadPoolExecutor(max_workers=13) as workers:
        futures = []
        try:
            for position in range(1, 13):
                futures.append(workers.submit(request))
                assert ready.get(timeout=5) == position
            target = workers.submit(request, True)
            futures.append(target)
            assert ready.get(timeout=5) == 13
            time.sleep(.025)
            before = time.monotonic()
            target_stop.set()
            target.result(.75)
            elapsed = time.monotonic() - before
            assert elapsed < .5
        finally:
            stop.set()
            for future in futures:
                future.result(5)
    assert item.snapshot()["active"] == item.snapshot()["queued"] == 0
    print("scheduler_deep_queue_cancel_seconds=" + str(elapsed))


def test_incidental_windows_lease_unlink_error_does_not_replace_completed_request(tmp_path, monkeypatch):
    item = SharedModelScheduler(tmp_path, IDENTITY, policy=POLICY)
    original = Path.unlink
    def shared_file_busy(path, *args, **kwargs):
        if path.suffix == ".lease":
            raise PermissionError("synthetic concurrent lease probe")
        return original(path, *args, **kwargs)
    with monkeypatch.context() as patch:
        patch.setattr(Path, "unlink", shared_file_busy)
        with item.acquire():
            pass
    item._next_housekeeping = 0
    assert item.snapshot()["active"] == item.snapshot()["queued"] == 0


def _thread_refill_workload(root, cls):
    ready, entered = queue.Queue(), []
    policy = {**POLICY, "max_concurrency": 1}
    holder = SharedModelScheduler(root, IDENTITY, policy=policy)
    def request(index):
        item = cls(root, IDENTITY, policy=policy)
        with item.acquire(on_wait=lambda info: ready.put(index)):
            entered.append((index, time.monotonic()))
            time.sleep(.04)
    with ThreadPoolExecutor(max_workers=6) as workers:
        with holder.acquire():
            futures = []
            for index in range(6):
                futures.append(workers.submit(request, index))
                assert ready.get(timeout=5) == index
            released = time.monotonic()
        for future in futures:
            future.result(5)
    assert [index for index, stamp in entered] == list(range(6))
    assert holder.snapshot()["active"] == holder.snapshot()["queued"] == 0
    gaps = [entered[0][1] - released] + [b[1] - a[1] - .04 for a, b in zip(entered, entered[1:])]
    return {"drain_seconds": entered[-1][1] - released + .04,
            "max_refill_gap_seconds": max(gaps)}


def test_process_local_completion_wakes_sibling_clients_without_poll_gap(tmp_path):
    fixed = _thread_refill_workload(tmp_path / "fixed", FixedCadenceScheduler)
    notified = _thread_refill_workload(tmp_path / "notified", SharedModelScheduler)
    print("scheduler_thread_refill_measurement=" + json.dumps({"fixed": fixed, "notified": notified}))
    assert notified["drain_seconds"] < fixed["drain_seconds"] * .7


def test_dead_lease_unlink_contention_is_ignored_only_after_successful_probe(tmp_path, monkeypatch):
    item = SharedModelScheduler(tmp_path, IDENTITY, policy=POLICY)
    token = "a" * 32
    path = item._lease_path(token)
    deletions = []
    original = Path.unlink
    def shared_file_busy(target, *args, **kwargs):
        if target == path:
            deletions.append(target)
            raise PermissionError("synthetic Windows sharing violation")
        return original(target, *args, **kwargs)
    with monkeypatch.context() as patch:
        patch.setattr(Path, "unlink", shared_file_busy)
        with FileLock(str(path), timeout=0):
            assert item._alive(token) is True
            assert deletions == []
        deletions.clear()
        state = item._state()
        state["active"].append(token)
        item._next_housekeeping = time.monotonic() + 60
        assert item._prune(state) is True
        assert state["active"] == []
        assert deletions and all(target == path for target in deletions)
    assert item._alive(token) is False
    assert not path.exists()
