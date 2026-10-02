"""Incremental reading of legacy JSON record arrays without loading the dataset."""
from __future__ import annotations

import json
from pathlib import Path


def iter_json_records(path: Path, *, chunk_size: int = 65536, object_only: bool = True,
                      max_record_chars: int | None = None):
    """Read an array of objects, retaining one object and a bounded input chunk.

    The schema deliberately rejects non-object elements, trailing commas, and
    trailing data. Large individual objects may require more than one chunk.
    """
    if max_record_chars is not None and (type(max_record_chars) is not int or max_record_chars < 1):
        raise ValueError("invalid_record_limit")
    decoder = json.JSONDecoder()
    buffer, eof = "", False
    with Path(path).open(encoding="utf-8") as handle:
        def refill():
            nonlocal buffer, eof
            chunk = handle.read(max(1, chunk_size))
            buffer += chunk
            eof = not chunk

        def whitespace():
            nonlocal buffer
            while True:
                buffer = buffer.lstrip(" \r\n\t")
                if buffer or eof:
                    return
                refill()

        whitespace()
        if not buffer.startswith("["):
            raise ValueError("invalid_record_array")
        buffer = buffer[1:]
        whitespace()
        if not buffer.startswith("]"):
            while True:
                whitespace()
                if object_only and not buffer.startswith("{"):
                    raise ValueError("invalid_record_array")
                while True:
                    try:
                        record, end = decoder.raw_decode(buffer)
                        if max_record_chars is not None and end > max_record_chars:
                            raise ValueError("record_exceeds_limit")
                        break
                    except json.JSONDecodeError:
                        if max_record_chars is not None and len(buffer) > max_record_chars + chunk_size:
                            raise ValueError("record_exceeds_limit") from None
                        if eof:
                            raise ValueError("invalid_record_array") from None
                        refill()
                buffer = buffer[end:]
                yield record
                whitespace()
                if buffer.startswith("]"):
                    break
                if not buffer.startswith(","):
                    raise ValueError("invalid_record_array")
                buffer = buffer[1:]
        buffer = buffer[1:]
        whitespace()
        if buffer:
            raise ValueError("invalid_record_array")


def iter_source_json_records(path: Path):
    """Stream dataset arrays; retain a whole messages array as one trace."""
    with Path(path).open(encoding="utf-8") as handle:
        first_character = ""
        while chunk := handle.read(1024):
            content = chunk.lstrip(" \r\n\t")
            if content:
                first_character = content[0]
                break
    if first_character != "[":
        with Path(path).open(encoding="utf-8") as handle:
            yield json.load(handle)
        return
    records = iter_json_records(path, object_only=False)
    try:
        first = next(records, None)
        message = lambda row: isinstance(row, dict) and ("role" in row or "from" in row)
        if message(first):
            # This compatibility case may be one long trace, not a dataset.
            if all(message(row) for row in records):
                yield {"messages": list(iter_json_records(path))}
                return
            yield from iter_json_records(path, object_only=False)
        elif first is not None:
            yield first
            yield from records
        else:
            # Distinguish an empty array from a first null element.
            yield from iter_json_records(path, object_only=False)
    finally:
        records.close()
