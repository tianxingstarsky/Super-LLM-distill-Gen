"""Disk-backed CPT indexes retain the in-memory matching contract at scale."""
from __future__ import annotations

import hashlib
import json
import tracemalloc

import pytest

from lib.domain.corpus_quality import CorpusClusterSizes, CorpusNearDuplicateIndex
from lib.infrastructure.corpus_reference import (exact_release_match, released_corpus_index,
                                                 snapshot_released_corpus)
from lib.infrastructure.training_workflow import (Cancelled, Workflow, _write_quality_report,
                                                  create_run, read_json)
from lib.infrastructure.workflow_rows import WorkflowRows


def _body() -> str:
    return "".join(f"第{i}节说明设备{i}号的维护顺序、检查记录与正常阈值为{i + 30}。" for i in range(26))


def test_disk_index_matches_memory_for_exact_near_numeric_and_structure(tmp_path):
    memory = CorpusNearDuplicateIndex()
    disk = CorpusNearDuplicateIndex(tmp_path / "index.sqlite3")
    base = _body()
    rows = [
        (base, {"id": "first", "source_name": "first"}),
        (base.replace("设备17号", "设备17甲号"), {"id": "near", "source_name": "near"}),
        (base.replace("阈值为47", "阈值为48"), {"id": "numeric", "source_name": "numeric"}),
        ("def compute_total(items):\n" + "\n".join(f"    value_{i} = items[{i}]" for i in range(30)),
         {"id": "code", "source_name": "code"}),
        ("短文本：设备断电后检查线路。", {"id": "short", "source_name": "short"}),
    ]
    try:
        for text, reference in rows:
            assert disk.check_and_add(text, reference) == memory.check_and_add(text, reference)
        queries = [base, base.replace("设备17号", "设备17乙号"),
                   base.replace("阈值为47", "阈值为48"),
                   base.replace("阈值为47", "阈值为49"),
                   "短文本：设备断电后检查线路。", "短文本：设备断电后检查电路。",
                   rows[3][0].replace("compute_total", "compute_amount")]
        for query in queries:
            assert disk.match(query) == memory.match(query)
            assert disk.exact_reference(query) == memory.exact_reference(query)
        # All verified release exemplars are indexed, including near variants.
        later = base.replace("设备17号", "设备17丙号")
        memory.add_reference(later, {"id": "later"})
        disk.add_reference(later, {"id": "later"})
        query = base.replace("设备17号", "设备17丁号")
        assert disk.match(query) == memory.match(query)
        assert disk.match(query)["duplicate_of"] == "first"
        assert disk._entries == [] and disk._exact == {} and not disk._buckets
    finally:
        disk.close()
    with pytest.raises(RuntimeError, match="corpus_index_closed"):
        disk.match(base)


