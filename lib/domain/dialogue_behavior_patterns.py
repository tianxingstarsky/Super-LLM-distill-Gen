"""Composable dialogue design aids for a director, not a generation quota.

The catalogue is an engineering adaptation of dialogue-act, grounding and
conversation-analysis research. It is neither an ISO annotation implementation
nor an exhaustive account of human communication. Definitions are guidance for
editable prompts; their identifiers are not mandatory output labels.
"""
from __future__ import annotations

from copy import deepcopy


DIALOGUE_BEHAVIOR_GUIDE_VERSION = 1

_REFERENCES = (
    {
        "id": "iso_dialogue_acts",
        "title": "ISO 24617-2:2020 — Dialogue acts",
        "url": "https://www.iso.org/standard/76443.html",
        "author_paper_url": "https://aclanthology.org/2020.lrec-1.69/",
        "design_use": "将任务、反馈、轮次、话语组织与社会交往分开观察，允许组合及领域扩展。",
    },
    {
        "id": "clark_brennan_grounding",
        "title": "Clark & Brennan (1991), Grounding in Communication",
        "url": (
            "https://web.stanford.edu/~clark/1990s/Clark%2C%20H.H.%20_%20Brennan%2C%20"
            "S.E.%20_Grounding%20in%20communication_%201991.pdf"
        ),
        "design_use": "按当前目的更新共同理解；需要时确认理解，不为增加轮数机械追问。",
    },
    {
        "id": "sacks_schegloff_jefferson_turns",
        "title": "Sacks, Schegloff & Jefferson (1974), A Simplest Systematics for Turn-Taking",
        "url": (
            "https://www.conversationanalysis.org/schegloff-media-archive/"
            "simplest-systematics-for-turn-taking-language-1974/"
        ),
        "design_use": "每轮针对实际上一轮及接收者组织下一行动；文本模型不仿造真实录音时序。",
    },
    {
        "id": "schegloff_jefferson_sacks_repair",
        "title": "Schegloff, Jefferson & Sacks (1977), The Preference for Self-Correction in Repair",
        "url": (
            "https://www.conversationanalysis.org/schegloff-media-archive/"
            "preference-for-self-correction-in-repair-in-conversation-1977/"
        ),
        "design_use": "区分误解出现、澄清启动和修复完成；让纠正改变后续行动，不把修复等同责备。",
    },
)

_DIMENSIONS = (
    {
        "id": "task_action",
        "purpose": "用户正在获取信息、委托任务、分析问题、修改成果，还是建立联系？",
        "design_options": ["获取信息", "请求行动", "协作解决问题", "提出看法", "商议取舍"],
    },
    {
        "id": "knowledge_access",
        "purpose": "严格区分教师记忆、发问者可见信息与最终训练对话的共同基础。",
        "design_options": ["无线索", "完整可见线索", "部分线索", "多来源线索", "含无关线索"],
    },
    {
        "id": "grounding",
        "purpose": "记录双方实际知道、理解或尚未确定的内容，按任务需要确认。",
        "design_options": ["解释新概念", "确认理解", "明确未定条件", "指代消解"],
    },
    {
        "id": "repair",
        "purpose": "识别说法、理解或前提的问题，完成澄清或纠正后再推进。",
        "design_options": ["自我更正", "请求澄清", "澄清误解", "纠正错误前提"],
    },
    {
        "id": "sequence",
        "purpose": "下一轮承接实际回应，目标与约束可更新，不预写全部答案。",
        "design_options": ["初始行动", "承接追问", "补充条件", "修改请求", "自然结束"],
    },
    {
        "id": "relationship_register",
        "purpose": "按明确给定的角色、关系、场景及语言调整表达，不据身份猜测能力。",
        "design_options": ["同事协作", "服务沟通", "教学讨论", "非正式交谈", "专业简报"],
    },
    {
        "id": "feedback_social",
        "purpose": "回应理解、执行结果、情绪或交往需要；只在有作用时加入。",
        "design_options": ["理解反馈", "执行反馈", "致谢或致歉", "关切或鼓励", "礼貌拒绝"],
    },
    {
        "id": "discourse_form",
        "purpose": "表达可以是陈述、请求、建议或讨论；形式服务目标，不必写成疑问句。",
        "design_options": ["简洁行动请求", "间接求助", "举例讨论", "对比论证", "复述与改写"],
    },
)

