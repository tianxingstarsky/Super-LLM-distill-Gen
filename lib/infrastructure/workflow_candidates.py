"""Build replayable candidates without retaining source documents in memory."""
from itertools import islice
from pathlib import Path

from lib.domain.workflow_scale import generation_variant
from lib.infrastructure.workflow_rows import RowSpool, WorkflowRows


def prepare_generation_rows(path: Path, units, count, check_cancel):
    requested = len(units) if count is None else count
    needs_variants = requested > len(units)
    pending = path.with_name('.' + path.name + '.pending')
    documents_path = path.with_name('.' + path.name + '.documents')
    rows, documents = RowSpool(pending), RowSpool(documents_path)
    try:
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
    finally:
        rows.close()
        documents.close()
    pending.replace(path)
    return WorkflowRows(path, len(rows))
