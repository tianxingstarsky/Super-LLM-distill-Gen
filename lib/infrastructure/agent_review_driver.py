"""Disk-backed human review of Agent trajectories with recorded replay evidence.

The positive training JSONL, complete replay sidecar and negative JSONL are
one review source. Negative trajectories have a separate read-only queue.
"""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import secrets
import sqlite3

from filelock import FileLock, Timeout

from lib.domain.agent_trajectory import REPLAY_POLICY_VERSION, assess_recorded_trajectory
from lib.domain.sft_review import validate_sft_record
from lib.domain.workflow_quality import text_issue
from lib.domain.workflow_targets import training_record
from lib.infrastructure.json_stream import iter_json_records
from lib.infrastructure.training_workflow import (
    digest, file_hash, list_runs, read_json, run_path, verify_artifacts,
)


_SOURCE_FILES = ("agent.jsonl", "agent.records.json", "agent.negative.jsonl")
_HEX64 = re.compile(r"[0-9a-f]{64}")
_REPLAY_METHODS = frozenset({
    "bounded_arithmetic_replay", "snapshot_json_pointer_replay",
    "isolated_docker_ledger_replay", "mixed_verified_tool_replay",
    "bounded_local_replay",
})
_DECISIONS = frozenset({"approved", "rejected"})
_MAX_PAGE = 20
_MAX_OFFSET = 50_000
_MAX_JSONL_ROW_BYTES = 4 * 1024 * 1024
_MAX_RECORD_BYTES = 8 * 1024 * 1024
_MAX_PAGE_BYTES = 16 * 1024 * 1024
_MAX_EVENTS_PER_CANDIDATE = 8
_MAX_EVENTS_PER_RUN = 100_000


