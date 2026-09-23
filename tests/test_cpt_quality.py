"""CPT quality evidence, conservative deduplication, and source retention."""
from __future__ import annotations

import hashlib
import json

from lib.application.corpus_review_service import CorpusReviewApplication
from lib.domain.corpus_quality import CorpusNearDuplicateIndex, inspect_corpus
from lib.infrastructure.corpus_review_driver import FilesystemCorpusReviewDriver
from lib.infrastructure.training_workflow import Workflow, create_run, read_json, run_path, verify_artifacts


def _run(tmp_path, texts: list[str], *, chunk_chars: int = 2000):
    sources = []
    for index, content in enumerate(texts):
        source = tmp_path / f"source-{index}.txt"
        source.write_text(content, encoding="utf-8")
        sources.append(source)
    output = tmp_path / "out"
    run_id = create_run(output, sources=sources, targets=["cpt"], chunk_chars=chunk_chars)
    state = Workflow(output, run_id, tmp_path).execute()
    path = run_path(output, run_id)
    return state, path


def test_short_distinct_chinese_and_numeric_documents_are_kept(tmp_path):
    state, path = _run(tmp_path, ["短手册：设备断电后检查线路。", "阈值 1234\n\n备用阈值 5678"])
    assert state["status"] == "completed"
    assert verify_artifacts(path)["counts"] == {"cpt": 2}
    rows = read_json(path / "artifacts" / "cpt.records.json")
    assert all(row["quality"]["keep"] for row in rows)
    assert all(row["retention_reason"] == "source_text_passed_deterministic_checks" for row in rows)
    assert [row["source_location"]["file"] for row in rows] == ["source-0.txt", "source-1.txt"]
    assert "1234" in (path / "artifacts" / "cpt.jsonl").read_text(encoding="utf-8")


def test_near_and_exact_duplicates_keep_one_source_with_explainable_lineage(tmp_path):
    base = "".join(f"第{i}节说明设备{i}号的维护顺序、检查记录与正常阈值为{i + 30}。" for i in range(26))
    variant = base.replace("设备17号", "设备17甲号")
    state, path = _run(tmp_path, [base, variant, base])
    assert state["status"] == "completed"
    manifest = verify_artifacts(path)
    assert manifest["counts"] == {"cpt": 1}
    records = read_json(path / "artifacts" / "cpt.records.json")
    assert [row["status"] for row in records] == ["eligible", "duplicate", "duplicate"]
    assert records[1]["reason"] == "near_duplicate_corpus"
    assert records[1]["similarity"] >= 0.90
    assert records[2]["reason"] == "exact_duplicate_corpus"
    assert len({row["id"] for row in records}) == 3
    assert all(row["duplicate_cluster_size"] == 3 for row in records)
    assert records[1]["duplicate_of"] == records[0]["id"]
    assert records[2]["duplicate_of"] == records[0]["id"]
    retention = read_json(path / "artifacts" / "quality.json")["targets"]["cpt"]["retention"]
    assert retention == {"parsed_units": 3, "parsed_ready": 3, "selected": 3,
                         "quality_passed": 3, "exact_duplicates": 1, "near_duplicates": 1,
                         "released_exact_duplicates": 0, "released_near_duplicates": 0,
                         "exported": 1, "parse_rate": 1.0, "quality_rate": 1.0,
                         "dedup_rate": 0.3333, "overall_rate": 0.3333}


def _publish(output, run_id, *, revised_text=None):
    application = CorpusReviewApplication(FilesystemCorpusReviewDriver(output))
    for item in application.queue(run_id)["items"]:
        application.decide(run_id, item["sample_id"], decision="approved", reviewer="test-reviewer",
                           expected_hash=item["sample_id"], text=revised_text)
    application.release(run_id)


