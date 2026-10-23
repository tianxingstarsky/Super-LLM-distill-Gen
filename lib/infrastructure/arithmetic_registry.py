"""Permanent local arithmetic identities shared by every run in one data root.

Keep this database when removing or archiving task outputs. Its guarantee is
normalized expression uniqueness while the registry survives, not uniqueness
under every mathematical equivalence or uniqueness across separate data roots.
"""
from __future__ import annotations

from contextlib import closing
import hashlib
from itertools import islice
import json
from pathlib import Path
import sqlite3
from filelock import FileLock

from lib.domain.math_tasks import (
    GSM8K_V2_SEED_CAPACITY,
    GSM8K_V2_SEED_MULTIPLIER,
    build_gsm8k,
    verified_arithmetic_fingerprint,
)


_SCAN_CHUNK = 256
_SCHEMA_VERSION = 1


class ArithmeticRegistry:
    """Allocate checked v2 problems exactly once, with durable resume keys."""

    def __init__(self, path: Path):
        self.path = Path(path)
        self._check_path()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with FileLock(str(self.path) + ".initialize.lock", timeout=30):
            existed = self.path.exists()
            if existed and self.path.stat().st_size == 0:
                raise ValueError("arithmetic_registry_integrity_error")
            try:
                self._initialize(existed)
            except sqlite3.Error as error:
                raise ValueError("arithmetic_registry_integrity_error") from error

    def _initialize(self, existed):
        with closing(self._connect()) as db:
            db.execute("PRAGMA journal_mode=WAL")
            db.execute("BEGIN IMMEDIATE")
            try:
                if existed:
                    tables = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
                    if not {"arithmetic_state", "arithmetic_fingerprints", "arithmetic_allocations",
                            "arithmetic_history", "arithmetic_history_files"} <= tables:
                        raise ValueError("arithmetic_registry_integrity_error")
                    self._state(db)
                db.execute("""CREATE TABLE IF NOT EXISTS arithmetic_state (
                    singleton INTEGER PRIMARY KEY CHECK(singleton=1),
                    schema_version INTEGER NOT NULL, capacity INTEGER NOT NULL,
                    cursor INTEGER NOT NULL CHECK(cursor>=0 AND cursor<=capacity),
                    allocated INTEGER NOT NULL DEFAULT 0, registered INTEGER NOT NULL DEFAULT 0,
                    history_identities INTEGER NOT NULL DEFAULT 0)""")
                db.execute("""CREATE TABLE IF NOT EXISTS arithmetic_fingerprints (
                    fingerprint TEXT PRIMARY KEY, origin TEXT NOT NULL,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)""")
                db.execute("""CREATE TABLE IF NOT EXISTS arithmetic_allocations (
                    run_id TEXT NOT NULL, ordinal INTEGER NOT NULL CHECK(ordinal>=0),
                    fingerprint TEXT NOT NULL UNIQUE REFERENCES arithmetic_fingerprints(fingerprint),
                    seed INTEGER NOT NULL, payload TEXT NOT NULL, payload_sha256 TEXT NOT NULL,
                    PRIMARY KEY(run_id, ordinal))""")
                db.execute("""CREATE TABLE IF NOT EXISTS arithmetic_history (
                    source TEXT NOT NULL,
                    fingerprint TEXT NOT NULL REFERENCES arithmetic_fingerprints(fingerprint),
                    PRIMARY KEY(source, fingerprint))""")
                db.execute("""CREATE TABLE IF NOT EXISTS arithmetic_history_files (
                    source TEXT PRIMARY KEY, signature TEXT NOT NULL)""")
                db.execute("INSERT INTO arithmetic_state(singleton, schema_version, capacity, cursor) VALUES (1, ?, ?, 0) "
                           "ON CONFLICT(singleton) DO NOTHING", (_SCHEMA_VERSION, GSM8K_V2_SEED_CAPACITY))
                self._state(db)
                columns = {row[1] for row in db.execute("PRAGMA table_info(arithmetic_state)")}
                for column, table in (("allocated", "arithmetic_allocations"),
                                      ("registered", "arithmetic_fingerprints"),
                                      ("history_identities", "arithmetic_history")):
                    if column not in columns:
                        db.execute(f"ALTER TABLE arithmetic_state ADD COLUMN {column} INTEGER NOT NULL DEFAULT 0")
                        db.execute(f"UPDATE arithmetic_state SET {column}=(SELECT COUNT(*) FROM {table})")
                db.commit()
            except BaseException:
                db.rollback()
                raise

    def _check_path(self):
        if self.path.is_symlink():
            raise ValueError("arithmetic_registry_invalid_path")

    def _connect(self):
        self._check_path()
        db = sqlite3.connect(self.path, timeout=30, isolation_level=None)
        db.execute("PRAGMA foreign_keys=ON")
        db.execute("PRAGMA synchronous=FULL")
        return db

    @staticmethod
    def _state(db):
        row = db.execute("SELECT schema_version, capacity, cursor FROM arithmetic_state WHERE singleton=1").fetchone()
        if (row is None or row[0] != _SCHEMA_VERSION or row[1] != GSM8K_V2_SEED_CAPACITY
                or type(row[2]) is not int or not 0 <= row[2] <= row[1]):
            raise ValueError("arithmetic_registry_integrity_error")
        return row[1], row[2]

    @staticmethod
    def _restore(row):
        fingerprint, seed, payload, checksum = row
        if hashlib.sha256(payload.encode("utf-8")).hexdigest() != checksum:
            raise ValueError("arithmetic_registry_integrity_error")
        try:
            sample = json.loads(payload)
            valid = (verified_arithmetic_fingerprint(sample) == fingerprint
                     and sample.get("_registry_fingerprint") == fingerprint
                     and sample.get("_registry_seed") == seed)
        except (ValueError, TypeError, AttributeError):
            valid = False
        if not valid:
            raise ValueError("arithmetic_registry_integrity_error")
        return sample

    def reserve(self, run_id: str, ordinal: int, theme: str = "", *, check_cancel=None) -> dict | None:
        """Reserve the next unused structure, or return None only at exhaustion.

        Repeating a run/ordinal returns its original sample, even if the caller
        changes theme. Scan progress is committed in bounded chunks; no random
        retries or calls to a model occur. A cancellation exception rolls back
        the active transaction and preserves prior completed reservations.
        """
        if not isinstance(run_id, str) or not run_id.strip() or len(run_id) > 2048:
            raise ValueError("invalid_arithmetic_run_id")
        if type(ordinal) is not int or ordinal < 0 or ordinal > 2**63 - 1:
            raise ValueError("invalid_arithmetic_ordinal")
        if not isinstance(theme, str):
            raise ValueError("invalid_arithmetic_theme")
        while True:
            if check_cancel is not None:
                check_cancel()
            with closing(self._connect()) as db:
                db.execute("BEGIN IMMEDIATE")
                try:
                    row = db.execute("SELECT fingerprint, seed, payload, payload_sha256 "
                                     "FROM arithmetic_allocations WHERE run_id=? AND ordinal=?",
                                     (run_id, ordinal)).fetchone()
                    if row is not None:
                        sample = self._restore(row)
                        db.commit()
                        return sample
                    capacity, cursor = self._state(db)
                    stop = min(cursor + _SCAN_CHUNK, capacity)
                    for position in range(cursor, stop):
                        if check_cancel is not None:
                            check_cancel()
                        # A full permutation spreads template parameters in
                        # early batches while visiting every seed only once.
                        seed = position * GSM8K_V2_SEED_MULTIPLIER % capacity
                        sample = build_gsm8k(theme, seed, version=2)
                        fingerprint = verified_arithmetic_fingerprint(sample)
                        inserted = db.execute("INSERT INTO arithmetic_fingerprints(fingerprint, origin) "
                                              "VALUES (?, ?) ON CONFLICT(fingerprint) DO NOTHING",
                                              (fingerprint, "run:" + run_id)).rowcount
                        if not inserted:
                            continue
                        sample.update(_registry_fingerprint=fingerprint, _registry_seed=seed)
                        payload = json.dumps(sample, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
                        checksum = hashlib.sha256(payload.encode("utf-8")).hexdigest()
                        db.execute("INSERT INTO arithmetic_allocations VALUES (?, ?, ?, ?, ?, ?)",
                                   (run_id, ordinal, fingerprint, seed, payload, checksum))
                        db.execute("UPDATE arithmetic_state SET cursor=?, allocated=allocated+1, "
                                   "registered=registered+1 WHERE singleton=1", (position + 1,))
                        db.commit()
                        return sample
                    db.execute("UPDATE arithmetic_state SET cursor=? WHERE singleton=1", (stop,))
                    db.commit()
                except BaseException:
                    db.rollback()
                    raise
            if stop == capacity:
                return None

    def reserve_many(self, run_id: str, start_ordinal: int, count: int, theme: str = "", *, check_cancel=None):
        """Yield bounded, fully committed batches with the same permanent keys.

        FULL synchronous transactions and the unique indexes are unchanged.
        A caller never observes a reservation before its batch is durable;
        cancellation rolls back only the active batch. Resume restores any
        already committed keys, including a batch not yet written downstream.
        """
        if not isinstance(run_id, str) or not run_id.strip() or len(run_id) > 2048:
            raise ValueError("invalid_arithmetic_run_id")
        if type(start_ordinal) is not int or not 0 <= start_ordinal <= 2**63 - 1:
            raise ValueError("invalid_arithmetic_ordinal")
        if type(count) is not int or count < 0 or start_ordinal + count > 2**63:
            raise ValueError("invalid_arithmetic_count")
        if not isinstance(theme, str):
            raise ValueError("invalid_arithmetic_theme")
        ordinal, end = start_ordinal, start_ordinal + count
        while ordinal < end:
            if check_cancel is not None:
                check_cancel()
            committed = []
            exhausted = False
            with closing(self._connect()) as db:
                db.execute("BEGIN IMMEDIATE")
                try:
                    capacity, cursor = self._state(db)
                    stop = min(cursor + _SCAN_CHUNK, capacity)
                    added = 0
                    while ordinal < end and len(committed) < _SCAN_CHUNK:
                        if check_cancel is not None:
                            check_cancel()
                        row = db.execute("SELECT fingerprint, seed, payload, payload_sha256 "
                                         "FROM arithmetic_allocations WHERE run_id=? AND ordinal=?",
                                         (run_id, ordinal)).fetchone()
                        if row is not None:
                            committed.append((ordinal, self._restore(row)))
                            ordinal += 1
                            continue
                        sample = None
                        while cursor < stop:
                            if check_cancel is not None:
                                check_cancel()
                            seed = cursor * GSM8K_V2_SEED_MULTIPLIER % capacity
                            cursor += 1
                            candidate = build_gsm8k(theme, seed, version=2)
                            fingerprint = verified_arithmetic_fingerprint(candidate)
                            inserted = db.execute("INSERT INTO arithmetic_fingerprints(fingerprint, origin) "
                                "VALUES (?, ?) ON CONFLICT(fingerprint) DO NOTHING",
                                (fingerprint, "run:" + run_id)).rowcount
                            if not inserted:
                                continue
                            candidate.update(_registry_fingerprint=fingerprint, _registry_seed=seed)
                            payload = json.dumps(candidate, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
                            checksum = hashlib.sha256(payload.encode("utf-8")).hexdigest()
                            db.execute("INSERT INTO arithmetic_allocations VALUES (?, ?, ?, ?, ?, ?)",
                                       (run_id, ordinal, fingerprint, seed, payload, checksum))
                            sample = candidate
                            added += 1
                            break
                        if sample is None:
                            exhausted = cursor == capacity
                            break
                        committed.append((ordinal, sample))
                        ordinal += 1
                    db.execute("UPDATE arithmetic_state SET cursor=?, allocated=allocated+?, "
                               "registered=registered+? WHERE singleton=1", (cursor, added, added))
                    db.commit()
                except BaseException:
                    db.rollback()
                    raise
            yield from committed
            if exhausted:
                return

    def seed_verified(self, records, *, source: str, check_cancel=None) -> dict:
        """Import checked history and report every invalid row to the caller.

        Source/fingerprint pairs are retained so repeated scans are idempotent.
        Invalid records never occupy an identity. The caller must inspect the
        invalid count and stop allocation when history cannot be trusted.
        Iterator/read failures propagate; already committed chunks remain safe
        to rescan. Caller-supplied digest fields are never trusted.
        """
        if not isinstance(source, str) or not source.strip() or len(source) > 8192:
            raise ValueError("invalid_arithmetic_history_source")
        totals = {"added": 0, "duplicate": 0, "invalid": 0}
        iterator = iter(records)
        while True:
            if check_cancel is not None:
                check_cancel()
            chunk = list(islice(iterator, _SCAN_CHUNK))
            if not chunk:
                return totals
            fingerprints = []
            for record in chunk:
                if check_cancel is not None:
                    check_cancel()
                try:
                    fingerprints.append(verified_arithmetic_fingerprint(record))
                except (ValueError, TypeError, KeyError, OverflowError, RecursionError):
                    totals["invalid"] += 1
            with closing(self._connect()) as db:
                db.execute("BEGIN IMMEDIATE")
                try:
                    self._state(db)
                    for fingerprint in fingerprints:
                        if check_cancel is not None:
                            check_cancel()
                        inserted = db.execute("INSERT INTO arithmetic_fingerprints(fingerprint, origin) "
                                              "VALUES (?, ?) ON CONFLICT(fingerprint) DO NOTHING",
                                              (fingerprint, "history:" + source)).rowcount
                        history_added = db.execute("INSERT INTO arithmetic_history VALUES (?, ?) "
                                   "ON CONFLICT(source, fingerprint) DO NOTHING", (source, fingerprint)).rowcount
                        db.execute("UPDATE arithmetic_state SET registered=registered+?, "
                                   "history_identities=history_identities+? WHERE singleton=1", (inserted, history_added))
                        totals["added" if inserted else "duplicate"] += 1
                    db.commit()
                except BaseException:
                    db.rollback()
                    raise

    def snapshot(self) -> dict:
        """Return durable usage. Capacity is an upper bound on candidate seeds."""
        with closing(self._connect()) as db:
            db.execute("BEGIN")
            try:
                capacity, cursor = self._state(db)
                allocated, registered, imported = db.execute("SELECT allocated, registered, history_identities "
                                                             "FROM arithmetic_state WHERE singleton=1").fetchone()
                db.commit()
            except BaseException:
                db.rollback()
                raise
        return {"capacity": capacity, "cursor": cursor, "allocated": allocated,
                "registered": registered, "history_identities": imported, "exhausted": cursor >= capacity}

    def history_file_seen(self, source: str, signature: str) -> bool:
        with closing(self._connect()) as db:
            row = db.execute("SELECT signature FROM arithmetic_history_files WHERE source=?", (source,)).fetchone()
        return row is not None and row[0] == signature

    def mark_history_file(self, source: str, signature: str):
        """Mark only a complete, validated and unchanged scan as reusable."""
        with closing(self._connect()) as db:
            db.execute("INSERT INTO arithmetic_history_files VALUES (?, ?) ON CONFLICT(source) "
                       "DO UPDATE SET signature=excluded.signature", (source, signature))
