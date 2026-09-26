"""Short-lived integrity leases for local, read-only artifact previews."""
from functools import lru_cache
from pathlib import Path
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
                          stat.st_size, stat.st_ino))
    return tuple(items)


@lru_cache(maxsize=16)
def _verify(filename: str, identity: tuple, time_window: int) -> None:
    from lib.infrastructure.training_workflow import verify_artifacts
    run = Path(filename)
    verify_artifacts(run)
    if inventory_identity(run) != identity:
        raise ValueError("preview_file_changed")


def verify_preview(run: Path) -> tuple:
    run = Path(run)
    identity = inventory_identity(run)
    _verify(str(run.resolve()), identity, int(time.monotonic() // LEASE_SECONDS))
    if inventory_identity(run) != identity:
        raise ValueError("preview_file_changed")
    return identity