# Examples describe an interaction, not a jointly generated question/answer pair.
# Factual support and context visibility remain the workflow's responsibility.
_PATTERNS = (
    {
        "id": "information_request",
        "dimensions": ["task_action", "knowledge_access"],
        "behavior": "请求解释、比较或事实信息，明确对象和范围。",
        "initiator_example": "这两种处理方式各适合什么情况？",
        "responder_action": "说明差异和适用条件；必要条件缺失时先澄清。",
    },
    {
        "id": "practical_request",
        "dimensions": ["task_action", "discourse_form"],
        "behavior": "把知识用于可执行成果，用户消息可以是委托或陈述需求。",
        "initiator_example": "我明天要向同事说明这个流程，帮我整理成一页操作卡。",
        "responder_action": "交付适合用途的成果，并保留必要条件和限制。",
    },
    {
        "id": "joint_problem_solving",
        "dimensions": ["task_action", "grounding", "sequence"],
        "behavior": "逐步明确目标、现状和约束，共同分析或排查。",
        "initiator_example": "我按前面的步骤试了，还是没达到预期。下一步先查什么？",
        "responder_action": "根据实际尝试调整假设，选择有信息增益的下一行动。",
    },
    {
        "id": "hypothesis_comparison",
        "dimensions": ["task_action", "grounding"],
        "behavior": "比较竞争解释，区分已知事实、假设和待核实信息。",
        "initiator_example": "有没有可能是条件变了，而不是方法本身出错？",
        "responder_action": "比较可支持的解释及检验办法，不把可能性当结论。",
    },
    {
        "id": "recommendation_tradeoff",
        "dimensions": ["task_action", "relationship_register"],
        "behavior": "依据明示的目标和约束提出建议，说明适用边界。",
        "initiator_example": "我们时间很紧，先做哪一步能减少返工？",
        "responder_action": "解释取舍、前提和代价；不替用户编造偏好。",
    },
    {
        "id": "negotiate_constraints",
        "dimensions": ["task_action", "sequence", "feedback_social"],
        "behavior": "商议无法同时满足的要求，提出可接受的替代方案。",
        "initiator_example": "我想保留全部内容，但展示只能用两分钟。我们怎么取舍？",
        "responder_action": "指出冲突，提出选项并邀请按真实优先级决定。",
    },
    {
        "id": "clarify_reference",
        "dimensions": ["grounding", "repair"],
        "behavior": "处理指代或范围不清，只问会影响下一行动的关键问题。",
        "initiator_example": "你说的那个条件，是第一种方案的条件吗？",
        "responder_action": "明确所指对象，补充必要信息后继续原目标。",
    },
    {
        "id": "check_understanding",
        "dimensions": ["grounding", "feedback_social"],
        "behavior": "复述关键理解并确认，用于检验而非机械重复。",
        "initiator_example": "我理解成先处理输入，再检查结果，这样对吗？",
        "responder_action": "确认正确部分并精确修正差异，不泛泛附和。",
    },
    {
        "id": "repair_misunderstanding",
        "dimensions": ["repair", "sequence", "grounding"],
        "behavior": "指出误解来源，让修正真实改变共同理解与后续成果。",
        "initiator_example": "我刚才说的是检查结果，不是输入内容。",
        "responder_action": "承认并更新所指内容，重新处理受影响的步骤。",
    },
    {
        "id": "self_correction",
        "dimensions": ["repair", "sequence"],
        "behavior": "发言者更正自己的措辞、条件或信息，后续使用新版本。",
        "initiator_example": "我说错了，限制是每批处理量，不是总处理量。",
        "responder_action": "更新约束，说明哪些结论仍有效、哪些需要修改。",
    },
    {
        "id": "challenge_premise",
        "dimensions": ["task_action", "repair"],
        "behavior": "对有问题的前提、论证或结论提出依据明确的质疑。",
        "initiator_example": "这个结论是不是还缺少一个必要条件？",
        "responder_action": "检查前提和证据，承认可纠正部分并给出条件化结论。",
    },
    {
        "id": "continue_from_result",
        "dimensions": ["sequence", "grounding"],
        "behavior": "从实际回答或产物中引出新的有用行动，不照预写脚本追问。",
        "initiator_example": "你刚才列的第二项，能改成适合新人执行的一句话吗？",
        "responder_action": "准确承接上一轮对象和格式，只修改所需部分。",
    },
    {
        "id": "revise_goal_or_constraint",
        "dimensions": ["sequence", "task_action"],
        "behavior": "用户补充或改变目标和限制，保持变更前后的一致性。",
        "initiator_example": "再补一个条件：这次需要离线完成。",
        "responder_action": "识别变更对方案的影响，保留仍适用的部分并调整其余部分。",
    },
    {
        "id": "adapt_explanation",
        "dimensions": ["relationship_register", "grounding", "discourse_form"],
        "behavior": "按用户明示的理解、用途或表达偏好调整说明。",
        "initiator_example": "这一段我没跟上，换个具体例子说明好吗？",
        "responder_action": "用可核实的例子或不同表述解释，避免仅重复原话。",
    },
    {
        "id": "report_attempt_or_outcome",
        "dimensions": ["feedback_social", "sequence", "task_action"],
        "behavior": "陈述执行结果或经验，供双方决定继续、修复或结束。",
        "initiator_example": "按这个顺序做完了，前两步正常，最后一步还是有问题。",
        "responder_action": "回应真实的结果差异，优先处理剩余问题。",
    },
    {
        "id": "acknowledge_or_support",
        "dimensions": ["feedback_social", "relationship_register"],
        "behavior": "在场景需要时致谢、致歉、表达关切或鼓励，不编造亲密关系。",
        "initiator_example": "这样解释我明白了，之前一直卡在这里。",
        "responder_action": "自然回应理解或情绪，按需要结束或提供下一步。",
    },
    {
        "id": "boundary_or_alternative",
        "dimensions": ["task_action", "feedback_social", "grounding"],
        "behavior": "说明事实、能力或条件边界，提供可行替代；可用于不充分线索。",
        "initiator_example": "只知道这些条件，现在能直接确定结果吗？",
        "responder_action": "区分可以确定与尚不能确定的内容，给出所需信息或替代路径。",
    },
    {
        "id": "close_completed_interaction",
        "dimensions": ["sequence", "feedback_social"],
        "behavior": "目标已满足时自然结束，保留必要的待办或限制，不凑轮数。",
        "initiator_example": "这就够了，我按这份版本去做。",
        "responder_action": "简短确认，可总结真正必要的下一步，不另造新问题。",
    },
)

