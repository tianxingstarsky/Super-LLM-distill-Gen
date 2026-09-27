"""Bounded source copying with a digest of the exact published bytes."""
from __future__ import annotations

import hashlib
from pathlib import Path

COPY_CHUNK_BYTES = 1024 * 1024


def snapshot_source(source: Path, destination: Path, max_bytes: int) -> dict:
    """Publish a complete snapshot, or remove only this copy's pending file."""
    pending = destination.with_name("." + destination.name + ".pending")
    digest = hashlib.sha256()
    size = 0
    created = False
    try:
        with source.open("rb") as reader, pending.open("xb") as writer:
            created = True
            while chunk := reader.read(COPY_CHUNK_BYTES):
                size += len(chunk)
                if size > max_bytes:
                    raise ValueError("来源在读取期间超出大小限制")
                digest.update(chunk)
                writer.write(chunk)
        pending.replace(destination)
    except BaseException:
        if created:
            pending.unlink(missing_ok=True)
        raise
    return {"sha256": digest.hexdigest(), "bytes": size}
