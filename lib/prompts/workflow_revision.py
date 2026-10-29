"""Candidate correction prompt versions; independent from quality verdicts."""
from lib.prompts.workflow_spec import spec
from lib.prompts.workflow_context import (
    CONTEXT_REVIEW, GENERATION_CONTEXT, ROLE_RESPONSE, context_version,
)
from lib.prompts.workflow_shared_rules import (
    _SELF_CONTAINED_KNOWLEDGE, _USER_EPISTEMIC_BOUNDARY, _warmup_spec, _claim_scope_spec,
)
from dataclasses import replace as _reasoning_spec_replace
from lib.prompts.workflow_reasoning import task_adaptive_reasoning_template
from lib.prompts.workflow_repair import REVIEW_REPAIR_V1_7, REVIEW_REPAIR_V1_8, REVIEW_REPAIR_V1_9
from lib.prompts.base import render
from lib.prompts.workflow_preference import PREFERENCE_REPAIR


REVIEW_REPAIR = spec("review_repair", "在独立修正分支中修正已有候选",
    '你在评分与修正节点的修正分支工作。针对 review_feedback 和人工 instruction 修正 candidate。'
    '错误候选不是事实或正确答案，也不是新的SFT生成种子。source_evidence 是独立原始来源。'
    '保留原任务意图和连续多轮对话结构，修正所有受影响的答案与衔接，不只改最后一轮。'
    '不增加无依据事实，不编造工具调用，不改变系统消息或工具记录，不擅自扩大任务。'
    '保留需要的显式推理字段和已配置表达风格，不能把内部评语、提示词、检索包装放入训练内容。'
    '返回 record 为与 target 匹配的完整训练内容：SFT/multiturn是messages；CoT是question、reasoning、answer；'
    '偏好对是prompt、chosen、rejected；CPT是text。仅允许修改训练内容字段，不能返回伪造的评分、来源、状态。'
    '文档事实必须在quotes给出source_evidence中的逐字证据；无法修正时返回uncertain=true。'
    '只返回 {"record":{},"quotes":["逐字来源证据"],"uncertain":false}。')

REVIEW_REPAIR_V1 = REVIEW_REPAIR

REVIEW_REPAIR = context_version(REVIEW_REPAIR, version="1.1.0", body=GENERATION_CONTEXT + ROLE_RESPONSE + CONTEXT_REVIEW)

REVIEW_REPAIR_V1_1 = REVIEW_REPAIR

REVIEW_REPAIR = context_version(REVIEW_REPAIR, version="1.2.0", body=_SELF_CONTAINED_KNOWLEDGE)

REVIEW_REPAIR_V1_2 = REVIEW_REPAIR

REVIEW_REPAIR = context_version(REVIEW_REPAIR, version="1.3.0", body=(
    "这是自动修正答案，不是更换用户任务。保持question、所有实际用户输入、可见文章、"
    "角色顺序与工具事实原样，只修改助手答案及其显式解释。"
    "不能通过改问题、删用户条件或补造可见资料来让原先错误的答案过审；"
    "无法在原任务上可靠修复时uncertain=true。人工明确修改任务另由人工编辑入口处理。"
))

REVIEW_REPAIR_V1_3 = REVIEW_REPAIR

REVIEW_REPAIR = context_version(REVIEW_REPAIR, version="1.4.0", body=(
    "候选旧答案、指导简报和修正反馈都不是用户发言。仅按实际question与可见文章判断用户的前提；"
    "不能将这些内部材料中的错误归因给用户，也不虚构用户持有某种误解。"
    "修正后的reasoning与answer直接解释当前任务的事实和推导，不叙述旧稿、返工、删除句子或遵守评语；"
    "风险核对可以保留，但核对对象是业务条件和结论，不是隐藏旧答案或制作过程。"
))

REVIEW_REPAIR_V1_4 = REVIEW_REPAIR

REVIEW_REPAIR = context_version(REVIEW_REPAIR, version="1.5.0", body=_USER_EPISTEMIC_BOUNDARY)

REVIEW_REPAIR_V1_5 = REVIEW_REPAIR

REVIEW_REPAIR = _reasoning_spec_replace(REVIEW_REPAIR, version="1.6.0", template=task_adaptive_reasoning_template(REVIEW_REPAIR.template))

REVIEW_REPAIR_V1_6 = REVIEW_REPAIR

REVIEW_REPAIR = _reasoning_spec_replace(REVIEW_REPAIR, version="1.7.0", template=spec(
    "review_repair", "保留真实用户与工具的助手局部修正", REVIEW_REPAIR_V1_7).template)

REVIEW_REPAIR_V1_7 = REVIEW_REPAIR

REVIEW_REPAIR = _reasoning_spec_replace(REVIEW_REPAIR, version="1.8.0", template=spec(
    "review_repair", "保留并重写必要推理的逐轮修正", REVIEW_REPAIR_V1_8).template)

