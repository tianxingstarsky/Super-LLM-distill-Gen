"""Soft, editable expression guidance, independent of evidence visibility.

Shortness and ambiguity are not malformed tasks. The director can use these
design aids when the source, interaction goal and actual visible turns support
them. Coverage labels are editorial annotations, not a linguistic classifier.
"""
from __future__ import annotations

from copy import deepcopy


EXPRESSION_GUIDE_VERSION = 1
EXPRESSION_FIELDS = ("form", "clarity", "context_dependency", "addressing", "roleplay_basis")

_PROFILES = (
    {"id": "ultra_short", "use": "对象明确时使用几个字的自然提问或请求，不补成完整场景。",
     "example": "怎么用？", "context_requirement": "前文或可见题面已有明确对象；首轮也可直接提概念。"},
    {"id": "ambiguous", "use": "保留真实的不明确条件，练习有价值的澄清，不替用户补全背景。",
     "example": "怎么又不行了", "context_requirement": "不声称具体故障已确定；答方先问最少必要信息。"},
    {"id": "ordinary", "use": "自然问问题、委托或讨论，不必职业化，也不必制造事故。",
     "example": "这两个有什么区别？", "context_requirement": "比较对象由本条消息或实际可见上下文确定。"},
    {"id": "elliptical_followup", "use": "在多轮中使用省略、指代和局部修改，承接实际上一轮。",
     "example": "那第二个呢？", "context_requirement": "实际前文明确列出对象；不能指向隐藏资料或预写答案。"},
    {"id": "detailed_request", "use": "任务确需条件时给充分信息，避免所有样本都成冗长场景题。",
     "example": "只保留结论和必要条件，帮我改成一段话。", "context_requirement": "条件属于用户真实可见任务，不捏造个人经历。"},
    {"id": "explicit_roleplay", "use": "只有明示的角色扮演、对话写作或称呼需求才设计人物关系。",
     "example": "我们演练一次面试，你当面试官。", "context_requirement": "角色扮演由可见任务或用户要求明确授权；不把虚构关系当现实。"},
)


NATURAL_EXPRESSION_DESIGN = (
    "自然表达覆盖：证据可见类型与措辞形式是不同维度，无线索不等于长场景，有线索也可以只问几个字。"
    "普通用户默认直接与你交谈，无需称呼；不要例行添加老周、老张、某同事、师傅、客户或虚构熟人。"
    "称呼本身并非禁止；只有实际可见语境、用户配置或明确角色扮演支持时才保留。"
    "同时考虑超短请求、普通提问、真实模糊求助、陈述或委托、连续对话的省略指代与必要的详细任务；"
    "按目标和已有覆盖选择，不规定比例，不把目录或例句照抄成题目，不为覆盖制造低价值任务。"
    "不完整条件可以是有用的训练任务：answer_policy=clarify时允许在首轮保留歧义，"
    "不要为了自包含擅自注入隐藏资料或把用户的几个字扩成职业事故。"
    "超短不等于无信息，模糊不等于故意猜谜；澄清只问影响下一行动的关键条件，"
    "并在真实多轮中等待澄清回答，不预设答案事实。指代只依赖实际可见前文。"
    "可选expression_design只记录当前表达设计：form、clarity、context_dependency、addressing、"
    "roleplay_basis均为简短开放文本，允许用户扩展或不填写，不是固定标签、强制行为或事实证据。"
    "在批次tasks的有效任务中可携带expression_design；独立发问的question_brief中也可携带相同设计。"
    "next_turn依据实际最新回应重新选择表达，不机械继承首轮长度。"
    "natural_expression_guidance和表达覆盖计数只作设计参考；用户question_rules优先决定目标语体，"
    "资料无法支持新价值时仍应停止。评审不要因消息短、没有称呼或真实条件未明就拒绝；"
    "检查当前答案是否按实际信息采取正常回答、最少澄清或条件化回应。"
)


def natural_expression_guide():
    """Return an independent bounded supplement without changing old guides."""
    return {
        "version": EXPRESSION_GUIDE_VERSION,
        "fixed_quotas": False,
        "labels_are_optional": True,
        "coverage_is_editorial_annotation": True,
        "configuration": "使用节点的question_rules与自定义提示词决定语体、称呼和角色扮演要求。",
        "default_addressing": "无需称呼，直接与助手交谈。",
        "profiles": deepcopy(list(_PROFILES)),
        "scheduling": "参考当前批次、已接受历史及自然表达覆盖，优先有实际新价值的未充分覆盖活动；不填固定比例。",
        "ambiguous_tasks": "问题缺少必要条件时保留缺口并澄清，不能用隐藏记忆虚构用户的意图或经历。",
    }


def compact_expression_guide(guide):
    """Shed optional examples, never remove expression modes or task rules.

    The caller applies this only to a new batch snapshot before persistence.
    Source facts and accepted history remain in their own durable stores.
    """
    for field in ("example", "context_requirement"):
        for profile in reversed(guide.get("profiles", [])):
            if field in profile:
                profile.pop(field)
                return True
    return False


def validate_expression_design(value):
    """Validate open editorial text only; do not infer an utterance's meaning."""
    if not isinstance(value, dict) or not value or set(value) - set(EXPRESSION_FIELDS):
        raise ValueError("invalid_expression_design")
    result = {}
    for field, text in value.items():
        if not isinstance(text, str) or not text.strip() or len(text) > 256 or "\x00" in text:
            raise ValueError("invalid_expression_design")
        result[field] = text.strip()
    return result
