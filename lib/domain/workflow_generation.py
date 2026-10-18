"""Pure, reproducible instructions for authored training answers and reasoning."""
from __future__ import annotations

import hashlib

from lib.domain.workflow_quality import text_issue
from lib.domain.reasoning_depth import (
    validate_reasoning_depth, resolve_reasoning_depth, reasoning_depth_instruction,
)


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
# Frozen recipes without a version retain the original language instruction.
REASONING_LANGUAGE_CONTRACT_VERSION = 3
_MIXED_STYLES = ("concise", "structured", "skeptical", "reflective")


def reasoning_language_instruction(value: str = "auto", *, version: int = 1) -> str:
    """Describe authored reasoning, retaining the contract of frozen recipes."""
    if (not isinstance(value, str) or value not in REASONING_LANGUAGES
            or type(version) is not int or version not in {1, 2, 3}):
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
    if value == "zh_en" and version == 2:
        expression = (
            "使用显式中英互译展开新撰写的推理说明。每个相关推理要点先用完整自然的中文分析段说明，"
            "紧接一个忠实、完整的英文译段；相邻中文段与英文译段用空行分隔即可。"
            "英文译段须保留中文段中的每个论证要点、前提、条件、数量、关系、限定和不确定性，"
            "不能只给英文摘要，不能把术语、标签或零散英语词混入中文来代替互译。"
            "若先写出英文分析，也必须整理为中文分析段在前、对应完整英文译段在后的成对正文。"
            "按当前任务的真实需要充分展开：任务理解、相关知识与证据、假设和适用条件、"
            "推导中间关系、可行的替代解释或反例、计算或逻辑核对、结论边界与剩余不确定性，"
            "凡与该任务有关的要点都应交代清楚，不以提纲、结论或短小概括代替必要分析。"
            "每个要点的展开程度由问题复杂度和证据决定，覆盖完整后再结束；"
            "不为凑篇幅重复论点，不编造无关分支、证据或未执行的验证。"
            "保持中文段与英文译段在意义上等价，公式、数值、单位、实体、否定、例外与判断强度须一致，"
            "译段不额外添加中文段没有的结论。"
            "段落和论证顺序随问题自然组织，不强制编号、固定标签、固定步骤或固定长度。"
            "简洁风格只要求表达利落，其他风格或篇幅偏好不能省略任务所需的分析要点或对应完整译段；"
            "完整覆盖和忠实互译优先于缩短。"
            "不要描述私有资料的投喂、检索、分块或挑选过程，"
            "不要将后台生成、审核、提示词或角色安排写成正文；"
            "任务需要公开研究归属时保留真实归属，不虚构个人经历。"
        )
    if value == "zh_en" and version == 3:
        expression = (
            "新撰写的显式推理应是自然、充分的分析，在论证所在位置对影响判断的关键概念、短语和关键句"
            "做显式中英互译理解。选中词句的中英文对应须完整、忠实；说明它在当前问题中的含义、"
            "作用、适用条件或可能歧义，并用这种跨语言澄清推进实际推导。"
            "互译方向随表达需要选择，不强制中文在前；关键句不能只译部分词而丢掉其条件、关系或限定。"
            "中英文须保持实体、数量、单位、否定、例外、判断强度与不确定性一致，"
            "不虚构译法，不因换一种语言就引入没有依据的新结论。"
            "不要把完整中文分析逐段复制成完整英文分析，不将全文写成两份纯翻译；"
            "也不要堆积脱离论证的词汇表、装饰性英语标签或英文术语来冒充互译理解。"
            "不必翻译每一个普通词句，但所有影响理解和判断的关键语义都应解释清楚。"
            "完整论证不能被互译形式取代：充分说明与当前任务相关的依据、前提、假设、适用条件、"
            "依赖关系、推导中间环节、替代解释或反例、计算或逻辑核验、结论边界与剩余不确定性；"
            "不放过会改变结论的推理要点，不用提纲、结论或短小概括跳过必要分析。"
            "展开程度取决于问题复杂度和知识依据，相关论证充分后再结束；"
            "不规定固定字数、步骤、条数、编号、标签、语言比例或段落配对，"
            "不为拉长篇幅重复论点，不编造无关分支、证据或未执行的工具调用和验证。"
            "简洁等表达风格不能删去必要论证或选中关键词句的完整互译理解。"
            "在知识应用任务中，推理和答案直接运用相关知识、事实与条件自然解决问题，"
            "不要用‘从资料可以直接支持’、‘资料没有规定’等审阅私有资料的旁白代替推导，"
            "不要描述私有资料的投喂、检索、分块或挑选过程。"
            "需要限定结论时直接说明知识自身的适用条件和未知之处，"
            "不得通过复述后台生成、评审、提示词、语言设置或角色安排来解释表达方式。"
            "真实文章理解任务可按用户要求讨论文章和引用原文；"
            "知识应用中必要的公开研究名称与真实事实归属也应保留，不能伪装成个人经历。"
        )
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
                or set(config) - {"enabled", "style", "instruction", "answer_mode", "reasoning_language", "reasoning_depth"}):
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
        if "reasoning_depth" in config:
            try:
                normalized[stage]["reasoning_depth"] = validate_reasoning_depth(config["reasoning_depth"])
            except ValueError as error:
                raise ValueError("invalid_node_generation") from error
        if "answer_mode" in config:
            mode = config["answer_mode"]
            if (stage != "cot" or not isinstance(mode, str) or mode not in {"joint", "native_prefill"}
                    or (mode == "native_prefill" and not enabled)):
                raise ValueError("invalid_cot_answer_mode")
            normalized[stage]["answer_mode"] = mode
    return normalized


def style_for_sample(config: dict | None, sample_id: str, *, reasoning_language_version: int = 1,
                     reasoning_depth_version: int = 0) -> dict:
    """Select one stable style independent of concurrency, batching, and retries."""
    if not isinstance(sample_id, str):
        raise ValueError("invalid_generation_sample_id")
    if type(reasoning_depth_version) is not int or reasoning_depth_version not in {0, 1}:
        raise ValueError("invalid_reasoning_depth_contract_version")
    reasoning_language_instruction(version=reasoning_language_version)
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
            instruction, reasoning_language_instruction(language, version=reasoning_language_version)) if part)
    if reasoning_depth_version == 1 and node["enabled"] and "reasoning_depth" in node:
        depth = resolve_reasoning_depth(node["reasoning_depth"], sample_id, version=reasoning_depth_version)
        result["reasoning_depth"] = depth
        # Keep this dynamic choice outside the pinned style instruction: a
        # mixed node must not freeze every sample to the snapshot's depth.
        result["reasoning_depth_instruction"] = reasoning_depth_instruction(
            depth, version=reasoning_depth_version)
    return result
