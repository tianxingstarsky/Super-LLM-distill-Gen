"""FIFO model admission shared by workflow subprocesses.

Queue entries hold OS file locks for their whole lifetime. A killed worker's
locks disappear immediately, unlike a heartbeat timeout which could expire a
healthy long stream. State contains only hashed identities and random tokens.
"""
from __future__ import annotations

from contextlib import contextmanager
import hashlib
import json
import math
import os
from pathlib import Path
import re
import stat
import sys
import threading
import time
from urllib.parse import urlsplit, urlunsplit
import uuid
import weakref

from filelock import FileLock, Timeout

from lib.domain.model_scheduling import validate_model_scheduling
from lib.io_utils import atomic_json


POLL_SECONDS = .15
MAX_POLL_SECONDS = 1.2
CLEANUP_SECONDS = .3
MAX_QUEUE_ENTRIES = 4096
_LEASE_NAME = re.compile(r"^[a-f0-9]{32}\.lease$")


class _PoolSignal:
    """Process-local wakeups; the file ledger remains the only authority."""

    def __init__(self):
        self.condition = threading.Condition()
        self.metadata = threading.Lock()
        self.revision = 0

    def notify(self):
        with self.condition:
            self.revision += 1
            self.condition.notify_all()


_SIGNALS_LOCK = threading.Lock()
_SIGNALS: weakref.WeakValueDictionary[str, _PoolSignal] = weakref.WeakValueDictionary()


def _pool_signal(directory: Path) -> _PoolSignal:
    key = os.path.normcase(str(directory.resolve()))
    with _SIGNALS_LOCK:
        signal = _SIGNALS.get(key)
        if signal is None:
            signal = _SIGNALS[key] = _PoolSignal()
        return signal


from lib.contexts.models.domain.request_errors import ModelQueueTimeout


def model_pool_identity(base_url: str, credential: str, model: str) -> str:
    """Backend aliases, roles and API formats must not allocate separate pools."""
    parsed = urlsplit(base_url)
    host = (parsed.hostname or "").lower()
    if ":" in host:
        host = "[" + host + "]"
    port = parsed.port
    if port is not None and not ((parsed.scheme.lower() == "https" and port == 443)
                                 or (parsed.scheme.lower() == "http" and port == 80)):
        host += ":" + str(port)
    route = urlunsplit((parsed.scheme.lower(), host, parsed.path.rstrip("/"), parsed.query, ""))
    payload = route + "\0" + credential + "\0" + model
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _backend_config(root: Path) -> dict:
    import yaml
    base = root / "configs" / "backends.yaml"
    local = root / "configs" / "backends.local.yaml"
    values = [yaml.safe_load(path.read_text(encoding="utf-8")) or {} if path.exists() else {}
              for path in (base, local)]
    merged = {name: dict(value) for name, value in (values[0].get("backends") or {}).items()}
    for name, value in (values[1].get("backends") or {}).items():
        merged[name] = {**merged.get(name, {}), **value}
    return merged


def _configured_policy(root: Path, identity: str, model: str) -> dict:
    from lib.model_protocols import validate_api_format
    explicit_limits, explicit_timeouts = [], []
    for endpoint in _backend_config(root).values():
        protocol = validate_api_format(endpoint.get("api_format", "chat"))
        default_env = "ANTHROPIC_API_KEY" if protocol == "anthropic" else "OPENAI_API_KEY"
        reference = endpoint.get("api_key_env") if "api_key_env" in endpoint else default_env
        credential = endpoint.get("api_key") or os.environ.get(reference or "", "")
        if model_pool_identity(endpoint.get("base_url", ""), credential, model) != identity:
            continue
        declaration = (endpoint.get("model_capabilities") or {}).get(model) or {}
        policy = validate_model_scheduling(declaration)
        if "max_concurrency" in declaration:
            explicit_limits.append(policy["max_concurrency"])
        if "request_queue_timeout_seconds" in declaration:
            explicit_timeouts.append(policy["request_queue_timeout_seconds"])
    default = validate_model_scheduling()
    return {"max_concurrency": min(explicit_limits) if explicit_limits else default["max_concurrency"],
            "request_queue_timeout_seconds": min(explicit_timeouts) if explicit_timeouts
            else default["request_queue_timeout_seconds"]}


