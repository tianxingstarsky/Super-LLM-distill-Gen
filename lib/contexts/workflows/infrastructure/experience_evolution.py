"""Append-only user-scoped experience editions with complete source lineage."""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import tempfile
import uuid

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
