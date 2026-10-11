"""Reusable, sealed candidate identities for review-to-rework handoffs.

The first access streams the immutable record artifact once. Subsequent
selections use an indexed lookup, never a row number or an inferred ID.
An authenticated batch context shares its verification with nested selections.
"""
from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
import hashlib
import json
from pathlib import Path
import sqlite3
import uuid

from filelock import FileLock, Timeout

from lib.domain.corpus_review import corpus_identity
from lib.domain.preference_review import pair_identity
from lib.domain.sft_review import sft_identity
from lib.domain.workflow_targets import TARGETS, training_record
from lib.infrastructure.json_stream import iter_json_records
from lib.infrastructure.local_input_cache import _assert_unlinked
from lib.infrastructure.verified_preview import inventory_identity
from lib.io_utils import atomic_json


_ACTIVE = ContextVar("verified_rework_candidate_indexes", default={})
_MAX_RECORD_CHARS = 20_000_000


def _canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _digest(value):
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


def _file_hash(path):
    with path.open("rb") as handle:
        return hashlib.file_digest(handle, "sha256").hexdigest()


def _projection_settings(run):
    recipe = run / "recipe.json"
    _assert_unlinked(recipe)
    if not recipe.is_file():
        return {}
    value = json.loads(recipe.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError("rework_index_integrity_error")
    return {key: value.get(key) for key in ("generation_preferences", "sft_output_style")}


def _review_identity(target, record, projection=None):
    # RLAIF's existing human queue hashes the full record, including AI labels.
    # Other queues hash exactly the native trainer payload.
    if target == "rlaif":
        return pair_identity(record)
    try:
        if target == "sft":
            from lib.infrastructure.training_workflow import preferred_training_record
            projection = projection or {}
            payload = preferred_training_record(target, record, projection.get("generation_preferences"),
                sft_output_style=projection.get("sft_output_style"))
        else:
            payload = training_record(target, record)
    except (KeyError, TypeError, ValueError):
        return None
    identity = (corpus_identity if target == "cpt" else sft_identity if target == "sft"
                else pair_identity if target in {"dpo", "orpo"} else _digest)
    return identity(payload)


class ReworkCandidateIndex:
    def __init__(self, run, target, db, signature, source_identity, projection, manifest):
        self.run, self.target, self.db = run, target, db
        self.signature, self.source_identity = signature, source_identity
        self.projection = projection
        self.manifest = manifest

    def _unchanged(self):
        if (inventory_identity(self.run) != self.source_identity
                or _projection_settings(self.run) != self.projection):
            raise ValueError("human_session_result_changed")

    def _decode(self, row):
        if row is None:
            raise ValueError("human_session_candidate_not_found")
        candidate_id, payload, content_hash, review_identity, status = row
        record = json.loads(payload)
        if (not isinstance(record, dict) or record.get("id") != candidate_id
                or _digest(record) != content_hash or record.get("status") != status
                or (status == "eligible" and _review_identity(self.target, record, self.projection) != review_identity)):
            raise ValueError("rework_index_integrity_error")
        return record

    def candidates(self, candidate_ids):
        self._unchanged()
        if (not isinstance(candidate_ids, (list, tuple)) or not 1 <= len(candidate_ids) <= 200
                or any(not isinstance(value, str) or not value or len(value) > 500 for value in candidate_ids)
                or len(set(candidate_ids)) != len(candidate_ids)):
            raise ValueError("invalid_human_session_issue_selection")
        rows = self.db.execute("SELECT id,payload,digest,review_id,status FROM candidates WHERE id IN ("
            + ",".join("?" for _ in candidate_ids) + ")", candidate_ids).fetchall()
        found = {row[0]: self._decode(row) for row in rows}
        if len(found) != len(candidate_ids):
            raise ValueError("human_session_candidate_not_found")
        self._unchanged()
        return found

    def reviewed_candidate(self, review_id):
        """Return the real sealed ID matching one exact native review payload."""
        self._unchanged()
        rows = self.db.execute("SELECT id,payload,digest,review_id,status FROM candidates "
            "WHERE review_id=? AND status='eligible' LIMIT 2", (review_id,)).fetchall()
        if len(rows) != 1:
            raise ValueError("review_rework_candidate_mapping_missing" if not rows
                             else "review_rework_candidate_mapping_ambiguous")
        record = self._decode(rows[0])
        return record["id"]


def _build(run, target, destination, receipt, signature, source_identity, projection):
    pending = destination.with_name(destination.stem + "." + uuid.uuid4().hex + ".pending.sqlite")
    db = sqlite3.connect(pending)
    try:
        db.executescript("CREATE TABLE metadata(signature TEXT NOT NULL);"
            "CREATE TABLE candidates(position INTEGER PRIMARY KEY,id TEXT UNIQUE NOT NULL,"
            "payload TEXT NOT NULL,digest TEXT NOT NULL,review_id TEXT,status TEXT);"
            "CREATE INDEX candidates_review_identity ON candidates(review_id,status);")
        records = iter_json_records(run / "artifacts" / f"{target}.records.json",
                                   max_record_chars=_MAX_RECORD_CHARS)
        try:
            for position, record in enumerate(records):
                candidate_id = record.get("id")
                # Historical files with no sealed ID can be reviewed but cannot
                # be authenticated as workflow repair parents.
                if not isinstance(candidate_id, str) or not candidate_id or len(candidate_id) > 500:
                    continue
                review_id = _review_identity(target, record, projection) if record.get("status") == "eligible" else None
                db.execute("INSERT INTO candidates VALUES (?,?,?,?,?,?)", (position, candidate_id,
                    _canonical(record), _digest(record), review_id, record.get("status")))
        finally:
            records.close()
        if inventory_identity(run) != source_identity or _projection_settings(run) != projection:
            raise ValueError("human_session_result_changed")
        db.execute("INSERT INTO metadata VALUES (?)", (signature,))
        db.commit()
        db.close()
        pending.replace(destination)
        atomic_json(receipt, {"version": 1, "signature": signature, "sha256": _file_hash(destination)})
    except sqlite3.IntegrityError as error:
        raise ValueError("rework_duplicate_candidate_id") from error
    finally:
        db.close()
        pending.unlink(missing_ok=True)


@contextmanager
def rework_candidate_index(run: Path, target: str):
    """Verify sealed artifacts, then share one bounded index across a batch.

    The context is read-only and never starts a model. A caller transferring
    many rejected rows can hold it around its bounded save_issues calls, so each
    nested candidate selection reuses this one verification and stream pass.
    """
    from lib.infrastructure.training_workflow import verify_artifacts
    run = Path(run)
    if target not in TARGETS:
        raise ValueError("unknown_training_target")
    _assert_unlinked(run)
    _assert_unlinked(run / "artifacts")
    key = (str(run.resolve()), target)
    if previous := _ACTIVE.get().get(key):
        previous._unchanged()
        yield previous
        return
    manifest = verify_artifacts(run)
    source_identity = inventory_identity(run)
    projection = _projection_settings(run)
    records_name = f"{target}.records.json"
    record_hash = manifest.get("sha256", {}).get(records_name)
    if not isinstance(record_hash, str) or not (run / "artifacts" / records_name).is_file():
        raise ValueError("review_rework_candidate_mapping_missing")
    signature = _digest({"version": 1, "target": target, "record_sha256": record_hash,
                         "training_sha256": manifest["sha256"].get(f"{target}.jsonl"),
                         "projection": projection})
    folder = run / "human-review" / ".indexes"
    _assert_unlinked(folder)
    folder.mkdir(parents=True, exist_ok=True)
    destination, receipt = folder / f"{target}.rework.sqlite", folder / f"{target}.rework.receipt.json"
    _assert_unlinked(destination)
    _assert_unlinked(receipt)
    _assert_unlinked(Path(str(destination) + ".lock"))
    try:
        with FileLock(str(destination) + ".lock", timeout=30):
            valid = False
            if destination.is_file() and receipt.is_file():
                try:
                    saved = json.loads(receipt.read_text(encoding="utf-8"))
                    valid = (saved.get("version") == 1 and saved.get("signature") == signature
                             and saved.get("sha256") == _file_hash(destination))
                    if valid:
                        check = sqlite3.connect(destination)
                        try:
                            valid = check.execute("SELECT signature FROM metadata").fetchone() == (signature,)
                            check.execute("SELECT position,id,payload,digest,review_id,status FROM candidates LIMIT 0")
                        finally:
                            check.close()
                except (OSError, ValueError, sqlite3.DatabaseError):
                    valid = False
            if not valid:
                _build(run, target, destination, receipt, signature, source_identity, projection)
            db = sqlite3.connect(destination)
            index = ReworkCandidateIndex(run, target, db, signature, source_identity, projection, manifest)
            token = _ACTIVE.set({**_ACTIVE.get(), key: index})
            try:
                yield index
                index._unchanged()
            except sqlite3.DatabaseError as error:
                raise ValueError("rework_index_integrity_error") from error
            finally:
                _ACTIVE.reset(token)
                db.close()
    except Timeout as error:
        raise ValueError("rework_index_busy_retry") from error


def lookup_candidates(run: Path, target: str, candidate_ids) -> dict[str, dict]:
    with rework_candidate_index(run, target) as index:
        return index.candidates(candidate_ids)
