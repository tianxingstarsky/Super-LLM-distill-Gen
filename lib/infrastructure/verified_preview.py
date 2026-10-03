"""Short-lived integrity leases for local, read-only artifact displays."""
from copy import deepcopy
from functools import lru_cache
from pathlib import Path
import hashlib
import time

LEASE_SECONDS = 30


def inventory_identity(run: Path) -> tuple:
    items = []
    for path in sorted((run / "artifacts").iterdir()):
        if path.is_symlink():
            raise ValueError("artifact_integrity_error")
        if path.is_file():
            stat = path.stat()
            items.append((path.name, stat.st_mtime_ns, stat.st_ctime_ns,
                          stat.st_size, stat.st_ino,
                          hashlib.sha256(path.read_bytes()).hexdigest()
                          if path.name == "manifest.json" else None))
    return tuple(items)


@lru_cache(maxsize=16)
def _verify(filename: str, identity: tuple, time_window: int) -> dict:
    from lib.infrastructure.training_workflow import verify_artifacts
    run = Path(filename)
    manifest = verify_artifacts(run)
    if inventory_identity(run) != identity:
        raise ValueError("preview_file_changed")
    return manifest


def verified_snapshot(run: Path) -> tuple[dict, tuple]:
    """Return a display-only verified manifest and its full artifact identity.

    Delivery and publication must call verify_artifacts again. A metadata-preserving
    rewrite can outlive this display lease until its 30-second window expires.
    """
    run = Path(run)
    identity = inventory_identity(run)
    manifest = _verify(str(run.resolve()), identity, int(time.monotonic() // LEASE_SECONDS))
    if inventory_identity(run) != identity:
        raise ValueError("preview_file_changed")
    return deepcopy(manifest), identity


def verify_preview(run: Path) -> tuple:
    return verified_snapshot(run)[1]
