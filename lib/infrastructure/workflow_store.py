"""Persisted run/manifest access, independent of generation and model clients.

This is the file-format boundary shared by execution and read-side adapters.
It preserves the existing paths, hashes, lock semantics and error contracts.
"""
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re

from filelock import FileLock, Timeout

from lib.domain.workflow_quality import canonical
from lib.io_utils import atomic_json


def now():
    return datetime.now(timezone.utc).isoformat()


def digest(value):
    return hashlib.sha256(canonical(value).encode("utf-8")).hexdigest()


def file_hash(path):
    with Path(path).open("rb") as handle:
        return hashlib.file_digest(handle, "sha256").hexdigest()


def read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def run_path(output, run_id):
    if not re.fullmatch(r"[a-f0-9]{32}", run_id):
        raise ValueError("无效运行 ID")
    return Path(output) / "workflows" / run_id


def list_runs(output):
    result = []
    for path in (Path(output) / "workflows").glob("*/state.json"):
        try:
            result.append(read_json(path))
        except (OSError, ValueError):
            continue
    return sorted(result, key=lambda item: item["created_at"], reverse=True)


def is_active(path):
    try:
        with FileLock(str(Path(path) / ".run.lock"), timeout=0):
            return False
    except Timeout:
        return True


def cancel(output, run_id):
    path = run_path(output, run_id)
    if not (path / "state.json").exists():
        raise ValueError("运行不存在")
    atomic_json(path / "cancel.json", {"requested_at": now()})


def verify_artifacts(path):
    path = Path(path)
    artifacts = path / "artifacts"
    manifest = read_json(artifacts / "manifest.json")
    hashes = manifest.get("sha256")
    if manifest.get("status") != "complete" or not isinstance(hashes, dict) or not hashes:
        raise ValueError("incomplete_artifact_manifest")
    actual = {item.name for item in artifacts.iterdir() if item.is_file() and item.name != "manifest.json"}
    if actual != set(hashes):
        raise ValueError("artifact_inventory_mismatch")
    for name, expected in hashes.items():
        item = artifacts / name
        if Path(name).name != name or item.is_symlink() or file_hash(item) != expected:
            raise ValueError("artifact_integrity_error")
    return manifest
