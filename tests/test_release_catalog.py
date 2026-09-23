"""Existing local releases remain accessible from the package application boundary."""
from __future__ import annotations

import hashlib
import json

import pytest

from lib.application.workflow_service import WorkflowApplication
from lib.infrastructure.workflow_driver import FilesystemWorkflowDriver


def _write_release(folder, status, files, **fields):
    folder.mkdir(parents=True)
    for name, contents in files.items():
        (folder / name).write_bytes(contents)
    manifest = {"status": status, **fields,
                "sha256": {name: hashlib.sha256(contents).hexdigest()
                           for name, contents in files.items()}}
    (folder / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    return manifest


def test_legacy_and_reviewed_releases_are_distinct_and_downloadable(tmp_path):
    output = tmp_path / "output"
    legacy = output / "export" / "older-export"
    legacy_manifest = _write_release(
        legacy, "complete", {"sft.jsonl": b'{"messages":[]}\n'},
        created_at="2026-09-01T00:00:00+00:00", format="chat", counts={"chat": 1},
    )
    (legacy / "quality.json").write_text('{"reviewed": 1}', encoding="utf-8")
    run_id = "a" * 32
    reviewed = output / "workflows" / run_id / "releases" / "sft-v0001"
    _write_release(
        reviewed, "human_reviewed", {"sft.jsonl": b'{"messages":[{"role":"assistant","content":"ok"}]}\n',
                                     "review.json": b'{"events":[]}'},
        created_at="2026-09-02T00:00:00+00:00", target="sft", run_id=run_id,
        version=1, counts={"candidate": 1, "approved": 1, "rejected": 0},
    )
    app = WorkflowApplication(FilesystemWorkflowDriver(tmp_path, output))
    rows = app.list_releases()
    assert [row["kind"] for row in rows] == ["review", "export"]
    assert all(row["verified"] for row in rows)
    assert [file["name"] for file in rows[1]["unverified_files"]] == ["quality.json"]
    assert app.release_file(rows[0]["id"], "review.json") == b'{"events":[]}'
    assert json.loads(app.release_file(rows[1]["id"], "manifest.json")) == legacy_manifest
    with pytest.raises(ValueError, match="未列入可下载清单"):
        app.release_file(rows[1]["id"], "quality.json")


def test_modified_release_stays_visible_but_cannot_be_downloaded(tmp_path):
    output = tmp_path / "output"
    folder = output / "export" / "old"
    _write_release(folder, "complete", {"sft.jsonl": b'{"messages":[]}\n'}, format="chat")
    app = WorkflowApplication(FilesystemWorkflowDriver(tmp_path, output))
    release_id = app.list_releases()[0]["id"]
    (folder / "sft.jsonl").write_text("tampered\n", encoding="utf-8")
    row = app.list_releases()[0]
    assert not row["verified"] and "文件校验失败" in row["error"]
    with pytest.raises(ValueError, match="文件校验失败"):
        app.release_file(release_id, "sft.jsonl")
    with pytest.raises(ValueError, match="不属于当前工作区"):
        app.release_file("../another/export/old", "sft.jsonl")
