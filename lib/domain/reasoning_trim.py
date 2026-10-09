"""Optional, bounded editing instructions for reasoning in training samples."""
from __future__ import annotations

from lib.domain.workflow_quality import text_issue


MAX_TRIM_INSTRUCTION_CHARS = 4000
MAX_TRIM_PROMPT_CHARS = 16000
_GUARDRAILS = (
    "仅处理样本的推理文本，不得改变最终答案，不得编造事实。"
    "保留完成任务必要的事实、引用、来源依据与关键推导。"
    "不得删除正常知识问答中的合法内容；当问题本身讨论提示词、检索或系统规则时，"
    "保留回答问题所需的相关内容。无法确认是否应删除时保留原文。"
)
TRIM_TEMPLATES = {
    "leakage": (
        "移除推理文本中泄露的系统或开发者指令、检索包装标记，以及与完成任务无关的提示语。"
        "去掉不必要的来源原文复述，保留有助于核对答案的必要引用。"
        "无需修剪时原样保留推理文本。" + _GUARDRAILS
    ),
    "concise": (
        "移除推理文本中泄露的系统或开发者指令、检索包装，以及不必要的原文复述。"
        "精简重复陈述和与任务无关的旁支，保留可核验的必要解释、依据和结论。"
        "不要仅为缩短篇幅而删除有用内容。" + _GUARDRAILS
    ),
}
TRIM_TEMPLATE_NAMES = (*TRIM_TEMPLATES, "custom")


def validate_reasoning_trim(value: dict | None) -> dict | None:
    """Validate before creating a run; a missing option keeps old runs intact."""
    if value is None:
        return None
    if (not isinstance(value, dict)
            or set(value) - {"enabled", "template", "instruction", "custom_prompt"}):
        raise ValueError("invalid_reasoning_trim")
    enabled = value.get("enabled", False)
    template = value.get("template", "leakage")
    instruction = value.get("instruction", "")
    custom_prompt = value.get("custom_prompt", "")
    if (type(enabled) is not bool or not isinstance(template, str) or template not in TRIM_TEMPLATE_NAMES
            or not isinstance(instruction, str) or len(instruction) > MAX_TRIM_INSTRUCTION_CHARS
            or not isinstance(custom_prompt, str) or len(custom_prompt) > MAX_TRIM_PROMPT_CHARS
            or (instruction.strip() and text_issue(instruction))
            or (custom_prompt.strip() and text_issue(custom_prompt))
            or (enabled and template == "custom" and not custom_prompt.strip())):
        raise ValueError("invalid_reasoning_trim")
    return {"enabled": enabled, "template": template, "instruction": instruction.strip(),
            "custom_prompt": custom_prompt.strip()}


def trim_prompt(config: dict | None) -> str:
    """Return effective editing instructions only when trimming is enabled."""
    normalized = validate_reasoning_trim(config)
    if not normalized or not normalized["enabled"]:
        return ""
    template = (normalized["custom_prompt"] + "\n" + _GUARDRAILS
                if normalized["template"] == "custom" else TRIM_TEMPLATES[normalized["template"]])
    return "\n".join(part for part in (template, normalized["instruction"]) if part)
