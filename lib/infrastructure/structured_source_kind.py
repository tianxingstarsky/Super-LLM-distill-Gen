"""Inspect fixed structured source snapshots without loading a whole dataset."""
from __future__ import annotations

import json
from pathlib import Path

from lib.infrastructure.json_stream import iter_source_json_records


def structured_source_kind(path: Path, targets) -> str | None:
    """Prove a complete conversation-only snapshot using the ingest adapters.

    Unknown, invalid and mixed sources cannot exempt a new dialogue run from
    its director. Records are streamed and never repaired by this preflight.
    """
    from lib.contexts.datasets.contracts import adapt_reasoning_record
    from lib.domain.review_edit import normalize_sample
    from lib.domain.source_conversation import source_conversation_issue

    path = Path(path)
    if path.suffix.lower() not in {".json", ".jsonl"}:
        return None

    def records():
        if path.suffix.lower() == ".jsonl":
            with path.open(encoding="utf-8-sig") as handle:
                for line in handle:
                    if line.strip():
                        try:
                            yield json.loads(line)
                        except ValueError:
                            yield None
        else:
            yield from iter_source_json_records(path)

    seen, complete = False, True
    try:
        for row in records():
            seen = True
            if (isinstance(row, dict) and isinstance(row.get("text"), str)
                    and not (row.get("messages") or row.get("conversations"))):
                return "document_collection"
            try:
                row = adapt_reasoning_record(row) or row
                if isinstance(row, dict) and "request" in row and "response" in row:
                    from lib.adapters.rollout_import import record_status, record_to_sample
                    if record_status(row) != "ok" or row["request"].get("messageOffset", 0):
                        raise ValueError("incomplete_rollout_context")
                    sample = record_to_sample(row)
                else:
                    sample = normalize_sample(row)
                if source_conversation_issue(sample, targets):
                    complete = False
            except (ValueError, TypeError, KeyError, AttributeError):
                complete = False
    except (ValueError, UnicodeError):
        return None
    return "conversation_collection" if seen and complete else None


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
