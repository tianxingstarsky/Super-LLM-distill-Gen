"""Uploads are validated as a batch and remain usable after console restarts."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import hashlib
import os
from pathlib import Path
import subprocess

import pytest

from lib import workspace
from lib.application import local_input_service as validation
from lib.bootstrap.asset_catalog import asset_catalog_application
from lib.bootstrap.local_inputs import local_input_application
from lib.infrastructure import local_input_cache
from lib.infrastructure.workflow_driver import FilesystemWorkflowDriver


@pytest.fixture
def cache_root(tmp_path, monkeypatch):
    root = tmp_path / "console"
    monkeypatch.setattr(workspace, "ROOT", root)
    monkeypatch.setattr(workspace, "SEEDS_DIR", root / "data/seeds")
    return root


def _path(root: Path, name: str, content: bytes) -> Path:
    return root / "data/seeds/uploads" / hashlib.sha256(content).hexdigest() / name


@pytest.mark.parametrize("name", [
    "../escape.txt", "..\\escape.txt", "/escape.txt", "C:\\escape.txt",
    "C:escape.txt", "secret.txt:stream", "CON.txt", "lpt1.md", "NUL",
    "bad\x00.txt", "bad\n.txt", "bad?.txt", "bad*.txt", "bad|.txt",
    "bad<.txt", "bad>.txt", 'bad".txt', "trailing.txt ", "trailing.txt.",
    ".", "..", "", "a" * 241 + ".txt", "资" * 100 + ".txt", "bad\ud800.txt",
])
def test_invalid_name_rejects_whole_batch_before_creating_cache(cache_root, name):
    with pytest.raises(ValueError, match="invalid_upload_name"):
        local_input_application(cache_root).store([("first.txt", b"valid"), (name, b"bad")])
    assert not cache_root.exists()


@pytest.mark.parametrize("item,reason", [
    (("bad.exe", b"bytes"), "unsupported_upload_type"),
    (("bad", b"bytes"), "unsupported_upload_type"),
    (("empty.txt", b""), "empty_upload"),
    (("bad.txt", "text"), "invalid_upload_content"),
    (("bad.txt", bytearray(b"bytes")), "invalid_upload_content"),
    (("bad.txt",), "invalid_upload_content"),
    ("bad.txt", "invalid_upload_content"),
    ((None, b"bytes"), "invalid_upload_name"),
])
def test_invalid_content_or_type_does_not_persist_prior_files(cache_root, item, reason):
    with pytest.raises(ValueError, match=reason):
        local_input_application(cache_root).store([("first.txt", b"valid"), item])
    assert not cache_root.exists()


def test_generator_failure_and_empty_batch_do_not_create_cache(cache_root):
    application = local_input_application(cache_root)
    with pytest.raises(ValueError, match="empty_upload_batch"):
        application.store([])

    def broken_uploads():
        yield "first.txt", b"valid"
        raise OSError("upload stream disconnected")

    with pytest.raises(OSError, match="disconnected"):
        application.store(broken_uploads())
    assert not cache_root.exists()


def test_each_supported_document_type_is_preserved_without_conversion(cache_root):
    documents = [(f"资料.{suffix.upper()}", f"content for {suffix}".encode())
                 for suffix in sorted(validation.ALLOWED_UPLOAD_SUFFIXES)]
    rows = local_input_application(cache_root).store(documents)
    assert [(row["name"], Path(row["path"]).read_bytes()) for row in rows] == documents
    assert [row["size"] for row in rows] == [len(content) for _, content in documents]


def test_content_survives_new_application_instances_and_is_deduplicated(cache_root):
    first = local_input_application(cache_root).store([("original.txt", b"persistent")])[0]
    path = Path(first["path"])
    initial_mtime = path.stat().st_mtime_ns
    second = local_input_application(cache_root).store([("renamed.txt", b"persistent")])[0]
    repeated = local_input_application(cache_root).store([("original.txt", b"persistent")])[0]
    assert first["path"] == second["path"] == repeated["path"]
    assert second["name"] == "renamed.txt"
    assert path.read_bytes() == b"persistent"
    assert path.stat().st_mtime_ns == initial_mtime
    assert list((cache_root / "data/seeds/uploads").rglob("*.*")) == [path]


def test_same_batch_aliases_share_content_and_same_names_keep_distinct_versions(cache_root):
    rows = local_input_application(cache_root).store([
        ("manual.txt", b"revision one"), ("alias.md", b"revision one"),
        ("manual.txt", b"revision two"),
    ])
    assert rows[0]["path"] == rows[1]["path"]
    assert rows[0]["path"] != rows[2]["path"]
    assert [Path(row["path"]).read_bytes() for row in rows] == [
        b"revision one", b"revision one", b"revision two",
    ]


def test_default_composition_uses_source_directory_and_workflow_and_catalog_can_read(cache_root):
    row = local_input_application().store([("客户资料.md", "真实资料".encode("utf-8"))])[0]
    path = Path(row["path"])
    assert path.is_relative_to(workspace.SEEDS_DIR)
    assert workspace.source_files("default") == [path]
    workflow_sources = FilesystemWorkflowDriver.source_files("default", frozenset({".md"}), 500)
    assert len(workflow_sources) == 1
    assert workflow_sources[0]["path"] == str(path)
    catalog = asset_catalog_application()
    inventory = catalog.inventory("default")
    assert len(inventory.assets) == 1
    asset = inventory.assets[0]
    assert asset.origin == "source"
    assert asset.name == "客户资料.md"
    assert catalog.download("default", asset) == "真实资料".encode("utf-8")
    assert catalog.excerpt("default", asset)[0] == "真实资料"


def test_single_file_limit_and_inclusive_boundary(cache_root, monkeypatch):
    monkeypatch.setattr(validation, "MAX_UPLOAD_BYTES", 4)
    with pytest.raises(ValueError, match="upload_too_large"):
        local_input_application(cache_root).store([("first.txt", b"ok"), ("large.txt", b"12345")])
    assert not cache_root.exists()
    row = local_input_application(cache_root).store([("exact.txt", b"1234")])[0]
    assert Path(row["path"]).read_bytes() == b"1234"


def test_batch_limit_counts_all_uploaded_bytes_and_is_inclusive(cache_root, monkeypatch):
    monkeypatch.setattr(validation, "MAX_UPLOAD_BYTES", 10)
    monkeypatch.setattr(validation, "MAX_UPLOAD_BATCH_BYTES", 6)
    with pytest.raises(ValueError, match="upload_batch_too_large"):
        local_input_application(cache_root).store([("first.txt", b"1234"), ("alias.txt", b"1234")])
    assert not cache_root.exists()
    rows = local_input_application(cache_root).store([("first.txt", b"123"), ("second.md", b"456")])
    assert sum(row["size"] for row in rows) == 6


def test_corrupt_later_cached_content_rejects_entire_batch_without_overwrite(cache_root):
    corrupt = _path(cache_root, "existing.txt", b"expected")
    corrupt.parent.mkdir(parents=True)
    corrupt.write_bytes(b"tampered")
    with pytest.raises(ValueError, match="upload_cache_content_mismatch"):
        local_input_application(cache_root).store([
            ("first.txt", b"new content"), ("existing.txt", b"expected"),
        ])
    assert corrupt.read_bytes() == b"tampered"
    assert not _path(cache_root, "first.txt", b"new content").exists()
    assert not (cache_root / "data/.upload-cache.lock").exists()


def test_existing_directory_in_place_of_source_rejects_entire_batch(cache_root):
    invalid = _path(cache_root, "existing.txt", b"expected")
    invalid.mkdir(parents=True)
    with pytest.raises(ValueError, match="invalid_upload_cache_path"):
        local_input_application(cache_root).store([
            ("first.txt", b"new content"), ("existing.txt", b"expected"),
        ])
    assert not _path(cache_root, "first.txt", b"new content").exists()


@pytest.mark.parametrize("linked_at", ["data", "uploads", "digest", "file", "lock"])
def test_symbolic_links_cannot_redirect_upload_or_read_outside_cache(cache_root, tmp_path, linked_at):
    content = b"protected content"
    target = _path(cache_root, "document.txt", content)
    outside = tmp_path / "outside"
    outside.mkdir()
    outside_file = outside / "protected.txt"
    outside_file.write_bytes(content)
    link = {
        "data": cache_root / "data", "uploads": cache_root / "data/seeds/uploads",
        "digest": target.parent, "file": target, "lock": cache_root / "data/.upload-cache.lock",
    }[linked_at]
    link.parent.mkdir(parents=True, exist_ok=True)
    is_directory = linked_at in {"data", "uploads", "digest"}
    try:
        link.symlink_to(outside if is_directory else outside_file, target_is_directory=is_directory)
    except OSError as error:
        pytest.skip(f"Host cannot create symbolic links: {error}")
    with pytest.raises(ValueError, match="linked_upload_cache_path"):
        local_input_application(cache_root).store([("document.txt", content)])
    assert outside_file.read_bytes() == content
    assert list(outside.iterdir()) == [outside_file]


def test_hard_linked_cached_file_is_rejected(cache_root, tmp_path):
    content = b"protected content"
    outside = tmp_path / "protected.txt"
    outside.write_bytes(content)
    target = _path(cache_root, "document.txt", content)
    target.parent.mkdir(parents=True)
    os.link(outside, target)
    with pytest.raises(ValueError, match="linked_upload_cache_path"):
        local_input_application(cache_root).store([("document.txt", content)])
    assert outside.read_bytes() == content


@pytest.mark.skipif(os.name != "nt", reason="Windows junction protection")
def test_windows_junction_cannot_redirect_uploads(cache_root, tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    junction = cache_root / "data/seeds/uploads"
    junction.parent.mkdir(parents=True)
    result = subprocess.run(["cmd", "/c", "mklink", "/J", str(junction), str(outside)],
                            capture_output=True)
    if result.returncode:
        pytest.skip("Host cannot create Windows junctions")
    try:
        with pytest.raises(ValueError, match="linked_upload_cache_path"):
            local_input_application(cache_root).store([("document.txt", b"content")])
        assert list(outside.iterdir()) == []
    finally:
        os.rmdir(junction)


def test_waiting_for_lock_revalidates_whole_batch(cache_root, monkeypatch):
    later = _path(cache_root, "later.txt", b"later")

    class InterveningLock:
        def __init__(self, *args, **kwargs):
            pass

        def __enter__(self):
            later.parent.mkdir(parents=True)
            later.write_bytes(b"wrong")

        def __exit__(self, *args):
            pass

    monkeypatch.setattr(local_input_cache, "FileLock", InterveningLock)
    with pytest.raises(ValueError, match="upload_cache_content_mismatch"):
        local_input_application(cache_root).store([("first.txt", b"first"), ("later.txt", b"later")])
    assert not _path(cache_root, "first.txt", b"first").exists()


def test_storage_adapter_also_confines_unvalidated_names_to_content_directory(cache_root):
    driver = local_input_cache.FilesystemLocalInputCacheDriver(cache_root)
    with pytest.raises(ValueError, match="invalid_upload_name"):
        driver.store((("first.txt", b"valid"), ("../../outside.txt", b"escape")))
    assert not cache_root.exists()


def test_parallel_sessions_deduplicate_identical_content(cache_root):
    def upload(index):
        return local_input_application(cache_root).store([(f"name-{index}.txt", b"one source")])[0]

    with ThreadPoolExecutor(max_workers=6) as executor:
        rows = list(executor.map(upload, range(12)))
    assert len({row["path"] for row in rows}) == 1
    assert workspace.source_files("default") == [Path(rows[0]["path"])]
    assert not list((cache_root / "data").glob(".upload-*.tmp"))


def test_interrupted_publish_does_not_expose_partial_source_or_leave_temporary_file(cache_root, monkeypatch):
    def fail_publish(*args):
        raise OSError("disk write failed")

    monkeypatch.setattr(local_input_cache.os, "replace", fail_publish)
    with pytest.raises(OSError, match="disk write failed"):
        local_input_application(cache_root).store([("document.txt", b"complete source")])
    assert workspace.source_files("default") == []
    assert not list((cache_root / "data").glob(".upload-*.tmp"))