def test_pinned_published_cpt_release_filters_cross_run_exact_overlap(tmp_path):
    original = "设备断电后，应检查电源线。\n检查正常后，记录维护结果。"
    first, first_path = _run(tmp_path, [original])
    assert first["status"] == "completed"
    output = tmp_path / "out"

    # An automatically checked candidate is not a cross-run dedup reference.
    same = tmp_path / "same.txt"
    same.write_text(original, encoding="utf-8")
    before_release = create_run(output, sources=[same], targets=["cpt"])
    assert read_json(run_path(output, before_release) / "recipe.json")["cpt_reference_releases"] == []
    assert Workflow(output, before_release, tmp_path).execute()["quality"]["targets"]["cpt"]["eligible"] == 1

    _publish(output, first_path.name)
    new_text = tmp_path / "new.txt"
    new_text.write_text("新文档：检查阀门状态并记录批次号。", encoding="utf-8")
    same.write_text(original.replace("。\n", "。 "), encoding="utf-8")
    later = create_run(output, sources=[same, new_text], targets=["cpt"])
    later_path = run_path(output, later)
    pinned = read_json(later_path / "recipe.json")["cpt_reference_releases"]
    assert len(pinned) == 1 and pinned[0]["run_id"] == first_path.name
    state = Workflow(output, later, tmp_path).execute()
    assert state["status"] == "completed"
    assert verify_artifacts(later_path)["counts"] == {"cpt": 1}
    records = read_json(later_path / "artifacts" / "cpt.records.json")
    assert [row["status"] for row in records] == ["duplicate", "eligible"]
    assert records[0]["reason"] == "released_corpus_exact_duplicate"
    assert records[0]["duplicate_scope"] == "prior_cpt_release"
    assert records[0]["reference_release"]["run_id"] == first_path.name
    assert records[0]["reference_release"]["version"] == 1
    assert records[0]["reference_release"]["line"] == 1
    assert records[0]["duplicate_cluster_size"] == 2
    report = read_json(later_path / "artifacts" / "quality.json")["targets"]["cpt"]
    assert report["retention"]["released_exact_duplicates"] == 1
    assert report["filter_policy"]["reference_rows"] == 1


def test_pinned_release_filters_near_overlap_but_preserves_changed_numeric_fact(tmp_path):
    base = "".join(f"第{i}节记录设备{i}号检查流程，正常阈值为{i + 30}，维护结论已复核。" for i in range(26))
    first, first_path = _run(tmp_path, [base])
    assert first["status"] == "completed"
    _publish(tmp_path / "out", first_path.name)

    near = tmp_path / "near.txt"
    near.write_text(base.replace("设备17号", "设备17甲号"), encoding="utf-8")
    changed_fact = tmp_path / "changed-fact.txt"
    changed_fact.write_text(base.replace("阈值为47", "阈值为48"), encoding="utf-8")
    output = tmp_path / "out"
    later = create_run(output, sources=[near, changed_fact], targets=["cpt"])
    state = Workflow(output, later, tmp_path).execute()
    path = run_path(output, later)

    assert state["status"] == "completed"
    assert verify_artifacts(path)["counts"] == {"cpt": 1}
    records = read_json(path / "artifacts" / "cpt.records.json")
    assert [row["status"] for row in records] == ["duplicate", "eligible"]
    assert records[0]["reason"] == "released_corpus_near_duplicate"
    assert records[0]["duplicate_scope"] == "prior_cpt_release"
    assert records[0]["reference_release"]["run_id"] == first_path.name
    assert records[0]["reference_release"]["line"] == 1
    assert records[0]["similarity"] >= 0.90
    assert records[1].get("reason") is None
    report = read_json(path / "artifacts" / "quality.json")["targets"]["cpt"]
    assert report["retention"]["released_near_duplicates"] == 1
    assert "pinned same-workspace CPT releases exact/near" in report["filter_policy"]["scope"]


def test_cpt_release_reference_uses_approved_revision_and_creation_snapshot(tmp_path):
    first, first_path = _run(tmp_path, ["原始维护建议。"])
    assert first["status"] == "completed"
    output = tmp_path / "out"
    source = tmp_path / "later.txt"
    source.write_text("原始维护建议。", encoding="utf-8")
    created_early = create_run(output, sources=[source], targets=["cpt"])
    _publish(output, first_path.name, revised_text="人工修订后的维护建议。")

    # A release created after the recipe was pinned does not change the run.
    early_path = run_path(output, created_early)
    assert Workflow(output, created_early, tmp_path).execute()["status"] == "completed"
    assert verify_artifacts(early_path)["counts"]["cpt"] == 1

    source.write_text("人工修订后的维护建议。", encoding="utf-8")
    created_late = create_run(output, sources=[source], targets=["cpt"])
    late_path = run_path(output, created_late)
    state = Workflow(output, created_late, tmp_path).execute()
    assert state["status"] == "needs_attention"
    assert verify_artifacts(late_path)["counts"]["cpt"] == 0
    assert read_json(late_path / "artifacts" / "cpt.records.json")[0]["reason"] == "released_corpus_exact_duplicate"


