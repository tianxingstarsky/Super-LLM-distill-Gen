"""Stable teacher line addresses and slices from the unchanged clean source.

Line labels only exist in the teacher projection. Original line endings and
numeric content are retained when selecting evidence for learner messages.
"""
from __future__ import annotations


SOURCE_LINE_PROTOCOL_VERSION = 1
MAX_SOURCE_LINE_RANGES = 32


def source_lines(text):
    """Return physical source lines, preserving endings and blank lines."""
    if not isinstance(text, str):
        raise ValueError("invalid_source_line_material")
    return text.splitlines(keepends=True)


def number_source_material(material):
    """Add deterministic one-based addresses without changing the raw body."""
    identity = material.get("unit_id", material.get("id"))
    if not isinstance(identity, str) or not identity or len(identity) > 200:
        raise ValueError("invalid_source_line_material")
    lines = source_lines(material.get("text"))
    if not lines:
        raise ValueError("invalid_source_line_material")
    return {**material, "material_id": identity,
            "source_line_protocol_version": SOURCE_LINE_PROTOCOL_VERSION,
            "line_count": len(lines),
            "numbered_text": "".join(f"{index}: {line}" for index, line in enumerate(lines, 1))}


def validate_numbered_material(material):
    """A stored numbering must resolve exactly to its unchanged clean text."""
    if (type(material.get("source_line_protocol_version")) is not int
            or material.get("source_line_protocol_version") != SOURCE_LINE_PROTOCOL_VERSION
            or type(material.get("line_count")) is not int):
        raise ValueError("invalid_source_line_material")
    expected = number_source_material(material)
    if any(material.get(key) != expected[key] for key in (
            "material_id", "source_line_protocol_version", "line_count", "numbered_text")):
        raise ValueError("invalid_source_line_material")
    return material


def teacher_material(material):
    """Send one numbered body while durable packs retain the original text."""
    if material.get("source_line_protocol_version") != SOURCE_LINE_PROTOCOL_VERSION:
        return material
    validate_numbered_material(material)
    return {key: value for key, value in material.items() if key != "text"}


def normalize_source_line_ranges(ranges):
    error = "invalid_source_line_ranges"
    if not isinstance(ranges, list) or not 1 <= len(ranges) <= MAX_SOURCE_LINE_RANGES:
        raise ValueError(error)
    normalized, seen = [], set()
    for item in ranges:
        if (not isinstance(item, dict) or set(item) != {"material_id", "start_line", "end_line"}
                or not isinstance(item["material_id"], str) or not item["material_id"]
                or len(item["material_id"]) > 200
                or type(item["start_line"]) is not int or type(item["end_line"]) is not int
                or not 1 <= item["start_line"] <= item["end_line"]):
            raise ValueError(error)
        key = (item["material_id"], item["start_line"], item["end_line"])
        if key in seen:
            raise ValueError(error)
        seen.add(key)
        normalized.append(dict(item))
    return normalized


def resolve_source_line_ranges(materials, ranges):
    """Resolve inclusive addresses to exact original lines; never trust a rewrite.

    Selection order follows the model's requested block order. Blocks may span
    different materials, while every address must belong to the provided pack.
    """
    normalized = normalize_source_line_ranges(ranges)
    if not isinstance(materials, list):
        raise ValueError("invalid_source_line_material")
    by_id = {}
    for material in materials:
        if not isinstance(material, dict):
            raise ValueError("invalid_source_line_material")
        validate_numbered_material(material)
        identity = material["material_id"]
        if identity in by_id:
            raise ValueError("invalid_source_line_material")
        by_id[identity] = material
    resolved = []
    for item in normalized:
        material = by_id.get(item["material_id"])
        if material is None:
            raise ValueError("source_line_material_not_found")
        if item["end_line"] > material["line_count"]:
            raise ValueError("source_line_range_out_of_bounds")
        text = "".join(source_lines(material["text"])[item["start_line"] - 1:item["end_line"]])
        if not text.strip():
            raise ValueError("source_line_range_empty")
        resolved.append({**item, "text": text})
    return resolved
