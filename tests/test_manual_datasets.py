"""Local authorship must survive restart and export actual, bounded media."""
from concurrent.futures import ThreadPoolExecutor
from io import BytesIO
import json
import os
from pathlib import Path
from zipfile import ZipFile

from PIL import Image
import pytest

from lib.bootstrap.manual_datasets import manual_dataset_application
from lib.domain.manual_dataset import MAX_IMAGES
from lib.infrastructure import manual_dataset_file


def image_bytes(format="PNG"):
    output = BytesIO()
    Image.new("RGB", (12, 8), (10, 90, 220)).save(output, format=format)
    return output.getvalue()


def saved(tmp_path):
    app = manual_dataset_application(tmp_path)
    dataset = app.create_dataset("Product questions")
    return app, dataset


def test_roundtrip_reopens_samples_and_export_has_real_relative_media(tmp_path):
    app, dataset = saved(tmp_path)
    payload = image_bytes()
    row = app.append_sample(dataset, question="Describe the image", answer="A blue rectangle.",
                            system="Answer from the supplied image.", attachments=[("photo.png", payload)])
    reopened = manual_dataset_application(tmp_path)
    assert reopened.list_samples(dataset) == [row]
    assert reopened.list_datasets()[0]["count"] == 1
    assert reopened.read_media(dataset, row["media"][0]["id"]) == payload
    assert row["metadata"] == {"origin": "manual", "review_status": "unreviewed"}
    assert [message["role"] for message in row["messages"]] == ["system", "user", "assistant"]
    media_path = row["media"][0]["path"]
    assert row["messages"][1]["content"][0]["image_url"]["url"] == media_path
    with ZipFile(BytesIO(reopened.export_dataset(dataset))) as package:
        assert set(package.namelist()) == {"manifest.json", "samples.jsonl", media_path}
        assert package.read(media_path) == payload
        exported = json.loads(package.read("samples.jsonl"))
        assert exported == row
        manifest = json.loads(package.read("manifest.json"))
        assert manifest["count"] == 1 and manifest["media_count"] == 1
        assert manifest["review_status"] == "unreviewed"
        assert all(not Path(name).is_absolute() and ".." not in name for name in package.namelist())
        assert str(tmp_path).encode() not in package.read("samples.jsonl")


def test_text_only_empty_dataset_and_page_order(tmp_path):
    app, dataset = saved(tmp_path)
    assert app.list_samples(dataset) == []
    with ZipFile(BytesIO(app.export_dataset(dataset))) as package:
        assert package.read("samples.jsonl") == b""
    first = app.append_sample(dataset, question="First?", answer="One")
    second = app.append_sample(dataset, question="Second?", answer="Two")
    assert second["media"] == []
    assert second["messages"] == [{"role": "user", "content": "Second?"}, {"role": "assistant", "content": "Two"}]
    assert app.list_samples(dataset, limit=1) == [second]
    assert app.list_samples(dataset, limit=1, offset=1) == [first]


@pytest.mark.parametrize("extension,format", [("jpg", "JPEG"), ("jpeg", "JPEG"), ("webp", "WEBP"), ("PNG", "PNG")])
def test_allowed_images_are_detected_from_actual_contents(tmp_path, extension, format):
    app, dataset = saved(tmp_path)
    row = app.append_sample(dataset, question="What is shown?", answer="Blue.",
                            attachments=[(f"figure.{extension}", image_bytes(format))])
    assert row["media"][0]["kind"] == "image"
    if extension == "jpeg":
        assert row["media"][0]["path"].endswith(".jpg")


@pytest.mark.parametrize("name,payload", [
    ("../photo.png", b"x"), ("C:\\photo.png", b"x"), ("https://example.com/a.png", b"x"),
    ("photo.svg", b"<svg/>"), ("photo.png", b"not an image"), ("photo.png", image_bytes("JPEG")),
    ("photo.jpg", image_bytes()[:-20]), ("photo.png\x00", image_bytes()), ("photo.png", b"")])