def _release(output, run_number: int, texts: list[str]) -> None:
    run_id = f"{run_number:032x}"
    folder = output / "workflows" / run_id / "releases" / "cpt-v0001"
    folder.mkdir(parents=True)
    data = "".join(json.dumps({"text": text}, ensure_ascii=False) + "\n" for text in texts)
    corpus = data.encode("utf-8")
    (folder / "cpt.jsonl").write_bytes(corpus)
    manifest = {"status": "human_reviewed", "target": "cpt", "run_id": run_id,
                "version": 1, "counts": {"approved": len(texts)},
                "sha256": {"cpt.jsonl": hashlib.sha256(corpus).hexdigest()}}
    (folder / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")


def test_every_pinned_release_is_indexed_with_bounded_python_state(tmp_path):
    output = tmp_path / "output"
    base = _body()
    for run_number in range(1, 65):
        texts = [f"发布 {run_number} 中的样本 {line}。" for line in range(1, 21)]
        if run_number in {1, 64}:
            texts[0] = "两个发布版本共有的短记录。"
        # These documents share shingles, so the final matching exemplar
        # must survive traversal past many changed numeric facts.
        texts[10] = (base if run_number == 37 else
                     base.replace("阈值为47", f"阈值为{run_number + 100}"))
        _release(output, run_number, texts)
    pinned = snapshot_released_corpus(output)
    assert len(pinned) == 64
    exact, near, total = released_corpus_index(
        output, pinned, sqlite_path=tmp_path / "released.sqlite3")
    try:
        assert total == 64 * 20
        assert exact is near
        for run_number in range(1, 65):
            match = exact_release_match(exact, f"发布 {run_number} 中的样本 20。")
            assert match["run_id"] == f"{run_number:032x}"
            assert match["line"] == 20
        assert exact_release_match(exact, "两个发布版本共有的短记录。") == {
            "run_id": f"{1:032x}", "version": 1, "line": 1,
            "corpus_sha256": pinned[0]["corpus_sha256"]}
        variant = base.replace("设备17号", "设备17甲号")
        match = near.match(variant)
        assert match["reason"] == "near_duplicate_corpus"
        assert match["reference_release"]["run_id"] == f"{37:032x}"
        assert match["reference_release"]["line"] == 11
        assert near._entries == [] and near._exact == {} and not near._buckets
    finally:
        near.close()


def test_disk_cluster_counts_add_each_prior_exemplar_once(tmp_path):
    clusters = CorpusClusterSizes(tmp_path / "clusters.sqlite3")
    rows = [{"id": "a", "status": "eligible"},
            {"id": "b", "status": "duplicate", "duplicate_of": "a"},
            {"id": "c", "status": "duplicate", "duplicate_of": "published",
             "duplicate_scope": "prior_cpt_release"},
            {"id": "d", "status": "duplicate", "duplicate_of": "published",
             "duplicate_scope": "prior_cpt_release"},
            {"id": "e", "status": "quarantined"}]
    try:
        for row in rows:
            clusters.add(row)
        assert [clusters.annotate(row).get("duplicate_cluster_size") for row in rows] == [2, 2, 3, 3, None]
    finally:
        clusters.close()


def test_package_cleans_disk_indexes_after_cancellation(tmp_path, monkeypatch):
    source = tmp_path / "source.txt"
    source.write_text("设备维护之前断电。", encoding="utf-8")
    output = tmp_path / "output"
    run_id = create_run(output, sources=[source], targets=["cpt"])
    run = Workflow(output, run_id, tmp_path)
    (run.path / "input_records.json").write_text("[]", encoding="utf-8")
    def cancel():
        raise Cancelled()
    monkeypatch.setattr(run, "check_cancel", cancel)
    with pytest.raises(Cancelled):
        run.package({"cpt": []})
    stage = run.path / "stage-results"
    assert not list(stage.glob("package-*.sqlite3"))


def test_streamed_quality_report_preserves_json_content_and_format(tmp_path):
    input_path = tmp_path / "input_records.json"
    input_path.write_text(json.dumps([
        {"id": "ok", "source_id": "one", "status": "ready"},
        {"id": "bad", "source_id": "two", "status": "quarantined",
         "source_name": "资料.txt", "reason": "invalid_record"},
        {"id": "bad2", "source_id": "two", "status": "quarantined",
         "source_name": "资料.txt", "location": 4, "reason": "empty_record"},
    ], ensure_ascii=False), encoding="utf-8")
    report = {"policy": "sample", "input_issues": None,
              "targets": {"cpt": {"eligible": 1}}, "limitations": ["说明"]}
    path = tmp_path / "quality.json"
    _write_quality_report(path, report, input_path)
    expected = dict(report, input_issues=[
        {"id": "bad", "source_id": "two", "source_name": "资料.txt",
         "location": None, "source_location": None, "reason": "invalid_record"},
        {"id": "bad2", "source_id": "two", "source_name": "资料.txt",
         "location": 4, "source_location": None, "reason": "empty_record"}])
    assert path.read_text(encoding="utf-8") == json.dumps(expected, ensure_ascii=False, indent=2)


def test_cpt_package_streams_many_quarantined_input_issues(tmp_path):
    source = tmp_path / "source.txt"
    source.write_text("设备维护之前断电。", encoding="utf-8")
    output = tmp_path / "output"
    run_id = create_run(output, sources=[source], targets=["cpt"])
    run = Workflow(output, run_id, tmp_path)
    input_path = run.path / "input_records.json"
    with input_path.open("w", encoding="utf-8") as handle:
        handle.write("[")
        for index in range(30_000):
            if index:
                handle.write(",")
            handle.write(json.dumps({"id": str(index), "source_id": "source",
                                     "status": "quarantined", "reason": "invalid_record"}))
        handle.write("]")
    tracemalloc.start()
    try:
        run.package({"cpt": []})
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    assert peak < 8 * 1024 * 1024
    assert len(read_json(run.path / "artifacts" / "quality.json")["input_issues"]) == 30_000
    assert not list((run.path / "stage-results").glob("package-*.sqlite3"))


def test_cpt_package_keeps_twenty_thousand_candidates_off_heap(tmp_path):
    source = tmp_path / "source.txt"
    source.write_text("设备维护之前断电。", encoding="utf-8")
    output = tmp_path / "output"
    run_id = create_run(output, sources=[source], targets=["cpt"])
    run = Workflow(output, run_id, tmp_path)
    (run.path / "input_records.json").write_text("[]", encoding="utf-8")
    stage = run.path / "stage-results"
    stage.mkdir(exist_ok=True)
    path = stage / "test-candidates.jsonl"
    with path.open("w", encoding="utf-8") as handle:
        for index in range(20_000):
            handle.write(json.dumps({"id": str(index), "source_id": "source",
                                     "source_name": "source.txt", "status": "eligible",
                                     "text": f"维护流程第 {index} 号：先断电，再记录检查结果。"},
                                    ensure_ascii=False) + "\n")
    tracemalloc.start()
    try:
        run.package({"cpt": WorkflowRows(path, 20_000)})
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    assert peak < 8 * 1024 * 1024
    assert read_json(run.path / "artifacts" / "quality.json")["targets"]["cpt"]["eligible"] == 20_000
    assert not list(stage.glob("package-*.sqlite3"))


def test_changed_pinned_release_fails_and_removes_temporary_index(tmp_path):
    output = tmp_path / "output"
    _release(output, 1, ["已审核的旧语料。"])
    source = tmp_path / "source.txt"
    source.write_text("新的独立语料。", encoding="utf-8")
    run_id = create_run(output, sources=[source], targets=["cpt"])
    run = Workflow(output, run_id, tmp_path)
    old_release = output / "workflows" / f"{1:032x}" / "releases" / "cpt-v0001" / "cpt.jsonl"
    old_release.write_text('{"text":"已被修改的旧语料。"}\n', encoding="utf-8")
    state = run.execute()
    assert state["status"] == "failed"
    assert state["error"] == "cpt_release_integrity_error"
    assert not list((run.path / "stage-results").glob("package-*.sqlite3"))
