"""Local, streaming statistics with a cache bound to the verified artifact version."""
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path

from filelock import FileLock

from lib.contexts.datasets.domain.distribution import Distribution
from lib.contexts.workflows.contracts import TARGETS
from lib.infrastructure.workflow_store import digest, verify_artifacts, read_json
from lib.contexts.datasets.infrastructure.verified_preview import inventory_identity
from lib.io_utils import atomic_json
from lib.workspace import is_linked

VERSION = 1
MAX_ROW_BYTES = 64 * 1024 * 1024


def _cache(run):
    path = Path(run) / "analysis" / "sample-distribution.json"
    if any(is_linked(p) for p in (path.parent, path) if p.exists() or p.is_symlink()):
        raise ValueError("linked_statistics_cache")
    return path


def cached_statistics(run, manifest):
    path = _cache(run)
    if not path.is_file():
        return None
    try:
        result = read_json(path)
        valid = (result["version"] == VERSION and result["manifest_digest"] == digest(manifest)
                 and result["inventory_digest"] == digest(inventory_identity(Path(run))))
    except (ValueError, TypeError, KeyError):
        return None
    return result if valid else None


def build_statistics(run, *, progress=None):
    run = Path(run)
    path = _cache(run)
    path.parent.mkdir(exist_ok=True)
    with FileLock(str(path) + ".lock", timeout=0):
        if progress:
            progress("source")
        manifest = verify_artifacts(run)
        if cached := cached_statistics(run, manifest):
            return cached
        identity = inventory_identity(run)
        targets, total, scanned = {}, Distribution(), 0
        counts = dict(manifest.get("counts") or {})
        negative = (manifest.get("negative_counts") or {}).get("agent", 0)
        if negative:
            counts["agent_negative"] = negative
        expected_total = sum(int(counts.get(t, 0)) for t in (*TARGETS, "agent_negative"))
        for target in (*TARGETS, "agent_negative"):
            expected = int(counts.get(target, 0))
            filename = "agent.negative.jsonl" if target == "agent_negative" else f"{target}.jsonl"
            if filename not in manifest["sha256"]:
                if expected:
                    raise ValueError("statistics_native_file_missing")
                continue
            result, hasher = Distribution(), hashlib.sha256()
            with (run / "artifacts" / filename).open("rb") as handle:
                while raw := handle.readline(MAX_ROW_BYTES + 1):
                    if len(raw) > MAX_ROW_BYTES:
                        raise ValueError("statistics_record_too_large")
                    hasher.update(raw)
                    if not raw.strip():
                        continue
                    try:
                        row = json.loads(raw)
                    except (ValueError, UnicodeError):
                        raise ValueError("statistics_invalid_record") from None
                    if not isinstance(row, dict):
                        raise ValueError("statistics_invalid_record")
                    result.add(target, row)
                    scanned += 1
                    if progress and scanned % 500 == 0:
                        progress("statistics", scanned, expected_total)
            if hasher.hexdigest() != manifest["sha256"][filename]:
                raise ValueError("artifact_integrity_error")
            if result.rows != expected:
                raise ValueError("statistics_count_mismatch")
            if target != "agent_negative":
                total.merge(result)
            targets[target] = result.result()
        # Do not attach a completed report to changing or partially replaced files.
        if verify_artifacts(run) != manifest or inventory_identity(run) != identity:
            raise ValueError("statistics_source_changed")
        result = {"version": VERSION, "manifest_digest": digest(manifest),
                  "inventory_digest": digest(identity), "run_id": run.name,
                  "created_at": datetime.now(timezone.utc).isoformat(),
                  "scope": "native_records_per_target_not_cross_run_unique_samples",
                  "language_method": "han_latin_character_share_20_to_80_percent_is_mixed",
                  "total": total.result(), "targets": targets}
        if progress:
            progress("statistics", scanned, expected_total)
        atomic_json(path, result)
        return result