def test_invalid_upload_never_creates_partial_sample(tmp_path, name, payload):
    app, dataset = saved(tmp_path)
    with pytest.raises(ValueError, match="invalid_manual_dataset_"):
        app.append_sample(dataset, question="Question", answer="Answer", attachments=[(name, payload)])
    assert app.list_datasets()[0]["count"] == 0
    assert list((tmp_path / "manual_datasets" / dataset / "media").iterdir()) == []


def test_animated_image_is_not_silently_treated_as_single_frame(tmp_path):
    app, dataset = saved(tmp_path)
    content = BytesIO()
    a = Image.new("RGB", (12, 8), "red")
    a.save(content, format="WEBP", save_all=True, append_images=[Image.new("RGB", (12, 8), "blue")], duration=100, loop=0)
    with pytest.raises(ValueError, match="invalid_manual_dataset_image"):
        app.append_sample(dataset, question="Q", answer="A", attachments=[("animated.webp", content.getvalue())])


def test_image_and_upload_budgets_checked_before_commit(tmp_path, monkeypatch):
    app, dataset = saved(tmp_path)
    with pytest.raises(ValueError, match="invalid_manual_dataset_attachments"):
        app.append_sample(dataset, question="Q", answer="A", attachments=[("a.png", image_bytes())] * (MAX_IMAGES + 1))
    monkeypatch.setattr(manual_dataset_file, "MAX_IMAGE_PIXELS", 10)
    with pytest.raises(ValueError, match="invalid_manual_dataset_image"):
        app.append_sample(dataset, question="Q", answer="A", attachments=[("a.png", image_bytes())])
    assert app.list_samples(dataset) == []


@pytest.mark.parametrize("field,value", [("question", ""), ("answer", " "), ("answer", "a" * 100001),
                                        ("system", "s" * 20001), ("question", None)],
                         ids=["empty-question", "blank-answer", "long-answer", "long-system", "missing-question"])
def test_invalid_text_cannot_be_saved(tmp_path, field, value):
    app, dataset = saved(tmp_path)
    arguments = {"question": "Q", "answer": "A", "system": ""}
    arguments[field] = value
    with pytest.raises(ValueError, match="invalid_manual_dataset_text"):
        app.append_sample(dataset, **arguments)


@pytest.mark.parametrize("identifier", ["../a", "https://example.com", "A" * 32, "f" * 31, None])
def test_ids_never_become_external_paths(tmp_path, identifier):
    app = manual_dataset_application(tmp_path)
    with pytest.raises(ValueError, match="invalid_manual_dataset_id"):
        app.list_samples(identifier)


@pytest.mark.parametrize("limit,offset", [(0, 0), (101, 0), (True, 0), (20, -1), (20, 1_000_001)])
def test_pagination_has_explicit_bounds(tmp_path, limit, offset):
    app, dataset = saved(tmp_path)
    with pytest.raises(ValueError, match="invalid_manual_dataset_page"):
        app.list_samples(dataset, limit, offset)


def test_cross_dataset_media_cannot_be_read(tmp_path):
    app, dataset = saved(tmp_path)
    row = app.append_sample(dataset, question="Q", answer="A", attachments=[("a.png", image_bytes())])
    second = app.create_dataset("Different")
    with pytest.raises(ValueError, match="manual_media_not_found"):
        app.read_media(second, row["media"][0]["id"])


def test_changed_media_blocks_preview_and_export(tmp_path):
    app, dataset = saved(tmp_path)
    row = app.append_sample(dataset, question="Q", answer="A", attachments=[("a.png", image_bytes())])
    media = row["media"][0]
    file = tmp_path / "manual_datasets" / dataset / media["path"]
    file.write_bytes(image_bytes("JPEG"))
    for read in (lambda: app.read_media(dataset, media["id"]), lambda: app.export_dataset(dataset)):
        with pytest.raises(ValueError, match="manual_media_integrity_failed"):
            read()


def test_tampered_media_path_and_review_claim_cannot_escape_in_export(tmp_path):
    app, dataset = saved(tmp_path)
    row = app.append_sample(dataset, question="Q", answer="A")
    file = tmp_path / "manual_datasets" / dataset / "samples" / (row["id"] + ".json")
    row["metadata"]["review_status"] = "approved"
    file.write_text(json.dumps(row), encoding="utf-8")
    with pytest.raises(ValueError, match="invalid_manual_dataset_storage"):
        app.export_dataset(dataset)


