"""Incremental reading of legacy JSON record arrays without loading the dataset."""
from __future__ import annotations

import json
from pathlib import Path


def iter_json_records(path: Path, *, chunk_size: int = 65536):
    """Read an array of objects, retaining one object and a bounded input chunk.

    The schema deliberately rejects non-object elements, trailing commas, and
    trailing data. Large individual objects may require more than one chunk.
    """
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
                if not buffer.startswith("{"):
                    raise ValueError("invalid_record_array")
                while True:
                    try:
                        record, end = decoder.raw_decode(buffer)
                        break
                    except json.JSONDecodeError:
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
