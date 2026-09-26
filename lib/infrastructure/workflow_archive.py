"""Verified, disk-backed delivery of automatic workflow artifacts."""
from __future__ import annotations

import hashlib
import io
import json
from pathlib import Path
import zipfile

from filelock import FileLock

from lib.domain.dataset_assets import DIRECT_DOWNLOAD_LIMIT_BYTES
from lib.infrastructure.training_workflow import atomic_json, file_hash, read_json, verify_artifacts


def fingerprint(manifest: dict) -> str:
    payload = json.dumps(manifest, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def verify_archive(data, manifest: dict) -> None:
    expected = manifest.get("sha256", {})
    if not isinstance(expected, dict) or not expected:
        raise ValueError("archive_manifest_missing")
    with zipfile.ZipFile(io.BytesIO(data) if isinstance(data, bytes) else data) as archive:
        names = archive.namelist()
        if len(names) != len(set(names)) or set(names) != set(expected) | {"manifest.json"}:
            raise ValueError("archive_inventory_mismatch")
        if json.loads(archive.read("manifest.json")) != manifest:
            raise ValueError("archive_manifest_changed")
        for filename, expected_hash in expected.items():
            digest = hashlib.sha256()
            with archive.open(filename) as handle:
                for block in iter(lambda: handle.read(1024 * 1024), b""):
                    digest.update(block)
            if digest.hexdigest() != expected_hash:
                raise ValueError("archive_integrity_error")


def _cached(run: Path, manifest: dict) -> dict | None:
    key = fingerprint(manifest)
    folder = run / "delivery"
    archive = folder / f"{key}.zip"
    reference = folder / f"{key}.json"
    if not reference.is_file() or not archive.is_file() or archive.is_symlink():
        return None
    try:
        saved = read_json(reference)
        if (saved.get("fingerprint") != key or saved.get("bytes") != archive.stat().st_size
                or saved.get("sha256") != file_hash(archive)):
            return None
    except (OSError, ValueError, TypeError, AttributeError):
        return None
    return {"fingerprint": key, "bytes": archive.stat().st_size,
            "sha256": saved["sha256"], "path": str(archive.resolve())}


def prepared_bundle(run: Path, *, verified_manifest: dict | None = None) -> dict | None:
    """Reuse the adapter's verified snapshot when rendering package contents."""
    return _cached(run, verified_manifest if verified_manifest is not None else verify_artifacts(run))


def prepare_bundle(run: Path) -> dict:
    folder = run / "delivery"
    folder.mkdir(exist_ok=True)
    with FileLock(str(folder / ".archive.lock")):
        manifest = verify_artifacts(run)
        cached = _cached(run, manifest)
        if cached:
            return cached
        key = fingerprint(manifest)
        pending = folder / f".{key}.pending"
        destination = folder / f"{key}.zip"
        try:
            with zipfile.ZipFile(pending, "w", zipfile.ZIP_DEFLATED) as archive:
                for name in sorted(manifest["sha256"]):
                    archive.write(run / "artifacts" / name, name)
                archive.write(run / "artifacts" / "manifest.json", "manifest.json")
            verify_archive(pending, manifest)
            if verify_artifacts(run) != manifest:
                raise ValueError("archive_source_changed")
            digest = file_hash(pending)
            size = pending.stat().st_size
            pending.replace(destination)
            atomic_json(folder / f"{key}.json", {"fingerprint": key, "sha256": digest, "bytes": size})
        finally:
            pending.unlink(missing_ok=True)
        return {"fingerprint": key, "sha256": digest, "bytes": size,
                "path": str(destination.resolve())}


def bundle_bytes(run: Path) -> bytes:
    prepared = prepare_bundle(run)
    if prepared["bytes"] > DIRECT_DOWNLOAD_LIMIT_BYTES:
        raise ValueError("archive_too_large_for_browser")
    with Path(prepared["path"]).open("rb") as handle:
        payload = handle.read(DIRECT_DOWNLOAD_LIMIT_BYTES + 1)
    if len(payload) > DIRECT_DOWNLOAD_LIMIT_BYTES:
        raise ValueError("archive_too_large_for_browser")
    if hashlib.sha256(payload).hexdigest() != prepared["sha256"]:
        raise ValueError("archive_integrity_error")
    return payload


def artifact_bytes(run: Path, filename: str, max_bytes: int) -> bytes:
    manifest = verify_artifacts(run)
    expected = manifest["sha256"].get(filename)
    if not expected or Path(filename).name != filename:
        raise ValueError("artifact_not_listed")
    path = run / "artifacts" / filename
    with path.open("rb") as handle:
        payload = handle.read(max_bytes + 1)
    if len(payload) > max_bytes:
        raise ValueError("artifact_too_large_for_browser")
    if hashlib.sha256(payload).hexdigest() != expected:
        raise ValueError("artifact_integrity_error")
    return payload
