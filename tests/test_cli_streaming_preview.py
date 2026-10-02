"""Legacy commands inspect large JSONL sources without retaining every row."""
from __future__ import annotations

from argparse import Namespace
import json
from pathlib import Path
import tracemalloc

import pytest

from lib import cli
from lib.infrastructure.release_file_driver import FilesystemReleaseDriver
from lib.render import render_preview_html  # noqa: F401 - exclude renderer import from peak usage


def _source(path: Path, count: int, *, padding: int = 0) -> None:
    with path.open("w", encoding="utf-8") as handle:
        for number in range(count):
            handle.write(json.dumps({
                "id": f"row-{number}", "model": "fake", "finish_reason": "stop",
                "error_tool_steps": 0, "messages": [
                    {"role": "user", "content": f"Question {number}" + "x" * padding},
                    {"role": "assistant", "content": f"Answer {number}"},
                ],
            }) + "\n")


def test_preview_counts_large_source_but_keeps_only_requested_rows(tmp_path, monkeypatch, capsys):
    source = tmp_path / "samples.jsonl"
    _source(source, 12_000, padding=900)
    monkeypatch.setattr(cli, "OUT_DIR", tmp_path)
    original_read_text = Path.read_text

    def guarded_read_text(path, *args, **kwargs):
        if path == source:
            pytest.fail("preview loaded the full JSONL file")
        return original_read_text(path, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", guarded_read_text)
    tracemalloc.start()
    try:
        assert cli.cmd_preview(Namespace(file=str(source), n=2, html=True)) == 0
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    html = (tmp_path / "preview.html").read_text(encoding="utf-8")
    assert "样本数: 12000" in html
    assert "Question 0" in html and "Question 1" in html
    assert "Question 2" not in html
    assert "（2 条）" in capsys.readouterr().out
    assert peak < source.stat().st_size


def test_preview_validates_late_rows_before_writing_html(tmp_path, monkeypatch):
    source = tmp_path / "broken.jsonl"
    _source(source, 2)
    with source.open("a", encoding="utf-8") as handle:
        handle.write("[]\n")
    monkeypatch.setattr(cli, "OUT_DIR", tmp_path)
    with pytest.raises(ValueError, match=r"broken\.jsonl:3: expected an object"):
        cli.cmd_preview(Namespace(file=str(source), n=1, html=True))
    assert not (tmp_path / "preview.html").exists()


def test_quality_report_streams_from_source(tmp_path, monkeypatch, capsys):
    source = tmp_path / "samples.jsonl"
    _source(source, 300)
    monkeypatch.setattr(FilesystemReleaseDriver, "review_decisions", lambda self, dataset: [])
    monkeypatch.setattr(FilesystemReleaseDriver, "read_samples",
                        lambda self, path: pytest.fail("quality report materialized every sample"))
    assert cli.cmd_quality_report(Namespace(input=str(source))) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["samples"] == 300
    assert report["length_chars"] == {"min": 19, "max": 23, "mean": 22}
