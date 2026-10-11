"""Bounded, read-only progress for completed rows before release packaging.

Stage files are presentation evidence, never a substitute for verified release
manifests. Each poll advances a small in-memory index instead of rescanning a
million-row file. Incomplete JSONL lines remain invisible until their newline.
"""
from __future__ import annotations

from collections import OrderedDict, deque
from copy import deepcopy
from dataclasses import dataclass, field
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import stat
import threading

from lib.domain.workflow_targets import STAGES

SCAN_BYTES = 256 * 1024
TAIL_BYTES = 512 * 1024
LINE_BYTES = 1024 * 1024
RECENT_ROWS = 20
CACHE_ENTRIES = 64
CACHE_BYTES = 16 * 1024 * 1024
_CACHE = OrderedDict()
_LOCK = threading.RLock()


def _linked(info):
    return stat.S_ISLNK(info.st_mode) or bool(getattr(info, "st_file_attributes", 0) & 0x400)


def _directory(run):
    directory = Path(run) / "stage-results"
    for path in (Path(run), directory):
        try:
            info = path.lstat()
        except FileNotFoundError:
            if path == directory:
                return None
            raise
        if _linked(info) or not stat.S_ISDIR(info.st_mode):
            raise ValueError("live_artifact_storage_invalid")
    return directory


def _identity(info):
    return info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns


def _open(path):
    info = path.lstat()
    if _linked(info) or not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
        raise ValueError("live_artifact_storage_invalid")
    descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_BINARY", 0))
    handle = os.fdopen(descriptor, "rb")
    current = os.fstat(handle.fileno())
    if (_linked(current) or not stat.S_ISREG(current.st_mode) or current.st_nlink != 1
            or (info.st_dev, info.st_ino) != (current.st_dev, current.st_ino)):
        handle.close()
        raise FileNotFoundError("live_artifact_file_changed")
    return handle, current


def _row(payload):
    if not payload.strip():
        return None
    try:
        value = json.loads(payload)
    except (ValueError, UnicodeError, RecursionError):
        return None
    return value if isinstance(value, dict) else None


def _record(payload, wrapped):
    row = _row(payload)
    if not wrapped or row is None:
        return row
    if (set(row) != {"item_index", "record"} or type(row.get("item_index")) is not int
            or row["item_index"] < 0 or not isinstance(row.get("record"), dict)):
        return None
    return row["record"]


@dataclass
class _Index:
    identity: tuple | None = None
    filename: str = ""
    offset: int = 0
    carry: bytes = b""
    discarding: bool = False
    written: int = 0
    eligible: int = 0
    ready: int = 0
    quarantined: int = 0
    reviewed: int = 0
    review_accepted: int = 0
    unreviewed: int = 0
    invalid: int = 0
    oversized: int = 0
    first: bytes = b""
    anchor: bytes = b""
    rows: deque = field(default_factory=lambda: deque(maxlen=RECENT_ROWS))

    @property
    def cost(self):
        return len(self.carry) + sum(len(row) for row in self.rows) + 2048

    def accept(self, payload, *, wrapped=False):
        if not payload.strip():
            return
        row = _record(payload, wrapped)
        if row is None:
            self.invalid += 1
            return
        self.written += 1
        status = row.get("status")
        self.eligible += status == "eligible"
        self.ready += status == "ready"
        self.quarantined += status == "quarantined"
        self.reviewed += any(isinstance(row.get(key), dict) and row[key].get("status") in {"accepted", "rejected"}
                             for key in ("review_repair", "package_review"))
        approved = any(isinstance(row.get(key), dict) and row[key].get("status") == "accepted"
                       for key in ("review_repair", "package_review"))
        self.review_accepted += status == "eligible" and approved
        self.unreviewed += status == "eligible" and not approved
        self.rows.append(payload)
        # Keep unusually large records from multiplying memory across stages.
        while len(self.rows) > 1 and sum(len(row) for row in self.rows) > 2 * LINE_BYTES:
            self.rows.popleft()


def _consume(index, payload, *, wrapped=False):
    parts = payload.split(b"\n")
    for part in parts[:-1]:
        if index.discarding:
            index.oversized += 1
        elif len(index.carry) + len(part) > LINE_BYTES:
            index.oversized += 1
        else:
            index.accept(index.carry + part, wrapped=wrapped)
        index.carry, index.discarding = b"", False
    remainder = parts[-1]
    if not index.discarding:
        if len(index.carry) + len(remainder) > LINE_BYTES:
            index.carry, index.discarding = b"", True
        else:
            index.carry += remainder


def _anchor(handle, offset):
    handle.seek(max(0, offset - 128))
    return handle.read(min(offset, 128))


def _latest(handle, size, limit, *, wrapped=False):
    """Cold large files can show a bounded tail while their index catches up."""
    start = max(0, size - TAIL_BYTES)
    handle.seek(start)
    payload = handle.read(min(size, TAIL_BYTES))
    parts = payload.split(b"\n")
    if start:
        parts = parts[1:]  # The first line may start outside this window.
    parts = parts[:-1]  # A suffix without a newline is still being written.
    rows = []
    for part in reversed(parts):
        if len(part) <= LINE_BYTES and (row := _record(part, wrapped)) is not None:
            rows.append(row)
            if len(rows) == limit:
                break
    return list(reversed(rows))


