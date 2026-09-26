"""Disposable SQLite indexes for verified, immutable training artifacts."""
from __future__ import annotations

from contextlib import contextmanager
import hashlib
import json
import sqlite3

from filelock import FileLock, Timeout

from lib.domain.review_queue import DECISIONS, review_query
from lib.infrastructure.json_stream import iter_json_records
from lib.infrastructure.review_artifacts import iter_review_rows


class ReviewArtifactIndex:
    def __init__(self, connection, validate, identity, target):
        self.connection, self.validate, self.identity, self.target = connection, validate, identity, target

    @property
    def count(self):
        return self.connection.execute("SELECT COUNT(*) FROM candidates").fetchone()[0]

    def row(self, sample_id):
        record = self.connection.execute("SELECT payload FROM candidates WHERE id=?", (sample_id,)).fetchone()
        if record is None:
            return None
        row = self.validate(json.loads(record[0]))
        if self.identity(row) != sample_id:
            raise ValueError("review_index_integrity_error")
        return row

    def page(self, current, *, offset=0, limit=20, decision=None):
        offset, limit, decision = review_query(offset, limit, decision)
        db = self.connection
        db.execute("CREATE TEMP TABLE decisions (id TEXT PRIMARY KEY, decision TEXT NOT NULL)")
        decisions = (current.decision_rows() if hasattr(current, "decision_rows") else (
            (key, value.get("decision")) for key, value in current.items()))
        db.executemany("INSERT INTO decisions VALUES (?,?)", (
            (key, value if value in DECISIONS else "pending") for key, value in decisions))
        counts = dict.fromkeys(("approved", "rejected", "skipped", "pending"), 0)
        for state, count in db.execute("SELECT COALESCE(d.decision,'pending'),COUNT(*) FROM candidates c "
                                      "LEFT JOIN decisions d ON c.id=d.id GROUP BY COALESCE(d.decision,'pending')"):
            counts[state] = count
        matched = sum(counts.values()) if decision is None else counts[decision]
        query = "SELECT c.id,c.payload,c.evidence FROM candidates c LEFT JOIN decisions d ON c.id=d.id "
        args = []
        if decision is not None:
            query += "WHERE COALESCE(d.decision,'pending')=? "
            args.append(decision)
        query += "ORDER BY c.position LIMIT ? OFFSET ?"
        args.extend((limit, offset))
        preference = self.target in {"dpo", "orpo"}
        id_key, payload_key = ("pair_id", "pair") if preference else ("sample_id", "row")
        items = []
        for sample_id, payload, evidence in db.execute(query, args):
            row = self.validate(json.loads(payload))
            if self.identity(row) != sample_id:
                raise ValueError("review_index_integrity_error")
            items.append({id_key: sample_id, payload_key: row, "evidence": json.loads(evidence),
                          "review": current.get(sample_id)})
        return {"items": items, "total": sum(counts.values()), "matched": matched, "counts": counts}


def _build(path, destination, target, signature, validate, identity):
    staging = destination.with_suffix(".pending.sqlite")
    if staging.exists():
        staging.unlink()
    db = sqlite3.connect(staging)
    try:
        db.execute("CREATE TABLE metadata (signature TEXT NOT NULL)")
        db.execute("CREATE TABLE candidates (position INTEGER PRIMARY KEY, id TEXT UNIQUE NOT NULL, "
                   "payload TEXT NOT NULL, evidence TEXT NOT NULL DEFAULT '{}')")
        # Validation consumes the complete source before the index can be published.
        preference = target in {"dpo", "orpo"}
        id_key, payload_key = ("pair_id", "pair") if preference else ("sample_id", "row")
        rows = iter_review_rows(path, target, validate, identity, payload_key=payload_key, id_key=id_key)
        for position, item in enumerate(rows):
            db.execute("INSERT INTO candidates(position,id,payload) VALUES (?,?,?)", (
                position, item[id_key], json.dumps(item[payload_key], ensure_ascii=False)))
        records = path / "artifacts" / f"{target}.records.json"
        if records.is_file() and target in {"sft", "cpt"}:
            fields = ("id", "source_id", "kind", "location", "evidence_level", "judge", "quotes", "citations")
            for record in iter_json_records(records):
                if record.get("status") != "eligible":
                    continue
                payload = {"text": record.get("text")} if target == "cpt" else {"messages": record.get("messages")}
                if target == "sft" and record.get("tools"):
                    payload["tools"] = record["tools"]
                evidence = {key: record[key] for key in fields if key in record}
                db.execute("UPDATE candidates SET evidence=? WHERE id=? AND evidence='{}'", (
                    json.dumps(evidence, ensure_ascii=False), identity(payload)))
        db.execute("INSERT INTO metadata VALUES (?)", (signature,))
        db.commit()
    finally:
        db.close()
    staging.replace(destination)


@contextmanager
def _index_lock(destination):
    try:
        with FileLock(str(destination) + ".lock", timeout=30):
            yield
    except Timeout as error:
        raise ValueError("review_index_busy_retry") from error


@contextmanager
def review_index(path, target, validate, identity):
    """Use only after the driver verifies the complete artifact manifest.

    The index is an expendable cache. It never replaces source artifacts or the
    review audit. Source hashes and the cache schema determine its lifetime.
    """
    manifest = json.loads((path / "artifacts" / "manifest.json").read_text(encoding="utf-8"))
    names = (f"{target}.jsonl", f"{target}.records.json")
    signature = hashlib.sha256(json.dumps({"schema": 1, "target": target,
        "sources": {name: manifest["sha256"].get(name) for name in names}}, sort_keys=True).encode()).hexdigest()
    folder = path / "human-review" / ".indexes"
    folder.mkdir(parents=True, exist_ok=True)
    destination = folder / f"{target}.sqlite"
    with _index_lock(destination):
        valid = False
        if destination.is_file():
            db = sqlite3.connect(destination)
            try:
                stored = db.execute("SELECT signature FROM metadata").fetchone()
                valid = stored is not None and stored[0] == signature
                db.execute("SELECT position,id,payload,evidence FROM candidates LIMIT 0")
            except sqlite3.DatabaseError:
                valid = False
            finally:
                db.close()
        if not valid:
            _build(path, destination, target, signature, validate, identity)
        db = sqlite3.connect(destination)
        try:
            yield ReviewArtifactIndex(db, validate, identity, target)
        finally:
            db.close()
