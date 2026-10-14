"""Bounded daemon workers for disposable, read-only report preparation.

Completed indexes are durable; interrupted work is rebuilt from verified source
files on the next request. Jobs never enter the generation scheduler or call a
model. A request only enqueues work and snapshots its state; it never waits for
another process's file lock or for a worker to finish.
"""
from collections import OrderedDict
from copy import deepcopy
import threading
import time


class ReportPreparationBusy(Exception):
    pass


class ReportJobs:
    def __init__(self, *, concurrency=2, capacity=16, lease_seconds=30):
        self.concurrency, self.capacity, self.lease_seconds = concurrency, capacity, lease_seconds
        self._lock = threading.RLock()
        self._jobs = OrderedDict()
        self._running = 0

    @staticmethod
    def _snapshot(job):
        return {key: deepcopy(job[key]) for key in ("status", "phase", "error", "retryable", "result")}

    def request(self, key, prepare, *, phase):
        with self._lock:
            now = time.monotonic()
            job = self._jobs.get(key)
            if job is not None and job["status"] == "ready" and now - job["finished"] > self.lease_seconds:
                self._jobs.pop(key)
                job = None
            if job is not None and job["status"] == "waiting" and now - job["finished"] > .25:
                self._jobs.pop(key)
                job = None
            if job is None:
                # Evict only terminal work. Running/queued jobs keep their
                # identity so concurrent requests cannot create duplicate scans.
                for old_key in list(self._jobs):
                    if len(self._jobs) < self.capacity:
                        break
                    if self._jobs[old_key]["status"] in {"ready", "failed", "waiting"}:
                        self._jobs.pop(old_key)
                if len(self._jobs) >= self.capacity:
                    return {"status": "waiting", "phase": "capacity", "error": None,
                            "retryable": True, "result": None}
                job = {"status": "queued", "phase": phase, "error": None, "retryable": False,
                       "result": None, "prepare": prepare, "finished": 0.0}
                self._jobs[key] = job
            self._jobs.move_to_end(key)
            self._launch()
            return self._snapshot(job)

    def _launch(self):
        for key, job in self._jobs.items():
            if self._running >= self.concurrency:
                break
            if job["status"] == "queued":
                self._running += 1
                job["status"] = "running"
                threading.Thread(target=self._work, args=(key, job), daemon=True,
                                 name="workflow-report-reader").start()

    def _work(self, key, job):
        try:
            result = job["prepare"]()
            status, error, retryable = "ready", None, False
        except ReportPreparationBusy:
            result, status, error, retryable = None, "waiting", None, True
        except Exception as exc:
            result, status, error, retryable = None, "failed", str(exc)[:500], True
        with self._lock:
            job.update(result=result, status=status, error=error, retryable=retryable,
                       finished=time.monotonic())
            job.pop("prepare", None)
            self._running -= 1
            self._launch()

    def invalidate(self, key):
        with self._lock:
            job = self._jobs.get(key)
            if job and job["status"] not in {"queued", "running"}:
                self._jobs.pop(key)

    def retry(self, run_name, target=None):
        with self._lock:
            for key in list(self._jobs):
                if key[1] == run_name and (target is None or key[0] == "verify" or key[2] == target):
                    self.invalidate(key)


REPORT_JOBS = ReportJobs()
