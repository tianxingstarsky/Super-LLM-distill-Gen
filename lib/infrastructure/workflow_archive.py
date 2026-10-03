"""Verified, disk-backed delivery of automatic workflow artifacts."""
from __future__ import annotations

import hashlib
import io
import json
from functools import lru_cache
from pathlib import Path
import time
import zipfile

from filelock import FileLock

from lib.domain.dataset_assets import DIRECT_DOWNLOAD_LIMIT_BYTES
from lib.infrastructure.training_workflow import atomic_json, file_hash, read_json, verify_artifacts
from lib.infrastructure.verified_preview import LEASE_SECONDS


def fingerprint(manifest: dict) -> str:
    payload = json.dumps(manifest, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def verify_archive(data, manifest: dict, *, progress=None) -> None:
    expected = manifest.get("sha256", {})
    if not isinstance(expected, dict) or not expected:
        raise ValueError("archive_manifest_missing")
    with zipfile.ZipFile(io.BytesIO(data) if isinstance(data, bytes) else data) as archive:
        names = archive.namelist()
        if len(names) != len(set(names)) or set(names) != set(expected) | {"manifest.json"}:
            raise ValueError("archive_inventory_mismatch")
        if json.loads(archive.read("manifest.json")) != manifest:
            raise ValueError("archive_manifest_changed")
        total = sum(archive.getinfo(name).file_size for name in expected)
        done = 0
        if progress:
            progress("verify", done, total)
        for filename, expected_hash in expected.items():
            digest = hashlib.sha256()
            with archive.open(filename) as handle:
                for block in iter(lambda: handle.read(1024 * 1024), b""):
                    digest.update(block)
                    done += len(block)
                    if progress:
                        progress("verify", done, total)
            if digest.hexdigest() != expected_hash:
                raise ValueError("archive_integrity_error")


def _cached_by_key(run: Path, key: str) -> dict | None:
    folder = run / "delivery"
    archive = folder / f"{key}.zip"
    reference = folder / f"{key}.json"
    if (not reference.is_file() or reference.is_symlink()
            or not archive.is_file() or archive.is_symlink()):
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


def _cached(run: Path, manifest: dict) -> dict | None:
    return _cached_by_key(run, fingerprint(manifest))


def _file_identity(path: Path) -> tuple | None:
    if path.is_symlink() or not path.is_file():
        return None
    stat = path.stat()
    return stat.st_mtime_ns, stat.st_ctime_ns, stat.st_size, stat.st_ino


def _delivery_identity(run: Path, key: str) -> tuple:
    folder = run / "delivery"
    return (_file_identity(folder / f"{key}.zip"),
            _file_identity(folder / f"{key}.json"))


@lru_cache(maxsize=16)
def _display_cached(filename: str, key: str, identity: tuple, time_window: int) -> dict | None:
    run = Path(filename)
    reference = _cached_by_key(run, key)
    if _delivery_identity(run, key) != identity:
        raise ValueError("archive_file_changed")
    return reference


def display_prepared_bundle(run: Path, manifest: dict) -> dict | None:
    """Show a recently checked ZIP; delivery still performs fresh full verification."""
    run = Path(run)
    key = fingerprint(manifest)
    identity = _delivery_identity(run, key)
    reference = _display_cached(str(run.resolve()), key, identity,
                                int(time.monotonic() // LEASE_SECONDS))
    if _delivery_identity(run, key) != identity:
        raise ValueError("archive_file_changed")
    return dict(reference) if reference is not None else None


def prepared_bundle(run: Path, *, verified_manifest: dict | None = None) -> dict | None:
    """Strictly verify a prepared archive and, unless supplied, its sources."""
    return _cached(run, verified_manifest if verified_manifest is not None else verify_artifacts(run))


def prepare_bundle(run: Path, *, progress=None) -> dict:
    folder = run / "delivery"
    folder.mkdir(exist_ok=True)
    with FileLock(str(folder / ".archive.lock")):
        if progress:
            progress("source")
        manifest = verify_artifacts(run)
        cached = _cached(run, manifest)
        if cached:
            return cached
        key = fingerprint(manifest)
        pending = folder / f".{key}.pending"
        destination = folder / f"{key}.zip"
        try:
            files = sorted(manifest["sha256"]) + ["manifest.json"]
            total = sum((run / "artifacts" / name).stat().st_size for name in files)
            done = 0
            if progress:
                progress("archive", done, total)
            with zipfile.ZipFile(pending, "w", zipfile.ZIP_DEFLATED) as archive:
                for name in files:
                    with (run / "artifacts" / name).open("rb") as source, archive.open(name, "w", force_zip64=True) as target:
                        for block in iter(lambda: source.read(1024 * 1024), b""):
                            target.write(block)
                            done += len(block)
                            if progress:
                                progress("archive", done, total)
            if progress:
                verify_archive(pending, manifest, progress=progress)
                progress("recheck")
            else:
                verify_archive(pending, manifest)
            if verify_artifacts(run) != manifest:
                raise ValueError("archive_source_changed")
            digest = file_hash(pending)
            size = pending.stat().st_size
            if progress:
                progress("publish")
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
