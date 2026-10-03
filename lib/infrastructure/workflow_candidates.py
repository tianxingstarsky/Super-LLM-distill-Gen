"""Build replayable candidates without retaining source documents in memory."""
from itertools import islice
from pathlib import Path

from lib.domain.workflow_scale import generation_variant
from lib.infrastructure.workflow_rows import RowSpool, WorkflowRows


def prepare_generation_rows(path: Path, units, count, check_cancel):
    path.parent.mkdir(parents=True, exist_ok=True)
    requested = len(units) if count is None else count
    needs_variants = requested > len(units)
    pending = path.with_name('.' + path.name + '.pending')
    documents_path = path.with_name('.' + path.name + '.documents')
    rows = documents = None
    try:
        rows = RowSpool(pending)
        documents = RowSpool(documents_path)
        for row in islice(units, requested):
            if len(rows) % 100 == 0:
                check_cancel()
            rows.append(row)
            if needs_variants and row.get('kind') == 'document':
                documents.append(row)
        while len(rows) < requested and len(documents):
            for original in documents:
                if len(rows) >= requested:
                    break
                if len(rows) % 100 == 0:
                    check_cancel()
                rows.append(generation_variant(original, len(rows), len(documents)))
        check_cancel()
        rows.close()
        documents.close()
        pending.replace(path)
        return WorkflowRows(path, len(rows))
    finally:
        if rows is not None:
            rows.close()
        if documents is not None:
            documents.close()
        pending.unlink(missing_ok=True)
        documents_path.unlink(missing_ok=True)
