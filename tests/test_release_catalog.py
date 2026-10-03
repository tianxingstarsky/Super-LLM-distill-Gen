"""Existing local releases remain accessible from the package application boundary."""
from __future__ import annotations

import hashlib
import json
import os

import pytest

from lib.application.workflow_service import WorkflowApplication
from lib.infrastructure import release_catalog
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


def test_release_download_rechecks_size_limit_at_delivery(tmp_path, monkeypatch):
    output = tmp_path / "output"
    folder = output / "export" / "old"
    _write_release(folder, "complete", {"sft.jsonl": b"ninebytes"}, format="chat")
    app = WorkflowApplication(FilesystemWorkflowDriver(tmp_path, output))
    release_id = app.list_releases()[0]["id"]
    monkeypatch.setattr(release_catalog, "DIRECT_DOWNLOAD_LIMIT_BYTES", 8)

    with pytest.raises(ValueError, match="浏览器下载上限"):
        app.release_file(release_id, "sft.jsonl")


def test_release_display_lease_avoids_rehash_and_is_scoped_to_directory(tmp_path, monkeypatch):
    output = tmp_path / "output"
    first = output / "export" / "first"
    second = output / "export" / "second"
    for folder in (first, second):
        _write_release(folder, "complete", {"sft.jsonl": b'abcdefgh\n'}, format="chat")
    app = WorkflowApplication(FilesystemWorkflowDriver(tmp_path, output))
    monkeypatch.setattr(release_catalog.time, "monotonic", lambda: 300.0)
    original = release_catalog.hashlib.file_digest
    calls = 0

    def counted(handle, algorithm):
        nonlocal calls
        calls += 1
        return original(handle, algorithm)

    monkeypatch.setattr(release_catalog.hashlib, "file_digest", counted)
    assert all(row["verified"] for row in app.list_releases())
    assert all(row["verified"] for row in app.list_releases())
    assert calls == 2

    source = first / "sft.jsonl"
    before = source.stat()
    source.write_bytes(b"tampered\n")
    os.utime(source, ns=(before.st_atime_ns, before.st_mtime_ns + 1_000_000_000))
    rows = {row["name"]: row for row in app.list_releases()}
    assert not rows["first"]["verified"]
    assert rows["second"]["verified"]
    assert calls == 3
    with pytest.raises(ValueError, match="文件校验失败"):
        app.release_file(rows["first"]["id"], source.name)


def test_release_manifest_fingerprint_invalidates_restored_metadata(tmp_path, monkeypatch):
    output = tmp_path / "output"
    folder = output / "export" / "one"
    _write_release(folder, "complete", {"sft.jsonl": b'abcdefgh\n'}, format="chat")
    app = WorkflowApplication(FilesystemWorkflowDriver(tmp_path, output))
    monkeypatch.setattr(release_catalog.time, "monotonic", lambda: 300.0)
    assert app.list_releases()[0]["verified"]
    manifest_path = folder / "manifest.json"
    before = manifest_path.stat()
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["sha256"]["sft.jsonl"] = "0" * 64
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    os.utime(manifest_path, ns=(before.st_atime_ns, before.st_mtime_ns))
    assert manifest_path.stat().st_size == before.st_size
    assert not app.list_releases()[0]["verified"]


def test_release_display_lease_expires_and_download_always_rechecks(tmp_path, monkeypatch):
    output = tmp_path / "output"
    folder = output / "export" / "one"
    _write_release(folder, "complete", {"sft.jsonl": b'abcdefgh\n'}, format="chat")
    app = WorkflowApplication(FilesystemWorkflowDriver(tmp_path, output))
    clock = [300.0]
    monkeypatch.setattr(release_catalog.time, "monotonic", lambda: clock[0])
    release = app.list_releases()[0]
    source = folder / "sft.jsonl"
    before = source.stat()
    source.write_bytes(b"tampered\n")
    os.utime(source, ns=(before.st_atime_ns, before.st_mtime_ns))
    assert source.stat().st_size == before.st_size

    with pytest.raises(ValueError, match="文件校验失败"):
        app.release_file(release["id"], source.name)
    clock[0] += release_catalog.LEASE_SECONDS + 1
    assert not app.list_releases()[0]["verified"]


def test_nested_release_entry_invalidates_display_lease(tmp_path, monkeypatch):
    output = tmp_path / "output"
    folder = output / "export" / "one"
    _write_release(folder, "complete", {"sft.jsonl": b'abcdefgh\n'}, format="chat")
    app = WorkflowApplication(FilesystemWorkflowDriver(tmp_path, output))
    monkeypatch.setattr(release_catalog.time, "monotonic", lambda: 300.0)
    assert app.list_releases()[0]["verified"]

    nested = folder / "unexpected"
    nested.mkdir()
    (nested / "inside.txt").write_text("not a release file", encoding="utf-8")
    row = app.list_releases()[0]
    assert not row["verified"]
    assert "子目录" in row["error"]
