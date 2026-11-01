"""Append-only user-scoped experience editions with complete source lineage."""
from __future__ import annotations

from copy import deepcopy
from contextlib import contextmanager
from datetime import datetime, timezone
import json
import os
import sqlite3
from pathlib import Path
import tempfile
import uuid

from filelock import FileLock

from ..domain.experience_evolution import FORMAT_VERSION, canonical, digest, validate_identifier, validate_scope

MAX_VERSION_BYTES = 2 * 1024 * 1024


class ExperienceEvolutionRepository:
    """The composition root supplies a directory already scoped to this user."""
    def __init__(self, root: Path):
        self.root = Path(root).absolute()
        if self.root.is_symlink():
            raise ValueError("experience_evolution_unsafe_path")
        self.root.mkdir(parents=True, exist_ok=True)

    def _path(self, identifier):
        validate_identifier(identifier)
        target = self.root / (identifier + ".json")
        if self.root.is_symlink() or target.is_symlink():
            raise ValueError("experience_evolution_unsafe_path")
        return target

    def get_version(self, identifier):
        path = self._path(identifier)
        try:
            if path.stat().st_size > MAX_VERSION_BYTES:
                raise ValueError("experience_evolution_corrupt_version")
            record = json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            raise ValueError("experience_evolution_not_found") from None
        except (OSError, UnicodeError, json.JSONDecodeError):
            raise ValueError("experience_evolution_corrupt_version") from None
        if not isinstance(record, dict):
            raise ValueError("experience_evolution_corrupt_version")
        checksum = record.get("sha256")
        body = {key: value for key, value in record.items() if key != "sha256"}
        if (body.get("id") != identifier or body.get("format_version") != FORMAT_VERSION
                or checksum != digest(body)):
            raise ValueError("experience_evolution_corrupt_version")
        validate_scope(body.get("scope"))
        validate_identifier(body.get("parent_id"), optional=True)
        return record

    def save(self, snapshot):
        if (not isinstance(snapshot, dict) or snapshot.get("format_version") != FORMAT_VERSION
                or set(snapshot).intersection({"id", "created_at", "sha256"})):
            raise ValueError("experience_evolution_invalid_snapshot")
        validate_scope(snapshot.get("scope"))
        parent_id = validate_identifier(snapshot.get("parent_id"), optional=True)
        if parent_id is not None and self.get_version(parent_id)["scope"] != snapshot["scope"]:
            raise ValueError("experience_evolution_parent_scope_mismatch")
        identifier = uuid.uuid4().hex
        body = {**deepcopy(snapshot), "id": identifier,
                "created_at": datetime.now(timezone.utc).isoformat(timespec="microseconds")}
        record = {**body, "sha256": digest(body)}
        encoded = canonical(record).encode("utf-8")
        if len(encoded) > MAX_VERSION_BYTES:
            raise ValueError("experience_evolution_version_too_large")
        target = self._path(identifier)
        with tempfile.NamedTemporaryFile(dir=self.root, prefix=".edition-", delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(encoded)
            stream.flush()
            os.fsync(stream.fileno())
        try:
            # Publishing a fully written file via hard link is atomic and
            # refuses an existing edition on both Windows and POSIX.
            os.link(temporary, target)
        except FileExistsError:
            raise ValueError("experience_evolution_id_conflict") from None
        finally:
            temporary.unlink(missing_ok=True)
        return record

    def list_versions(self, scope):
        validate_scope(scope)
        metadata = []
        for path in self.root.glob("*.json"):
            record = self.get_version(path.stem)
            if record["scope"] == scope:
                metadata.append({key: record[key] for key in
                    ("id", "scope", "label", "status", "quality", "parent_id", "created_at", "sha256")})
        return sorted(metadata, key=lambda item: (item["created_at"], item["id"]), reverse=True)[:200]

    @contextmanager
    def _observations(self):
        path = self.root / "observations.sqlite"
        if self.root.is_symlink() or path.is_symlink():
            raise ValueError("experience_evolution_unsafe_path")
        database = sqlite3.connect(path, timeout=30)
        try:
            database.execute("PRAGMA journal_mode=WAL")
            database.executescript("""CREATE TABLE IF NOT EXISTS observations(
                sequence INTEGER PRIMARY KEY AUTOINCREMENT, id TEXT UNIQUE NOT NULL,
                scope TEXT NOT NULL, run_id TEXT NOT NULL, outcome TEXT NOT NULL,
                payload TEXT NOT NULL, sha256 TEXT NOT NULL, state TEXT NOT NULL,
                batch_id TEXT, created_at TEXT NOT NULL);
                CREATE INDEX IF NOT EXISTS observation_pending ON observations(scope,state,sequence);
                CREATE INDEX IF NOT EXISTS observation_run ON observations(run_id,scope,state,sequence);
                CREATE TABLE IF NOT EXISTS observation_batches(
                id TEXT PRIMARY KEY, payload TEXT NOT NULL, sha256 TEXT NOT NULL);""")
            with database:
                yield database
        finally:
            database.close()

    def _observation(self, database, identifier):
        row = database.execute("SELECT payload,sha256,state,batch_id FROM observations WHERE id=?", (identifier,)).fetchone()
        if row is None:
            raise ValueError("experience_evolution_observation_not_found")
        body = json.loads(row[0])
        if digest(body) != row[1] or body.get("id") != identifier:
            raise ValueError("experience_evolution_observation_changed")
        return body, row[1], row[2], row[3]

    def get_observation(self, identifier):
        with self._observations() as database:
            body, checksum, state, batch_id = self._observation(database, identifier)
            return {**body, "sha256": checksum, "collection_state": state, "batch_id": batch_id}

    def record_observation(self, observation):
        body = deepcopy(observation)
        checksum = digest(body)
        if digest({key: value for key, value in body.items() if key != "id"}) != body.get("id"):
            raise ValueError("experience_evolution_invalid_observation")
        with self._observations() as database:
            inserted = database.execute("INSERT OR IGNORE INTO observations "
                "(id,scope,run_id,outcome,payload,sha256,state,created_at) VALUES (?,?,?,?,?,?,?,?)",
                (body["id"], body["scope"], body["run_id"], body["outcome"], canonical(body), checksum,
                 "pending", datetime.now(timezone.utc).isoformat(timespec="microseconds"))).rowcount
            saved, saved_hash, state, batch_id = self._observation(database, body["id"])
            if saved_hash != checksum or saved != body:
                raise ValueError("experience_evolution_observation_changed")
        return {"id": body["id"], "sha256": checksum, "state": state, "batch_id": batch_id,
            "recorded": bool(inserted), "outcome": body["outcome"], "evidence_status": body["evidence_status"],
            "model_calls": 0, "adopted": False}

    @staticmethod
    def _episode(observation):
        return {**deepcopy(observation["episode"]), "id": observation["id"],
            "source_episode_id": observation["episode"]["id"],
            "observed_outcome": observation["outcome"],
            "observed_evidence_status": observation["evidence_status"]}

    @staticmethod
    def _save_batch(database, body):
        database.execute("INSERT OR REPLACE INTO observation_batches(id,payload,sha256) VALUES (?,?,?)",
                         (body["id"], canonical(body), digest(body)))

    @staticmethod
    def _batch(database, identifier):
        row = database.execute("SELECT payload,sha256 FROM observation_batches WHERE id=?", (identifier,)).fetchone()
        if row is None:
            raise ValueError("experience_evolution_batch_not_found")
        body = json.loads(row[0])
        if body.get("id") != identifier or digest(body) != row[1]:
            raise ValueError("experience_evolution_batch_changed")
        return body

    def claim_observation_batch(self, scope, *, max_records, max_chars, run_id=None):
        with self._observations() as database:
            database.execute("BEGIN IMMEDIATE")
            query = "SELECT id FROM observations WHERE scope=? AND state='pending'"
            params = [scope]
            if run_id is not None:
                query += " AND run_id=?"
                params.append(run_id)
            selected = database.execute(query + " ORDER BY sequence LIMIT ?", (*params, max_records)).fetchall()
            entries, episodes = [], []
            for (identifier,) in selected:
                observation, checksum, _, _ = self._observation(database, identifier)
                episode = self._episode(observation)
                needed = len(canonical([*episodes, episode]))
                if needed > max_chars:
                    if not episodes:
                        return {"status": "needs_capacity", "scope": scope,
                            "blocked_observation_id": identifier, "required_characters": needed,
                            "records": [], "model_calls": 0}
                    break
                episodes.append(episode)
                entries.append({"id": identifier, "sha256": checksum, "run_id": observation["run_id"],
                    "outcome": observation["outcome"], "evidence_status": observation["evidence_status"]})
            if not entries:
                return {"status": "empty", "scope": scope, "records": [], "model_calls": 0}
            body = {"id": uuid.uuid4().hex, "scope": scope, "status": "claimed", "entries": entries,
                "created_at": datetime.now(timezone.utc).isoformat(timespec="microseconds")}
            for entry in entries:
                database.execute("UPDATE observations SET state='claimed',batch_id=? WHERE id=? AND state='pending'",
                                 (body["id"], entry["id"]))
            self._save_batch(database, body)
        return self.get_observation_batch(body["id"])

    def get_observation_batch(self, identifier):
        with self._observations() as database:
            body = self._batch(database, identifier)
            episodes = []
            for entry in body["entries"]:
                observation, checksum, _, batch_id = self._observation(database, entry["id"])
                if checksum != entry["sha256"] or (body["status"] != "released" and batch_id != identifier):
                    raise ValueError("experience_evolution_batch_changed")
                episodes.append(self._episode(observation))
            return {**body, "records": episodes, "source_receipt": {
                "kind": "workflow_review_observations", "batch_id": body["id"], "entries": deepcopy(body["entries"]),
                "meaning": "recorded_feedback_and_outcomes_not_independent_quality_certification"}}

    def observation_batch_guard(self, identifier):
        validate_identifier(identifier)
        return FileLock(str(self.root / (".observation-batch-" + identifier + ".lock")), timeout=0)

    def bind_observation_batch_request(self, identifier, request_hash):
        with self._observations() as database:
            database.execute("BEGIN IMMEDIATE")
            body = self._batch(database, identifier)
            if body["status"] not in {"claimed", "needs_attention"}:
                raise ValueError("experience_evolution_batch_not_claimed")
            if body.get("request_sha256", request_hash) != request_hash:
                raise ValueError("experience_evolution_batch_request_changed")
            body.update(status="claimed", request_sha256=request_hash)
            body.pop("error_type", None)
            self._save_batch(database, body)

    def complete_observation_batch(self, identifier, edition_id, request_hash):
        edition = self.get_version(edition_id)
        with self._observations() as database:
            database.execute("BEGIN IMMEDIATE")
            body = self._batch(database, identifier)
            if (body["status"] != "claimed" or body.get("request_sha256") != request_hash
                    or edition.get("source_receipt", {}).get("batch_id") != identifier):
                raise ValueError("experience_evolution_batch_changed")
            body.update(status="proposed", edition_id=edition_id)
            self._save_batch(database, body)
            database.execute("UPDATE observations SET state='proposed' WHERE batch_id=?", (identifier,))

    def fail_observation_batch(self, identifier, error_type):
        with self._observations() as database:
            body = self._batch(database, identifier)
            body.update(status="needs_attention", error_type=error_type)
            self._save_batch(database, body)

    def release_observation_batch(self, identifier):
        with self.observation_batch_guard(identifier), self._observations() as database:
            database.execute("BEGIN IMMEDIATE")
            body = self._batch(database, identifier)
            if body["status"] not in {"claimed", "needs_attention"}:
                raise ValueError("experience_evolution_batch_not_releasable")
            body["status"] = "released"
            self._save_batch(database, body)
            database.execute("UPDATE observations SET state='pending',batch_id=NULL WHERE batch_id=?", (identifier,))
        return self.get_observation_batch(identifier)

    def observation_progress(self, scope, *, run_id=None):
        with self._observations() as database:
            query, params = "SELECT state,outcome,count(*) FROM observations WHERE scope=?", [scope]
            if run_id is not None:
                query += " AND run_id=?"
                params.append(run_id)
            rows = database.execute(query + " GROUP BY state,outcome", params).fetchall()
        return {"scope": scope, "total": sum(row[2] for row in rows),
            "groups": [{"state": state, "outcome": outcome, "count": count} for state, outcome, count in rows]}
