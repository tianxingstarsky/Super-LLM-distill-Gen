"""Research-grounded defaults for context-complete training interactions."""
from dataclasses import replace


RESEARCH = (
    "Decontextualization (TACL 2021): https://aclanthology.org/2021.tacl-1.27/; "
    "CANARD (EMNLP 2019): https://aclanthology.org/D19-1605/; "
    "RAFT: https://arxiv.org/abs/2403.10131; "
    "Reflect, Not Reflex (EMNLP 2022): https://aclanthology.org/2022.emnlp-main.714/; "
    "CAMEL (NeurIPS 2023): https://arxiv.org/abs/2303.17760"
)

CONVERSATION_DESIGN = (
    "指导的是人在具体情境中如何交流和解决问题，不只是把资料改成定义、列举或解释题。"
    "先结合材料、用户需求及已完成互动，判断双方关系、各自目标与已有共同理解，再设计有用的语言行动。"
    "question字段表示第一条真实用户消息，可以是求助、陈述、请求、意见、反馈或带约束的任务，不必写成问句。"
    "必要时在dialogue_design中添加可选conversational_frame对象，字段为speaker_role、listener_role、relationship、setting、register，"
    "用开放文本描述谁在说话、希望对方承担什么职责、关系和情境、合适的表达方式；不需要填满每个字段。"
    "角色应服务沟通目的，例如学习者与导师澄清误解、同事共同排查问题、客户与服务人员明确需求、作者与编辑修改内容、"
    "合作方讨论分歧并确定方案。这些是启发，不是固定类别、抽样配额或必须轮流套用的模板。"
    "只有与任务有关且有依据的角色约束才有效；不凭角色补造用户经历、职业权限、事件、外部事实或工具能力。"
    "训练读者需要知道的关系、目标或情境必须自然出现在实际消息中，不能只藏在conversational_frame或guidance里。"
    "将材料用作解释、推断、比较、诊断、决策或共同制作的依据，围绕具体对象和条件设计真实的信息需求。"
    "同一批与history比较的是问题目的、信息缺口、推理路径及所需结果；只换人称、职业名、语气或同义词不构成新任务。"
    "已覆盖内容可以在确有新条件、新目标或新的上下文依赖时合理复用；没有价值的新互动时应结束或报告资料不足，不能凑数。"
    "多轮的下一行动应取决于真实messages里的回应、未解决问题和约束变化，不能照预写剧本自动提第二题。"
    "对方的解释可以引出应用，对方的澄清应改变后续条件，对方的修改应成为下一次反馈的对象。"
    "dialogue_state可以调整可选conversational_frame中的表达方式或关系判断，但未说出的假设不属于共同理解。"
    "任务已解决、自然结束或缺少进一步信息时停止；不要为填满轮数制造分歧、重复确认或突兀角色转换。"
)

ROLE_RESPONSE = (
    "根据实际用户消息、已经保存的对话、dialogue_design和当前conversational_frame选择合适的回应方式。"
    "角色控制沟通职责、语气和解释深度，不授权虚构身份经历、外部查证、权限或工具结果。"
    "任务可以是共同修改、诊断、比较、协商或处理反馈，不要一律输出百科解释或把用户陈述强行改成问答。"
    "必要情境以实际消息为准，隐藏角色设计不能代替训练输入；随真实回应调整，但保持已有事实和约束一致。"
)

GENERATION_CONTEXT = (
    "教师核验资料与训练读者实际收到的输入必须区分。teacher_context_pack 若提供，是已清洗资料包，"
    "与本次 source/source_context 的 source_context_pack.id 对应；先阅读相关材料及完整条件再设计或作答，"
    "不只使用孤立首段。资料不足时不得靠编造补足。"
    "资料包存在时，teacher_evidence或text中的短主片段只用于定位当前主题；"
    "逐字evidence_quotes、quotes和visible_context可以来自该资料包任一相关已清洗材料，不限于主片段。"
    "对无线索知识内化样本作去语境化：明确必要的实体、名称、版本、适用范围、条件和指代对象，"
    "保持原事实及限定，不把某个产品或实验的事实泛化为所有对象。"
    "条件、对象和任务不能只写在教师guidance中；首条用户消息须能独立理解。"
    "有资料的阅读理解或RAG样本须在实际用户消息中包含作答所需线索；教师私有资料和来源元数据不算可见线索。"
    "多轮可自然使用省略与指代，但只能依赖已保存的真实对话前文，不要求每一轮机械重述背景。"
    "直接推进用户任务，不以‘根据所给资料’、‘根据提供的文档’或‘According to the provided material’作为固定开场。"
    "问题明确要求引用或实际输入带资料时可以自然说明出处；这不是关键词禁用规则。"
    "来源证据放在quotes等核验元数据中，答案与显式解释只写完成任务所需内容，不叙述教师的内部检索或注入过程。"
)

CONTEXT_REVIEW = (
    "检查上下文完整性时，先把自己置于仅能看到learner_messages或candidate实际训练内容的读者位置。"
    "不得用教师原文、证据quotes、planned visible_context、guidance或设计状态补救训练输入缺失的对象、版本和必要条件。"
    "知识内化样本可以使用有来源支持的领域知识，但必须明确是在谈哪个对象，不能声称读者提供了实际未提供的资料。"
    "有资料样本的依据须确实在用户消息或工具记录中；多轮指代可由真实已保存的前文还原。"
    "判断的是语义和可恢复的上下文，不能凭‘根据’、‘文档’等字词机械淘汰合法的引用或文档讨论。"
    "缺少必要背景、答非所指或虚构可见资料时不通过，并指出需要补全的具体对象或条件；"
    "不能删掉套话后就认为问题已经修好。"
    "对角色与话术的检查以实际交流目的、情境、约束和信息进展为准；"
    "隐藏角色标签不能证明输入完整，不把只换称呼的重复解释当成有价值的对话推进。"
)

BLIND_REVIEW = (
    "assessment=learner_context_only 时，此次只评审实际learner_messages的上下文完整性，替代普通来源事实评分任务。"
    "source_evidence为空是有意隔离教师资料，不等于题目无法回答；不得因为无线索样本没有附来源就拒绝。"
    "qa_contract仅指定题型和回答策略，事实正确性由另一份来源评审负责。"
    "只根据实际消息判断对象、条件、指代、证据可见性和回应是否匹配；不宣称独立外部事实验证。"
    "仍返回此提示词既定JSON schema；仅当上下文实际完整且可理解才keep=true。"
)

SFT_VISIBLE_CONTEXT = (
    "普通文档SFT的输出可额外带visible_context字段，放入本题必需的清洗原文片段（多个片段用空行拼接）。"
    "不要重复塞入整份资料包，也不要只取一句缺少实体或条件的片段。"
    "问题和visible_context共同构成实际训练用户输入，须能支持本题；quotes须覆盖关键事实。"
    "visible_context只选原文，不在其中编造标题或补造事实；这是带资料样本。"
    "需要不附资料的知识内化样本时，由对话指导员选择closed_book并生成自包含用户任务。"
)


def context_version(previous, *, version, body):
    return replace(previous, version=version,
                   source=previous.source + "; " + RESEARCH,
                   template=previous.template + "\n" + body)