class SharedModelScheduler:
    def __init__(self, root: Path, identity: str, *, policy: dict | None = None):
        if (not isinstance(identity, str) or len(identity) != 64
                or any(char not in "0123456789abcdef" for char in identity)):
            raise ValueError("invalid_model_scheduler_identity")
        self.root = Path(root)
        self.directory = self.root / ".dataforge" / "model-scheduler" / identity
        self.directory.mkdir(parents=True, exist_ok=True)
        self.path = self.directory / "state.json"
        self._default_policy = validate_model_scheduling(policy)
        self._next_housekeeping = 0.
        self._signal = _pool_signal(self.directory)

    def _wait(self, seconds: float, *, revision: int, cancel_check=None):
        """Poll cancellation cheaply while distant waiters leave the disk idle."""
        deadline = time.monotonic() + seconds
        while True:
            if cancel_check:
                cancel_check()
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return
            with self._signal.condition:
                if self._signal.revision != revision:
                    return
                self._signal.condition.wait(min(POLL_SECONDS, remaining))

    @staticmethod
    def _poll_interval(position: int, limit: int, cooldown: float) -> float:
        if cooldown > 0:
            return min(MAX_POLL_SECONDS, max(POLL_SECONDS, cooldown))
        # Keep two admission windows (at least eight entries) responsive even
        # across processes. Only deeper waiters read the ledger less often.
        lookahead = max(2 * limit, 8)
        further = max(0, position + 1 - lookahead)
        extra_intervals = (further + limit - 1) // limit
        return min(MAX_POLL_SECONDS, POLL_SECONDS * (1 + extra_intervals))

    @contextmanager
    def _locked(self, *, deadline=None, cancel_check=None):
        limit = time.monotonic() + 10 if deadline is None else min(deadline, time.monotonic() + 10)
        # Serialize same-process contenders in memory. Otherwise each thread
        # repeatedly opens the same file and joins a synchronized retry convoy.
        while True:
            if cancel_check:
                cancel_check()
            remaining = limit - time.monotonic()
            if remaining <= 0:
                raise ModelQueueTimeout("model_request_queue_timeout") from None
            if self._signal.metadata.acquire(timeout=min(POLL_SECONDS, remaining)):
                break
        try:
            lock = FileLock(str(self.path) + ".lock", timeout=0)
            while True:
                if cancel_check:
                    cancel_check()
                if time.monotonic() >= limit:
                    raise ModelQueueTimeout("model_request_queue_timeout") from None
                try:
                    lock.acquire()
                    break
                except Timeout:
                    remaining = limit - time.monotonic()
                    if remaining <= 0:
                        raise ModelQueueTimeout("model_request_queue_timeout") from None
                    time.sleep(min(POLL_SECONDS, remaining))
            try:
                yield
            finally:
                lock.release()
        finally:
            self._signal.metadata.release()

    def _state(self):
        if not self.path.exists():
            return {"version": 1, **self._default_policy, "waiting": [], "active": [],
                    "cooldown_until": 0., "cooldown_monotonic_set": 0., "cooldown_monotonic_until": 0.}
        try:
            if self.path.stat().st_size > 2 * 1024 * 1024:
                raise ValueError("invalid_model_scheduler_state")
            state = json.loads(self.path.read_text(encoding="utf-8"))
            validate_model_scheduling(state)
            tokens = [*state["waiting"], *state["active"]]
            if (state.get("version") != 1 or not isinstance(state["waiting"], list)
                    or not isinstance(state["active"], list)
                    or len(state["waiting"]) > MAX_QUEUE_ENTRIES or len(state["active"]) > 256
                    or len(set(tokens)) != len(tokens)
                    or any(type(token) is not str or len(token) != 32
                           or any(char not in "0123456789abcdef" for char in token) for token in tokens)
                    or type(state["cooldown_until"]) not in (int, float)
                    or not math.isfinite(state["cooldown_until"]) or state["cooldown_until"] < 0):
                raise ValueError("invalid_model_scheduler_state")
            from lib.model_request_reliability import MAX_RETRY_DELAY_SECONDS
            clock = time.monotonic()
            set_at, until = state.get("cooldown_monotonic_set"), state.get("cooldown_monotonic_until")
            if set_at is not None or until is not None:
                if (type(set_at) not in (int, float) or type(until) not in (int, float)
                        or not math.isfinite(set_at) or not math.isfinite(until)
                        or set_at < 0 or until < set_at or until - set_at > MAX_RETRY_DELAY_SECONDS + 1e-6):
                    raise ValueError("invalid_model_scheduler_state")
            if set_at is None or clock < set_at:
                # Old ledgers and a reset monotonic clock (e.g. reboot) fall
                # back once to the wall deadline, never to an unlimited wait.
                remaining = min(MAX_RETRY_DELAY_SECONDS, max(0., state["cooldown_until"] - time.time()))
                state.update(cooldown_monotonic_set=clock, cooldown_monotonic_until=clock + remaining,
                             cooldown_until=time.time() + remaining)
                atomic_json(self.path, state)
            return state
        except (KeyError, TypeError, json.JSONDecodeError):
            raise ValueError("invalid_model_scheduler_state") from None

    def _lease_path(self, token):
        return self.directory / (token + ".lease")

    def _alive(self, token):
        probe = FileLock(str(self._lease_path(token)), timeout=0)
        try:
            probe.acquire()
        except Timeout:
            return True
        else:
            probe.release()
            try:
                self._lease_path(token).unlink(missing_ok=True)
            except OSError:
                # Successful probing already proved this lease was released.
                # Another Windows reader can briefly prevent deletion; retain
                # the dead-lease result and let housekeeping reclaim the file.
                pass
            return False

    def _prune(self, state, *, full=False, active=True):
        changed = False
        if active:
            for token in tuple(state["active"]):
                if not self._alive(token):
                    state["active"].remove(token)
                    changed = True
        if full:
            previous = state["waiting"]
            state["waiting"] = [token for token in previous if self._alive(token)]
            changed |= state["waiting"] != previous
        else:
            while state["waiting"] and not self._alive(state["waiting"][0]):
                state["waiting"].pop(0)
                changed = True
        if time.monotonic() >= self._next_housekeeping:
            self._cleanup_orphans(state)
            self._next_housekeeping = time.monotonic() + 60
        return changed

    def _cleanup_orphans(self, state):
        """Reclaim crash residue; never unlink a healthy unregistered lease."""
        registered = set(state["active"]) | set(state["waiting"])
        for path in self.directory.iterdir():
            try:
                if not stat.S_ISREG(path.lstat().st_mode):
                    continue
            except FileNotFoundError:
                continue
            if _LEASE_NAME.fullmatch(path.name):
                token = path.name[:-6]
                if token not in registered:
                    self._alive(token)
            elif path.name.startswith(".pending-") and path.name.endswith(".json"):
                # All state writers hold the same metadata lock as this pass.
                path.unlink(missing_ok=True)

    def synchronize(self, model: str) -> dict:
        """Read authority inside the pool lock; stale clients never reset limits."""
        with self._locked():
            state = self._state()
            policy = _configured_policy(self.root, self.directory.name, model)
            changed = any(state[key] != value for key, value in policy.items())
            state.update(policy)
            self._prune(state)
            atomic_json(self.path, state)
            summary = self._summary(state)
        if changed:
            self._signal.notify()
        return summary

    def snapshot(self) -> dict:
        with self._locked():
            state = self._state()
            if self._prune(state):
                atomic_json(self.path, state)
            return self._summary(state)

    @staticmethod
    def _summary(state):
        return {"max_concurrency": state["max_concurrency"],
                "request_queue_timeout_seconds": state["request_queue_timeout_seconds"],
                "active": len(state["active"]), "queued": len(state["waiting"]),
                "cooldown_seconds": max(0., state["cooldown_monotonic_until"] - time.monotonic())}

    def defer(self, seconds: float) -> None:
        from lib.model_request_reliability import MAX_RETRY_DELAY_SECONDS
        with self._locked():
            state = self._state()
            clock = time.monotonic()
            duration = max(state["cooldown_monotonic_until"] - clock,
                           min(MAX_RETRY_DELAY_SECONDS, max(0., seconds)))
            state.update(cooldown_until=time.time() + duration, cooldown_monotonic_set=clock,
                         cooldown_monotonic_until=clock + duration)
            atomic_json(self.path, state)
        self._signal.notify()

    @contextmanager
    def acquire(self, *, cancel_check=None, on_wait=None):
        """Acquire FIFO admission, keeping the lease through complete streaming."""
        if cancel_check:
            cancel_check()
        started = time.monotonic()
        token = uuid.uuid4().hex
        live = FileLock(str(self._lease_path(token)), timeout=0)
        live.acquire()
        enqueued = False
        admitted = False
        try:
            with self._locked(cancel_check=cancel_check):
                state = self._state()
                self._prune(state, full=len(state["waiting"]) >= MAX_QUEUE_ENTRIES)
                if len(state["waiting"]) >= MAX_QUEUE_ENTRIES:
                    raise ModelQueueTimeout("model_request_queue_timeout")
                deadline = started + state["request_queue_timeout_seconds"]
                if time.monotonic() >= deadline:
                    raise ModelQueueTimeout("model_request_queue_timeout")
                state["waiting"].append(token)
                available = state["max_concurrency"] - len(state["active"])
                if (state["waiting"] == [token] and available > 0
                        and state["cooldown_monotonic_until"] <= time.monotonic()):
                    state["waiting"].remove(token)
                    state["active"].append(token)
                    admitted = True
                atomic_json(self.path, state)
                enqueued = True
            notified = False
            while not admitted:
                if cancel_check:
                    cancel_check()
                # Take the revision before reading capacity so a completion
                # between that read and the wait cannot be lost.
                revision = self._signal.revision
                with self._locked(deadline=deadline, cancel_check=cancel_check):
                    state = self._state()
                    near_front = state["waiting"].index(token) < state["max_concurrency"]
                    changed = self._prune(state, active=near_front)
                    if not near_front and state["waiting"].index(token) < state["max_concurrency"]:
                        changed |= self._prune(state)
                    if time.monotonic() >= deadline:
                        raise ModelQueueTimeout("model_request_queue_timeout")
                    cooldown = max(0., state["cooldown_monotonic_until"] - time.monotonic())
                    available = state["max_concurrency"] - len(state["active"])
                    position = state["waiting"].index(token)
                    if position < available and cooldown <= 0:
                        state["waiting"].remove(token)
                        state["active"].append(token)
                        atomic_json(self.path, state)
                        admitted = True
                        break
                    if changed:
                        atomic_json(self.path, state)
                    info = {**self._summary(state), "position": position + 1}
                if on_wait and not notified:
                    on_wait(info)
                    notified = True
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise ModelQueueTimeout("model_request_queue_timeout")
                interval = self._poll_interval(position, info["max_concurrency"], cooldown)
                self._wait(min(interval, remaining), revision=revision, cancel_check=cancel_check)
            if cancel_check:
                cancel_check()
            yield
        finally:
            # Release the capacity lease first, including on cancellation. A
            # contended metadata lock must not extend cancellation or mask its
            # original exception; the next reader can prune the dead entry.
            failed = sys.exc_info()[0] is not None
            live.release()
            try:
                if enqueued:
                    with self._locked(deadline=time.monotonic() + CLEANUP_SECONDS):
                        state = self._state()
                        for collection in ("waiting", "active"):
                            if token in state[collection]:
                                state[collection].remove(token)
                        atomic_json(self.path, state)
            except ModelQueueTimeout:
                pass
            except Exception:
                if not failed:
                    raise
            finally:
                try:
                    self._lease_path(token).unlink(missing_ok=True)
                except OSError:
                    # A concurrent Windows probe may still have this released
                    # file open. Housekeeping reclaims it; successful generation
                    # must not be replaced by an incidental unlink failure.
                    pass
                finally:
                    self._signal.notify()
