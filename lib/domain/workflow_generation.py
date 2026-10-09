"""Pure, reproducible instructions for authored training answers and reasoning."""
from __future__ import annotations

import hashlib

from lib.domain.workflow_quality import text_issue


MAX_GENERATION_INSTRUCTION_CHARS = 4000
STYLE_PRESETS = {
    "default": "遵循任务要求，使用清晰自然的表达，完整回答问题。",
    "concise": "采用简洁的工程分析风格，突出关键依据、必要推导和结论，避免重复。",
    "structured": "采用条理清晰的教学风格，按适当步骤解释依据与推导，再给出明确答案。",
    "skeptical": "采用审慎核验风格，检查前提、证据和边界条件，明确不确定性后给出答案。",
    "reflective": "采用反思校验风格，检查方案中的关键假设与可能错误，给出经核验的解释和结论。",
}
GENERATION_STYLES = (*STYLE_PRESETS, "mixed", "custom")
_MIXED_STYLES = ("concise", "structured", "skeptical", "reflective")


def validate_node_generation(value: dict | None) -> dict:
    """Normalize only supported generation nodes, leaving old recipes optional."""
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise ValueError("invalid_node_generation")
    normalized = {}
    for stage, config in value.items():
        if (stage not in {"sft", "cot"} or not isinstance(config, dict)
                or set(config) - {"enabled", "style", "instruction"}):
            raise ValueError("invalid_node_generation")
        enabled = config.get("enabled", True)
        style = config.get("style", "default")
        instruction = config.get("instruction", "")
        if (type(enabled) is not bool or not isinstance(style, str) or style not in GENERATION_STYLES
                or not isinstance(instruction, str)
                or len(instruction) > MAX_GENERATION_INSTRUCTION_CHARS
                or (instruction.strip() and text_issue(instruction))
                or (enabled and style == "custom" and not instruction.strip())):
            raise ValueError("invalid_node_generation")
        normalized[stage] = {"enabled": enabled, "style": style, "instruction": instruction.strip()}
    return normalized


def style_for_sample(config: dict | None, sample_id: str) -> dict:
    """Select one stable style independent of concurrency, batching, and retries."""
    if not isinstance(sample_id, str):
        raise ValueError("invalid_generation_sample_id")
    node = validate_node_generation({"sft": {} if config is None else config})["sft"]
    style = node["style"]
    if style == "mixed":
        index = int.from_bytes(hashlib.sha256(sample_id.encode("utf-8")).digest(), "big")
        style = _MIXED_STYLES[index % len(_MIXED_STYLES)]
    instruction = "\n".join(part for part in (STYLE_PRESETS.get(style, ""), node["instruction"]) if part)
    return {"preset": style, "instruction": instruction}
