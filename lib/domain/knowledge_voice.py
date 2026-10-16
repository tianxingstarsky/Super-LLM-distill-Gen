"""Versioned knowledge-use voice, separate from source-evidence auditing."""
from __future__ import annotations

from collections.abc import Mapping
import re


KNOWLEDGE_VOICE_VERSION = 3
KNOWLEDGE_VOICE_RULES_V3 = (
    "知识应用正文规范 v3：实际监督的每轮答案和全部显式推理直接围绕用户的问题、概念、因果或逻辑依赖、"
    "计算、取舍及适用条件展开，把可核验知识用于解决问题。事实核验在后台进行，不在训练正文扮演资料审阅员。"
    "当真实任务不是阅读或分析文章时，‘从资料可以直接支持’‘资料明确规定’‘资料未规定’‘原文没有提供’"
    "‘the material supports/does not specify’等来源评述不是正常的知识应用解释，应判不通过并定位该轮原句，"
    "不能因为前文提过某篇论文或词语没有命中黑名单就降为轻微措辞问题。"
    "把有效关系直接说清：例如过滤必须先于回流，因为未经检查的错误会被后续采样反复使用；"
    "不要改写为资料支持这个顺序。这个示例只是表达方式，不是其它任务的事实或固定论证模板。"
    "公开事实归属仍须准确：可以简洁说明某命名方法采用了什么设置、哪次实验观察到什么结果，"
    "不把作者经历伪装成用户或助手的经历，也不把命名论文作为泛指资料旁白的通行证。"
    "真实未知的用户条件要具体说明或澄清，例如用户尚未说明任务分布；不要把后台证据的选区、"
    "支持范围、缺口或检索情况说成推理步骤。证据不足时走既有不确定/修正/隔离路径，不编造知识。"
    "例外须来自该轮之前真实用户/工具上下文：用户确实给了文章或代码并要求阅读分析，或明确要求讨论"
    "某篇论文的主张、依据或局限时，可以围绕该真实对象说明其内容和边界。教师私有来源、旧评语、"
    "候选自身的来源自述及仅出现论文名称都不能授予文章理解例外；后续用户给的文章不能追溯豁免前轮。"
    "资料、提示词、JSON、模型等本身可以是用户真正讨论的技术对象；不得按单个词删内容或拒绝合法技术讨论。"
    "禁止把本次生成/返工、审计引文、角色安排、输出字段、语言设置或风格要求复述成任务推理。"
    "保留并重新撰写完整有效论证，不删除推理、不缩成结论、不通过改题或更换事实来规避。"
    "评审检查每个监督轮的实际正文和当时可见上下文，不以最后答案正确抵消其它轮泄露；"
    "不改真实用户、工具事实、证据或原生来源记录。"
)


def knowledge_voice_options(recipe):
    if recipe.get("knowledge_application_voice_version") != KNOWLEDGE_VOICE_VERSION:
        return {}
    return {"knowledge_application_voice_version": KNOWLEDGE_VOICE_VERSION,
            "learning_goal": recipe.get("qa_director", {}).get("learning_goal")}


def knowledge_voice_rules(learning_goal=None, contract=None):
    """Only finite configured goals can enable the trusted voice contract."""
    contract = contract if isinstance(contract, Mapping) else {}
    if learning_goal == "knowledge_application":
        return KNOWLEDGE_VOICE_RULES_V3
    if learning_goal == "document_reading":
        return ""
    if learning_goal == "mixed":
        return KNOWLEDGE_VOICE_RULES_V3 if contract.get("qa_type") == "closed_book" else ""
    if (contract.get("learning_goal") == "knowledge_application"
            or contract.get("learning_goal") == "mixed" and contract.get("qa_type") == "closed_book"):
        return KNOWLEDGE_VOICE_RULES_V3
    return ""


