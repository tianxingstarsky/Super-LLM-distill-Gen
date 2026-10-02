"""Legacy distill keeps large JSONL inputs out of memory and publishes complete results."""
from __future__ import annotations

from argparse import Namespace
import json
from pathlib import Path
import shutil
import tracemalloc

import pytest

from lib import cli


def _samples(path: Path, count: int, *, padding: int = 0, errors: list[int] | None = None) -> None:
    with path.open("w", encoding="utf-8") as output:
        for index in range(count):
            output.write(json.dumps({
                "id": f"row-{index}", "error_tool_steps": errors[index] if errors else 0,
                "messages": [
                    {"role": "user", "content": f"Question {index}" + "x" * padding},
                    {"role": "assistant", "content": f"Answer {index}"},
                ],
            }) + "\n")


def _args(raw: Path, *, llm_check: int = 0) -> Namespace:
    return Namespace(rollout_dir=str(raw), ws=None, llm_check=llm_check, backend=None, model=None)


def _raw_folder(tmp_path: Path) -> Path:
    raw = tmp_path / "raw"
    raw.mkdir()
    (raw / "model-io-sess_empty.jsonl").write_text("", encoding="utf-8")
    return raw


def test_distill_streams_large_samples_without_loading_jsonl(tmp_path, monkeypatch):
    sample_path = tmp_path / "rollout_samples.jsonl"
    _samples(sample_path, 12_000, padding=900)
    monkeypatch.setattr(cli, "OUT_DIR", tmp_path)
    monkeypatch.setattr("lib.monitor.trace_run", lambda *args, **kwargs: None)
    original_read_text = Path.read_text

    def guarded_read_text(path, *args, **kwargs):
        if path == sample_path:
            pytest.fail("distill loaded the whole samples JSONL")
        return original_read_text(path, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", guarded_read_text)
    tracemalloc.start()
    try:
        assert cli.cmd_distill(_args(_raw_folder(tmp_path))) == 0
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()

    report = json.loads((tmp_path / "distill_report.json").read_text(encoding="utf-8"))
    assert report["counts"] == {"clean/answer": 12_000}
    assert report["n_samples"] == 12_000
    assert report["n_dpo_pairs"] == 0
    assert peak < sample_path.stat().st_size


def test_distill_keeps_candidate_order_with_bounded_selection(tmp_path, monkeypatch):
    _samples(tmp_path / "rollout_samples.jsonl", 5, errors=[0, 2, 2, 1, 0])
    old_pairs = tmp_path / "dpo_pairs.jsonl"
    old_pairs.write_text("previous pairs\n", encoding="utf-8")
    monkeypatch.setattr(cli, "OUT_DIR", tmp_path)
    monkeypatch.setattr("lib.monitor.trace_run", lambda *args, **kwargs: None)

    class Gate:
        def require(self, gate):
            assert gate == "G0"

    class Client:
        usage = {"calls": 3}

        def chat(self, messages, **kwargs):
            return "score"

    monkeypatch.setattr(cli, "_gates", lambda: Gate())
    monkeypatch.setattr(cli, "_client", lambda *args, **kwargs: (Client(), "fake"))
    assert cli.cmd_distill(_args(_raw_folder(tmp_path), llm_check=3)) == 0
    report = json.loads((tmp_path / "distill_report.json").read_text(encoding="utf-8"))
    assert [item["id"] for item in report["llm_scores"]] == ["row-1", "row-2", "row-3"]
    assert old_pairs.read_text(encoding="utf-8") == "previous pairs\n"


def test_distill_validates_late_sample_before_replacing_outputs(tmp_path, monkeypatch):
    sample_path = tmp_path / "rollout_samples.jsonl"
    _samples(sample_path, 2)
    with sample_path.open("a", encoding="utf-8") as output:
        output.write("{broken\n")
    report_path = tmp_path / "distill_report.json"
    report_path.write_text("old report", encoding="utf-8")
    monkeypatch.setattr(cli, "OUT_DIR", tmp_path)
    with pytest.raises(json.JSONDecodeError):
        cli.cmd_distill(_args(_raw_folder(tmp_path)))
    assert report_path.read_text(encoding="utf-8") == "old report"


def test_distill_writes_real_dpo_pairs_in_source_order(tmp_path, monkeypatch):
    _samples(tmp_path / "rollout_samples.jsonl", 1)
    raw = _raw_folder(tmp_path)
    shutil.copyfile(Path(__file__).parent / "fixtures" / "rollout_sample.jsonl",
                    raw / "model-io-sess_valid.jsonl")
    monkeypatch.setattr(cli, "OUT_DIR", tmp_path)
    monkeypatch.setattr("lib.monitor.trace_run", lambda *args, **kwargs: None)
    assert cli.cmd_distill(_args(raw)) == 0
    report = json.loads((tmp_path / "distill_report.json").read_text(encoding="utf-8"))
    pairs = [json.loads(line) for line in (tmp_path / "dpo_pairs.jsonl").read_text(encoding="utf-8").splitlines()]
    assert report["n_dpo_pairs"] == len(pairs) == 1
    assert pairs[0]["rejected"][0]["toolCalls"][0]["name"] == "Bash"


def test_distill_discards_staged_pairs_on_late_source_error(tmp_path, monkeypatch):
    _samples(tmp_path / "rollout_samples.jsonl", 1)
    raw = _raw_folder(tmp_path)
    source = raw / "model-io-sess_valid.jsonl"
    shutil.copyfile(Path(__file__).parent / "fixtures" / "rollout_sample.jsonl", source)
    with source.open("a", encoding="utf-8") as output:
        output.write("{broken\n")
    report_path = tmp_path / "distill_report.json"
    pairs_path = tmp_path / "dpo_pairs.jsonl"
    report_path.write_text("old report", encoding="utf-8")
    pairs_path.write_text("old pairs", encoding="utf-8")
    monkeypatch.setattr(cli, "OUT_DIR", tmp_path)
    with pytest.raises(json.JSONDecodeError):
        cli.cmd_distill(_args(raw))
    assert report_path.read_text(encoding="utf-8") == "old report"
    assert pairs_path.read_text(encoding="utf-8") == "old pairs"
    assert list(tmp_path.glob(".dpo_pairs-*.jsonl")) == []
