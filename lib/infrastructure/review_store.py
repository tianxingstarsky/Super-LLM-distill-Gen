"""Transactional append-only review events with a small current-head index."""
from __future__ import annotations

from contextlib import contextmanager
import hashlib
import json
import sqlite3

from filelock import FileLock, Timeout

from lib.domain.review_audit import validate_review_event
from lib.infrastructure.audit_stream import JSONStream


def _canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _file_hash(path):
    if not path.exists():
        return ""
    with path.open("rb") as handle:
        return hashlib.file_digest(handle, "sha256").hexdigest()


class ReviewStore:
    def __init__(self, connection, target, source_row):
        self.db, self.target, self.source_row = connection, target, source_row
        self.id_key = "pair_id" if target in {"dpo", "orpo"} else "sample_id"
        self.family = "preference" if target in {"dpo", "orpo"} else target

    def _history_error(self):
        return ValueError(f"invalid_{self.family}_review_history")

    def _validate(self, record):
        if not isinstance(record, dict):
            raise self._history_error()
        source = self.source_row(record.get(self.id_key, ""))
        if source is None:
            raise ValueError(f"orphaned_{self.family}_reviews_refresh_required")
        return validate_review_event(self.target, source, record)

    def _decode(self, row):
        sample_id, decision, payload, digest = row
        if hashlib.sha256(payload.encode("utf-8")).hexdigest() != digest:
            raise self._history_error()
        record = json.loads(payload)
        if record.get(self.id_key) != sample_id or record.get("decision") != decision:
            raise self._history_error()
        return self._validate(record)

    def _insert(self, record):
        payload = _canonical(record)
        cursor = self.db.execute("INSERT INTO events(sample_id,decision,payload,digest) VALUES (?,?,?,?)", (
            record[self.id_key], record["decision"], payload, hashlib.sha256(payload.encode("utf-8")).hexdigest()))
        self.db.execute("INSERT INTO current(sample_id,sequence) VALUES (?,?) "
                        "ON CONFLICT(sample_id) DO UPDATE SET sequence=excluded.sequence", (
                            record[self.id_key], cursor.lastrowid))

    def append(self, record):
        self._validate(record)
        with self.db:
            self._insert(record)
        return record

    def get(self, sample_id):
        head = self.db.execute("SELECT sequence FROM current WHERE sample_id=?", (sample_id,)).fetchone()
        latest = self.db.execute("SELECT MAX(sequence) FROM events WHERE sample_id=?", (sample_id,)).fetchone()[0]
        if (head[0] if head else None) != latest:
            raise self._history_error()
        row = self.db.execute("SELECT e.sample_id,e.decision,e.payload,e.digest FROM current c "
                              "JOIN events e ON c.sequence=e.sequence WHERE c.sample_id=?", (sample_id,)).fetchone()
        if row is None:
            return None
        if row[0] != sample_id:
            raise self._history_error()
        return self._decode(row)

    def decision_rows(self):
        for current_id, sample_id, decision in self.db.execute(
                "SELECT c.sample_id,e.sample_id,e.decision FROM current c JOIN events e ON c.sequence=e.sequence"):
            if current_id != sample_id:
                raise self._history_error()
            yield sample_id, decision

    def verify(self, *, progress=None):
        mismatch = self.db.execute("SELECT c.sample_id FROM current c LEFT JOIN events e ON c.sequence=e.sequence "
            "WHERE e.sequence IS NULL OR e.sample_id<>c.sample_id OR c.sequence<>"
            "(SELECT MAX(sequence) FROM events WHERE sample_id=c.sample_id) LIMIT 1").fetchone()
        orphan = self.db.execute("SELECT sample_id FROM events WHERE sample_id NOT IN (SELECT sample_id FROM current) LIMIT 1").fetchone()
        if mismatch or orphan:
            raise self._history_error()
        total = self.db.execute("SELECT COUNT(*) FROM events").fetchone()[0]
        if progress:
            progress("audit", 0, total)
        for position, row in enumerate(self.db.execute("SELECT sample_id,decision,payload,digest FROM events ORDER BY sequence"), 1):
            self._decode(row)
            if progress and (position % 250 == 0 or position == total):
                progress("audit", position, total)

    def write_snapshot(self, destination):
        """Keep the public audit format while writing one event at a time."""
        with destination.open("w", encoding="utf-8", newline="\n") as handle:
            handle.write('{"events":[')
            for position, (payload,) in enumerate(self.db.execute("SELECT payload FROM events ORDER BY sequence")):
                if position:
                    handle.write(",")
                handle.write(payload)
            handle.write('],"current":{')
            query = "SELECT c.sample_id,e.payload FROM current c JOIN events e ON c.sequence=e.sequence ORDER BY c.sample_id"
            for position, (sample_id, payload) in enumerate(self.db.execute(query)):
                if position:
                    handle.write(",")
                handle.write(json.dumps(sample_id) + ":" + payload)
            handle.write("}}")

    def migrate(self, legacy):
        """Import a legacy JSON snapshot atomically and leave its bytes intact."""
        before = _file_hash(legacy)
        with self.db:
            if legacy.exists():
                self.db.execute("CREATE TEMP TABLE imported_current (sample_id TEXT PRIMARY KEY,payload TEXT NOT NULL)")
                fields = set()
                with legacy.open(encoding="utf-8") as handle:
                    reader = JSONStream(handle)
                    for key in reader.keys():
                        fields.add(key)
                        if key == "events":
                            for record in reader.array():
                                self._validate(record)
                                self._insert(record)
                        elif key == "current":
                            for sample_id in reader.keys():
                                record = reader.value()
                                self.db.execute("INSERT INTO imported_current VALUES (?,?)", (sample_id, _canonical(record)))
                        elif key == "target":
                            if reader.value() != self.target:
                                raise ValueError(f"{self.family}_review_target_mismatch")
                        else:
                            reader.value()
                    reader.finish()
                if not {"events", "current"}.issubset(fields):
                    raise ValueError(f"invalid_{self.family}_review_state")
                query = ("SELECT i.sample_id FROM imported_current i LEFT JOIN current c ON i.sample_id=c.sample_id "
                         "LEFT JOIN events e ON c.sequence=e.sequence WHERE e.payload IS NULL OR e.payload<>i.payload "
                         "UNION ALL SELECT c.sample_id FROM current c LEFT JOIN imported_current i "
                         "ON c.sample_id=i.sample_id WHERE i.sample_id IS NULL LIMIT 1")
                if self.db.execute(query).fetchone():
                    raise self._history_error()
            if _file_hash(legacy) != before:
                raise self._history_error()
            self.db.execute("INSERT INTO metadata VALUES (1,?,?)", (self.target, before))


