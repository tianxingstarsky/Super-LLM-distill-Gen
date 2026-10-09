"""Bounded durable previews, kept separate from trusted sample checkpoints."""
from __future__ import annotations

from datetime import datetime, timezone
from collections import OrderedDict
import json
import os
from pathlib import Path
import re
import stat
import sys
import threading
import time
import uuid

from filelock import FileLock

from lib.domain.workflow_quality import canonical
from lib.domain.workflow_targets import STAGES
from lib.io_utils import atomic_json
import hashlib

TEXT_LIMIT = 12_000
RECENT_LIMIT = 32
FILE_LIMIT = 160_000
FLUSH_INTERVAL = 0.05
CACHE_ENTRIES = 512
CACHE_BYTES = 16 * 1024 * 1024
DELTA_LIMIT = 16 * 1024 * 1024
DELTA_LINE_LIMIT = 8192
DELTA_CHUNK_CHARS = 512
_NAME = re.compile(r"(?:(?P<stage>[a-z_]{1,80})\.)?(?P<id>[a-f0-9]{32})\.(?P<status>active|completed|interrupted)\.json")
_STAGES = frozenset(STAGES)
_CACHE = OrderedDict()
_CACHE_LOCK = threading.RLock()
_CACHE_SIZE = 0
_clock = time.monotonic


def _now():
    return datetime.now(timezone.utc).isoformat()


def _unlink(path: Path):
    for attempt in range(6):
        try:
            path.unlink(missing_ok=True)
            return
        except OSError as error:
            if getattr(error, "winerror", None) not in {5, 32, 33} or attempt == 5:
                raise
            time.sleep(0.02 * 2 ** attempt)


def _linked(info):
    return stat.S_ISLNK(info.st_mode) or bool(getattr(info, "st_file_attributes", 0) & 0x400)


def _directory(run: Path, *, create=False) -> Path:
    directory = Path(run) / "streams"
    for path in (Path(run), directory):
        try:
            info = path.lstat()
        except FileNotFoundError:
            if create and path == directory:
                directory.mkdir(exist_ok=True)
                info = directory.lstat()
            else:
                raise
        if _linked(info) or not stat.S_ISDIR(info.st_mode):
            raise ValueError("model_stream_storage_invalid")
    return directory


def _fingerprint(info):
    return (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns,
            info.st_mode, info.st_nlink, getattr(info, "st_file_attributes", 0))


def _checked_read(path: Path, limit: int, known=None):
    """Even a cache hit opens the file and verifies its current safe identity."""
    for _ in range(3):
        info = path.lstat()
        if _linked(info) or not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_size > limit:
            raise ValueError("model_stream_storage_invalid")
        fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_BINARY", 0))
        with os.fdopen(fd, "rb") as handle:
            current = os.fstat(handle.fileno())
            if not stat.S_ISREG(current.st_mode) or current.st_nlink > 1:
                raise ValueError("model_stream_storage_invalid")
            if ((current.st_dev, current.st_ino) != (info.st_dev, info.st_ino) or current.st_nlink == 0):
                continue  # A legitimate atomic update raced this polling read.
            if _linked(current) or current.st_size > limit:
                raise ValueError("model_stream_storage_invalid")
            identity = _fingerprint(current)
            if known == identity:
                return None, identity
            payload = handle.read(limit + 1)
        if len(payload) > limit:
            raise ValueError("model_stream_storage_invalid")
        return payload, identity
    raise FileNotFoundError("model_stream_snapshot_changed")


def _read_file(path: Path, limit: int) -> bytes:
    return _checked_read(path, limit)[0]


def _cache_key(path: Path):
    # Lexical absolute paths isolate runs. Parent identities also invalidate
    # entries when a directory is deleted and recreated at the same path.
    directory = _directory(path.parent.parent)
    parents = tuple((info.st_dev, info.st_ino) for info in
                    (directory.parent.lstat(), directory.lstat()))
    return os.path.normcase(os.path.abspath(path)), parents


