"""The package view verifies each large artifact once per refresh."""
from __future__ import annotations

import hashlib
import json

from lib.application.workflow_service import WorkflowApplication
from lib.infrastructure import workflow_driver


def test_package_contents_uses_one_verified_snapshot(tmp_path, monkeypatch):
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

    calls = 0
    original = workflow_driver.verify_artifacts

    def counted(path):
        nonlocal calls
        calls += 1
        return original(path)

    monkeypatch.setattr(workflow_driver, "verify_artifacts", counted)
    application = WorkflowApplication(workflow_driver.FilesystemWorkflowDriver(tmp_path, tmp_path))
    contents = application.package_contents(run_id)

    assert calls == 1
    assert contents["manifest"]["sha256"] == hashes
    assert {row["name"] for row in contents["files"]} == set(hashes)
    assert contents["quality"]["targets"]["cpt"]["eligible"] == 1
