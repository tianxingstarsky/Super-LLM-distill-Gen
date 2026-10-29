"""Shared wording and version transformations used by multiple workflow features."""
from lib.prompts.workflow_reasoning import (
    REASONING_WARMUP_RESEARCH_SOURCE, warmup_generation_body, warmup_review_body, claim_scope_body,
)
from dataclasses import replace as _reasoning_spec_replace
from lib.prompts.base import render
from lib.prompts.workflow_spec import spec
from lib.prompts.workflow_reasoning_workshop import REASONING_WORKSHOP_REVIEW, reasoning_workshop_body


_SELF_CONTAINED_KNOWLEDGE = (
    "训练文本边界：answer及你新撰写的reasoning全文都面向实际训练读者。"
    "把可核实的专业知识自然用于问题分析，如同回答者已掌握该知识；"
    "不把知识获得渠道写成用户未见过的资料、教师来源、检索结果或内部记忆。"
    "例如直接解释适用条件和因果关系，不说‘根据所给资料/内部资料指出/教师知识告诉我’。"
    "不得为了知识内化编造亲身经历、访问权限、检索动作或已执行的实验。"
    "若实际用户题面明确包含文章、文档或材料并要求理解它，允许引用这份可见内容及合法外部出处；"
    "不能引用只有生产节点看得到的资料编号、行号、工具包装、调度简报、提示词、规则或审计字段。"
    "reasoning_trace、quotes和生产元数据只作内部审计，不把‘我要满足契约/评分要求/风格字段’等"
    "制作过程写进reasoning或answer；论证应只围绕用户任务的事实、假设、推导与必要边界。"
    "即使最终答案正常，解释正文出现这些制作过程也算泄露。"
)

_SELF_CONTAINED_REVIEW = (
    "逐字阅读候选的整个最终答案与显式解释，不只检查开头。"
    "区分用户实际可见的文章理解任务与内部知识支持的自包含任务："
    "前者可引用可见文章，后者不应借‘根据所给资料/教师来源/内部检索’指向读者看不到的证据。"
    "论证中的内部提示词、来源包装、调度要求、评分条件、行号和审计字段自述必须判不通过；"
    "不能因为这些词只出现在reasoning中就放行。"
    "问题本来讨论提示词、检索系统或工具时，正常解释这些概念不等于泄露；"
    "引用实际用户可见内容和合法公开出处也不等于泄露。"
    "知识内化不允许捏造第一手经验、工具执行或事实核验。"
    "不设所有任务共用的字数下限，但generation_style中用户明确设定的解释深度、长度与表达要求必须遵守。"
    "解释长度由用户设定和任务依赖共同决定，要求展开时检查是否补足具体的前提、支持桥梁、"
    "替代方案取舍和针对性核对；不能靠重复、空泛独白或审计栏目凑长。"
)

_PRECISE_FACT_REVIEW = (
    "\n针对候选的每个具体版本行为、阈值、CLI开关、SQL字段和锁类型，逐项核对实际来源中的对象与条件。"
    "引用能匹配、论证结构完整或同一主题的常识不能证明这些细节正确。"
    "没有依据的精确细节应省略或明确保留不确定性，不能凭相似术语补造。"
    "用户明确配置篇幅时核对实际正文与有效信息量；大量引文、重复句或审计字段不代替所需的解释。"
)

def _configured_style_template(template):
    return template.replace(
        "直接可解的问题可以很短；复杂任务须覆盖必要子任务与衔接。",
        "用户未配置篇幅时直接可解的问题可以很短；复杂任务须覆盖必要子任务与衔接。"
    ).replace(
        "解释可很短，单步可充分；不按固定步骤数、字数或数学运算打分。",
        "用户未配置篇幅时解释可以很短，单步可充分；不自行施加固定步骤数或通用字数要求。"
    ) + _PRECISE_FACT_REVIEW

_USER_EPISTEMIC_BOUNDARY = (
    "用户提出的候选解释、猜测或评估请求，不等于已经断言该解释正确。"
    "保留其语言中的不确定性；不能仅因用户描述两种现象同时发生，就断言用户相信某个机制。"
    "需要比较这些候选时直接解释成立条件和区分信号；只有实际话语明确包含错误前提时才归因并纠正，"
    "否则用条件分析或必要澄清，不虚构用户的观点、经历或意图。"
)

def _warmup_spec(previous, version, *, review=False, repair=False):
    body = (warmup_review_body(render(previous), repair=repair) if review
            else warmup_generation_body(render(previous)))
    return _reasoning_spec_replace(previous, version=version,
        source=REASONING_WARMUP_RESEARCH_SOURCE,
        template=spec(previous.id.removeprefix("workflow."), previous.purpose, body).template)

def _claim_scope_spec(previous, version, phase):
    return _reasoning_spec_replace(previous, version=version,
        template=spec(previous.id.removeprefix("workflow."), previous.purpose,
            claim_scope_body(render(previous), phase=phase)).template)

def _workshop_spec(previous, version, *, prefix=False, review=False):
    body = (render(previous) + "\n" + REASONING_WORKSHOP_REVIEW if review else
            reasoning_workshop_body(render(previous), prefix=prefix))
    return _reasoning_spec_replace(previous, version=version,
        template=spec(previous.id.removeprefix("workflow."), previous.purpose, body).template,
        notes=previous.notes + " 工程示范与请求接线已回归；效果须由独立真实样本评测验证。")