def _canonical(value) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _sha(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _page(offset: int, limit: int):
    if (type(offset) is not int or not 0 <= offset <= _MAX_OFFSET
            or type(limit) is not int or not 1 <= limit <= _MAX_PAGE):
        raise ValueError("invalid_agent_review_page")
    return offset, limit


def _page_budget(db, query: str, args: tuple):
    size = db.execute(query, args).fetchone()[0] or 0
    if size > _MAX_PAGE_BYTES:
        raise ValueError("agent_review_page_too_large_reduce_limit")


def _source(output: Path, run_id: str):
    path = run_path(output, run_id)
    if (path.is_symlink() or (path / "artifacts").is_symlink()
            or (path / "state.json").is_symlink()
            or (path / "artifacts" / "manifest.json").is_symlink()):
        raise ValueError("linked_agent_review_source")
    state = read_json(path / "state.json")
    if (state.get("id") != run_id or state.get("status") not in {"completed", "needs_attention"}
            or "agent" not in state.get("targets", [])):
        raise ValueError("agent_run_not_ready")
    manifest = verify_artifacts(path)
    hashes = manifest.get("sha256", {})
    if (manifest.get("run_id") != run_id or
            any(not isinstance(hashes.get(name), str) or not _HEX64.fullmatch(hashes[name])
                for name in _SOURCE_FILES)):
        raise ValueError("agent_review_evidence_missing")
    positive = manifest.get("counts", {}).get("agent")
    negative = manifest.get("negative_counts", {}).get("agent", 0)
    if (type(positive) is not int or positive < 0 or
            type(negative) is not int or negative < 0):
        raise ValueError("agent_review_counts_invalid")
    source_hashes = {name: hashes[name] for name in _SOURCE_FILES}
    manifest_hash = file_hash(path / "artifacts" / "manifest.json")
    signature = _sha(_canonical({"run_id": run_id, "manifest_sha256": manifest_hash,
                                 "artifact_sha256": source_hashes}))
    return path, manifest, signature, source_hashes, manifest_hash


def _jsonl(path: Path):
    with path.open("rb") as handle:
        while line := handle.readline(_MAX_JSONL_ROW_BYTES + 1):
            if len(line) > _MAX_JSONL_ROW_BYTES:
                raise ValueError("agent_review_record_too_large")
            if not line.strip():
                continue
            row = json.loads(line.decode("utf-8"))
            if type(row) is not dict:
                raise ValueError("agent_review_artifact_mismatch")
            yield row


def _verified_positive(record: dict, native: dict):
    if (record.get("status") != "eligible" or "negative" in record
            or record.get("evidence_level") not in {
                "local_tool_replay", "isolated_container_replay"}):
        raise ValueError("agent_review_replay_evidence_invalid")
    verification = record.get("verification")
    if type(verification) is not dict or verification.get("method") not in _REPLAY_METHODS:
        raise ValueError("agent_review_replay_evidence_invalid")
    calls = verification.get("verified_call_ids")
    turns = verification.get("verified_turns")
    evidence = verification.get("call_evidence")
    if (verification.get("policy") != REPLAY_POLICY_VERSION
            or type(calls) is not list or not calls or
            any(type(call) is not str or not call for call in calls)
            or len(set(calls)) != len(calls)
            or type(turns) is not list or not turns or
            any(type(turn) is not int or turn < 1 for turn in turns)
            or type(evidence) is not list or len(evidence) != len(calls)
            or any(type(item) is not dict or item.get("call_id") != call
                   or item.get("method") not in {
                       "bounded_arithmetic_replay", "snapshot_json_pointer_replay",
                       "isolated_docker_ledger_replay"}
                   for call, item in zip(calls, evidence))
            or verification.get("source_snapshot_sha256") != record.get("source_id")
            or not isinstance(record.get("original_messages_sha256"), str)
            or not _HEX64.fullmatch(record["original_messages_sha256"])):
        raise ValueError("agent_review_replay_evidence_invalid")
    pruned = verification.get("pruned_call_ids")
    if (type(pruned) is not list or any(type(call) is not str for call in pruned)
            or len(set(pruned)) != len(pruned)):
        raise ValueError("agent_review_replay_evidence_invalid")
    retained = []
    if type(native.get("messages")) is not list:
        raise ValueError("agent_review_replay_evidence_invalid")
    for message in native["messages"]:
        if type(message) is not dict or type(message.get("tool_calls", [])) is not list:
            raise ValueError("agent_review_replay_evidence_invalid")
        for call in message.get("tool_calls", []):
            if type(call) is not dict or type(call.get("id")) is not str:
                raise ValueError("agent_review_replay_evidence_invalid")
            retained.append(call["id"])
    if (not retained or len(set(retained)) != len(retained)
            or set(retained) | set(pruned) != set(calls)
            or set(retained) & set(pruned)):
        raise ValueError("agent_review_replay_evidence_invalid")
    methods = {item["method"] for item in evidence}
    expected_method = (next(iter(methods)) if len(methods) == 1 else
                       "mixed_verified_tool_replay" if "isolated_docker_ledger_replay" in methods
                       else "bounded_local_replay")
    level = ("isolated_container_replay" if expected_method in {
        "isolated_docker_ledger_replay", "mixed_verified_tool_replay"} else "local_tool_replay")
    user_turns = sum(message.get("role") == "user" for message in native["messages"])
    if (verification["method"] != expected_method or record["evidence_level"] != level
            or turns != list(range(1, user_turns + 1))
            or (not pruned and record["original_messages_sha256"] != digest(record["messages"]))):
        raise ValueError("agent_review_replay_evidence_invalid")
    try:
        projected = training_record("agent", record)
        validate_sft_record(native)
    except (KeyError, TypeError, ValueError) as error:
        raise ValueError("agent_review_artifact_mismatch") from error
    if projected != native:
        raise ValueError("agent_review_artifact_mismatch")
    # Only an unpruned local tool trace can be independently replayed from the
    # native row alone. Snapshot and container traces remain inspectable, but
    # the review backend cannot safely publish them without their inputs.
    if pruned or methods != {"bounded_arithmetic_replay"}:
        return False
    replay = assess_recorded_trajectory(native["messages"],
                                        source_snapshot_sha256=record["source_id"])
    if (replay.get("status") != "eligible" or replay.get("messages") != native["messages"]
            or replay.get("verification") != verification):
        raise ValueError("agent_review_independent_replay_failed")
    return True


def _artifact_rows(path: Path, manifest: dict, signature: str):
    """Validate paired files one row at a time; SQLite enforces unique IDs."""
    folder = path / "artifacts"
    native = _jsonl(folder / "agent.jsonl")
    negatives = _jsonl(folder / "agent.negative.jsonl")
    missing = object()
    positive_count = negative_count = 0
    sources = manifest.get("sources")
    if type(sources) is not list or any(type(item) is not dict or
                                        type(item.get("sha256")) is not str for item in sources):
        raise ValueError("agent_review_source_identity_invalid")
    source_ids = {item["sha256"] for item in sources}
    records = iter_json_records(folder / "agent.records.json",
                                max_record_chars=_MAX_RECORD_BYTES // 4)
    try:
        for record in records:
            record_id = record.get("id")
            source_id = record.get("source_id")
            if (type(record_id) is not str or not _HEX64.fullmatch(record_id)
                    or type(source_id) is not str or not _HEX64.fullmatch(source_id)
                    or source_id not in source_ids):
                raise ValueError("agent_review_source_identity_invalid")
            if len(_canonical(record).encode("utf-8")) > _MAX_RECORD_BYTES:
                raise ValueError("agent_review_record_too_large")
            if record.get("status") == "eligible":
                row = next(native, missing)
                if row is missing:
                    raise ValueError("agent_review_positive_count_mismatch")
                independent_replay = _verified_positive(record, row)
                kind, position = "positive", positive_count
                positive_count += 1
            elif record.get("negative") is not None:
                row = next(negatives, missing)
                if (row is missing or type(record["negative"]) is not dict
                        or row != record["negative"] or row.get("id") != record_id
                        or row.get("source_id") != source_id
                        or row.get("original_messages_sha256") != record.get("original_messages_sha256")
                        or type(row.get("failure")) is not str
                        or type(row.get("evidence")) is not dict):
                    raise ValueError("agent_review_negative_mismatch")
                kind, position, independent_replay = "negative", negative_count, False
                negative_count += 1
            else:
                continue
            row_json, record_json = _canonical(row), _canonical(record)
            row_sha, record_sha = _sha(row_json), _sha(record_json)
            candidate_id = _sha(_canonical({"run_id": path.name, "source_signature": signature,
                                            "kind": kind, "position": position,
                                            "native_sha256": row_sha,
                                            "record_sha256": record_sha}))
            yield (kind, position, candidate_id, record_id, row_json, record_json,
                   row_sha, record_sha, int(independent_replay))
        if next(native, missing) is not missing or next(negatives, missing) is not missing:
            raise ValueError("agent_review_artifact_count_mismatch")
        if (positive_count != manifest["counts"]["agent"] or
                negative_count != manifest.get("negative_counts", {}).get("agent", 0)):
            raise ValueError("agent_review_artifact_count_mismatch")
    finally:
        records.close()
        native.close()
        negatives.close()


def _build(path: Path, manifest: dict, signature: str, destination: Path):
    staging = destination.parent / f".agent-{secrets.token_hex(8)}.pending.sqlite"
    db = sqlite3.connect(staging)
    try:
        db.executescript("""
            CREATE TABLE metadata (schema_version INTEGER NOT NULL, run_id TEXT NOT NULL,
                                   source_signature TEXT NOT NULL);
            CREATE TABLE identities (record_id TEXT PRIMARY KEY);
            CREATE TABLE positives (position INTEGER PRIMARY KEY, candidate_id TEXT UNIQUE NOT NULL,
                                    record_id TEXT NOT NULL, native_json TEXT NOT NULL,
                                    record_json TEXT NOT NULL, native_sha256 TEXT NOT NULL,
                                    record_sha256 TEXT NOT NULL, independent_replay INTEGER NOT NULL);
            CREATE TABLE negatives (position INTEGER PRIMARY KEY, candidate_id TEXT UNIQUE NOT NULL,
                                    record_id TEXT NOT NULL, native_json TEXT NOT NULL,
                                    record_json TEXT NOT NULL, native_sha256 TEXT NOT NULL,
                                    record_sha256 TEXT NOT NULL, independent_replay INTEGER NOT NULL);
            CREATE TABLE events (sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                                 candidate_id TEXT NOT NULL REFERENCES positives(candidate_id),
                                 version INTEGER NOT NULL, payload TEXT NOT NULL,
                                 digest TEXT NOT NULL, UNIQUE(candidate_id,version));
            CREATE TABLE current (candidate_id TEXT PRIMARY KEY REFERENCES positives(candidate_id),
                                  sequence INTEGER NOT NULL REFERENCES events(sequence),
                                  version INTEGER NOT NULL);
        """)
        db.execute("PRAGMA foreign_keys=ON")
        with db:
            for kind, position, candidate_id, record_id, native, record, native_sha, record_sha, ready in (
                    _artifact_rows(path, manifest, signature)):
                db.execute("INSERT INTO identities VALUES (?)", (record_id,))
                table = "positives" if kind == "positive" else "negatives"
                db.execute(f"INSERT INTO {table} VALUES (?,?,?,?,?,?,?,?)", (
                    position, candidate_id, record_id, native, record, native_sha, record_sha, ready))
            db.execute("INSERT INTO metadata VALUES (1,?,?)", (path.name, signature))
        if _source(path.parent.parent, path.name)[2] != signature:
            raise ValueError("agent_review_source_changed")
        db.close()
        staging.replace(destination)
    except BaseException:
        db.close()
        staging.unlink(missing_ok=True)
        raise


@contextmanager
def _open(output: Path, run_id: str):
    path, manifest, signature, source_hashes, manifest_hash = _source(output, run_id)
    folder = path / "human-review"
    if folder.is_symlink():
        raise ValueError("linked_agent_review_store")
    folder.mkdir(exist_ok=True)
    destination = folder / "agent.sqlite"
    lock_path = folder / "agent.sqlite.lock"
    if destination.is_symlink() or lock_path.is_symlink():
        raise ValueError("linked_agent_review_store")
    try:
        with FileLock(str(lock_path), timeout=30):
            if not destination.is_file():
                _build(path, manifest, signature, destination)
            db = sqlite3.connect(destination)
            try:
                db.execute("PRAGMA foreign_keys=ON")
                row = db.execute("SELECT schema_version,run_id,source_signature FROM metadata").fetchone()
                if row != (1, run_id, signature):
                    raise ValueError("agent_review_source_changed")
                yield path, manifest, signature, source_hashes, manifest_hash, db
            except sqlite3.DatabaseError as error:
                raise ValueError("invalid_agent_review_store") from error
            finally:
                db.close()
    except Timeout as error:
        raise ValueError("agent_review_busy_retry") from error


def _event(payload: str, digest: str, *, candidate_id: str, version: int,
           signature: str, native_sha: str, record_sha: str):
    if _sha(payload) != digest:
        raise ValueError("invalid_agent_review_history")
    record = json.loads(payload)
    if (record.get("candidate_id") != candidate_id or record.get("version") != version
            or record.get("source_signature") != signature
            or record.get("native_sha256") != native_sha
            or record.get("record_sha256") != record_sha
            or record.get("decision") not in _DECISIONS):
        raise ValueError("invalid_agent_review_history")
    return record


class FilesystemAgentReviewDriver:
    """Bounded review API; no negative decision or release method exists."""

    def __init__(self, output: Path):
        self.output = Path(output)

    def reviewable_runs(self) -> list[dict]:
        result = []
        for state in list_runs(self.output):
            if state.get("status") not in {"completed", "needs_attention"} or "agent" not in state.get("targets", []):
                continue
            try:
                _, manifest, _, _, _ = _source(self.output, state["id"])
            except (OSError, ValueError, TypeError, KeyError):
                continue
            result.append({"id": state["id"], "name": state.get("name", "Agent workflow"),
                           "status": state["status"], "positive_count": manifest["counts"]["agent"],
                           "negative_count": manifest.get("negative_counts", {}).get("agent", 0)})
        return result

    def queue(self, run_id: str, *, kind: str = "positive", offset: int = 0,
              limit: int = 20, decision: str | None = None) -> dict:
        offset, limit = _page(offset, limit)
        if type(kind) is not str or kind not in {"positive", "negative"}:
            raise ValueError("invalid_agent_review_kind")
        if kind == "negative" and decision is not None:
            raise ValueError("negative_agent_trajectories_are_read_only")
        if decision is not None and (type(decision) is not str or
                                     decision not in _DECISIONS | {"pending"}):
            raise ValueError("invalid_agent_review_filter")
        with _open(self.output, run_id) as (_, _, signature, _, _, db):
            if kind == "negative":
                total = db.execute("SELECT COUNT(*) FROM negatives").fetchone()[0]
                _page_budget(db, "SELECT SUM(bytes) FROM (SELECT "
                    "length(CAST(native_json AS BLOB))+length(CAST(record_json AS BLOB)) AS bytes "
                    "FROM negatives ORDER BY position LIMIT ? OFFSET ?)", (limit, offset))
                rows = db.execute("SELECT candidate_id,position,native_json,record_json,"
                                  "native_sha256,record_sha256 FROM negatives "
                                  "ORDER BY position LIMIT ? OFFSET ?", (limit, offset))
                items = []
                for key, position, native, record, native_sha, record_sha in rows:
                    if _sha(native) != native_sha or _sha(record) != record_sha:
                        raise ValueError("invalid_agent_review_store")
                    items.append({"candidate_id": key, "ordinal": position + 1,
                                  "negative": json.loads(native), "record": json.loads(record),
                                  "native_sha256": native_sha, "record_sha256": record_sha,
                                  "independent_replay": False})
                return {"run_id": run_id, "source_signature": signature, "kind": kind,
                        "total": total, "matched": total, "items": items,
                        "next_offset": offset + len(items) if offset + len(items) < total else None}
            counts = {"approved": 0, "rejected": 0, "pending": 0}
            for status, count in db.execute("SELECT COALESCE(e.decision,'pending'),COUNT(*) "
                    "FROM positives p LEFT JOIN current c ON c.candidate_id=p.candidate_id "
                    "LEFT JOIN (SELECT sequence,json_extract(payload,'$.decision') AS decision "
                    "FROM events) e ON e.sequence=c.sequence "
                    "GROUP BY COALESCE(e.decision,'pending')"):
                if status not in counts:
                    raise ValueError("invalid_agent_review_history")
                counts[status] = count
            matched = sum(counts.values()) if decision is None else counts[decision]
            where = "" if decision is None else "WHERE COALESCE(json_extract(e.payload,'$.decision'),'pending')=? "
            args = () if decision is None else (decision,)
            _page_budget(db, "SELECT SUM(bytes) FROM (SELECT "
                "length(CAST(p.native_json AS BLOB))+length(CAST(p.record_json AS BLOB)) AS bytes "
                "FROM positives p LEFT JOIN current c ON c.candidate_id=p.candidate_id "
                "LEFT JOIN events e ON e.sequence=c.sequence " + where +
                "ORDER BY p.position LIMIT ? OFFSET ?)", (*args, limit, offset))
            rows = db.execute("SELECT p.candidate_id,p.position,p.native_json,p.record_json,"
                "p.native_sha256,p.record_sha256,p.independent_replay,c.version,e.payload,e.digest FROM positives p "
                "LEFT JOIN current c ON c.candidate_id=p.candidate_id "
                "LEFT JOIN events e ON e.sequence=c.sequence " + where +
                "ORDER BY p.position LIMIT ? OFFSET ?", (*args, limit, offset))
            items = []
            for key, position, native, record, native_sha, record_sha, ready, version, payload, digest in rows:
                review = (_event(payload, digest, candidate_id=key, version=version,
                                 signature=signature, native_sha=native_sha,
                                 record_sha=record_sha) if payload is not None else None)
                if _sha(native) != native_sha or _sha(record) != record_sha:
                    raise ValueError("invalid_agent_review_store")
                native_row, sidecar_row = json.loads(native), json.loads(record)
                if int(_verified_positive(sidecar_row, native_row)) != ready:
                    raise ValueError("invalid_agent_review_store")
                items.append({"candidate_id": key, "ordinal": position + 1,
                              "native": native_row, "record": sidecar_row,
                              "native_sha256": native_sha, "record_sha256": record_sha,
                              "independent_replay": bool(ready),
                              "review_status": review["decision"] if review else "pending",
                              "review_version": version or 0, "review": review})
            return {"run_id": run_id, "source_signature": signature, "kind": kind,
                    "total": sum(counts.values()), "matched": matched, "counts": counts,
                    "items": items,
                    "next_offset": offset + len(items) if offset + len(items) < matched else None}

    def decide(self, run_id: str, candidate_id: str, *, source_signature: str,
               native_sha256: str, record_sha256: str, expected_version: int,
               decision: str, reviewer: str, reason: str = "") -> dict:
        if (type(candidate_id) is not str or not _HEX64.fullmatch(candidate_id)
                or type(source_signature) is not str or not _HEX64.fullmatch(source_signature)
                or type(native_sha256) is not str or not _HEX64.fullmatch(native_sha256)
                or type(record_sha256) is not str or not _HEX64.fullmatch(record_sha256)
                or type(expected_version) is not int or not 0 <= expected_version <= _MAX_EVENTS_PER_CANDIDATE
                or type(decision) is not str or decision not in _DECISIONS
                or type(reviewer) is not str
                or not reviewer.strip() or len(reviewer) > 128
                or text_issue(reviewer)
                or type(reason) is not str or len(reason) > 2000
                or (reason.strip() and text_issue(reason))):
            raise ValueError("invalid_agent_review_decision")
        with _open(self.output, run_id) as (path, _, signature, _, _, db):
            if source_signature != signature:
                raise ValueError("stale_agent_review_source")
            try:
                db.execute("BEGIN IMMEDIATE")
                row = db.execute("SELECT native_json,record_json,native_sha256,record_sha256,independent_replay "
                                 "FROM positives WHERE candidate_id=?", (candidate_id,)).fetchone()
                if row is None:
                    raise ValueError("agent_positive_candidate_not_found")
                native, record, saved_native_sha, saved_record_sha, ready = row
                if (saved_native_sha != native_sha256 or saved_record_sha != record_sha256
                        or _sha(native) != saved_native_sha or _sha(record) != saved_record_sha):
                    raise ValueError("stale_agent_review_source")
                independently_replayed = _verified_positive(json.loads(record), json.loads(native))
                if int(independently_replayed) != ready:
                    raise ValueError("invalid_agent_review_store")
                if decision == "approved" and not independently_replayed:
                    raise ValueError("agent_review_independent_replay_required")
                current = db.execute("SELECT c.version,e.payload,e.digest FROM current c "
                    "JOIN events e ON e.sequence=c.sequence WHERE c.candidate_id=?",
                    (candidate_id,)).fetchone()
                version = current[0] if current else 0
                if version != expected_version:
                    raise ValueError("stale_agent_review_version")
                if (version >= _MAX_EVENTS_PER_CANDIDATE or
                        db.execute("SELECT COUNT(*) FROM events").fetchone()[0] >= _MAX_EVENTS_PER_RUN):
                    raise ValueError("agent_review_event_limit")
                if current:
                    _event(current[1], current[2], candidate_id=candidate_id,
                           version=version, signature=signature,
                           native_sha=native_sha256, record_sha=record_sha256)
                event = {"candidate_id": candidate_id, "source_signature": signature,
                         "native_sha256": native_sha256, "record_sha256": record_sha256,
                         "version": version + 1, "decision": decision,
                         "reviewer": reviewer.strip(), "reason": reason.strip(),
                         "reviewed_at": datetime.now(timezone.utc).isoformat()}
                payload = _canonical(event)
                cursor = db.execute("INSERT INTO events(candidate_id,version,payload,digest) "
                                    "VALUES (?,?,?,?)", (candidate_id, version + 1, payload, _sha(payload)))
                db.execute("INSERT INTO current(candidate_id,sequence,version) VALUES (?,?,?) "
                           "ON CONFLICT(candidate_id) DO UPDATE SET sequence=excluded.sequence,"
                           "version=excluded.version", (candidate_id, cursor.lastrowid, version + 1))
                if _source(self.output, run_id)[2] != signature:
                    raise ValueError("agent_review_source_changed")
                db.commit()
                return event
            except BaseException:
                db.rollback()
                raise