def _read(path: Path) -> dict:
    global _CACHE_SIZE
    key = _cache_key(path)
    with _CACHE_LOCK:
        cached = _CACHE.get(key)
    payload, identity = _checked_read(path, FILE_LIMIT, cached[0] if cached else None)
    if payload is None:
        with _CACHE_LOCK:
            if key in _CACHE:
                _CACHE.move_to_end(key)
        return dict(cached[1])
    try:
        row = json.loads(payload)
    except (ValueError, UnicodeError, RecursionError):
        raise ValueError("model_stream_storage_invalid") from None
    match = _NAME.fullmatch(path.name)
    if (not match or not isinstance(row, dict) or row.get("id") != match["id"]
            or row.get("status") != match["status"]
            or any(not isinstance(row.get(key), str) for key in
                   ("stage", "unit", "role", "text", "reasoning", "updated_at"))):
        raise ValueError("model_stream_storage_invalid")
    if (any(type(row.get(key)) is not int or row[key] < 0 for key in
            ("attempt", "run_attempt", "text_chars", "reasoning_chars"))
            or row["attempt"] < 1 or type(row.get("truncated")) is not bool):
        raise ValueError("model_stream_storage_invalid")
    checkpoint = row.get("checkpoint")
    if checkpoint is not None and (not isinstance(checkpoint, str)
                                   or not re.fullmatch(r"[a-f0-9]{64}", checkpoint)):
        raise ValueError("model_stream_storage_invalid")
    # Return only the presentation contract, never arbitrary fields from disk.
    safe = {key: row.get(key) for key in (
        "id", "stage", "unit", "role", "status", "text", "reasoning", "updated_at",
        "attempt", "run_attempt", "text_chars", "reasoning_chars", "truncated", "checkpoint")}
    if safe["stage"] not in _STAGES:
        raise ValueError("model_stream_storage_invalid")
    if match["stage"] is not None and safe["stage"] != match["stage"]:
        raise ValueError("model_stream_storage_invalid")
    safe["started_at"] = row.get("started_at") if isinstance(row.get("started_at"), str) else row["updated_at"]
    safe["delta_version"] = row.get("delta_version")
    safe["delta_truncated"] = row.get("delta_truncated", False)
    if ((safe["delta_version"] is not None and (type(safe["delta_version"]) is not int or safe["delta_version"] != 1))
            or type(safe["delta_truncated"]) is not bool):
        raise ValueError("model_stream_storage_invalid")
    for key in ("text", "reasoning"):
        safe[key] = safe[key][-TEXT_LIMIT:]
    cost = sys.getsizeof(safe) + sum(sys.getsizeof(value) for value in safe.values())
    key = _cache_key(path)
    with _CACHE_LOCK:
        previous = _CACHE.pop(key, None)
        if previous:
            _CACHE_SIZE -= previous[2]
        _CACHE[key] = (identity, dict(safe), cost)
        _CACHE_SIZE += cost
        while len(_CACHE) > CACHE_ENTRIES or _CACHE_SIZE > CACHE_BYTES:
            _, removed = _CACHE.popitem(last=False)
            _CACHE_SIZE -= removed[2]
    return safe


def _slot_path(directory: Path, row: dict):
    return directory / f"{row['stage']}.{row['id']}.{row['status']}.json"


def _delta_path(directory: Path, row: dict):
    return directory / f"{row['stage']}.{row['id']}.deltas.jsonl"


def _line(value):
    # ASCII escaping preserves all Unicode code points, including provider
    # escape sequences, and gives each 512-character delta an 8KiB bound.
    return (json.dumps(value, ensure_ascii=True, separators=(",", ":")) + "\n").encode("ascii")


def _committed(run: Path, row: dict) -> bool:
    """Handle a process exit between committing a sample and promoting its preview."""
    checkpoint = row.get("checkpoint")
    stage = row["stage"]
    if (not isinstance(checkpoint, str) or not re.fullmatch(r"[a-f0-9]{64}", checkpoint)
            or not re.fullmatch(r"[a-z_]{1,80}", stage)):
        return False
    directory = Path(run) / "checkpoints"
    path = directory / stage / f"{checkpoint}.json"
    try:
        for parent in (directory, directory / stage):
            info = parent.lstat()
            if _linked(info) or not stat.S_ISDIR(info.st_mode):
                return False
        # Checkpoint size is governed by the model output ceiling. This read
        # happens only for unfinished previews; completed ones need no reread.
        saved = json.loads(_read_file(path, 16 * 1024 * 1024))
        return (isinstance(saved, dict) and "data" in saved
                and saved.get("sha256") == hashlib.sha256(canonical(saved["data"]).encode("utf-8")).hexdigest())
    except (OSError, ValueError, KeyError, TypeError, RecursionError):
        return False