@pytest.mark.parametrize("unsafe", ["../outside.png", "C:/outside.png", "https://example.com/a.png"])
def test_media_sidecar_cannot_redirect_a_read_or_archive(tmp_path, unsafe):
    app, dataset = saved(tmp_path)
    row = app.append_sample(dataset, question="Q", answer="A", attachments=[("a.png", image_bytes())])
    media = row["media"][0]
    index_path = tmp_path / "manual_datasets" / dataset / "media" / (media["id"] + ".json")
    index = json.loads(index_path.read_text(encoding="utf-8"))
    index["path"] = unsafe
    index_path.write_text(json.dumps(index), encoding="utf-8")
    with pytest.raises(ValueError, match="invalid_manual_dataset_storage"):
        app.read_media(dataset, media["id"])
    with pytest.raises(ValueError, match="invalid_manual_dataset_storage"):
        app.export_dataset(dataset)


def test_uncommitted_orphan_media_is_not_exposed_or_packaged(tmp_path):
    app, dataset = saved(tmp_path)
    orphan_id = "a" * 32
    directory = tmp_path / "manual_datasets" / dataset / "media"
    (directory / (orphan_id + ".png")).write_bytes(image_bytes())
    with pytest.raises(ValueError, match="manual_media_not_found"):
        app.read_media(dataset, orphan_id)
    with ZipFile(BytesIO(app.export_dataset(dataset))) as package:
        assert set(package.namelist()) == {"samples.jsonl", "manifest.json"}


def test_invalid_dataset_name_does_not_leave_an_unreadable_library(tmp_path):
    app = manual_dataset_application(tmp_path)
    for name in ("", "  ", "a" * 101, "broken\ud800", "broken\nname"):
        with pytest.raises(ValueError, match="invalid_manual_dataset_name"):
            app.create_dataset(name)
    assert app.list_datasets() == []


def test_failed_manifest_creation_keeps_existing_library_readable(tmp_path, monkeypatch):
    app, dataset = saved(tmp_path)
    previous = app.append_sample(dataset, question="Existing?", answer="Keep this sample.")
    original = manual_dataset_file._commit
    def fail_manifest(path, payload):
        if path.name == "manifest.json":
            assert path.parent.name.startswith(".pending-")
            raise OSError("simulated manifest write failure")
        return original(path, payload)
    monkeypatch.setattr(manual_dataset_file, "_commit", fail_manifest)
    with pytest.raises(OSError, match="simulated manifest write failure"):
        app.create_dataset("Interrupted dataset")
    assert [row["id"] for row in app.list_datasets()] == [dataset]
    assert app.list_samples(dataset) == [previous]
    directory = tmp_path / "manual_datasets"
    assert {item.name for item in directory.iterdir() if item.name != ".lock"} == {dataset}


def test_interrupted_pending_directory_does_not_break_library(tmp_path):
    app, dataset = saved(tmp_path)
    pending = tmp_path / "manual_datasets" / (".pending-" + "b" * 32)
    (pending / "samples").mkdir(parents=True)
    (pending / "media").mkdir()
    # An interrupted write may leave both an incomplete manifest and a temp file.
    (pending / "manifest.json").write_bytes(b'{"version":')
    (pending / ".unfinished.pending").write_bytes(b"incomplete")
    reopened = manual_dataset_application(tmp_path)
    assert [row["id"] for row in reopened.list_datasets()] == [dataset]
    new_dataset = reopened.create_dataset("After restart")
    assert {row["id"] for row in reopened.list_datasets()} == {dataset, new_dataset}
    assert (pending / ".unfinished.pending").read_bytes() == b"incomplete"


