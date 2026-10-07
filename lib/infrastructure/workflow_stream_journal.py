"""Bounded durable previews, kept separate from trusted sample checkpoints."""
from __future__ import annotations

from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import stat
import time
import uuid

from filelock import FileLock

from lib.domain.workflow_quality import canonical
from lib.io_utils import atomic_json
import hashlib

TEXT_LIMIT = 12_000
RECENT_LIMIT = 32
FILE_LIMIT = 160_000
_NAME = re.compile(r"([a-f0-9]{32})\.(active|completed|interrupted)\.json")


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


def _read_file(path: Path, limit: int) -> bytes:
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
            payload = handle.read(limit + 1)
        if len(payload) > limit:
            raise ValueError("model_stream_storage_invalid")
        return payload
    raise FileNotFoundError("model_stream_snapshot_changed")


def _read(path: Path) -> dict:
    row = json.loads(_read_file(path, FILE_LIMIT))
    match = _NAME.fullmatch(path.name)
    if (not match or not isinstance(row, dict) or row.get("id") != match[1]
            or row.get("status") != match[2]
            or any(not isinstance(row.get(key), str) for key in
                   ("stage", "unit", "role", "text", "reasoning", "updated_at"))):
        raise ValueError("model_stream_storage_invalid")
    if (any(type(row.get(key)) is not int or row[key] < 0 for key in
            ("attempt", "run_attempt", "text_chars", "reasoning_chars"))
            or row["attempt"] < 1 or type(row.get("truncated")) is not bool):
        raise ValueError("model_stream_storage_invalid")
    # Return only the presentation contract, never arbitrary fields from disk.
    safe = {key: row.get(key) for key in (
        "id", "stage", "unit", "role", "status", "text", "reasoning", "updated_at",
        "attempt", "run_attempt", "text_chars", "reasoning_chars", "truncated", "checkpoint")}
    for key in ("text", "reasoning"):
        safe[key] = safe[key][-TEXT_LIMIT:]
    return safe


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
    except (OSError, ValueError, KeyError, TypeError):
        return False


def read_streams(run: Path, *, active: bool, run_attempt: int) -> list[dict]:
    """Read bounded slots. A dead worker's unfinished slot is retryable."""
    try:
        directory = _directory(run)
    except FileNotFoundError:
        return []
    candidates = []
    for path in directory.iterdir():
        if _NAME.fullmatch(path.name):
            try:
                candidates.append((path.stat(follow_symlinks=False).st_mtime_ns, path))
            except FileNotFoundError:
                continue
    # Active requests always stay visible, including under sustained throughput.
    candidates.sort(key=lambda item: (".active." in item[1].name, item[0]), reverse=True)
    rows = {}
    for _, path in candidates[:RECENT_LIMIT + 32]:
        try:
            row = _read(path)
        except FileNotFoundError:
            continue  # Atomic status promotion raced a polling browser.
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


class StreamJournal:
    """One logical call, with independent slots for its transport/JSON retries.

    Atomic snapshots retain a tail of each received channel. Flush at the first
    delta, every 0.25 seconds or 1024 characters, and on every terminal change.
    Only a successfully committed sample checkpoint marks a slot completed.
    """

    def __init__(self, run: Path, *, stage: str, unit: str, role: str, run_attempt: int,
                 checkpoint: str | None = None):
        self.run = Path(run)
        self.directory = _directory(run, create=True)
        self.context = {"stage": stage[:80], "unit": unit[:80], "role": role[:40],
                        "run_attempt": run_attempt, "checkpoint": checkpoint}
        self.row = None
        self.attempt = 0
        self._last_flush = 0.0
        self._pending = 0

    def _path(self, row=None):
        row = row or self.row
        return self.directory / f"{row['id']}.{row['status']}.json"

    def _flush(self):
        _directory(self.run)
        self.row["updated_at"] = _now()
        atomic_json(self._path(), self.row)
        self._last_flush = time.monotonic()
        self._pending = 0

    def _finish(self, status):
        if self.row is None or self.row["status"] != "active":
            return
        with FileLock(str(self.directory / ".catalog.lock")):
            previous = self._path()
            self.row["status"] = status
            self._flush()
            _unlink(previous)
            self._prune()

    def _prune(self):
        # No completed sample depends on preview retention. Active slots are
        # never evicted; completed checkpoints remain in their original store.
        terminal = []
        for path in self.directory.iterdir():
            match = _NAME.fullmatch(path.name)
            if not match:
                continue
            try:
                if match[2] == "active":
                    row = _read(path)
                    if row["run_attempt"] == self.context["run_attempt"]:
                        continue
                    row["status"] = "completed" if _committed(self.run, row) else "interrupted"
                    promoted = self.directory / f"{row['id']}.{row['status']}.json"
                    atomic_json(promoted, row)
                    _unlink(path)
                    path = promoted
                terminal.append((path.stat(follow_symlinks=False).st_mtime_ns, path))
            except FileNotFoundError:
                continue
        for _, path in sorted(terminal, reverse=True)[RECENT_LIMIT:]:
            _unlink(path)

    def __call__(self, event: dict):
        kind = event.get("type")
        if kind == "start":
            self._finish("interrupted")
            self.attempt += 1
            self.row = {**self.context, "id": uuid.uuid4().hex, "attempt": self.attempt,
                        "status": "active", "text": "", "reasoning": "",
                        "text_chars": 0, "reasoning_chars": 0, "truncated": False}
            self._flush()
        elif kind == "delta" and self.row is not None:
            channel, text = event.get("channel"), event.get("text")
            if channel not in {"text", "reasoning"} or not isinstance(text, str):
                raise ValueError("model_stream_invalid_event")
            self.row[channel + "_chars"] += len(text)
            self.row[channel] = (self.row[channel] + text)[-TEXT_LIMIT:]
            self.row["truncated"] = any(self.row[key + "_chars"] > TEXT_LIMIT for key in ("text", "reasoning"))
            self._pending += len(text)
            if (self.row[channel + "_chars"] == len(text) or self._pending >= 1024
                    or time.monotonic() - self._last_flush >= 0.25):
                self._flush()
        elif kind == "response_completed" and self.row is not None:
            self._flush()

    def completed(self):
        self._finish("completed")

    def interrupted(self):
        self._finish("interrupted")
