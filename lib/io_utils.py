"""Atomic local state writes and quiet Windows subprocess options."""
from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import tempfile
import time


def quiet_process() -> dict:
    return {"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {}


def _replace_state(temporary, path):
    """Tolerate short Windows file holds without weakening atomic replacement."""
    for attempt in range(6):
        try:
            os.replace(temporary, path)
            return
        except OSError as error:
            if getattr(error, "winerror", None) not in {5, 32, 33} or attempt == 5:
                raise
            time.sleep(0.02 * 2 ** attempt)


def atomic_json(path: Path, value) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(dir=path.parent, prefix=".pending-", suffix=".json")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(value, handle, ensure_ascii=False, indent=2)
            handle.flush()
            os.fsync(handle.fileno())
        _replace_state(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)
