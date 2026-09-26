import hashlib
import json

import pytest

from lib.infrastructure import training_workflow, verified_preview
from lib.infrastructure.workflow_driver import FilesystemWorkflowDriver


def fixture(tmp_path):
    run_id = "a" * 32
    folder = tmp_path / "workflows" / run_id / "artifacts"
    folder.mkdir(parents=True)
    data = b'{"id":0}\n{"id":1}\n'
    (folder / "sft.jsonl").write_bytes(data)
    (folder / "manifest.json").write_text(json.dumps({
        "status": "complete", "sha256": {"sft.jsonl": hashlib.sha256(data).hexdigest()}
    }))
    return FilesystemWorkflowDriver(tmp_path, tmp_path), run_id, folder


def test_preview_reuses_verification_and_expires(tmp_path, monkeypatch):
    driver, run_id, _ = fixture(tmp_path)
    calls = []
    original = training_workflow.verify_artifacts
    monkeypatch.setattr(training_workflow, "verify_artifacts",
                        lambda path: (calls.append(path), original(path))[1])
    monkeypatch.setattr(verified_preview.time, "monotonic", lambda: 60)
    assert driver.artifact_preview(run_id, "sft", 1, 0) == [{"id": 0}]
    assert driver.artifact_preview(run_id, "sft", 1, 1) == [{"id": 1}]
    assert len(calls) == 1
    monkeypatch.setattr(verified_preview.time, "monotonic", lambda: 90)
    driver.artifact_preview(run_id, "sft", 1, 1)
    assert len(calls) == 2


@pytest.mark.parametrize("change", ["data", "manifest", "extra"])
def test_changed_inventory_invalidates_preview_lease(tmp_path, change):
    driver, run_id, folder = fixture(tmp_path)
    driver.artifact_preview(run_id, "sft", 1)
    if change == "data":
        (folder / "sft.jsonl").write_bytes(b'{"id":99}\n')
    elif change == "manifest":
        (folder / "manifest.json").write_text('{}')
    else:
        (folder / "extra.jsonl").write_text('{}')
    with pytest.raises(ValueError):
        driver.artifact_preview(run_id, "sft", 1)


def test_change_during_row_read_is_rejected(tmp_path, monkeypatch):
    driver, run_id, folder = fixture(tmp_path)
    from lib.infrastructure import jsonl_preview
    def read(*args):
        (folder / "sft.jsonl").write_bytes(b'{"id":99}\n')
        return [{"id": 0}]
    monkeypatch.setattr(jsonl_preview, "read_rows", read)
    with pytest.raises(ValueError, match="preview_file_changed"):
        driver.artifact_preview(run_id, "sft", 1)