def _snapshot(run, stage, path, *, limit):
    key = (os.path.normcase(os.path.abspath(run)), stage)
    wrapped = path.name == f".{stage}.live.jsonl"
    with _LOCK, _open(path)[0] as handle:
        current = os.fstat(handle.fileno())
        identity = _identity(current)
        index = _CACHE.pop(key, _Index())
        changed = index.identity is not None and identity != index.identity
        if index.identity is not None:
            replaced = identity[:2] != index.identity[:2] or current.st_size < index.offset
            if changed and not replaced:
                if current.st_size == index.identity[2] and path.name == index.filename:
                    replaced = True
                else:
                    handle.seek(0)
                    replaced = (handle.read(len(index.first)) != index.first
                                or _anchor(handle, index.offset) != index.anchor)
            if replaced:
                index = _Index()
        handle.seek(index.offset)
        payload = handle.read(min(SCAN_BYTES, max(0, current.st_size - index.offset)))
        _consume(index, payload, wrapped=wrapped)
        index.offset += len(payload)
        index.identity = identity
        index.filename = path.name
        if payload or not index.first:
            handle.seek(0)
            index.first = handle.read(min(index.offset, 128))
            index.anchor = _anchor(handle, index.offset)
        indexing = index.offset < current.st_size
        rows = (_latest(handle, current.st_size, limit, wrapped=wrapped) if indexing and limit else
                [_record(payload, wrapped) for payload in list(index.rows)[-limit:]] if limit else [])
        result = {"written": index.written, "eligible": index.eligible, "ready": index.ready,
                  "quarantined": index.quarantined, "reviewed": index.reviewed, "invalid": index.invalid,
                  "oversized": index.oversized,
                  "review_accepted": index.review_accepted, "unreviewed": index.unreviewed,
                  "indexing": indexing, "partial_line": bool(index.carry or index.discarding),
                  "scanned_bytes": index.offset, "file_bytes": current.st_size, "rows": rows}
        _CACHE[key] = index
        while len(_CACHE) > CACHE_ENTRIES or sum(entry.cost for entry in _CACHE.values()) > CACHE_BYTES:
            _CACHE.popitem(last=False)
        return result


def _stage_file(directory, stage, metrics, state):
    if directory is None:
        return None, "none"
    # Completion-order presentation survives a slow first item, cancellation,
    # and failure. A finished node switches to its canonical input-order file.
    journal = directory / f".{stage}.live.jsonl"
    if metrics.get("status") != "completed" and journal.exists():
        return journal, "stage_live"
    pending = directory / f".{stage}.pending"
    if pending.exists():
        return pending, "stage_pending"
    # The SFT view follows its completed CoT result when that route is present.
    if stage == "sft" and metrics.get("status") != "running" and state.get("stages", {}).get("cot", {}).get("status") == "completed":
        finalized = directory / "sft-cot-final.jsonl"
        if finalized.exists():
            return finalized, "stage_result"
    final = directory / f"{stage}.jsonl"
    return (final, "stage_result") if final.exists() else (None, "none")


def live_artifacts(run: Path, state: dict, *, stage: str | None = None, limit: int = 3) -> dict:
    """Return node completion evidence, not permission to release partial data.

``written`` and row-status counters cover the scanned complete lines only;
``indexing`` signals an unfinished cold index. ``outputs`` is the worker's
checkpointed completion count. ``eligible`` means the node passed a candidate,
which can still be rejected by later review or package checks.
    """
    stages = state.get("stages", {})
    if not isinstance(stages, dict):
        raise ValueError("live_artifact_state_invalid")
    if stage is not None and (not isinstance(stage, str) or stage not in stages
                              or not re.fullmatch(r"[a-z_]{1,80}", stage)):
        raise ValueError("unknown_workflow_stage")
    limit = max(0, min(int(limit), RECENT_ROWS))
    directory = _directory(run)
    descriptors, snapshots = [], {}
    for name, metrics in stages.items():
        if not re.fullmatch(r"[a-z_]{1,80}", name) or not isinstance(metrics, dict):
            continue
        path, source_kind = _stage_file(directory, name, metrics, state)
        snapshot = {"written": 0, "eligible": 0, "ready": 0, "quarantined": 0, "reviewed": 0,
                    "review_accepted": 0, "unreviewed": 0,
                    "invalid": 0, "oversized": 0, "indexing": False, "partial_line": False, "rows": []}
        if path is not None:
            try:
                snapshot = _snapshot(run, name, path, limit=limit)
            except FileNotFoundError:
                # Atomic pending promotion is normal while the UI is polling.
                path, source_kind = _stage_file(directory, name, metrics, state)
                if path is not None:
                    snapshot = _snapshot(run, name, path, limit=limit)
        snapshots[name] = snapshot
        descriptors.append({"stage": name, "label": metrics.get("label", STAGES.get(name, name)),
                            "status": metrics.get("status", "pending"),
                            "processed": metrics.get("done", 0), "outputs": metrics.get("outputs", 0),
                            **{key: value for key, value in snapshot.items() if key != "rows"},
                            "source_kind": source_kind,
                            "complete": metrics.get("status") == "completed" and not snapshot["indexing"]
                                        and not snapshot["partial_line"] and source_kind != "stage_pending"})
    selected = stage
    if selected is None:
        available = [row for row in descriptors if row["written"] or snapshots[row["stage"]]["rows"]]
        running = [row for row in available if row["status"] == "running"]
        selected = (running[-1] if running else available[-1])["stage"] if available else None
    descriptor = next((row for row in descriptors if row["stage"] == selected), None)
    return {"updated_at": state.get("updated_at") or datetime.now(timezone.utc).isoformat(),
            "stages": descriptors, "selected_stage": selected,
            "rows": deepcopy(snapshots[selected]["rows"]) if selected is not None else [],
            "source_kind": descriptor["source_kind"] if descriptor else "none",
            "finalized": False}
