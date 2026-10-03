"""Verified row checkpoints without an aggregate JSON payload in memory."""
import hashlib
import json
from pathlib import Path

from lib.domain.workflow_quality import canonical
from lib.infrastructure.workflow_rows import WorkflowRows
from lib.io_utils import atomic_json


def _digest(value):
    return hashlib.sha256(canonical(value).encode('utf-8')).hexdigest()


def _file_hash(path):
    with path.open('rb') as handle:
        return hashlib.file_digest(handle, 'sha256').hexdigest()


def row_checkpoint(destination: Path, action, check_cancel):
    check_cancel()
    rows_path = destination.with_suffix('.jsonl')
    if destination.exists():
        with destination.open(encoding='utf-8') as handle:
            saved = json.load(handle)
        if not isinstance(saved, dict) or 'data' not in saved or 'sha256' not in saved:
            raise ValueError('checkpoint_integrity_error')
        data = saved['data']
        if _digest(data) != saved['sha256']:
            raise ValueError('checkpoint_integrity_error')
        if isinstance(data, list):
            # Existing run checkpoints keep their original validation and data.
            return data
        if (not isinstance(data, dict) or data.get('storage') != 'rows-v1'
                or type(data.get('count')) is not int or data['count'] < 0
                or not rows_path.is_file() or _file_hash(rows_path) != data.get('file_sha256')):
            raise ValueError('checkpoint_integrity_error')
        check_cancel()
        return WorkflowRows(rows_path, data['count'])
    destination.parent.mkdir(parents=True, exist_ok=True)
    pending = rows_path.with_name('.' + rows_path.name + '.pending')
    count = 0
    promoted = completed = False
    try:
        with pending.open('w', encoding='utf-8') as handle:
            for row in action():
                if count % 100 == 0:
                    check_cancel()
                handle.write(canonical(row) + '\n')
                count += 1
        check_cancel()
        pending.replace(rows_path)
        promoted = True
        data = {'storage': 'rows-v1', 'count': count, 'file_sha256': _file_hash(rows_path)}
        atomic_json(destination, {'data': data, 'sha256': _digest(data)})
        completed = True
        return WorkflowRows(rows_path, count)
    finally:
        pending.unlink(missing_ok=True)
        if promoted and not completed and not destination.exists():
            rows_path.unlink(missing_ok=True)