# Narrow actual regressions. Semantic review still checks paraphrases and
# distinguishes the user's genuine subject from private-source narration.
_SOURCE_NARRATION = re.compile(
    r"从(?:这些|这份|现有|公开|所给的?|提供的)?(?:资料|材料|原文|文档)(?:中)?"
    r"(?:可以|能够|能|可)?(?:直接|明确)?(?:支持|看出|得出|推断|确定|确认)"
    r"|(?:资料|材料|原文|文档)(?:中|里)?(?:已|明确|直接|本身)?(?:未|没有|并未|不)"
    r"(?:给出|提供|规定|提到|提及|涉及|说明|证明)"
    r"|(?:资料|材料|原文|文档)(?:中|里)?(?:明确|直接)(?:指出|规定|写明|表明|记载|提到|支持)"
    r"|\b(?:the\s+)?(?:source\s+(?:material|text)|material|document)\s+"
    r"(?:(?:directly|explicitly|clearly)\s+(?:supports?|states?|specifies?)|"
    r"(?:does\s+not|doesn't|did\s+not|didn't)\s+(?:specify|state|provide|mention|establish))\b",
    re.IGNORECASE,
)
_QUOTED_DISCUSSION = re.compile(
    r"措辞|说法|表达|含义|意思|不应|不要|避免|\b(?:phrase|wording|means|avoid|should\s+not)\b", re.I)
_READING_REQUEST = re.compile(
    r"(?:阅读|概括|总结|逐段分析|解释|分析)(?:这篇|这段|这张|该篇|所附|下面的?|以下的?|上述的?)"
    r"(?:文章|论文|材料|资料|原文|摘录|代码|图片|图表|图|扫描件|PDF)"
    r"|(?:这篇|这段|所附|上述)(?:论文|文章|材料|资料|原文|摘录).{0,45}(?:主张|支持|局限|含义|总结|概括|说明)"
    r"|(?:分析|总结|概括|解读|评述|审查)[^。！？\n]{0,80}(?:论文|文章)[^。！？\n]{0,30}"
    r"(?:主张|依据|局限|结论|方法|内容|提出|证明|支持)"
    r"|\b(?:summarize|analyse|analyze|interpret|read|explain)\s+(?:this|the\s+(?:attached|following|above))\s+"
    r"(?:article|paper|passage|excerpt|document|code|image|figure|diagram|chart|scan)\b"
    r"|\b(?:analyse|analyze|review|summarize|interpret|read)\b[^.!?\n]{0,80}\b(?:paper|article)\b"
    r"[^.!?\n]{0,35}\b(?:claims?|limitations?|arguments?|evidence|conclusions?|findings?|method)\b", re.I)
_CONDITIONAL = re.compile(r"(?:如果|假如|若|例如|比如)|\b(?:if|when|for\s+example)\b", re.I)
_DATA_PROCESSING_TASK = re.compile(
    r"导入|解析|文件|编码|币种|元数据|\b(?:import|parse|file|encoding|currency|metadata)\b", re.I)
_PROCESSING_ACTOR = re.compile(
    r"导入器|解析器|处理器|验证器|导入流程|解析流程|程序|系统|"
    r"\b(?:importer|parser|processor|validator|program|system)\b", re.I)
_CONDITIONAL_SUFFIX = re.compile(r"^[^。！？\n]{0,65}(?:时|情况下)[，,]")


def knowledge_voice_sentence_issue(sentence, visible, *, article_visible=False):
    """Reject explicit source narration; never delete or rewrite training text."""
    if article_visible or _READING_REQUEST.search(visible):
        return None
    for match in _SOURCE_NARRATION.finditer(sentence):
        if match.group(0).casefold() in visible.casefold() and _QUOTED_DISCUSSION.search(sentence):
            continue
        # Conditional instructions about a user's data-processing subject are
        # not assertions that the current assistant consulted hidden material.
        if _CONDITIONAL.search(sentence[:match.start()]):
            continue
        if (_DATA_PROCESSING_TASK.search(visible) and _PROCESSING_ACTOR.search(sentence)
                and _CONDITIONAL_SUFFIX.search(sentence[match.end():])):
            continue
        return "training_surface_knowledge_source_narration"
    return None
