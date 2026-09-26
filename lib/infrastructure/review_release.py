"""Stream reviewed versions to disk and defer loading archives until download."""
from __future__ import annotations

from datetime import datetime, timezone
import json
import re
import zipfile

from lib.domain.corpus_review import corpus_identity, validate_corpus_row
from lib.domain.preference_review import pair_identity, validate_pair
from lib.domain.sft_review import sft_identity, validate_sft_record
from lib.infrastructure.review_artifacts import iter_review_rows
from lib.infrastructure.training_workflow import file_hash, verify_artifacts
from lib.io_utils import atomic_json
from lib.workspace import is_linked


def _validators(target):
    if target == "cpt":
        return validate_corpus_row, corpus_identity
    if target == "sft":
        return validate_sft_record, sft_identity
    return validate_pair, pair_identity


def _verify_zip(path, manifest):
    with zipfile.ZipFile(path) as archive:
        names = archive.namelist()
        if len(names) != len(set(names)) or set(names) != set(manifest["sha256"]) | {"manifest.json"}:
            raise ValueError("review_archive_inventory_mismatch")
        if json.loads(archive.read("manifest.json")) != manifest:
            raise ValueError("review_archive_manifest_mismatch")
        import hashlib
        for name, expected in manifest["sha256"].items():
            digest = hashlib.sha256()
            with archive.open(name) as handle:
                for block in iter(lambda: handle.read(1024 * 1024), b""):
                    digest.update(block)
            if digest.hexdigest() != expected:
                raise ValueError("review_archive_integrity_error")


def prepare_review_release(path, target, prefix, index, store, *, progress=None):
    """The caller holds the artifact-index and audit locks for this operation."""
    before = verify_artifacts(path)
    store.verify(progress=progress)
    queue = index.page(store, limit=1)
    counts = queue["counts"]
    family = "preference" if target in {"dpo", "orpo"} else target
    pending = counts["pending"] + counts["skipped"]
    if pending:
        raise ValueError(f"{family}_review_incomplete:{pending}")
    if not counts["approved"]:
        noun = "preference_pairs" if family == "preference" else f"{target}_samples"
        raise ValueError(f"no_approved_{noun}")
    releases = path / "releases"
    releases.mkdir(parents=True, exist_ok=True)
    pattern = re.compile(rf"\.?{re.escape(prefix)}-v(\d{{4}})(?:\.pending)?")
    versions = [int(match.group(1)) for item in releases.iterdir()
                if (match := pattern.fullmatch(item.name))]
    version = max(versions, default=0) + 1
    release_id = f"{prefix}-v{version:04d}"
    destination = releases / release_id
    staging = releases / f".{release_id}.pending"
    staging.mkdir(exist_ok=False)
    artifact_name = f"{target}.jsonl"
    data = staging / artifact_name
    validate, identity = _validators(target)
    preference = family == "preference"
    id_key, payload_key = ("pair_id", "pair") if preference else ("sample_id", "row")
    rows = iter_review_rows(path, target, validate, identity, id_key=id_key, payload_key=payload_key)
    written, total = 0, 0
    if progress:
        progress("data", 0, queue["total"])
    with data.open("w", encoding="utf-8", newline="\n") as handle:
        for row in rows:
            review = store.get(row[id_key])
            if review is None or review["decision"] not in {"approved", "rejected"}:
                raise ValueError(f"{family}_review_incomplete")
            if review["decision"] == "approved":
                handle.write(json.dumps(review["candidate"], ensure_ascii=False, sort_keys=True,
                                        separators=(",", ":")) + "\n")
                written += 1
            total += 1
            if progress and (total % 250 == 0 or total == queue["total"]):
                progress("data", total, queue["total"])
    if total != queue["total"] or written != counts["approved"]:
        raise ValueError("review_release_counts_changed")
    audit = staging / "review.json"
    if progress:
        progress("snapshot")
    store.write_snapshot(audit)
    if verify_artifacts(path) != before:
        raise ValueError("review_source_changed_retry")
    manifest = {"status": "human_reviewed", "target": target, "run_id": path.name,
                "version": version, "created_at": datetime.now(timezone.utc).isoformat(),
                "source_artifact_sha256": before["sha256"][artifact_name],
                "counts": {"candidate": total, "approved": written, "rejected": total - written},
                "sha256": {artifact_name: file_hash(data), "review.json": file_hash(audit)}}
    atomic_json(staging / "manifest.json", manifest)
    archives = releases / ".archives"
    archives.mkdir(exist_ok=True)
    archive_path = archives / f"{release_id}.zip"
    pending_archive = archives / f".{release_id}.pending.zip"
    if progress:
        progress("archive", 0, 3)
    with zipfile.ZipFile(pending_archive, "w", zipfile.ZIP_DEFLATED) as archive:
        for position, name in enumerate((artifact_name, "review.json", "manifest.json"), 1):
            archive.write(staging / name, name)
            if progress:
                progress("archive", position, 3)
    if progress:
        progress("verify")
    _verify_zip(pending_archive, manifest)
    if progress:
        progress("verify", 1, 1)
    staging.rename(destination)
    pending_archive.replace(archive_path)
    return {"id": release_id, "target": target, "counts": manifest["counts"],
            "bytes": archive_path.stat().st_size, "sha256": file_hash(archive_path)}


def review_archive(path, prefix, release_id, expected_hash):
    if not re.fullmatch(rf"{re.escape(prefix)}-v\d{{4}}", release_id or ""):
        raise ValueError("invalid_review_release_id")
    archive = path / "releases" / ".archives" / f"{release_id}.zip"
    if any(is_linked(item) for item in (path, path / "releases", archive.parent, archive)):
        raise ValueError("linked_review_archive")
    if not isinstance(expected_hash, str) or not re.fullmatch(r"[a-f0-9]{64}", expected_hash):
        raise ValueError("invalid_review_archive_hash")
    handle = archive.open("rb")
    try:
        import hashlib
        if hashlib.file_digest(handle, "sha256").hexdigest() != expected_hash:
            raise ValueError("review_archive_integrity_error")
        handle.seek(0)
        return handle
    except BaseException:
        handle.close()
        raise
