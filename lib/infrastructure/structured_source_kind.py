"""Inspect fixed structured source snapshots without loading a whole dataset."""
from __future__ import annotations

import json
from pathlib import Path

from lib.infrastructure.json_stream import iter_source_json_records


def contains_document_records(path: Path) -> bool:
    """Use the same document predicate as the ingest parser.

    A text-bearing conversation remains a recorded conversation whenever its
    messages/conversations field is populated. Invalid records are left for
    normal ingest quarantine; this preflight never repairs or executes them.
    """
    path = Path(path)
    if path.suffix.lower() not in {".json", ".jsonl"}:
        return False

    def is_document(row):
        return (isinstance(row, dict) and isinstance(row.get("text"), str)
                and not (row.get("messages") or row.get("conversations")))

    try:
        if path.suffix.lower() == ".jsonl":
            with path.open(encoding="utf-8-sig") as handle:
                for line in handle:
                    if not line.strip():
                        continue
                    try:
                        row = json.loads(line)
                    except ValueError:
                        continue
                    if is_document(row):
                        return True
            return False
        records = iter_source_json_records(path)
        try:
            return any(is_document(row) for row in records)
        finally:
            records.close()
    except (ValueError, UnicodeError):
        return False
