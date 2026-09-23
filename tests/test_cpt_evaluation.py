"""CPT candidates can be checked against explicitly pinned evaluation text."""
from __future__ import annotations

import json

from lib.infrastructure.training_workflow import Workflow, create_run, read_json, run_path, verify_artifacts


def _source(path, name: str, text: str):
    item = path / name
    item.write_text(text, encoding="utf-8")
    return item


def _evaluation(path, text: str):
    item = path / "heldout.jsonl"
    item.write_text(json.dumps({"text": text}, ensure_ascii=False) + "\n", encoding="utf-8")
    return item


def test_cpt_evaluation_overlap_is_quarantined_without_leaking_reference_text(tmp_path):
    heldout = "设备断电后检查线路，再记录温度与阀门状态，并由另一人复核维护结果。"
    leaked = _source(tmp_path, "leaked.txt", "培训材料：\n" + heldout + "\n请按流程执行。")
    independent = _source(tmp_path, "independent.txt", "独立材料：定期维护传感器与液压泵，并核对保养记录。")
    evaluation = _evaluation(tmp_path, heldout)
    output = tmp_path / "out"

    run_id = create_run(output, sources=[leaked, independent], evaluation_sources=[evaluation], targets=["cpt"])
    evaluation.write_text('{"text":"原参照后来改变"}\n', encoding="utf-8")
    state = Workflow(output, run_id, tmp_path).execute()
    run = run_path(output, run_id)

    assert state["status"] == "completed"
    assert verify_artifacts(run)["counts"] == {"cpt": 1}
    rows = read_json(run / "artifacts" / "cpt.records.json")
    assert [row["status"] for row in rows] == ["quarantined", "eligible"]
    assert rows[0]["reason"] == "evaluation_verbatim_overlap"
    assert rows[0]["evaluation_reference"]["reference_source"] == "heldout.jsonl"
    assert rows[0]["evaluation_reference"]["reference_record"] == 1
    assert "text" not in rows[0]
    assert len(rows[0]["quarantined_text_sha256"]) == 64
    report = read_json(run / "artifacts" / "quality.json")["targets"]["cpt"]
    assert report["decontamination"]["status"] == "checked"
    assert report["decontamination"]["reference_rows"] == 1
    assert report["decontamination"]["verbatim_overlaps"] == 1
    per_source = {item["source_name"]: item for item in report["source_breakdown"]}
    assert per_source["leaked.txt"]["exported"] == 0
    assert per_source["leaked.txt"]["quarantined"] == 1
    assert per_source["independent.txt"]["exported"] == 1
    assert heldout not in (run / "artifacts" / "quality.json").read_text(encoding="utf-8")
    assert not any(heldout in item.read_text(encoding="utf-8")
                   for item in (run / "artifacts").iterdir() if item.is_file())


def test_cpt_without_evaluation_set_says_not_configured(tmp_path):
    source = _source(tmp_path, "manual.txt", "独立手册：设备停机后记录温度，再检查液压泵的状态。")
    output = tmp_path / "out"
    run_id = create_run(output, sources=[source], targets=["cpt"])
    assert Workflow(output, run_id, tmp_path).execute()["status"] == "completed"
    report = read_json(run_path(output, run_id) / "artifacts" / "quality.json")["targets"]["cpt"]
    assert report["decontamination"]["status"] == "not_configured"


def test_tampered_evaluation_snapshot_fails_closed_before_packaging(tmp_path):
    source = _source(tmp_path, "manual.txt", "独立手册：设备停机后记录温度，再检查液压泵的状态。")
    evaluation = _evaluation(tmp_path, "评测题：设备停机后如何核对液压系统的压力记录？")
    output = tmp_path / "out"
    run_id = create_run(output, sources=[source], evaluation_sources=[evaluation], targets=["cpt"])
    run = run_path(output, run_id)
    manifest = read_json(run / "recipe.json")["evaluation_references"][0]
    (run / "evaluations" / manifest["file"]).write_text('{"text":"被改动"}\n', encoding="utf-8")

    state = Workflow(output, run_id, tmp_path).execute()
    assert state["status"] == "failed"
    assert state["error"] == "evaluation_reference_integrity_error"
    assert not (run / "artifacts" / "manifest.json").exists()
