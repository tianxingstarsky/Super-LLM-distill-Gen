"""All-or-nothing optional TRL artifact preparation."""
from __future__ import annotations

from copy import deepcopy

import pytest

from lib.application.trainer_export_service import prepare_trl_export


def _sft_row(record_id: str, answer: str = "4") -> dict:
    return {"id": record_id, "status": "eligible", "messages": [
        {"role": "user", "content": "2+2=?"},
        {"role": "assistant", "content": answer},
    ]}


def _preference_row(record_id: str) -> dict:
    return {"id": record_id, "status": "eligible",
            "prompt": [{"role": "user", "content": "2+2=?"}],
            "chosen": [{"role": "assistant", "content": "4"}],
            "rejected": [{"role": "assistant", "content": "5"}]}


@pytest.mark.parametrize("target", ["sft", "agent"])
def test_complete_sft_export_returns_only_trainer_fields(target):
    records = [_sft_row("a"), _sft_row("b", "四")]
    original = deepcopy(records)

    result = prepare_trl_export(target, records)

    assert result["ready"] is True and result["format"] == "trl_sft"
    assert result["summary"] == {"total": 2, "compatible": 2, "incompatible": 0, "reasons": {}}
    assert result["failures"] == []
    assert result["rows"] == [
        {"messages": row["messages"]} for row in records
    ]
    assert records == original


def test_multiturn_trainer_export_keeps_every_turn_in_order():
    messages = [
        {"role": "user", "content": "第一问"},
        {"role": "assistant", "content": "第一答"},
        {"role": "user", "content": "基于刚才的回答继续"},
        {"role": "assistant", "content": "第二答"},
    ]
    result = prepare_trl_export("multiturn", [{"id": "dialog", "status": "eligible", "messages": messages}])
    assert result["ready"] and result["format"] == "trl_sft"
    assert result["rows"] == [{"messages": messages}]


@pytest.mark.parametrize("target", ["dpo", "orpo"])
def test_complete_preference_export_is_explicit_for_both_trainers(target):
    result = prepare_trl_export(target, [_preference_row("p1")])
    assert result["ready"] is True and result["format"] == "trl_preference"
    assert set(result["rows"][0]) == {"prompt", "chosen", "rejected"}
    assert result["rows"][0]["chosen"][0]["content"] == "4"


def test_one_invalid_record_blocks_all_rows_and_reports_every_failure():
    bad_multimodal = _sft_row("image")
    bad_multimodal["messages"][0]["content"] = [{"type": "image_url", "image_url": "https://invalid"}]
    missing_answer = _sft_row("empty")
    missing_answer["messages"][-1]["content"] = ""
    result = prepare_trl_export("sft", [_sft_row("valid"), bad_multimodal, missing_answer])

    assert result["ready"] is False and result["rows"] == []
    assert result["summary"] == {"total": 3, "compatible": 1, "incompatible": 2,
                                 "reasons": {"trl_empty_assistant_message": 1,
                                             "trl_multimodal_content_unsupported": 1}}
    assert result["failures"] == [
        {"index": 1, "id": "image", "reason": "trl_multimodal_content_unsupported"},
        {"index": 2, "id": "empty", "reason": "trl_empty_assistant_message"},
    ]


def test_ineligible_record_and_non_object_are_reported_not_skipped():
    rejected = _sft_row("r1")
    rejected["status"] = "quarantined"
    result = prepare_trl_export("sft", [rejected, None])
    assert result["rows"] == [] and not result["ready"]
    assert result["summary"]["incompatible"] == 2
    assert result["failures"] == [
        {"index": 0, "id": "r1", "reason": "trl_ineligible_record"},
        {"index": 1, "id": None, "reason": "trl_record_must_be_object"},
    ]


def test_empty_and_unsupported_exports_cannot_be_reported_ready():
    empty = prepare_trl_export("dpo", [])
    assert not empty["ready"] and empty["rows"] == []
    assert empty["summary"] == {"total": 0, "compatible": 0, "incompatible": 0,
                                "reasons": {"trl_no_records": 1}}
    assert empty["failures"] == [{"index": None, "id": None, "reason": "trl_no_records"}]
    with pytest.raises(ValueError, match="trl_unsupported_target"):
        prepare_trl_export("cpt", [_sft_row("s1")])
    with pytest.raises(ValueError, match="trl_records_must_be_iterable"):
        prepare_trl_export("sft", {"messages": []})


def test_generator_input_is_consumed_once_and_failures_still_block_export():
    records = (row for row in [_sft_row("one"), _sft_row("two", "")])
    result = prepare_trl_export("sft", records)
    assert result["summary"]["compatible"] == 1
    assert result["rows"] == []
    assert result["failures"][0]["id"] == "two"