def read_streams(run: Path, *, active: bool, run_attempt: int, stage: str | None = None) -> list[dict]:
    """Read bounded slots. A dead worker's unfinished slot is retryable."""
    if stage is not None and (not isinstance(stage, str) or stage not in _STAGES):
        raise ValueError("invalid_workflow_stream_stage")
    try:
        directory = _directory(run)
    except FileNotFoundError:
        return []
    candidates = []
    for path in directory.iterdir():
        match = _NAME.fullmatch(path.name)
        if match and (stage is None or match["stage"] in {None, stage}):
            try:
                candidates.append((path.stat(follow_symlinks=False).st_mtime_ns, path))
            except FileNotFoundError:
                continue
    # Active requests always stay visible, including under sustained throughput.
    candidates.sort(key=lambda item: (".active." in item[1].name, item[0]), reverse=True)
    rows = {}
    # Retention is per node. Inspecting an earlier node must not lose its
    # requests just because a later node is producing a large batch.
    for _, path in candidates[:(RECENT_LIMIT + 32) * len(_STAGES)]:
        try:
            row = _read(path)
        except FileNotFoundError:
            continue  # Atomic status promotion raced a polling browser.
        if stage is not None and row["stage"] != stage:
            continue
        if row["status"] == "active":
            if _committed(run, row):
                row["status"] = "completed"
            elif not active or row["run_attempt"] != run_attempt:
                row["status"] = "interrupted"
        row.pop("checkpoint", None)
        row["retryable"] = row["status"] == "interrupted"
        if row["id"] not in rows or rows[row["id"]]["updated_at"] < row["updated_at"]:
            rows[row["id"]] = row
    return sorted(rows.values(), key=lambda row: (row["status"] == "active", row["updated_at"]),
                  reverse=True)[:RECENT_LIMIT]


def _request_row(run: Path, directory: Path, request_id: str, stage, active, run_attempt):
    rows = []
    for path in directory.iterdir():
        match = _NAME.fullmatch(path.name)
        if not match or match["id"] != request_id or (stage is not None and match["stage"] not in {None, stage}):
            continue
        try:
            row = _read(path)
        except FileNotFoundError:
            continue
        if stage is not None and row["stage"] != stage:
            continue
        if row["status"] == "active":
            if _committed(run, row):
                row["status"] = "completed"
            elif not active or row["run_attempt"] != run_attempt:
                row["status"] = "interrupted"
        rows.append(row)
    if not rows:
        raise ValueError("model_stream_request_not_found")
    return max(rows, key=lambda row: row["updated_at"])


def _delta_page(handle, row, offset, limit_bytes, size):
    header = handle.readline(513)
    if not header.endswith(b"\n") or len(header) > 512:
        raise ValueError("model_stream_storage_invalid")
    try:
        saved = json.loads(header)
    except (ValueError, UnicodeError, RecursionError):
        raise ValueError("model_stream_storage_invalid") from None
    if (not isinstance(saved, dict) or type(saved.get("version")) is not int or saved["version"] != 1
            or saved.get("id") != row["id"] or saved.get("stage") != row["stage"]):
        raise ValueError("model_stream_storage_invalid")
    begin = handle.tell()
    if offset:
        if offset < begin or offset > size:
            raise ValueError("invalid_model_stream_cursor")
        handle.seek(offset - 1)
        if handle.read(1) != b"\n":
            raise ValueError("invalid_model_stream_cursor")
        handle.seek(offset)
    else:
        offset = begin
    events, consumed, next_offset, partial = [], 0, offset, False
    while handle.tell() < size:
        line = handle.readline(min(DELTA_LINE_LIMIT + 1, size - handle.tell()))
        if len(line) > DELTA_LINE_LIMIT:
            raise ValueError("model_stream_storage_invalid")
        if not line.endswith(b"\n"):
            partial = True
            break
        if consumed + len(line) > limit_bytes:
            break
        try:
            event = json.loads(line)
        except (ValueError, UnicodeError, RecursionError):
            raise ValueError("model_stream_storage_invalid") from None
        if (not isinstance(event, dict) or not isinstance(event.get("channel"), str)
                or event["channel"] not in {"text", "reasoning"} or not isinstance(event.get("text"), str)):
            raise ValueError("model_stream_storage_invalid")
        events.append({"channel": event["channel"], "text": event["text"]})
        consumed += len(line)
        next_offset = handle.tell()
    terminal = row["status"] != "active"
    return {"next_offset": next_offset, "events": events,
            "done": terminal and (next_offset == size or partial),
            "truncated": row["delta_truncated"] or (terminal and partial)}


