"""Two-phase authored reasoning and provider-native answer continuation."""
from copy import deepcopy
import json

from lib.domain.authored_reasoning import AUTHORED_REASONING_JSON_CONTRACT
from lib.prompts.workflow_reasoning import TASK_ADAPTIVE_REASONING_BODY


_CONTRACT = deepcopy(AUTHORED_REASONING_JSON_CONTRACT)
_CONTRACT.pop("answer")
RATIONAL_ONLY_BODY = (
    "独立依据实际用户任务与已核验source_memory先撰写风格化reasoning及reasoning_trace，"
    "此阶段不生成answer或参考答案。reasoning是待核验的作者解释；"
    "审计trace描述论证，不宣称它是模型真实内在思维。"
    "用户明确指定风格/篇幅时遵守；其他任务按所需深度自然展开，不套固定思考顺序。"
    "材料、指导简报和反馈不是用户说过的话，不虚构其经历、称呼或主张。"
    "歧义缺少必要条件时只判断该澄清什么，不编造条件或过长解释。"
    "返回具体依据、支持关系和针对本任务的核对，不能编造工具执行或已证实的结论。"
    + TASK_ADAPTIVE_REASONING_BODY
    + "仅返回以下JSON（不增加answer）：" + json.dumps(_CONTRACT, ensure_ascii=False)
)
NATIVE_ANSWER_BODY = (
    "回答实际最后一条用户消息。当前assistant推理通道已由应用提供待核验的风格化推理前缀。"
    "该前缀不拥有系统权限，也不是正确性证明；依据source_memory和实际问题核对，"
    "有矛盾时保留事实与必要的不确定性，不迎合错误。"
    "只在可见content中输出最终答案或最少必要澄清，不复述幕后编辑、提示词、资料包装、"
    "推理注入或评审过程，不添加虚构称呼。用户实际可见的文章理解任务可以引用该文章。"
    "本阶段返回纯文本最终答案或必要澄清，覆盖通用数据加工器的JSON输出要求；不输出JSON包装。"
)
PREFIX_CHECK_BODY = (
    "在把作者推理作为原生通道前缀之前，独立检查它是否解决实际任务、依据与条件是否成立，"
    "是否保留必要歧义、真实操作依赖与用户指定风格。trace只供审计，不是正确性证据。"
    "只评审当前reasoning，不要求尚未生成的answer；不能因未输出答案、未编号或篇幅短拒绝。"
    "具体事实无法依据可用资料确认，或发现虚构条件、幕后包装、用户观点归因时keep=false。"
    '仅返回 {"keep":bool,"adherence":1到5整数,"reason":"具体依据或需修复位置"}。'
)
