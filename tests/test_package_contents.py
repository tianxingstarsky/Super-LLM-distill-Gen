"""The package view leases verification only for read-only display."""
from __future__ import annotations

import hashlib
import json
import os

import pytest

from lib.application.workflow_service import WorkflowApplication
from lib.infrastructure import training_workflow, verified_preview, workflow_driver


def _application_and_files(tmp_path):
    run_id = "a" * 32
    artifacts = tmp_path / "workflows" / run_id / "artifacts"
    artifacts.mkdir(parents=True)
    sample = artifacts / "cpt.jsonl"
    quality = artifacts / "quality.json"
    sample.write_text('{"text":"verified corpus"}\n', encoding="utf-8")
    quality.write_text(json.dumps({"targets": {"cpt": {"total": 1, "eligible": 1}}}),
                       encoding="utf-8")
    hashes = {path.name: hashlib.sha256(path.read_bytes()).hexdigest()
              for path in (sample, quality)}
    (artifacts / "manifest.json").write_text(json.dumps({"status": "complete", "sha256": hashes}),
                                               encoding="utf-8")

    return (WorkflowApplication(workflow_driver.FilesystemWorkflowDriver(tmp_path, tmp_path)),
            run_id, sample, quality, artifacts / "manifest.json", hashes)


def test_package_contents_reuses_verified_snapshot_across_rerenders_and_preview(tmp_path, monkeypatch):
    application, run_id, sample, _, _, hashes = _application_and_files(tmp_path)
    calls = 0
    original = training_workflow.verify_artifacts

    def counted(path):
        nonlocal calls
        calls += 1
        return original(path)

    monkeypatch.setattr(training_workflow, "verify_artifacts", counted)
    contents = application.package_contents(run_id)
    again = application.package_contents(run_id)
    preview = application.artifact_preview(run_id, "cpt", limit=1)

    assert calls == 1
    assert contents["manifest"]["sha256"] == hashes
    assert {row["name"] for row in contents["files"]} == set(hashes)
    assert contents["quality"]["targets"]["cpt"]["eligible"] == 1
    assert again == contents
    assert preview == [{"text": "verified corpus"}]


def test_package_contents_rechecks_changed_source_and_download_fails(tmp_path):
    application, run_id, sample, _, _, _ = _application_and_files(tmp_path)
    application.package_contents(run_id)
    before = sample.stat()
    sample.write_text('{"text":"tampered corpus"}\n', encoding="utf-8")
    os.utime(sample, ns=(before.st_atime_ns, before.st_mtime_ns + 1_000_000_000))

    with pytest.raises(ValueError, match="artifact_integrity_error"):
        application.package_contents(run_id)
    with pytest.raises(ValueError, match="artifact_integrity_error"):
        application.artifact_file(run_id, sample.name)
    with pytest.raises(ValueError, match="artifact_integrity_error"):
        application.bundle(run_id)


def test_package_contents_fingerprints_manifest_even_if_metadata_is_restored(tmp_path):
    application, run_id, _, _, manifest_path, _ = _application_and_files(tmp_path)
    application.package_contents(run_id)
    before = manifest_path.stat()
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["sha256"]["cpt.jsonl"] = "0" * 64
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    os.utime(manifest_path, ns=(before.st_atime_ns, before.st_mtime_ns))
    assert manifest_path.stat().st_size == before.st_size

    with pytest.raises(ValueError, match="artifact_integrity_error"):
        application.package_contents(run_id)


def test_download_rejects_source_tampering_even_if_display_metadata_is_restored(tmp_path):
    application, run_id, sample, _, _, _ = _application_and_files(tmp_path)
    application.package_contents(run_id)
    before = sample.stat()
    sample.write_bytes(sample.read_bytes().replace(b"verified", b"tampered"))
    os.utime(sample, ns=(before.st_atime_ns, before.st_mtime_ns))
    assert sample.stat().st_size == before.st_size

    with pytest.raises(ValueError, match="artifact_integrity_error"):
        application.artifact_file(run_id, sample.name)
    with pytest.raises(ValueError, match="artifact_integrity_error"):
        application.bundle(run_id)


def test_display_lease_expires_after_metadata_preserving_source_change(tmp_path, monkeypatch):
    application, run_id, sample, _, _, _ = _application_and_files(tmp_path)
    clock = [300.0]
    monkeypatch.setattr(verified_preview.time, "monotonic", lambda: clock[0])
    application.package_contents(run_id)
    before = sample.stat()
    sample.write_bytes(sample.read_bytes().replace(b"verified", b"tampered"))
    os.utime(sample, ns=(before.st_atime_ns, before.st_mtime_ns))
    clock[0] += verified_preview.LEASE_SECONDS + 1

    with pytest.raises(ValueError, match="artifact_integrity_error"):
        application.package_contents(run_id)
