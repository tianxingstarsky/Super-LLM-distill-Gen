"""Atomic workspace-local drafts. Upload bodies and credentials are excluded."""
import json
from pathlib import Path
from filelock import FileLock
from lib.domain.creation_draft import validate_creation_draft
from lib.io_utils import atomic_json

MAX_DRAFT_BYTES = 2 * 1024 * 1024


class CreationDraftFile:
    def __init__(self, output: Path):
        self.path = Path(output) / '.creation-draft.json'

    def load(self) -> dict:
        if not self.path.exists():
            return {}
        with self.path.open('rb') as handle:
            content = handle.read(MAX_DRAFT_BYTES + 1)
        if len(content) > MAX_DRAFT_BYTES:
            raise ValueError('invalid_creation_draft')
        document = json.loads(content)
        if (not isinstance(document, dict) or type(document.get('version')) is not int
                or document['version'] != 1):
            raise ValueError('invalid_creation_draft')
        return validate_creation_draft(document.get('values'))

    def update(self, changes: dict) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with FileLock(str(self.path) + '.lock', timeout=2):
            previous = self.load()
            values = validate_creation_draft({**previous, **validate_creation_draft(changes)})
            if values == previous:
                return
            document = {'version': 1, 'values': values}
            if len(json.dumps(document, ensure_ascii=False, indent=2).encode('utf-8')) > MAX_DRAFT_BYTES:
                raise ValueError('invalid_creation_draft')
            atomic_json(self.path, document)
