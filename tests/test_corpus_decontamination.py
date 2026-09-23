"""Evaluation overlap signals must be pinned, explainable and conservative."""
from __future__ import annotations

import json

from lib.domain.corpus_decontamination import EvaluationOverlapIndex, EvaluationReference
from lib.infrastructure.evaluation_reference import load_evaluation_index, snapshot_evaluation_sources


def _reference(text: str, identifier: str = "eval-1") -> EvaluationReference:
    return EvaluationReference(identifier, "heldout.jsonl", 1, text)


def test_exact_embedded_evaluation_text_is_flagged_without_exposing_it():
    secret_eval_text = "设备断电后检查线路，再记录温度与阀门状态，并由另一人复核维护结果。"
    index = EvaluationOverlapIndex([_reference(secret_eval_text)])

    match = index.inspect("这是培训材料。\n" + secret_eval_text + "\n以下是补充说明。")
    assert match == {"reason": "evaluation_verbatim_overlap", "reference_id": "eval-1",
                     "reference_source": "heldout.jsonl", "reference_record": 1,
                     "similarity": 1.0, "match_type": "embedded"}
    assert secret_eval_text not in json.dumps(match, ensure_ascii=False)


def test_short_evaluation_question_only_matches_as_whole_record():
    index = EvaluationOverlapIndex([_reference("2 + 2 = ?")])
    assert index.short_for_embedded == 1
    assert index.inspect("2 + 2 = ?")["match_type"] == "whole_record"
    assert index.inspect("练习题：2 + 2 = ?，请解释。") is None


def test_near_overlap_checks_numeric_facts_and_code_structure():
    base = "".join(f"第{i}节描述设备{i}号的维护顺序，正常阈值为{i + 30}。" for i in range(30))
    index = EvaluationOverlapIndex([_reference(base)])
    nearby = base.replace("维护顺序", "保养顺序", 1)
    changed_fact = base.replace("阈值为47", "阈值为48")
    assert index.inspect(nearby)["reason"] == "evaluation_near_overlap"
    assert index.inspect(changed_fact) is None

    code = "def compute_total(items):\n" + "\n".join(
        f"    value_{i} = items[{i}]" for i in range(30)) + "\n    return value_0"
    code_index = EvaluationOverlapIndex([_reference(code)])
    assert code_index.inspect(code.replace("compute_total", "compute_amount")) is None


def test_duplicate_ids_and_oversized_records_fail_closed():
    record = _reference("独立的评测集条目")
    try:
        EvaluationOverlapIndex([record, record])
    except ValueError as error:
        assert str(error) == "invalid_evaluation_reference"
    else:
        raise AssertionError("duplicate evaluation id was accepted")

    try:
        EvaluationOverlapIndex([_reference("甲" * 20_001)])
    except ValueError as error:
        assert str(error) == "evaluation_record_too_long"
    else:
        raise AssertionError("oversized evaluation text was accepted")


def test_evaluation_snapshots_are_pinned_and_never_read_from_original_again(tmp_path):
    original = tmp_path / "heldout.jsonl"
    text = "设备应先断电，再检查主电源线、传感器与阀门状态，最后记录复核人员和检查时间。"
    original.write_text(json.dumps({"text": text}, ensure_ascii=False) + "\n", encoding="utf-8")
    run = tmp_path / "run"
    run.mkdir()
    manifests = snapshot_evaluation_sources(run, [original])
    original.write_text('{"text":"原文件后来改变"}\n', encoding="utf-8")

    index = load_evaluation_index(run, manifests)
    assert index is not None and index.reference_rows == 1
    assert index.inspect("前言：" + text)["reason"] == "evaluation_verbatim_overlap"
    assert index.inspect("原文件后来改变") is None

    (run / "evaluations" / manifests[0]["file"]).write_text('{"text":"被篡改"}\n', encoding="utf-8")
    try:
        load_evaluation_index(run, manifests)
    except ValueError as error:
        assert str(error) == "evaluation_reference_integrity_error"
    else:
        raise AssertionError("tampered evaluation snapshot was accepted")


def test_evaluation_loader_requires_explicit_text_records(tmp_path):
    source = tmp_path / "ambiguous.jsonl"
    source.write_text('{"question":"2 + 2?","answer":"4"}\n', encoding="utf-8")
    run = tmp_path / "run"
    run.mkdir()
    try:
        snapshot_evaluation_sources(run, [source])
    except ValueError as error:
        assert str(error) == "invalid_evaluation_record"
    else:
        raise AssertionError("ambiguous benchmark record was accepted")
    assert not (run / "evaluations").exists()