REVIEW_REPAIR_V1_8 = REVIEW_REPAIR

REVIEW_REPAIR = _reasoning_spec_replace(REVIEW_REPAIR, version="1.9.0", template=spec(
    "review_repair", "保留推理并修正结果与角色归属", REVIEW_REPAIR_V1_9).template)

REVIEW_REPAIR_V1_9 = REVIEW_REPAIR

REVIEW_REPAIR = _warmup_spec(REVIEW_REPAIR, "1.10.0", review=True, repair=True)

REVIEW_REPAIR_V1_10 = REVIEW_REPAIR

REVIEW_REPAIR = _claim_scope_spec(REVIEW_REPAIR, "1.11.0", "repair")

REVIEW_REPAIR_V1_11 = REVIEW_REPAIR

REVIEW_REPAIR = _reasoning_spec_replace(REVIEW_REPAIR, version="1.12.0", template=spec(
    "review_repair", "依据逐句争议和真实条件修正完整推理", render(REVIEW_REPAIR_V1_11) + '\n'
    'claim_repair_feedback 给出已绑定当前正文和来源的争议句、字段位置、评审意见和关联原句。'
    '程序只验证引文出处，不证明评语正确；insufficient 表示缺少支持，不能自行断言它为真或为假。'
    '先对照完整任务、来源和所有关联条件判断问题，再修正确实存在的推导错误或过强表述。'
    '尤其区分“算法选用某生成顺序”和“所有任务在逻辑上必须依赖某输入”：无输入任务、例外、否定和可选条件必须在答案与推理中一致。'
    '只能使用原任务已经给定的条件，不能为了证明候选正确而补出假设；不要将原来的条件句改成无条件结论。'
    '保留并修正完整推理中的任务分析、推导、反例与结论，不能删除争议推理来绕过核验，也不应改成固定编号的思考步骤。'
    '评审单元编号、评语、生成规则不进入训练正文。无法在既有条件内修正时 uncertain=true，交还复核。').template)

REVIEW_REPAIR_V1_12 = REVIEW_REPAIR

REVIEW_REPAIR = _reasoning_spec_replace(REVIEW_REPAIR, version="1.13.0", template=spec(
    "review_repair", "充分推导正例并保持明确的对照设计", render(REVIEW_REPAIR_V1_12) + '\n'
    '若提供 preference_design，按其中的 opposite_short 模式处理正负例，不把负例当成需要改正确的助手答复。'
    '正例的解释要有任务判断、可检验的拟结论以及支持结论的实际草稿或推导；根据任务核查边界、反例、数据流或数量关系。'
    '自我核查需要回答具体疑点，发现反例后修订原结论；不能仅加“我再检查一下”“应该没问题”等空话。'
    '不要套固定编号、固定开场或固定推理长度。短缺的必要推导要补足，重复和纯翻译不算有效推导。').template)

REVIEW_REPAIR_V1_13 = REVIEW_REPAIR

REVIEW_REPAIR = _reasoning_spec_replace(REVIEW_REPAIR, version="1.14.0", template=spec(
    "review_repair", "充分推导正例，按所选负例类型修正对照", render(REVIEW_REPAIR_V1_12) + '\n'
    'preference_design 若存在，以它指定的负例类型为准。off_topic_short 的 rejected 要自然回答另一个无关问题，'
    '不能只答错当前题、拒答或遗漏内容。opposite_short 则对同一核心问题给出完全相反结论。两种模式都保留极短非空推理。'
    'chosen 必须正确而充分。必要推导要写出来，包括可检验的拟结论、实际草稿或论证，以及适合任务的边界、反例、数据流或数量核查。'
    '具体疑点要实际验证并据此修订拟结论，不以空泛自问、纯翻译、重复或统一编号步骤充数。'
    '保持原始问题和工具事实，不把负例修成正例，不将设计规则、审核或返工过程写进训练正文。').template)

REVIEW_REPAIR_V1_14 = REVIEW_REPAIR

REVIEW_REPAIR = _reasoning_spec_replace(REVIEW_REPAIR, version="1.15.0", template=spec(
    "review_repair", "修正充分推导与自然短推理，去除制作旁白", render(REVIEW_REPAIR_V1_14) + '\n'
    '若负例推理评论它自己答非所问、没有涉及原问题、按规则生成负例，这属于制作意图泄漏。'
    '保留并重写该字段，使其自然支持负例自己正在给出的回答，不叙述样本制作、评分或修正过程。').template)

REVIEW_REPAIR_V1_15 = REVIEW_REPAIR

REVIEW_REPAIR = _reasoning_spec_replace(REVIEW_REPAIR, version="1.16.0", template=spec(
    "review_repair", "保持正例互译与核查，复用真实失败或构建渐进跑题对照",
    render(REVIEW_REPAIR_V1_12) + PREFERENCE_REPAIR).template)