@contextmanager
def review_store(legacy, target, source_row):
    legacy.parent.mkdir(parents=True, exist_ok=True)
    try:
        with FileLock(str(legacy) + ".lock", timeout=30):
            db = sqlite3.connect(legacy.with_suffix(".sqlite"))
            try:
                db.execute("PRAGMA foreign_keys=ON")
                db.execute("CREATE TABLE IF NOT EXISTS metadata (version INTEGER,target TEXT,legacy_sha256 TEXT)")
                db.execute("CREATE TABLE IF NOT EXISTS events (sequence INTEGER PRIMARY KEY AUTOINCREMENT, "
                           "sample_id TEXT NOT NULL,decision TEXT NOT NULL,payload TEXT NOT NULL,digest TEXT NOT NULL)")
                db.execute("CREATE INDEX IF NOT EXISTS events_sample ON events(sample_id,sequence)")
                db.execute("CREATE TABLE IF NOT EXISTS current (sample_id TEXT PRIMARY KEY,sequence INTEGER NOT NULL "
                           "REFERENCES events(sequence))")
                store = ReviewStore(db, target, source_row)
                metadata = db.execute("SELECT version,target,legacy_sha256 FROM metadata").fetchone()
                if metadata is None:
                    store.migrate(legacy)
                elif metadata[0] != 1 or metadata[1] != target:
                    raise ValueError("invalid_review_store")
                elif metadata[2] != _file_hash(legacy):
                    raise store._history_error()
                yield store
            except sqlite3.DatabaseError as error:
                raise ValueError("invalid_review_store") from error
            finally:
                db.close()
    except Timeout as error:
        raise ValueError("review_store_busy_retry") from error