def read_stream_delta(run: Path, request_id: str, *, active: bool, run_attempt: int,
                      offset: int = 0, limit_bytes: int = 65536, stage: str | None = None) -> dict:
    """Read a selected request in bounded whole lines, without scanning its prefix."""
    if not isinstance(request_id, str) or not re.fullmatch(r"[a-f0-9]{32}", request_id):
        raise ValueError("invalid_model_stream_request")
    if stage is not None and (not isinstance(stage, str) or stage not in _STAGES):
        raise ValueError("invalid_workflow_stream_stage")
    if type(offset) is not int or offset < 0 or type(limit_bytes) is not int or not DELTA_LINE_LIMIT <= limit_bytes <= 65536:
        raise ValueError("invalid_model_stream_cursor")
    try:
        directory = _directory(run)
    except FileNotFoundError:
        raise ValueError("model_stream_request_not_found") from None
    row = _request_row(run, directory, request_id, stage, active, run_attempt)
    result = {"id": row["id"], "stage": row["stage"], "status": row["status"], "offset": offset,
              "next_offset": offset, "events": [], "done": False, "truncated": False,
              "legacy": False, "text": "", "reasoning": ""}
    if row["delta_version"] is None:
        if offset:
            raise ValueError("invalid_model_stream_cursor")
        return {**result, "legacy": True, "text": row["text"], "reasoning": row["reasoning"],
                "done": row["status"] != "active", "truncated": row["truncated"]}
    path = _delta_path(directory, row)
    for _ in range(3):
        try:
            info = path.lstat()
        except FileNotFoundError:
            raise ValueError("model_stream_storage_invalid") from None
        if _linked(info) or not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_size > DELTA_LIMIT:
            raise ValueError("model_stream_storage_invalid")
        try:
            fd = os.open(path, os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0))
        except OSError:
            raise ValueError("model_stream_storage_invalid") from None
        with os.fdopen(fd, "rb") as handle:
            current = os.fstat(handle.fileno())
            if (_linked(current) or not stat.S_ISREG(current.st_mode) or current.st_nlink > 1
                    or current.st_size > DELTA_LIMIT):
                raise ValueError("model_stream_storage_invalid")
            if (current.st_dev, current.st_ino) != (info.st_dev, info.st_ino) or current.st_nlink == 0:
                continue
            return {**result, **_delta_page(handle, row, offset, limit_bytes, current.st_size)}
    raise ValueError("model_stream_storage_invalid")


