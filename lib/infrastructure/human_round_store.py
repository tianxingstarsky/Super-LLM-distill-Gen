"""Strongly bound large rework-round details, separate from the small session head."""
from __future__ import annotations

from copy import deepcopy
import hashlib
import json
from pathlib import Path

from lib.domain.human_session import identifier
from lib.io_utils import atomic_json


def _digest(value):
    data = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(data.encode("utf-8")).hexdigest()


def store_human_round(session_path: Path, session_id: str, row: dict) -> dict:
    """Publish details before the session grants authorization to the child."""
    round_id = identifier(row["id"], "invalid_human_session_round_id")
    payload = {"version": 1, "session_id": identifier(session_id), "round_id": round_id,
               "run_id": identifier(row["run_id"], "invalid_workflow_id"),
               "parent_results": deepcopy(row.get("parent_results", [])), "issue_ids": list(row.get("issue_ids", []))}
    relative = f"rounds/{round_id}.json"
    destination = Path(session_path) / relative
    if destination.is_symlink() or destination.exists():
        raise ValueError("human_session_round_details_conflict")
    atomic_json(destination, payload)
    return {"file": relative, "sha256": _digest(payload)}


def verified_human_round(session_path: Path, row: dict) -> dict:
    """Resolve an authenticated sidecar; a missing/changed reference never falls back."""
    result = deepcopy(row)
    if "details_ref" not in row:
        return result
    ref = row["details_ref"]
    round_id = identifier(row["id"], "invalid_human_session_round_id")
    if (not isinstance(ref, dict) or set(ref) != {"file", "sha256"}
            or ref["file"] != f"rounds/{round_id}.json"):
        raise ValueError("human_session_round_details_changed")
    file = Path(session_path) / ref["file"]
    if (file.is_symlink() or not file.is_file() or file.stat().st_size > 1_000_000
            or file.parent.is_symlink()):
        raise ValueError("human_session_round_details_changed")
    try:
        payload = json.loads(file.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise ValueError("human_session_round_details_changed") from error
    if (not isinstance(payload, dict) or _digest(payload) != ref["sha256"]
            or set(payload) != {"version", "session_id", "round_id", "run_id", "parent_results", "issue_ids"}
            or payload["version"] != 1 or payload["session_id"] != Path(session_path).name
            or payload["round_id"] != round_id or payload["run_id"] != row["run_id"]
            or not isinstance(payload["parent_results"], list) or len(payload["parent_results"]) > 200
            or not isinstance(payload["issue_ids"], list) or len(payload["issue_ids"]) != row.get("issue_count")):
        raise ValueError("human_session_round_details_changed")
    for identity in payload["issue_ids"]:
        identifier(identity, "human_session_round_details_changed")
    result.update(parent_results=payload["parent_results"], issue_ids=payload["issue_ids"])
    return result
