"""Compact task inventory cached only while authoritative state files match."""
from copy import deepcopy
from functools import lru_cache
from pathlib import Path

from lib.infrastructure.training_workflow import read_json


def _identity(path):
    stat = path.stat()
    return stat.st_mtime_ns, stat.st_ctime_ns, stat.st_size, stat.st_ino


@lru_cache(maxsize=2048)
def _summary(path, identity):
    state = read_json(Path(path))
    if _identity(Path(path)) != identity:
        raise ValueError("task_state_changed_during_read")
    return {**{key: state[key] for key in
               ("id", "name", "status", "created_at", "updated_at", "targets") if key in state},
            "stages": {key: {field: value for field, value in stage.items()
                             if field in {"label", "status"}}
                       for key, stage in state.get("stages", {}).items()},
            "events": state.get("events", [])[-3:]}


def task_runs(output):
    rows = []
    for path in (Path(output) / "workflows").glob("*/state.json"):
        for attempt in range(3):
            try:
                row = _summary(str(path.resolve()), _identity(path))
                if "created_at" in row and "id" in row:
                    rows.append(deepcopy(row))
                break
            except (OSError, ValueError, TypeError, AttributeError):
                continue
    return sorted(rows, key=lambda row: row["created_at"], reverse=True)
