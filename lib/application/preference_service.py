"""Pure form adapter for the commented generation-preference YAML file.

Parsing and scalar-span replacement happen in memory. Untouched fields,
comments, key order, and line endings remain byte-for-byte unchanged.
"""
from __future__ import annotations

from copy import deepcopy
from decimal import Decimal, InvalidOperation
from math import isfinite
from typing import Any, Mapping

import yaml


WEIGHT_KEYS = ("reasoning", "context_use", "tool_use", "bilingual")
ALL_WEIGHT_KEYS = (*WEIGHT_KEYS, "default")
TAGGERS = frozenset({"unitag", "ultrafeedback"})
COT_STYLES = frozenset({"separated", "tags", "plain", "drop"})
_MICRO = Decimal("0.000001")


class _UniqueKeyLoader(yaml.SafeLoader):
    def construct_mapping(self, node: yaml.MappingNode, deep: bool = False) -> dict:
        mapping: dict[Any, Any] = {}
        for key_node, value_node in node.value:
            key = self.construct_object(key_node, deep=deep)
            try:
                duplicate = key in mapping
            except TypeError as exc:
                raise ValueError("preference_invalid_mapping_key") from exc
            if duplicate:
                raise ValueError("preference_duplicate_yaml_key")
            mapping[key] = self.construct_object(value_node, deep=deep)
        return mapping


def _config(text: str) -> dict:
    if not isinstance(text, str) or not text.strip():
        raise ValueError("preference_yaml_required")
    try:
        root = yaml.compose(text, Loader=yaml.SafeLoader)
        seen: set[int] = set()

        def check_aliases(node: yaml.Node) -> None:
            identity = id(node)
            if identity in seen:
                raise ValueError("preference_yaml_aliases_unsupported")
            seen.add(identity)
            if isinstance(node, yaml.MappingNode):
                for key_node, value_node in node.value:
                    check_aliases(key_node)
                    check_aliases(value_node)
            elif isinstance(node, yaml.SequenceNode):
                for value_node in node.value:
                    check_aliases(value_node)

        if root is not None:
            check_aliases(root)
        data = yaml.load(text, Loader=_UniqueKeyLoader)
    except yaml.YAMLError as exc:
        raise ValueError("preference_invalid_yaml") from exc
    if not isinstance(data, dict):
        raise ValueError("preference_root_must_be_mapping")
    return data


def _section(data: Mapping[str, Any], name: str) -> dict:
    section = data.get(name)
    if not isinstance(section, dict):
        raise ValueError(f"preference_{name}_must_be_mapping")
    return section


def _decimal(value: Any, code: str) -> Decimal:
    if isinstance(value, bool) or not isinstance(value, (int, float, Decimal)):
        raise ValueError(code)
    if isinstance(value, float) and not isfinite(value):
        raise ValueError(code)
    try:
        number = Decimal(str(value))
    except InvalidOperation as exc:
        raise ValueError(code) from exc
    if not number.is_finite():
        raise ValueError(code)
    return number


def _bool(value: Any, code: str) -> bool:
    if type(value) is not bool:
        raise ValueError(code)
    return value


def _templates(value: Any) -> int:
    if type(value) is not int or not 1 <= value <= 100:
        raise ValueError("preference_invalid_templates_per_dim")
    return value


def _tagger(value: Any) -> str:
    if not isinstance(value, str) or value not in TAGGERS:
        raise ValueError("preference_invalid_tagger")
    return value


def _style(value: Any, think_tokens: Any) -> str:
    if not isinstance(value, str) or value not in COT_STYLES:
        raise ValueError("preference_invalid_cot_style")
    if not isinstance(think_tokens, list) or len(think_tokens) != 2 or any(
        not isinstance(token, str) for token in think_tokens
    ):
        raise ValueError("preference_invalid_think_tokens")
    if value == "tags" and (not all(token.strip() for token in think_tokens)
                            or think_tokens[0] == think_tokens[1]):
        raise ValueError("preference_tags_require_model_tokens")
    return value


def preference_summary(text: str) -> dict[str, Any]:
    """Validate YAML text and return values suitable for a visual form."""
    data = _config(text)
    preferences = _section(data, "preferences")
    sampling = _section(data, "sampling")
    correction = _section(data, "correction")
    cot = _section(data, "cot")

    weights: dict[str, Decimal] = {}
    for key in ALL_WEIGHT_KEYS:
        weight = _decimal(preferences.get(key), f"preference_invalid_weight_{key}")
        if weight < 0 or weight > 1:
            raise ValueError(f"preference_invalid_weight_{key}")
        weights[key] = weight
    if abs(sum(weights.values()) - Decimal("1")) > _MICRO:
        raise ValueError("preference_weights_must_sum_to_one")
    floor = _decimal(sampling.get("default_floor"), "preference_invalid_default_floor")
    if not 0 < floor < 1:
        raise ValueError("preference_invalid_default_floor")
    if weights["default"] < floor:
        raise ValueError("preference_default_below_floor")
    if weights["default"] >= 1 or sum(weights[key] for key in WEIGHT_KEYS) <= 0:
        raise ValueError("preference_relative_tendencies_required")

    templates = _templates(sampling.get("templates_per_dim"))
    shuffle = _bool(sampling.get("shuffle_per_batch"), "preference_invalid_shuffle")
    correction_enabled = _bool(correction.get("enabled"), "preference_invalid_correction_enabled")
    threshold = _decimal(correction.get("threshold"), "preference_invalid_correction_threshold")
    if not 0 <= threshold <= 1:
        raise ValueError("preference_invalid_correction_threshold")
    tagger = _tagger(correction.get("tagger"))
    style = _style(cot.get("style"), cot.get("think_tokens"))
    relative_total = sum(weights[key] for key in WEIGHT_KEYS)
    return {
        "weights": {key: float(value) for key, value in weights.items()},
        "default_share": float(weights["default"]),
        "relative_tendencies": {key: float(weights[key] / relative_total) for key in WEIGHT_KEYS},
        "default_floor": float(floor),
        "templates_per_dim": templates,
        "shuffle_per_batch": shuffle,
        "correction_enabled": correction_enabled,
        "correction_threshold": float(threshold),
        "correction_tagger": tagger,
        "cot_style": style,
        "think_tokens": tuple(cot["think_tokens"]),
    }