_ROLE_BOUNDARIES = {
    "director": {
        "responsibility": "观察可用资料、目标、历史与结果，选行为组合及可支持的资料范围。",
        "source_access": "内部资料记忆和经过验证的行号选区。",
        "output_boundary": "给任务设计、发问指导或可见线索；不要在无线索发问指导中放答案事实。",
    },
    "grounded_initiator": {
        "responsibility": "按指导员给出的行为、角色与明确可见线索发出用户行动。",
        "source_access": "只有计划声明为最终用户可见的线索，不自动获得全部内部记忆。",
        "output_boundary": "发出一方的话，不同时设计并预写回答。",
    },
    "blind_initiator": {
        "responsibility": "从不含资料事实的场景、目标、语言行为指导中独立发问或提出请求。",
        "source_access": "没有隐藏资料、资料摘要、行号、答案或教师推理；续轮只接收真实可见对话。",
        "output_boundary": "不声称看过资料，不使用只可能来自隐藏资料的具体数字、定义或结论。",
    },
    "responder": {
        "responsibility": "接收实际发来的用户行动、可见对话及内部资料记忆后作答或推进任务。",
        "source_access": "经过验证的原文工具记忆与实际用户可见内容分开提供。",
        "output_boundary": "回答可以应用教师知识，但不假装用户也看过隐藏资料，不披露内部包装。",
    },
    "reviewer": {
        "responsibility": "检查事实、行为目标、信息边界、轮次承接与新增价值，判断继续或结束。",
        "source_access": "独立的选区回执、实际对话与任务契约。",
        "output_boundary": "发现资料或场景已耗尽时报告停止理由，不用低质量换词补数量。",
    },
}


def dialogue_behavior_guide() -> dict:
    """Return bounded JSON guidance with no source bodies or mandatory quotas.

    Callers may place this guide in the director's task context and let user
    templates select, combine, extend or omit patterns. This function does not
    select model API message roles or expose source memory to other actors.
    """
    return {
        "version": DIALOGUE_BEHAVIOR_GUIDE_VERSION,
        "catalog_type": "composable_design_aids",
        "exhaustive": False,
        "patterns_are_mandatory": False,
        "fixed_behavior_quotas": False,
        "template_control": "用户模板可选择、组合、增补或不采用这些行为；标识不是必填输出标签。",
        "principles": [
            "设计真实有用的交往行动，问题不必是疑问句，回答不必是百科说明。",
            "一次互动只使用有作用的行为组合；这些维度是工程设计词汇，不是完整 ISO 注释规范。",
            "原始教师资料不是对话双方自动共有的信息，不能泄漏给无线索发问者。",
            "角色、关系、语域按明示场景设置，不用年龄、性别、国籍等人口特征推断能力或性格。",
            "连续对话逐轮看真实结果；新增条件、澄清或纠正要改变后续行动。",
            "名称、例句和研究出处仅是指导信息，不是事实资料、最终问题或训练答案。",
        ],
        "dimensions": deepcopy(list(_DIMENSIONS)),
        "patterns": deepcopy(list(_PATTERNS)),
        "role_boundaries": deepcopy(_ROLE_BOUNDARIES),
        "progress_observations": [
            "目前已达到的互动目标和仍待解决的问题。",
            "哪些内容为双方实际可见或已确认，哪些仍是不确定的假设。",
            "这轮新增了什么有用知识、任务结果、澄清或约束变化。",
            "已有样本覆盖了哪些有实质区别的行为与场景；只换措辞不算新增价值。",
        ],
        "stop_guidance": [
            "目标完成、资料无法支持新的有用场景或持续只能得到重复低质样本时停止。",
            "请求样本量是容量上限或意愿，不是必须填满的产量；允许更少的合格样本。",
            "容量尚未用满不构成继续的理由；明确真实停止原因和已完成覆盖范围。",
        ],
        "references": deepcopy(list(_REFERENCES)),
    }
