import hashlib
import tracemalloc
from pathlib import Path
from unittest.mock import patch

import pytest

from lib.infrastructure.source_snapshot import snapshot_source


def test_large_snapshot_is_exact_and_memory_bounded(tmp_path):
    source, destination = tmp_path / "source.jsonl", tmp_path / "snapshot.jsonl"
    block = b'{"text":"source"}\n' * 65536
    digest = hashlib.sha256()
    with source.open("wb") as handle:
        for _ in range(12):
            handle.write(block)
            digest.update(block)
    tracemalloc.start()
    try:
        result = snapshot_source(source, destination, source.stat().st_size)
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    assert result == {"sha256": digest.hexdigest(), "bytes": source.stat().st_size}
    with destination.open("rb") as handle:
        assert hashlib.file_digest(handle, "sha256").hexdigest() == digest.hexdigest()
    assert peak < 3 * 1024 * 1024
    assert not (tmp_path / ".snapshot.jsonl.pending").exists()


def test_oversize_snapshot_leaves_existing_destination_intact(tmp_path):
    source, destination = tmp_path / "source.txt", tmp_path / "snapshot.txt"
    source.write_bytes(b"too large")
    destination.write_bytes(b"previous")
    with pytest.raises(ValueError):
        snapshot_source(source, destination, 3)
    assert destination.read_bytes() == b"previous"
    assert not (tmp_path / ".snapshot.txt.pending").exists()


def test_preexisting_pending_file_is_never_removed(tmp_path):
    source, destination = tmp_path / "source.txt", tmp_path / "snapshot.txt"
    source.write_bytes(b"content")
    pending = tmp_path / ".snapshot.txt.pending"
    pending.write_bytes(b"another copy")
    with pytest.raises(FileExistsError):
        snapshot_source(source, destination, 100)
    assert pending.read_bytes() == b"another copy"
    assert not destination.exists()


def test_workflow_creation_never_reads_entire_source(tmp_path):
    from lib.infrastructure.training_workflow import create_run, read_json, run_path
    source = tmp_path / "source.txt"
    source.write_text("Source content", encoding="utf-8")
    output = tmp_path / "out"
    source_path, output_path = source.resolve(), output.resolve()
    original_read_bytes = Path.read_bytes
    configuration_reads = []

    def checked_read_bytes(path):
        resolved = path.resolve()
        # Large source bodies and their copied inputs must be streamed. The
        # workflow also reads its small, fixed preference configuration; that
        # read is unrelated to source snapshot memory and must remain valid.
        if resolved == source_path or resolved.is_relative_to(output_path):
            raise AssertionError("unbounded source read")
        configuration_reads.append(resolved)
        return original_read_bytes(path)

    with patch.object(Path, "read_bytes", autospec=True, side_effect=checked_read_bytes):
        run_id = create_run(output, sources=[source], targets=["cpt"])
    assert configuration_reads == [Path(__file__).resolve().parents[1] / "configs" / "preferences.yaml"]
    run = run_path(output, run_id)
    snapshot = read_json(run / "recipe.json")["sources"][0]
    assert snapshot["bytes"] == source.stat().st_size
    assert snapshot["sha256"] == hashlib.sha256(b"Source content").hexdigest()
    assert (run / "inputs" / snapshot["file"]).read_bytes() == b"Source content"