class StreamJournal:
    """One logical call, with independent slots for its transport/JSON retries.

    Atomic snapshots retain a tail of each received channel. Flush at the first
    delta, at most every 50 milliseconds, and on every terminal change.
    Only a successfully committed sample checkpoint marks a slot completed.
    """

    def __init__(self, run: Path, *, stage: str, unit: str, role: str, run_attempt: int,
                 checkpoint: str | None = None):
        self.run = Path(run)
        if not isinstance(stage, str) or stage not in _STAGES:
            raise ValueError("invalid_workflow_stream_stage")
        self.directory = _directory(run, create=True)
        self.context = {"stage": stage[:80], "unit": unit[:80], "role": role[:40],
                        "run_attempt": run_attempt, "checkpoint": checkpoint}
        self.row = None
        self.attempt = 0
        self._last_flush = 0.0
        self._pending = 0
        self._lock = threading.RLock()
        self._timer = None
        self._io_error = False
        self._log = None
        self._log_bytes = 0
        self._pending_events = []
        self._pending_bytes = 0

    def _path(self, row=None):
        row = row or self.row
        return _slot_path(self.directory, row)

    def _flush(self):
        self._cancel_timer()
        _directory(self.run)
        self._flush_log()
        self.row["updated_at"] = _now()
        atomic_json(self._path(), self.row)
        self._last_flush = _clock()
        self._pending = 0

    def _cancel_timer(self):
        if self._timer is not None:
            self._timer.cancel()
            self._timer = None

    def _deferred_flush(self):
        with self._lock:
            self._timer = None
            if self.row is None or self.row["status"] != "active" or not self._pending:
                return
            try:
                self._flush()
            except Exception:
                self._io_error = True
                self.row["delta_truncated"] = True

    def _schedule_flush(self):
        if self._timer is None:
            delay = max(0.0, FLUSH_INTERVAL - (_clock() - self._last_flush))
            self._timer = threading.Timer(delay, self._deferred_flush)
            self._timer.daemon = True
            self._timer.start()

    def _open_log(self):
        _directory(self.run)
        path = _delta_path(self.directory, self.row)
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
        try:
            fd = os.open(path, flags, 0o600)
        except FileExistsError:
            raise ValueError("model_stream_storage_invalid") from None
        self._log = os.fdopen(fd, "ab", buffering=0)
        header = _line({"version": 1, "id": self.row["id"], "stage": self.row["stage"]})
        self._pending_events = [header]
        self._pending_bytes = len(header)
        self._log_bytes = 0

    def _flush_log(self):
        if self._log is None or not self._pending_events:
            return
        info = _delta_path(self.directory, self.row).lstat()
        current = os.fstat(self._log.fileno())
        if (_linked(info) or not stat.S_ISREG(info.st_mode) or info.st_nlink != 1
                or current.st_nlink != 1 or not stat.S_ISREG(current.st_mode)
                or (info.st_dev, info.st_ino) != (current.st_dev, current.st_ino)
                or current.st_size != self._log_bytes):
            raise ValueError("model_stream_storage_invalid")
        payload = memoryview(b"".join(self._pending_events))
        try:
            while payload:
                written = self._log.write(payload)
                if not written:
                    raise ValueError("model_stream_storage_invalid")
                self._log_bytes += written
                payload = payload[written:]
        finally:
            # If a write or fsync fails, a later interruption flush must append
            # only the unwritten suffix, never duplicate received tokens.
            self._pending_events = [bytes(payload)] if payload else []
            self._pending_bytes = len(payload)
        os.fsync(self._log.fileno())

    def _append_delta(self, channel, text):
        if self.row["delta_truncated"]:
            return
        for position in range(0, len(text), DELTA_CHUNK_CHARS):
            event = _line({"channel": channel, "text": text[position:position + DELTA_CHUNK_CHARS]})
            if self._log_bytes + self._pending_bytes + len(event) > DELTA_LIMIT:
                self.row["delta_truncated"] = True
                return
            self._pending_events.append(event)
            self._pending_bytes += len(event)

    def _close_log(self):
        self._cancel_timer()
        if self._log is not None:
            self._log.close()
            self._log = None

    def _finish(self, status):
        if self.row is None or self.row["status"] != "active":
            return
        try:
            with FileLock(str(self.directory / ".catalog.lock")):
                previous = self._path()
                self.row["status"] = status
                self._flush()
                _unlink(previous)
                self._prune()
        finally:
            self._close_log()

    def _prune(self):
        # No completed sample depends on preview retention. Active slots are
        # never evicted; completed checkpoints remain in their original store.
        terminal = []
        for path in self.directory.iterdir():
            match = _NAME.fullmatch(path.name)
            if not match:
                continue
            try:
                if match["status"] == "active":
                    row = _read(path)
                    if row["run_attempt"] == self.context["run_attempt"]:
                        continue
                    row["status"] = "completed" if _committed(self.run, row) else "interrupted"
                    promoted = _slot_path(self.directory, row)
                    atomic_json(promoted, row)
                    _unlink(path)
                    path = promoted
                if match["status"] != "active":
                    row = _read(path)
                terminal.append((path.stat(follow_symlinks=False).st_mtime_ns, path, row["stage"]))
            except FileNotFoundError:
                continue
        retained = {}
        for _, path, stage in sorted(terminal, reverse=True):
            retained[stage] = retained.get(stage, 0) + 1
            if retained[stage] > RECENT_LIMIT:
                _unlink(_delta_path(self.directory, _read(path)))
                _unlink(path)

    def __call__(self, event: dict):
        with self._lock:
            self._receive(event)

    def _receive(self, event: dict):
        if self._io_error:
            raise ValueError("model_stream_storage_invalid")
        kind = event.get("type")
        if kind == "start":
            self._finish("interrupted")
            self.attempt += 1
            self.row = {**self.context, "id": uuid.uuid4().hex, "attempt": self.attempt,
                        "status": "active", "text": "", "reasoning": "",
                        "started_at": _now(),
                        "delta_version": 1, "delta_truncated": False,
                        "text_chars": 0, "reasoning_chars": 0, "truncated": False}
            self._open_log()
            self._flush()
        elif kind == "delta" and self.row is not None:
            channel, text = event.get("channel"), event.get("text")
            if channel not in {"text", "reasoning"} or not isinstance(text, str):
                raise ValueError("model_stream_invalid_event")
            if not text:
                return
            self.row[channel + "_chars"] += len(text)
            self._append_delta(channel, text)
            self.row[channel] = (self.row[channel] + text)[-TEXT_LIMIT:]
            self.row["truncated"] = any(self.row[key + "_chars"] > TEXT_LIMIT for key in ("text", "reasoning"))
            self._pending += len(text)
            if (self.row["text_chars"] + self.row["reasoning_chars"] == len(text)
                    or _clock() - self._last_flush >= FLUSH_INTERVAL):
                self._flush()
            else:
                self._schedule_flush()
        elif kind == "response_completed" and self.row is not None:
            self._flush()

    def completed(self):
        with self._lock:
            self._finish("completed")

    def interrupted(self):
        with self._lock:
            self._finish("interrupted")