def test_publish_failure_removes_only_own_pending_directory(tmp_path, monkeypatch):
    app, dataset = saved(tmp_path)
    stale = tmp_path / "manual_datasets" / (".pending-" + "c" * 32)
    stale.mkdir()
    (stale / "keep.bin").write_bytes(b"from another interrupted operation")
    def fail_publish(source, target):
        assert source.parent == target.parent
        assert source.name.startswith(".pending-")
        assert not target.exists()
        raise OSError("simulated publish failure")
    monkeypatch.setattr(manual_dataset_file.os, "rename", fail_publish)
    with pytest.raises(OSError, match="simulated publish failure"):
        app.create_dataset("Never published")
    assert [row["id"] for row in app.list_datasets()] == [dataset]
    assert (stale / "keep.bin").read_bytes() == b"from another interrupted operation"
    assert list((tmp_path / "manual_datasets").glob(".pending-*")) == [stale]


def test_atomic_commit_failure_rolls_back_media_and_keeps_previous_work(tmp_path, monkeypatch):
    app, dataset = saved(tmp_path)
    old = app.append_sample(dataset, question="Previous", answer="Keep this")
    original = manual_dataset_file._commit
    def fail_sample(path, payload):
        if path.parent.name == "samples":
            raise OSError("simulated disk failure")
        return original(path, payload)
    monkeypatch.setattr(manual_dataset_file, "_commit", fail_sample)
    with pytest.raises(OSError, match="simulated disk failure"):
        app.append_sample(dataset, question="New", answer="Image", attachments=[("a.png", image_bytes())])
    assert app.list_samples(dataset) == [old]
    assert list((tmp_path / "manual_datasets" / dataset / "media").iterdir()) == []


def test_concurrent_append_uses_complete_independent_sample_commits(tmp_path):
    app, dataset = saved(tmp_path)
    def add(index):
        reopened = manual_dataset_application(tmp_path)
        return reopened.append_sample(dataset, question=f"Question {index}", answer=f"Answer {index}")
    with ThreadPoolExecutor(max_workers=8) as pool:
        expected = list(pool.map(add, range(24)))
    actual = app.list_samples(dataset, limit=100)
    assert {row["id"] for row in expected} == {row["id"] for row in actual}
    assert app.list_datasets()[0]["count"] == 24
    assert len({row["question"] for row in actual}) == 24


def test_hardlinked_sample_and_media_are_rejected(tmp_path):
    app, dataset = saved(tmp_path)
    row = app.append_sample(dataset, question="Q", answer="A", attachments=[("a.png", image_bytes())])
    root = tmp_path / "manual_datasets" / dataset
    media = root / row["media"][0]["path"]
    os.link(media, tmp_path / "other.png")
    with pytest.raises(ValueError, match="invalid_manual_dataset_storage"):
        app.read_media(dataset, row["media"][0]["id"])
    (tmp_path / "other.png").unlink()
    sample = root / "samples" / (row["id"] + ".json")
    os.link(sample, tmp_path / "other.json")
    with pytest.raises(ValueError, match="invalid_manual_dataset_storage"):
        app.list_samples(dataset)


def test_symlinked_dataset_root_and_lock_rejected(tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    output = tmp_path / "output"
    output.mkdir()
    try:
        (output / "manual_datasets").symlink_to(outside, target_is_directory=True)
    except OSError:
        pytest.skip("Symlinks require Windows developer mode or an enabled privilege")
    app = manual_dataset_application(output)
    with pytest.raises(ValueError, match="invalid_manual_dataset_storage"):
        app.create_dataset("No traversal")
    assert list(outside.iterdir()) == []


def test_hardlinked_lock_does_not_truncate_the_linked_target(tmp_path):
    app, dataset = saved(tmp_path)
    target = tmp_path / "keep.txt"
    target.write_bytes(b"must remain intact")
    lock = tmp_path / "manual_datasets" / dataset / ".lock"
    lock.unlink(missing_ok=True)
    os.link(target, lock)
    with pytest.raises(ValueError, match="invalid_manual_dataset_storage"):
        app.append_sample(dataset, question="Q", answer="A")
    assert target.read_bytes() == b"must remain intact"


def test_export_budget_does_not_return_partial_archive(tmp_path, monkeypatch):
    app, dataset = saved(tmp_path)
    app.append_sample(dataset, question="Q", answer="A", attachments=[("a.png", image_bytes())])
    monkeypatch.setattr(manual_dataset_file, "EXPORT_LIMIT", 100)
    with pytest.raises(ValueError, match="manual_dataset_export_too_large"):
        app.export_dataset(dataset)
