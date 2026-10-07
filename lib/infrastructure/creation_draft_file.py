"""Atomic local drafts and named snapshots. Credentials remain excluded."""
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import stat
import uuid

from filelock import FileLock
from lib.domain.creation_draft import validate_creation_draft
from lib.io_utils import atomic_json

MAX_DRAFT_BYTES = 2 * 1024 * 1024
_SNAPSHOT_ID = re.compile(r'[a-f0-9]{32}')


def _unlinked(path: Path) -> None:
    for candidate in (path, *path.parents):
        try:
            info = candidate.lstat()
        except FileNotFoundError:
            continue
        if (stat.S_ISLNK(info.st_mode) or getattr(info, 'st_reparse_tag', 0)
                or (stat.S_ISREG(info.st_mode) and info.st_nlink > 1)):
            raise ValueError('linked_creation_draft_path')


def _directory(path: Path) -> None:
    _unlinked(path)
    if path.exists() and not path.is_dir():
        raise ValueError('invalid_creation_draft')


def _document(path: Path) -> dict:
    _unlinked(path)
    if not path.is_file():
        raise ValueError('invalid_creation_draft')
    with path.open('rb') as handle:
        content = handle.read(MAX_DRAFT_BYTES + 1)
    if len(content) > MAX_DRAFT_BYTES:
        raise ValueError('invalid_creation_draft')
    document = json.loads(content)
    if (not isinstance(document, dict) or type(document.get('version')) is not int
            or document['version'] != 1):
        raise ValueError('invalid_creation_draft')
    return document


def _write(path: Path, document: dict) -> None:
    serialized = json.dumps(document, ensure_ascii=False, indent=2).replace('\n', os.linesep)
    if len(serialized.encode('utf-8')) > MAX_DRAFT_BYTES:
        raise ValueError('invalid_creation_draft')
    _unlinked(path)
    atomic_json(path, document)


def _field(values: dict, field: str) -> str:
    return next((value for key, value in values.items() if key.startswith(field + ':')), '')


class CreationDraftFile:
    def __init__(self, output: Path):
        self.path = Path(output) / '.creation-draft.json'
        self._snapshots = self.path.parent / '.creation-drafts'
        self._lock_path = Path(str(self.path) + '.lock')

    def _lock(self) -> FileLock:
        _directory(self.path.parent)
        _unlinked(self._lock_path)
        try:
            lock_info = self._lock_path.lstat()
        except FileNotFoundError:
            pass  # A different FileLock can remove its released lock.
        else:
            if not stat.S_ISREG(lock_info.st_mode):
                raise ValueError('invalid_creation_draft')
        self.path.parent.mkdir(parents=True, exist_ok=True)
        return FileLock(str(self._lock_path), timeout=2)

    def load(self) -> dict:
        _unlinked(self.path)
        if not self.path.exists():
            return {}
        return validate_creation_draft(_document(self.path).get('values'))

    def update(self, changes: dict) -> None:
        changes = validate_creation_draft(changes)
        with self._lock():
            previous = self.load()
            values = validate_creation_draft({**previous, **changes})
            if values == previous:
                return
            _write(self.path, {'version': 1, 'values': values})

    def replace(self, values: dict) -> None:
        """Autosave one complete session form without inheriting another form."""
        values = validate_creation_draft(values)
        with self._lock():
            # Keep unreadable existing drafts intact, as update does. A failed
            # read must remain visible rather than silently discard recovery data.
            previous = self.load()
            if values == previous:
                return
            _write(self.path, {'version': 1, 'values': values})

    def _snapshot_path(self, identifier: str) -> Path:
        if not isinstance(identifier, str) or not _SNAPSHOT_ID.fullmatch(identifier):
            raise ValueError('invalid_creation_draft_id')
        _directory(self._snapshots)
        path = self._snapshots / (identifier + '.json')
        _unlinked(path)
        return path

    def _snapshot(self, path: Path) -> dict:
        document = _document(path)
        validate_creation_draft(document.get('values'))
        if (document.get('id') != path.stem or not _SNAPSHOT_ID.fullmatch(path.stem)
                or not isinstance(document.get('name'), str) or len(document['name']) > 100
                or not isinstance(document.get('source_mode'), str) or len(document['source_mode']) > 128
                or not isinstance(document.get('created_at'), str) or len(document['created_at']) > 64):
            raise ValueError('invalid_creation_draft')
        try:
            created = datetime.fromisoformat(document['created_at'])
        except ValueError as error:
            raise ValueError('invalid_creation_draft') from error
        if created.tzinfo is None:
            raise ValueError('invalid_creation_draft')
        return document

    def save_snapshot(self, name: str | None = None, *, values: dict | None = None) -> str:
        if name is not None and (not isinstance(name, str) or len(name) > 100):
            raise ValueError('invalid_creation_draft')
        # A UI session may be ahead of autosave, or another session may have
        # changed the shared automatic draft. Snapshot exactly the supplied
        # validated form without replacing either session's current draft.
        supplied = validate_creation_draft(values) if values is not None else None
        with self._lock():
            values = self.load() if supplied is None else supplied
            _directory(self._snapshots)
            for _ in range(16):
                identifier = uuid.uuid4().hex
                path = self._snapshot_path(identifier)
                if not path.exists():
                    break
            else:
                raise ValueError('creation_draft_id_collision')
            document = {'version': 1, 'id': identifier,
                        'name': _field(values, 'workflow-name') if name is None else name,
                        'created_at': datetime.now(timezone.utc).isoformat(timespec='microseconds'),
                        'source_mode': _field(values, 'workflow-source-mode'), 'values': values}
            _write(path, document)
            return identifier

    def snapshots(self, limit: int = 50, offset: int = 0) -> list[dict]:
        if type(limit) is not int or not 1 <= limit <= 500 or type(offset) is not int or offset < 0:
            raise ValueError('invalid_creation_draft_page')
        _directory(self._snapshots)
        if not self._snapshots.exists():
            return []
        with self._lock():
            _directory(self._snapshots)
            result = []
            for path in self._snapshots.glob('*.json'):
                if not _SNAPSHOT_ID.fullmatch(path.stem):
                    continue
                document = self._snapshot(path)
                result.append({key: document[key] for key in ('id', 'name', 'created_at', 'source_mode')})
            result.sort(key=lambda row: (row['created_at'], row['id']), reverse=True)
            return result[offset:offset + limit]

    def restore_snapshot(self, identifier: str) -> dict:
        path = self._snapshot_path(identifier)
        with self._lock():
            path = self._snapshot_path(identifier)
            values = validate_creation_draft(self._snapshot(path)['values'])
            _write(self.path, {'version': 1, 'values': values})
            return values
