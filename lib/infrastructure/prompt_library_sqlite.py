"""Private, durable prompt templates with atomic optimistic updates."""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
import getpass
import hashlib
import json
from pathlib import Path
import sqlite3
import stat
import uuid

from lib.domain.prompt_library import (
    MAX_TEMPLATE_REVISION, validate_prompt_payload, validate_prompt_revision, validate_prompt_scope,
    validate_prompt_template_id, validate_prompt_template_name,
)


_METADATA_COLUMNS = "id,name,scope,created_at,updated_at,revision"
_MAX_PAYLOAD_BYTES = 256 * 1024


def _unlinked(path: Path) -> None:
    for candidate in (path, *path.parents):
        try:
            info = candidate.lstat()
        except FileNotFoundError:
            continue
        if (stat.S_ISLNK(info.st_mode) or getattr(info, "st_reparse_tag", 0)
                or (stat.S_ISREG(info.st_mode) and info.st_nlink > 1)):
            raise ValueError("prompt_library_linked_path")


class PromptLibrarySQLite:
    """Each call uses a short connection; no templates or credentials in URLs.

    IDs only address rows, never filenames. A hashed user namespace isolates
    explicit application users when several users share one storage root.
    The default root belongs to the current OS user's home directory.
    """

    def __init__(self, *, root: Path | None = None, user_key: str | None = None):
        self.root = Path.home() / ".shujian-cube" if root is None else Path(root)
        self.path = self.root / "prompt-library.sqlite3"
        user_key = getpass.getuser() if user_key is None else user_key
        if not isinstance(user_key, str) or not user_key.strip() or len(user_key) > 1024 or "\x00" in user_key:
            raise ValueError("prompt_library_invalid_user")
        try:
            self._namespace = hashlib.sha256(user_key.encode("utf-8")).hexdigest()
        except UnicodeError as error:
            raise ValueError("prompt_library_invalid_user") from error

    def _check_paths(self) -> None:
        _unlinked(self.root)
        if self.root.exists() and not self.root.is_dir():
            raise ValueError("prompt_library_invalid_path")
        for suffix in ("", "-journal", "-wal", "-shm"):
            path = Path(str(self.path) + suffix)
            _unlinked(path)
            if path.exists() and not path.is_file():
                raise ValueError("prompt_library_invalid_path")

    @contextmanager
    def _connection(self):
        connection = None
        try:
            self._check_paths()
            self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
            self._check_paths()
            connection = sqlite3.connect(self.path, timeout=2, isolation_level=None)
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA busy_timeout=2000")
            version = connection.execute("PRAGMA user_version").fetchone()[0]
            if version not in (0, 1):
                raise ValueError("prompt_library_unsupported_version")
            connection.execute("""CREATE TABLE IF NOT EXISTS prompt_templates (
                user_namespace TEXT NOT NULL,
                id TEXT NOT NULL,
                scope TEXT NOT NULL,
                name TEXT NOT NULL,
                name_key TEXT NOT NULL,
                payload TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                revision INTEGER NOT NULL CHECK(revision >= 1),
                PRIMARY KEY(user_namespace,id),
                UNIQUE(user_namespace,scope,name_key)
            )""")
            connection.execute("CREATE INDEX IF NOT EXISTS prompt_templates_listing "
                               "ON prompt_templates(user_namespace,scope,updated_at DESC,id)")
            if version == 0:
                connection.execute("PRAGMA user_version=1")
            yield connection
        except sqlite3.IntegrityError as error:
            raise ValueError("prompt_library_name_conflict") from error
        except sqlite3.Error as error:
            code = ("prompt_library_busy" if "locked" in str(error).lower()
                    or "busy" in str(error).lower() else "prompt_library_storage_error")
            raise ValueError(code) from error
        except OSError as error:
            raise ValueError("prompt_library_storage_error") from error
        finally:
            if connection is not None:
                connection.close()

    @staticmethod
    def _payload_text(scope: str, payload: dict) -> str:
        payload = validate_prompt_payload(scope, payload)
        result = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
        try:
            if len(result.encode("utf-8")) > _MAX_PAYLOAD_BYTES:
                raise ValueError("prompt_library_invalid_payload")
        except UnicodeError as error:
            raise ValueError("prompt_library_invalid_payload") from error
        return result

    @staticmethod
    def _metadata(row: sqlite3.Row) -> dict:
        if row is None:
            raise ValueError("prompt_library_not_found")
        result = {key: row[key] for key in ("id", "name", "scope", "created_at", "updated_at", "revision")}
        try:
            validate_prompt_template_id(result["id"])
            if validate_prompt_template_name(result["name"]) != result["name"]:
                raise ValueError("invalid_name")
            validate_prompt_scope(result["scope"])
            validate_prompt_revision(result["revision"])
            for field in ("created_at", "updated_at"):
                timestamp = result[field]
                if (not isinstance(timestamp, str) or len(timestamp) > 64
                        or datetime.fromisoformat(timestamp).tzinfo is None):
                    raise ValueError("invalid_timestamp")
        except (ValueError, TypeError) as error:
            raise ValueError("prompt_library_invalid_record") from error
        return result

    @classmethod
    def _record(cls, row: sqlite3.Row) -> dict:
        result = cls._metadata(row)
        try:
            result["payload"] = validate_prompt_payload(row["scope"], json.loads(row["payload"]))
        except (ValueError, TypeError) as error:
            raise ValueError("prompt_library_invalid_record") from error
        return result

    def list_templates(self, scope: str) -> list[dict]:
        scope = validate_prompt_scope(scope)
        with self._connection() as connection:
            rows = connection.execute(
                "SELECT " + _METADATA_COLUMNS + " FROM prompt_templates "
                "WHERE user_namespace=? AND scope=? ORDER BY updated_at DESC,id",
                (self._namespace, scope)).fetchall()
        return [self._metadata(row) for row in rows]

    def get_template(self, identifier: str, scope: str) -> dict:
        identifier, scope = validate_prompt_template_id(identifier), validate_prompt_scope(scope)
        with self._connection() as connection:
            row = connection.execute(
                "SELECT " + _METADATA_COLUMNS + ",payload FROM prompt_templates "
                "WHERE user_namespace=? AND id=? AND scope=?",
                (self._namespace, identifier, scope)).fetchone()
        return self._record(row)

    def save_template(self, scope: str, name: str, payload: dict) -> dict:
        scope, name = validate_prompt_scope(scope), validate_prompt_template_name(name)
        payload_text = self._payload_text(scope, payload)
        identifier = uuid.uuid4().hex
        timestamp = datetime.now(timezone.utc).isoformat(timespec="microseconds")
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                connection.execute(
                    "INSERT INTO prompt_templates "
                    "(user_namespace,id,scope,name,name_key,payload,created_at,updated_at,revision) "
                    "VALUES (?,?,?,?,?,?,?,?,1)",
                    (self._namespace, identifier, scope, name, name.casefold(), payload_text, timestamp, timestamp))
                row = connection.execute(
                    "SELECT " + _METADATA_COLUMNS + ",payload FROM prompt_templates "
                    "WHERE user_namespace=? AND id=?", (self._namespace, identifier)).fetchone()
                connection.execute("COMMIT")
            except Exception:
                connection.execute("ROLLBACK")
                raise
        return self._record(row)

    def update_template(self, identifier: str, scope: str, name: str,
                        payload: dict, expected_revision: int) -> dict:
        identifier, scope = validate_prompt_template_id(identifier), validate_prompt_scope(scope)
        name = validate_prompt_template_name(name)
        expected_revision = validate_prompt_revision(expected_revision)
        if expected_revision == MAX_TEMPLATE_REVISION:
            raise ValueError("prompt_library_invalid_revision")
        payload_text = self._payload_text(scope, payload)
        timestamp = datetime.now(timezone.utc).isoformat(timespec="microseconds")
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                existing = connection.execute(
                    "SELECT revision FROM prompt_templates WHERE user_namespace=? AND id=? AND scope=?",
                    (self._namespace, identifier, scope)).fetchone()
                if existing is None:
                    raise ValueError("prompt_library_not_found")
                if existing["revision"] != expected_revision:
                    raise ValueError("prompt_library_revision_conflict")
                connection.execute(
                    "UPDATE prompt_templates SET name=?,name_key=?,payload=?,updated_at=?,revision=revision+1 "
                    "WHERE user_namespace=? AND id=? AND scope=? AND revision=?",
                    (name, name.casefold(), payload_text, timestamp,
                     self._namespace, identifier, scope, expected_revision))
                row = connection.execute(
                    "SELECT " + _METADATA_COLUMNS + ",payload FROM prompt_templates "
                    "WHERE user_namespace=? AND id=?", (self._namespace, identifier)).fetchone()
                connection.execute("COMMIT")
            except Exception:
                connection.execute("ROLLBACK")
                raise
        return self._record(row)
