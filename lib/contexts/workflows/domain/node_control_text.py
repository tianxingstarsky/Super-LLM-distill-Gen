"""Freeze node-owned text mappings without rewriting generated reasoning."""
from copy import deepcopy

CONTROL_TEXT_NODES = frozenset({"sft", "multiturn", "cot"})


def validate_node_control_text_configs(value):
    from lib.contexts.datasets.contracts import validate_control_text_config
    if value is None:
        return {}
    if not isinstance(value, dict) or set(value) - CONTROL_TEXT_NODES:
        raise ValueError("invalid_node_control_text_configs")
    return {node: validate_control_text_config(config) for node, config in value.items()}


def attach_node_control_text(record, stage, recipe):
    """Attach only this author node's frozen mapping; never alter text or status."""
    config = recipe.get("node_control_text_configs", {}).get(stage)
    if config is None or stage not in CONTROL_TEXT_NODES:
        return record
    return {**record, "control_text_node": stage, "control_text_config": deepcopy(config)}
