"""The legacy CLI export avoids holding whole JSONL inputs and outputs in memory."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import tracemalloc

import pytest

from lib.domain.release_quality import sample_hash


def _sample(number: int, payload: str) -> dict:
    return {"id": f"row-{number}", "messages": [
        {"role": "user", "content": f"Question {number}: {payload}"},
        {"role": "assistant", "content": f"Answer {number}"},
    ]}


def test_large_cli_export_is_replayable_and_bounded(tmp_path, monkeypatch):
    from lib import cli, monitor
    from lib.infrastructure.release_file_driver import FilesystemReleaseDriver

    source = tmp_path / "samples.jsonl"
    payload = "x" * 1536
    count = 12000
    with source.open("w", encoding="utf-8") as handle:
        for number in range(count):
            handle.write(json.dumps(_sample(number, payload)) + "\n")

    release = tmp_path / "releases" / "large"
    monkeypatch.setattr(
        FilesystemReleaseDriver, "read_samples",
        lambda *_: pytest.fail("CLI export must not load all source rows"),
    )
    monkeypatch.setattr(FilesystemReleaseDriver, "review_decisions", lambda *_: [])
    monkeypatch.setattr(monitor, "trace_run", lambda *_args, **_kwargs: None)

    original_read_bytes = Path.read_bytes

    def guarded_read_bytes(path):
        if path.parent == release and path.suffix == ".jsonl":
            pytest.fail("release hashing must not load a whole JSONL output")
        return original_read_bytes(path)

    monkeypatch.setattr(Path, "read_bytes", guarded_read_bytes)
    args = cli.build_parser().parse_args([
        "export", "--input", str(source), "--out", str(tmp_path / "releases"),
        "--format", "all", "--tag", "large",
    ])

    tracemalloc.start()
    try:
        assert cli.cmd_export(args) == 0
        peak = tracemalloc.get_traced_memory()[1]
    finally:
        tracemalloc.stop()

    # A full decoded sample list would exceed the source's already sizable bytes.
    assert peak < source.stat().st_size
    manifest = json.loads((release / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["status"] == "complete"
    assert len(manifest["sample_hashes"]) == count
    assert manifest["sample_hashes"][0] == sample_hash(_sample(0, payload))
    assert manifest["sample_hashes"][-1] == sample_hash(_sample(count - 1, payload))
    assert manifest["counts"]["llamafactory"] == count
    assert manifest["counts"]["chat"] == count
    for name in ("sft_llamafactory.jsonl", "sft_chat.jsonl"):
        output = release / name
        digest = hashlib.sha256()
        with output.open("rb") as handle:
            for block in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(block)
        assert manifest["sha256"][name] == digest.hexdigest()
        with output.open(encoding="utf-8") as handle:
            assert sum(1 for _ in handle) == count