def _normalized_weights(default_share: Any, tendencies: Mapping[str, Any], floor: Decimal) -> dict[str, Decimal]:
    base = _decimal(default_share, "preference_invalid_default_share")
    if not floor <= base < 1:
        raise ValueError("preference_default_below_floor" if base < floor else "preference_invalid_default_share")
    if not isinstance(tendencies, Mapping) or set(tendencies) != set(WEIGHT_KEYS):
        raise ValueError("preference_invalid_relative_tendencies")
    relative = {key: _decimal(tendencies[key], f"preference_invalid_tendency_{key}") for key in WEIGHT_KEYS}
    if any(value < 0 for value in relative.values()) or sum(relative.values()) <= 0:
        raise ValueError("preference_invalid_relative_tendencies")

    base = base.quantize(_MICRO)
    total_units = int((Decimal("1") - base) / _MICRO)
    relative_total = sum(relative.values())
    quotas = {key: Decimal(total_units) * relative[key] / relative_total for key in WEIGHT_KEYS}
    units = {key: int(quotas[key]) for key in WEIGHT_KEYS}
    remaining = total_units - sum(units.values())
    ranked = sorted((key for key in WEIGHT_KEYS if relative[key] > 0),
                    key=lambda key: (-(quotas[key] - units[key]), WEIGHT_KEYS.index(key)))
    for key in ranked[:remaining]:
        units[key] += 1
    return {**{key: Decimal(units[key]) * _MICRO for key in WEIGHT_KEYS}, "default": base}


def _mapping_node(root: yaml.Node, path: tuple[str, ...]) -> yaml.ScalarNode:
    node = root
    for key in path:
        if not isinstance(node, yaml.MappingNode):
            raise ValueError("preference_yaml_shape_changed")
        matches = [value for name, value in node.value
                   if isinstance(name, yaml.ScalarNode) and name.value == key]
        if len(matches) != 1:
            raise ValueError("preference_yaml_shape_changed")
        node = matches[0]
    if not isinstance(node, yaml.ScalarNode) or node.style in {"|", ">"}:
        raise ValueError("preference_yaml_scalar_required")
    return node


def _yaml_scalar(value: Any) -> str:
    if type(value) is bool:
        return "true" if value else "false"
    if isinstance(value, Decimal):
        result = format(value, "f").rstrip("0").rstrip(".")
        return result if "." in result else result + ".0"
    return str(value)


def update_preference_yaml(
    text: str, *, default_share: float, relative_tendencies: Mapping[str, float],
    templates_per_dim: int, shuffle_per_batch: bool, correction_enabled: bool,
    correction_threshold: float, correction_tagger: str, cot_style: str,
) -> str:
    """Apply form values in memory while preserving comments and other YAML fields."""
    current = preference_summary(text)
    floor = Decimal(str(current["default_floor"]))
    weights = _normalized_weights(default_share, relative_tendencies, floor)
    templates = _templates(templates_per_dim)
    shuffle = _bool(shuffle_per_batch, "preference_invalid_shuffle")
    enabled = _bool(correction_enabled, "preference_invalid_correction_enabled")
    threshold = _decimal(correction_threshold, "preference_invalid_correction_threshold")
    if not 0 <= threshold <= 1:
        raise ValueError("preference_invalid_correction_threshold")
    tagger = _tagger(correction_tagger)
    style = _style(cot_style, list(current["think_tokens"]))

    updates = {
        **{("preferences", key): weights[key] for key in ALL_WEIGHT_KEYS},
        ("sampling", "templates_per_dim"): templates,
        ("sampling", "shuffle_per_batch"): shuffle,
        ("correction", "enabled"): enabled,
        ("correction", "threshold"): threshold,
        ("correction", "tagger"): tagger,
        ("cot", "style"): style,
    }
    existing = _config(text)
    try:
        root = yaml.compose(text, Loader=yaml.SafeLoader)
    except yaml.YAMLError as exc:
        raise ValueError("preference_invalid_yaml") from exc
    replacements = []
    expected = deepcopy(existing)
    for path, value in updates.items():
        section = expected[path[0]]
        old = section[path[1]]
        section[path[1]] = float(value) if isinstance(value, Decimal) else value
        if old == section[path[1]] and type(old) is type(section[path[1]]):
            continue
        node = _mapping_node(root, path)
        replacements.append((node.start_mark.index, node.end_mark.index, _yaml_scalar(value)))
    result = text
    for start, end, replacement in sorted(replacements, reverse=True):
        result = result[:start] + replacement + result[end:]
    if _config(result) != expected:
        raise ValueError("preference_yaml_roundtrip_mismatch")
    preference_summary(result)
    return result
