"""Lazily prepare one supervised assistant turn from each canonical prefix."""
from __future__ import annotations

from collections.abc import Iterable, Iterator, Mapping
from copy import deepcopy
from typing import Any, Protocol

from ..domain.trainer_formats import to_trl_sft
from ..domain.training_control import training_efforts_by_message, validate_training_effort


class TrainingTurnEncoder(Protocol):
    """Encode a validated prefix ending at its supervised assistant message.

    Native reasoning has already been normalized to ``thinking``. Encoders
    must preserve that target reasoning and reject unsupported tools or other
    capabilities rather than silently discarding them.
    """

    def encode_turn(self, messages: list[dict[str, Any]], control: str, *,
                    tools: list[dict[str, Any]] | None = None,
                    control_text_config: Mapping | None = None) -> dict[str, Any]: ...


def iter_training_examples(records: Iterable[Mapping[str, Any]], encoder: TrainingTurnEncoder, *,
                           default_effort: str | None = None) -> Iterator[dict[str, Any]]:
    """Yield encoded assistant turns without changing or collecting source rows.

    The explicit default is a read-only view for legacy rows lacking a control;
    it does not infer a label or overwrite an existing control. Every assistant
    gets its own target view so templates that omit historical reasoning cannot
    remove that turn's reasoning from all supervised examples.
    """
    if isinstance(records, (str, bytes, Mapping)) or not isinstance(records, Iterable):
        raise ValueError("training_records_must_be_iterable")
    if default_effort is not None:
        validate_training_effort(default_effort)
    for record_index, record in enumerate(records):
        if not isinstance(record, Mapping):
            raise ValueError("training_record_must_be_object")
        if "status" in record and record["status"] != "eligible":
            raise ValueError("training_record_ineligible")
        if "control_text_config" in record and not isinstance(record["control_text_config"], Mapping):
            raise ValueError("invalid_control_text_configuration")
        normalized = to_trl_sft(record)
        messages = normalized["messages"]
        indices = [index for index, message in enumerate(messages) if message["role"] == "assistant"]
        efforts = training_efforts_by_message(record, indices, default_effort=default_effort)
        for message_index, message in enumerate(messages):
            if message["role"] != "assistant":
                continue
            control = record.get("training_control", {})
            labelled = "effort" in control or str(message_index) in control.get("by_message", {})
            encoded = encoder.encode_turn(deepcopy(messages[:message_index + 1]), efforts[message_index],
                                          tools=deepcopy(normalized.get("tools")),
                                          **({"control_text_config": deepcopy(record["control_text_config"])}
                                             if "control_text_config" in record else {}))
            yield {**encoded, "metadata": {
                **deepcopy(encoded.get("metadata", {})),
                **({"control_text_node": record["control_text_node"]}
                   if "control_text_node" in record else {}),
                "source_record_index": record_index,
                "source_message_index": message_index,
                "control_source": "record" if labelled else "explicit_default",
            }}
