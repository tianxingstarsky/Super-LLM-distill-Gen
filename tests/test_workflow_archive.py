"""Archive delivery stays on disk and rejects stale or changed content."""
import json
from pathlib import Path
import tracemalloc
import zipfile

import pytest

from lib.infrastructure import workflow_archive as archives
from lib.infrastructure.training_workflow import file_hash


def fixture_run(tmp_path, *, rows=1):
    run = tmp_path / "run"
    artifacts = run / "artifacts"
    artifacts.mkdir(parents=True)
    source = artifacts / "sft.jsonl"
    with source.open("w", encoding="utf-8") as handle:
        for i in range(rows):
            handle.write(json.dumps({"id": i, "text": "source evidence " * 50}) + "\n")
    manifest = {"status": "complete", "sha256": {source.name: file_hash(source)}}
    (artifacts / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    return run, source, manifest


def test_fifty_thousand_rows_prepare_without_whole_archive_buffer(tmp_path):
    run, source, manifest = fixture_run(tmp_path, rows=50000)
    tracemalloc.start()
    try:
        reference = archives.prepare_bundle(run)
        peak = tracemalloc.get_traced_memory()[1]
    finally:
        tracemalloc.stop()
    assert source.stat().st_size > 30 * 1024 * 1024
    assert peak < 10 * 1024 * 1024
    assert isinstance(reference["bytes"], int)
    assert reference["sha256"] == file_hash(Path(reference["path"]))
    archives.verify_archive(reference["path"], manifest)


def test_prepared_archive_survives_session_and_is_reused(tmp_path, monkeypatch):
    run, _, _ = fixture_run(tmp_path)
    reference = archives.prepare_bundle(run)
    stamp = Path(reference["path"]).stat().st_mtime_ns
    monkeypatch.setattr(archives, "verify_archive", lambda *args: pytest.fail("Repacked valid archive"))
    assert archives.prepared_bundle(run) == reference
    assert archives.prepare_bundle(run) == reference
    assert Path(reference["path"]).stat().st_mtime_ns == stamp
    assert archives.bundle_bytes(run).startswith(b"PK")


def test_archive_tampering_is_detected_and_can_be_repaired(tmp_path):
    run, _, manifest = fixture_run(tmp_path)
    reference = archives.prepare_bundle(run)
    Path(reference["path"]).write_bytes(b"damaged")
    assert archives.prepared_bundle(run) is None
    repaired = archives.prepare_bundle(run)
    archives.verify_archive(repaired["path"], manifest)


def test_source_changes_during_packaging_cannot_publish(tmp_path, monkeypatch):
    run, source, _ = fixture_run(tmp_path)
    original = archives.verify_archive

    def mutate_after_check(path, manifest):
        original(path, manifest)
        source.write_text("changed", encoding="utf-8")

    monkeypatch.setattr(archives, "verify_archive", mutate_after_check)
    with pytest.raises(ValueError, match="artifact_integrity_error"):
        archives.prepare_bundle(run)
    assert not list((run / "delivery").glob("*.zip"))
    assert not list((run / "delivery").glob("*.json"))
    assert not list((run / "delivery").glob("*.pending"))


def test_single_artifact_download_is_verified_and_bounded(tmp_path):
    run, source, _ = fixture_run(tmp_path)
    with pytest.raises(ValueError, match="artifact_too_large_for_browser"):
        archives.artifact_bytes(run, source.name, 2)
    with pytest.raises(ValueError, match="artifact_not_listed"):
        archives.artifact_bytes(run, "../sft.jsonl", 1024)
    assert archives.artifact_bytes(run, source.name, 1024) == source.read_bytes()


def test_browser_archive_download_cap_is_enforced_at_read_time(tmp_path, monkeypatch):
    run, _, _ = fixture_run(tmp_path)
    prepared = archives.prepare_bundle(run)
    monkeypatch.setattr(archives, "DIRECT_DOWNLOAD_LIMIT_BYTES", prepared["bytes"] - 1)
    with pytest.raises(ValueError, match="archive_too_large_for_browser"):
        archives.bundle_bytes(run)
    assert archives.prepared_bundle(run) == prepared


def test_new_manifest_does_not_reuse_old_zip(tmp_path):
    run, source, manifest = fixture_run(tmp_path)
    old = archives.prepare_bundle(run)
    source.write_text("new content", encoding="utf-8")
    manifest["sha256"][source.name] = file_hash(source)
    (run / "artifacts" / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    assert archives.prepared_bundle(run) is None
    fresh = archives.prepare_bundle(run)
    assert fresh["fingerprint"] != old["fingerprint"]
    with zipfile.ZipFile(fresh["path"]) as archive:
        assert archive.read(source.name) == b"new content"
