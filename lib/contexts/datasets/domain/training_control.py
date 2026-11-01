"""Explicit learner controls, independent of teacher request parameters."""
from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
from typing import Any

from lib.contexts.models.contracts import STANDARD_REASONING_EFFORTS, STANDARD_REASONING_SCHEMA


TRAINING_CONTROL_SCHEMA = "shujian.training-control/v1"
TRAINING_EFFORTS = STANDARD_REASONING_EFFORTS


def validate_training_effort(effort: Any) -> str:
    if not isinstance(effort, str) or effort not in TRAINING_EFFORTS:
        raise ValueError("invalid_training_effort")
    return effort


def validate_training_control(control) -> dict:
    if (not isinstance(control, Mapping) or control.get("schema") != TRAINING_CONTROL_SCHEMA
            or set(control) not in ({"schema", "effort"}, {"schema", "by_message"})):
        raise ValueError("invalid_training_control")
    if "effort" in control:
        validate_training_effort(control["effort"])
    else:
        turns = control["by_message"]
        if (not isinstance(turns, Mapping) or not turns
                or any(not isinstance(key, str) or not key.isascii() or not key.isdigit()
                       or str(int(key)) != key for key in turns)):
            raise ValueError("invalid_training_control_turns")
        for effort in turns.values():
            validate_training_effort(effort)
    return deepcopy(dict(control))


def training_efforts_by_message(record, assistant_indices, *, default_effort=None):
    """Resolve controls once at the loader boundary, without guessing gaps."""
    if "training_control" not in record:
        if default_effort is None:
            raise ValueError("training_control_missing")
        return {index: default_effort for index in assistant_indices}
    control = validate_training_control(record["training_control"])
    if "effort" in control:
        return {index: control["effort"] for index in assistant_indices}
    if any(int(index) not in assistant_indices for index in control["by_message"]):
        raise ValueError("training_control_not_assistant_turn")
    resolved = {}
    for index in assistant_indices:
        effort = control["by_message"].get(str(index), default_effort)
        if effort is None:
            raise ValueError(f"training_control_missing_for_message:{index}")
        resolved[index] = effort
    return resolved


def projected_training_control(row, messages):
    """Opt new canonical records into versioned metadata; legacy exports stay exact."""
    if "training_control" in row:
        return validate_training_control(row["training_control"])
    controls = row.get("generation_controls")
    if controls is None:
        return None
    if (not isinstance(controls, Mapping) or controls.get("version") != 1
            or not isinstance(controls.get("assistant_turns"), list)):
        raise ValueError("invalid_generation_controls")
    by_message = {}
    for entry in controls["assistant_turns"]:
        index, receipt = entry.get("message_index"), entry.get("generation_control")
        if (type(index) is not int or not 0 <= index < len(messages)
                or messages[index].get("role") != "assistant" or str(index) in by_message
                or not isinstance(receipt, Mapping) or receipt.get("schema") != STANDARD_REASONING_SCHEMA):
            raise ValueError("invalid_generation_control_turn")
        by_message[str(index)] = validate_training_effort(receipt.get("effort"))
    if not by_message:
        return None
    return {"schema": TRAINING_CONTROL_SCHEMA, "by_message": by_message}


def training_control_effort(record: Mapping[str, Any], *, default_effort: str | None = None) -> str:
    """Read a versioned control; an explicit default only fills an absent field.

    Teacher effort, explanation depth and response length are never consulted.
    A malformed or unknown existing control cannot be replaced by a default.
    """
    if not isinstance(record, Mapping):
        raise ValueError("training_record_must_be_object")
    if default_effort is not None:
        validate_training_effort(default_effort)
    if "training_control" not in record:
        if default_effort is None:
            raise ValueError("training_control_missing")
        return default_effort
    control = validate_training_control(record["training_control"])
    if "effort" not in control:
        raise ValueError("training_control_is_per_message")
    return control["effort"]


def with_training_control(record: Mapping[str, Any], effort: str) -> dict[str, Any]:
    """Copy a record and attach an explicit control without relabeling it."""
    if not isinstance(record, Mapping):
        raise ValueError("training_record_must_be_object")
    effort = validate_training_effort(effort)
    if "training_control" in record and training_control_effort(record) != effort:
        raise ValueError("training_control_conflict")
    result = deepcopy(dict(record))
    result["training_control"] = {"schema": TRAINING_CONTROL_SCHEMA, "effort": effort}
    return result
