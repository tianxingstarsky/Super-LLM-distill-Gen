"""Seek selected JSONL rows with a bounded cache of compact line indexes."""
from array import array
from functools import lru_cache
import json
from pathlib import Path


def _identity(path):
    stat = path.stat()
    return stat.st_mtime_ns, stat.st_ctime_ns, stat.st_size, stat.st_ino


@lru_cache(maxsize=16)
def _offsets(filename, identity):
    path = Path(filename)
    positions = array("Q")
    count = 0
    with path.open("rb") as handle:
        while True:
            position = handle.tell()
            line = handle.readline()
            if not line:
                break
            if line.strip():
                if count % 128 == 0:
                    positions.append(position)
                count += 1
    if _identity(path) != identity:
        raise ValueError("preview_file_changed")
    return positions, count


def read_rows(path, offset, limit):
    path = Path(path)
    identity = _identity(path)
    rows = []
    with path.open("rb") as handle:
        skip = offset
        if offset:
            positions, count = _offsets(str(path.resolve()), identity)
            if offset >= count:
                return []
            handle.seek(positions[offset // 128])
            skip = offset % 128
        while len(rows) < limit:
            line = handle.readline()
            if not line:
                break
            if not line.strip():
                continue
            if skip:
                skip -= 1
            else:
                rows.append(json.loads(line))
    if _identity(path) != identity:
        raise ValueError("preview_file_changed")
    return rows
