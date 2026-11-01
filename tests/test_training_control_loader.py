"""Lazy training views preserve source records and every assistant target."""
from __future__ import annotations

from copy import deepcopy

import pytest

from lib.contexts.datasets.api import iter_training_examples, with_training_control


def _dialogue_record():
    return {
        "training_control": {"schema": "shujian.training-control/v1", "effort": "high"},
        "messages": [
            {"role": "user", "content": "差异能说明干预有效吗？"},
            {"role": "assistant", "reasoning_content": "我需区分差异与因果。", "content": "还要检查对照条件。"},
            {"role": "user", "content": "那不确定性呢？"},
            {"role": "assistant", "reasoning_content": "我还需考虑测量和抽样误差。", "content": "应报告误差范围。"},
        ],
    }


class RecordingEncoder:
    def __init__(self, *, mutate=False):
        self.calls = []
        self.mutate = mutate

    def encode_turn(self, messages, control, *, tools=None):
        self.calls.append({"messages": deepcopy(messages), "control": control, "tools": deepcopy(tools)})
        if self.mutate:
            messages[-1]["thinking"] = "encoder-local change"
        return {"input_ids": [10, 20], "attention_mask": [1, 1], "labels": [-100, 20],
                "metadata": {"adapter": "recording-test-encoder"}}


def test_loader_is_lazy_and_does_not_read_a_later_record_to_yield_a_target():
    reads = []

    def source_records():
        reads.append("first")
        yield _dialogue_record()
        reads.append("second")
        raise AssertionError("The second source record was requested too early")

    encoder = RecordingEncoder()
    examples = iter_training_examples(source_records(), encoder)
    assert reads == [] and encoder.calls == []

    first = next(examples)
    second = next(examples)

    assert reads == ["first"]
    assert first["metadata"]["source_message_index"] == 1
    assert second["metadata"]["source_message_index"] == 3


def test_every_assistant_reaches_encoder_with_reasoning_without_mutating_source():
    record = _dialogue_record()
    original = deepcopy(record)
    encoder = RecordingEncoder(mutate=True)

    examples = list(iter_training_examples([record], encoder))

    assert len(examples) == 2
    assert [call["messages"][-1]["thinking"] for call in encoder.calls] == [
        "我需区分差异与因果。", "我还需考虑测量和抽样误差。",
    ]
    assert encoder.calls[1]["messages"][1]["thinking"] == "我需区分差异与因果。"
    assert [call["messages"][-1]["content"] for call in encoder.calls] == [
        "还要检查对照条件。", "应报告误差范围。",
    ]
    assert [example["metadata"]["source_record_index"] for example in examples] == [0, 0]
    assert all(example["metadata"]["adapter"] == "recording-test-encoder" for example in examples)
    assert record == original


def test_legacy_record_requires_an_explicit_default_and_remains_unlabeled():
    record = _dialogue_record()
    del record["training_control"]
    original = deepcopy(record)
    with pytest.raises(ValueError, match="training_control_missing"):
        next(iter_training_examples([record], RecordingEncoder()))

    encoder = RecordingEncoder()
    list(iter_training_examples([record], encoder, default_effort="medium"))

    assert [call["control"] for call in encoder.calls] == ["medium", "medium"]
    assert record == original
    assert "training_control" not in record


@pytest.mark.parametrize("bad_control", [
    {"schema": "unknown/v9", "effort": "high"},
    {"schema": "shujian.training-control/v1", "effort": "automatic"},
])
def test_default_cannot_replace_an_invalid_existing_control(bad_control):
    record = _dialogue_record()
    record["training_control"] = bad_control
    encoder = RecordingEncoder()

    with pytest.raises(ValueError, match="invalid_training_"):
        next(iter_training_examples([record], encoder, default_effort="high"))

    assert encoder.calls == []
    assert record["training_control"] == bad_control


def test_explicit_ineligible_record_is_not_encoded():
    record = _dialogue_record()
    record["status"] = "quarantined"
    encoder = RecordingEncoder()

    with pytest.raises(ValueError, match="training_record_ineligible"):
        next(iter_training_examples([record], encoder))

    assert encoder.calls == []


def test_control_attachment_copies_record_and_refuses_conflicting_relabeling():
    record = _dialogue_record()
    del record["training_control"]
    original = deepcopy(record)
    controlled = with_training_control(record, "low")
    controlled["messages"][1]["content"] = "only in the copy"

    assert record == original
    assert controlled["training_control"] == {"schema": "shujian.training-control/v1", "effort": "low"}
    with pytest.raises(ValueError, match="training_control_conflict"):
        with_training_control(controlled, "max")
    assert controlled["training_control"]["effort"] == "low"


def test_per_message_controls_apply_to_assistant_targets_and_keep_gaps_explicit():
    record = _dialogue_record()
    record["training_control"] = {
        "schema": "shujian.training-control/v1", "by_message": {"1": "low", "3": "max"},
    }
    original = deepcopy(record)
    encoder = RecordingEncoder()
    examples = list(iter_training_examples([record], encoder))
    assert [call["control"] for call in encoder.calls] == ["low", "max"]
    assert [example["metadata"]["source_message_index"] for example in examples] == [1, 3]
    assert record == original

    del record["training_control"]["by_message"]["3"]
    with pytest.raises(ValueError, match="training_control_missing_for_message:3"):
        next(iter_training_examples([record], RecordingEncoder()))
    fallback_encoder = RecordingEncoder()
    fallback_examples = list(iter_training_examples([record], fallback_encoder, default_effort="medium"))
    assert [call["control"] for call in fallback_encoder.calls] == ["low", "medium"]
    assert [example["metadata"]["control_source"] for example in fallback_examples] == [
        "record", "explicit_default",
    ]
    assert "3" not in record["training_control"]["by_message"]

    record["training_control"]["by_message"]["0"] = "high"
    with pytest.raises(ValueError, match="training_control_not_assistant_turn"):
        next(iter_training_examples([record], RecordingEncoder(), default_effort="medium"))