def test_tampered_pinned_cpt_release_fails_closed_at_packaging(tmp_path):
    state, first_path = _run(tmp_path, ["设备断电后检查线路。"])
    assert state["status"] == "completed"
    output = tmp_path / "out"
    _publish(output, first_path.name)
    source = tmp_path / "new.txt"
    source.write_text("新的独立文档内容。", encoding="utf-8")
    run_id = create_run(output, sources=[source], targets=["cpt"])
    folder = first_path / "releases" / "cpt-v0001"
    release = folder / "cpt.jsonl"
    release.write_text('{"text":"被篡改的内容"}\n', encoding="utf-8")
    # Even a rewritten, internally consistent release manifest cannot alter
    # the reference set that was pinned when the new task was created.
    manifest_path = folder / "manifest.json"
    manifest = read_json(manifest_path)
    manifest["sha256"]["cpt.jsonl"] = hashlib.sha256(release.read_bytes()).hexdigest()
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")
    result = Workflow(output, run_id, tmp_path).execute()
    assert result["status"] == "failed"
    assert result["error"] == "cpt_release_integrity_error"
    assert not (run_path(output, run_id) / "artifacts" / "manifest.json").exists()


def test_changed_equation_is_not_collapsed_by_exact_deduplication():
    index = CorpusNearDuplicateIndex()
    first = "公式：2 + 2 = 4。"
    second = "公式：2 + 2 = 5。"
    assert index.check_and_add(first, {"id": "a"}) is None
    assert index.check_and_add(second, {"id": "b"}) is None
    assert index.check_and_add("代码符号 Foo 与 foo 有不同含义。", {"id": "code-a"}) is None
    assert index.check_and_add("代码符号 foo 与 foo 有不同含义。", {"id": "code-b"}) is None
    assert index.check_and_add("公式 x² 的含义。", {"id": "math-a"}) is None
    assert index.check_and_add("公式 x2 的含义。", {"id": "math-b"}) is None
    assert index.check_and_add(first, {"id": "c"})["duplicate_of"] == "a"


def test_near_matching_preserves_changed_numeric_facts():
    index = CorpusNearDuplicateIndex()
    base = "".join(f"设备{i}号在第{i}天完成检查，结论正常。" for i in range(35))
    changed = base.replace("设备17号", "设备18号", 1)
    assert index.check_and_add(base, {"id": "old"}) is None
    assert index.check_and_add(changed, {"id": "new"}) is None


def test_release_reference_near_matching_preserves_changed_code():
    index = CorpusNearDuplicateIndex()
    original = "def compute_total(items):\n" + "\n".join(
        f"    value_{i} = items[{i}]" for i in range(30)) + "\n    return value_0"
    changed = original.replace("compute_total", "compute_amount")
    index.add_reference(original, {"id": "published"})
    assert index.match(changed) is None


def test_pathological_repetition_is_quarantined_but_short_lists_are_signals_only(tmp_path):
    noisy = "重复无信息模板行。" * 60
    useful = "简短表格\n1  设备甲\n2  设备乙\n3  设备丙"
    assert inspect_corpus(useful)["keep"]
    state, path = _run(tmp_path, [noisy, useful])
    assert state["status"] == "needs_attention"
    records = read_json(path / "artifacts" / "cpt.records.json")
    assert records[0]["status"] == "quarantined"
    assert records[0]["reason"] == "low_information_repetition"
    assert records[0]["source_location"] == {"file": "source-0.txt", "record": "document", "chunk": 0}
    assert records[0]["quality"]["signals"]["shingle_uniqueness"] < 0.08
    assert records[1]["status"] == "eligible"
    retention = read_json(path / "artifacts" / "quality.json")["targets"]["cpt"]["retention"]
    assert retention["quality_passed"] == 1
    assert retention["quality_rate"] == 0.5
    assert retention["exported"] == 1


def test_very_long_single_paragraph_is_split_in_order_instead_of_discarded(tmp_path):
    paragraph = "".join(f"设备{i:04d}应记录温度{i % 75}度并检查线路连接。" for i in range(280))
    state, path = _run(tmp_path, [paragraph], chunk_chars=800)
    assert state["input_summary"]["quarantined"] == 0
    records = read_json(path / "artifacts" / "cpt.records.json")
    assert len(records) > 2
    assert all(row["source_location"]["chunk"] == index for index, row in enumerate(records))
    exported = [json.loads(line)["text"] for line in (path / "artifacts" / "cpt.jsonl").read_text(encoding="utf-8").splitlines()]
    assert "".join(exported) == paragraph
    assert all(len(chunk) <= 800 for chunk in exported)
