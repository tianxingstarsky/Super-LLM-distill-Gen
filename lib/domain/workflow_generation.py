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
REASONING_LANGUAGES = ("auto", "zh", "en", "zh_en")
_MIXED_STYLES = ("concise", "structured", "skeptical", "reflective")


def reasoning_language_instruction(value: str = "auto") -> str:
    """Describe authored reasoning only; provider-native history is untouched."""
    if not isinstance(value, str) or value not in REASONING_LANGUAGES:
        raise ValueError("invalid_node_generation")
    if value == "auto":
        return ""
    expression = {
        "zh": "显式推理使用自然中文，必要的英文术语、代码与准确引用可保留原样。",
        "en": "Write the explicit reasoning in natural English; retain necessary terms, code, and accurate quotations.",
        "zh_en": (
            "显式推理自然结合中文与 English，让两种语言共同表达具体依据、推导与核对；"
            "可以在适合的语境中切换，不做逐句双语翻译，不设置固定语言比例或固定步骤。"
            "不能只给单语正文添加几个英文标签、术语或重复翻译来冒充中英混合推理。"
        ),
    }[value]
    return (
        "显式推理语言要求：" + expression
        + "最终答案跟随当前用户的语言与明确要求，不因推理语言设置而强制双语。"
        "语言要求只控制新撰写的显式推理，不修改或冒称上游原生推理；"
        "事实正确、证据范围和用户任务优先于语言表现。"
        "不在答案或推理正文复述这些配置、角色指令、内部审核或生成流程。"
    )


def validate_node_generation(value: dict | None) -> dict:
    """Normalize only supported generation nodes, leaving old recipes optional."""
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise ValueError("invalid_node_generation")
    normalized = {}
    for stage, config in value.items():
        if (stage not in {"sft", "cot", "multiturn"} or not isinstance(config, dict)
                or set(config) - {"enabled", "style", "instruction", "answer_mode", "reasoning_language"}):
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
        if "reasoning_language" in config:
            reasoning_language_instruction(config["reasoning_language"])
            normalized[stage]["reasoning_language"] = config["reasoning_language"]
        if "answer_mode" in config:
            mode = config["answer_mode"]
            if (stage != "cot" or not isinstance(mode, str) or mode not in {"joint", "native_prefill"}
                    or (mode == "native_prefill" and not enabled)):
                raise ValueError("invalid_cot_answer_mode")
            normalized[stage]["answer_mode"] = mode
    return normalized


def style_for_sample(config: dict | None, sample_id: str) -> dict:
    """Select one stable style independent of concurrency, batching, and retries."""
    if not isinstance(sample_id, str):
        raise ValueError("invalid_generation_sample_id")
    # Answer transport belongs to the CoT stage, not the text style itself.
    # Creation validates its scope; neutral style selection can serve either node.
    style_config = ({} if config is None else
                    {key: value for key, value in config.items() if key != "answer_mode"}
                    if isinstance(config, dict) else config)
    node = validate_node_generation({"sft": style_config})["sft"]
    style = node["style"]
    if style == "mixed":
        index = int.from_bytes(hashlib.sha256(sample_id.encode("utf-8")).digest(), "big")
        style = _MIXED_STYLES[index % len(_MIXED_STYLES)]
    instruction = "\n".join(part for part in (STYLE_PRESETS.get(style, ""), node["instruction"]) if part)
    result = {"preset": style, "instruction": instruction}
    language = node.get("reasoning_language", "auto")
    if node["enabled"] and language != "auto":
        result["reasoning_language"] = language
        result["instruction"] = "\n".join(part for part in (
            instruction, reasoning_language_instruction(language)) if part)
    return result
