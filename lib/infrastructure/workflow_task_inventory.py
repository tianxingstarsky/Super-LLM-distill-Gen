"""Compact history cached only while persisted state and recipe files match."""
from copy import deepcopy
from functools import lru_cache
from pathlib import Path

from lib.infrastructure.training_workflow import read_json


def _identity(path):
    stat = path.stat()
    return stat.st_mtime_ns, stat.st_ctime_ns, stat.st_size, stat.st_ino


def _recipe_identity(path):
    """Unavailable recipes must not remove otherwise readable task history."""
    try:
        return _identity(path)
    except OSError:
        return None


def _source_summary(recipe):
    unknown = {"source_mode": "unknown", "source_names": [], "source_brief": ""}
    if not isinstance(recipe, dict):
        return unknown
    sources = recipe.get("sources", [])
    if not isinstance(sources, list) or any(not isinstance(source, dict) for source in sources):
        return unknown
    names = [source.get("name") or source.get("file") for source in sources[:200]]
    if any(not isinstance(name, str) or not name.strip() for name in names):
        return unknown
    brief = recipe.get("brief", "")
    if not isinstance(brief, str):
        return unknown
    if sources:
        # Older recipes predate the explicit UI mode. JSON/JSONL are the
        # context formats accepted by the Agent source control.
        formats = [source.get("file") or name for source, name in zip(sources[:200], names)]
        mode = "agent" if all(isinstance(value, str) and
                              Path(value).suffix.casefold() in {".json", ".jsonl"}
                              for value in formats) else "document"
    elif brief.strip():
        mode = "brief"
    else:
        return unknown
    saved_mode = recipe.get("source_mode")
    modes = {"document": "document", "agent": "agent", "brief": "brief",
             "文档资料": "document", "Agent 上下文": "agent", "开放需求": "brief"}
    mode = modes.get(saved_mode, mode) if isinstance(saved_mode, str) else mode
    return {"source_mode": mode, "source_names": names, "source_brief": brief.strip()[:120]}


@lru_cache(maxsize=2048)
def _summary(path, identity, recipe_identity):
    state = read_json(Path(path))
    recipe_path = Path(path).with_name("recipe.json")
    try:
        recipe = read_json(recipe_path) if recipe_identity is not None else None
    except (OSError, ValueError, TypeError):
        recipe = None
    if (_identity(Path(path)) != identity or
            _recipe_identity(recipe_path) != recipe_identity):
        raise ValueError("task_state_changed_during_read")
    sources = _source_summary(recipe)
    return {**{key: state[key] for key in
               ("id", "name", "status", "created_at", "updated_at", "targets") if key in state},
            **sources,
            "recipe_readable": (sources["source_mode"] != "unknown" and
                                isinstance(recipe.get("targets"), list) and
                                all(isinstance(target, str) for target in recipe["targets"])),
            "stages": {key: {field: value for field, value in stage.items()
                             if field in {"label", "status"}}
                       for key, stage in state.get("stages", {}).items()},
            "events": state.get("events", [])[-3:]}


def task_runs(output):
    rows = []
    for path in (Path(output) / "workflows").glob("*/state.json"):
        for attempt in range(3):
            try:
                row = _summary(str(path.resolve()), _identity(path),
                               _recipe_identity(path.with_name("recipe.json")))
                if "created_at" in row and "id" in row:
                    rows.append(deepcopy(row))
                break
            except (OSError, ValueError, TypeError, AttributeError):
                continue
    return sorted(rows, key=lambda row: row["created_at"], reverse=True)
