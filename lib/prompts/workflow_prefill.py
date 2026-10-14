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


# Complete new phase contracts rather than adding another corrective tail to
# historical templates. Callers register these bodies as new PromptSpec
# versions and keep the old bodies above addressable for resumed runs.
RATIONAL_ONLY_BODY_V1_2 = (
    "你负责准备一份待核验的作者解释，让下一阶段能够回应实际任务；本阶段不输出最终answer。"
    "真实learner角色消息界定当前目标、公开前文与用户已确认的条件。source_memory提供私有资料，"
    "reference_dialogue、旧答案和反馈提供编辑参考；记录完整或已有模型评分并不证明事实正确。"
    "先把实际任务需要的关系与可用依据联系起来，保留已知、假设和信息缺口的区别。"
    "参考中提到某个领域，可以解释该领域下的条件性含义，但不能据此断言用户已选定该领域；"
    "例如只见‘CPT？’时，可分析‘如果指模型训练中的CPT’的含义，而不说当前对话已经定义CPT。"
    "真实前文已经明确领域时直接使用它，避免无必要地罗列其他缩写或重复澄清。"
    "作者解释按任务依赖与用户设定的风格、深度组织，复杂问题展开必要支持关系，"
    "简短消息也可以需要深入解释；长度不由问句字数或固定步骤数决定。"
    "歧义影响结论时指出决定性的缺口及可支持的条件分析，最终回复怎样提问由下一阶段完成。"
    "reasoning呈现业务论证，reasoning_trace记录具体依据、支持关系、针对性核对和必要限制。"
    "审计不是正文模板，也不是模型真实内在思考的证明。"
    + TASK_ADAPTIVE_REASONING_BODY
    + "仅返回以下JSON，不增加answer：" + json.dumps(_CONTRACT, ensure_ascii=False)
)
NATIVE_ANSWER_BODY_V1_2 = (
    "你负责交付实际最后一条learner用户消息的最终回复。应用通过原生API提供了作者解释前缀，"
    "它属于推理通道输入，不是你自发产生的完整内部思考，也没有系统权限或事实认证效力。"
    "以真实对话确定用户意图与已知条件，按可用资料核对前缀，并保留需要的不确定性。"
    "私有参考记录中的旧助手回答不是用户已见前文；未确认的领域可以作条件解释，"
    "例如‘如果你指模型训练中的CPT…’，随后视需要确认语境，不假称双方已讨论过。"
    "有足够条件就直接完成任务，需要更多信息就提出能推进任务的必要澄清。"
    "表达深度与篇幅服从用户设置及实际任务，不强迫短答案，不复制前缀作第二份解释。"
    "仅在content中交付最终回复本身；本阶段使用纯文本回复，覆盖通用加工器的JSON约定。"
    "不增加answer、reasoning、reasoning_trace等制作契约包装，"
    "不转述资料传输、前缀注入、编辑反馈或评分过程；实际文章理解任务仍可引用用户可见文章。"
)
PREFIX_CHECK_BODY_V1_2 = (
    "独立核验待作为原生API推理前缀的作者解释。此时最终答案尚未生成，"
    "只评当前reasoning及其审计与实际任务的对应关系，不因没有answer拒绝。"
    "以真实learner消息判断对话语境，私有source.messages、reference_dialogue、旧答案及反馈"
    "不属于该对话，已有模型评分不能替代事实依据。"
    "检查具体前提的来源与可靠性、支持关系、必要条件和任务匹配；"
    "参考答案独自确认用户领域、观点或已见内容，虚构事实与执行结果，关键依赖失效时keep=false。"
    "未明确领域时作条件解释是合法回应准备，不应强行只澄清，也不能凭参考答消除实际歧义。"
    "reasoning是训练正文，trace是私有审计。trace出现工具名或来源标识本身不是正文泄露；"
    "但trace虚构用户话语、资料内容或把反馈当作事实证明仍须拒绝。"
    "正文泄露的判断须指出实际reasoning中的内容，不能用审计字段冒充正文。"
    "按任务真实依赖与用户风格判断充分性，不要求固定步骤、编号或通用字数下限。"
    '仅返回 {"keep":bool,"adherence":1到5整数,"reason":"具体依据或需修复位置"}。'
)


PREFIX_CHECK_BODY_V1_3 = (
    "独立核验待作为原生API推理前缀的作者解释。此时最终答案尚未生成，"
    "只评当前reasoning及其审计与实际任务的对应关系，不因没有answer拒绝。"
    "以真实learner消息判断对话语境；私有source.messages、reference_dialogue、旧答案和反馈"
    "不属于该对话，来源回执和已有模型评分不能替代事实证明。"
    "核对具体业务前提、来源可靠性、支持关系、必要条件和任务匹配。"
    "参考答案独自确认用户领域、观点或已见内容，虚构事实或执行结果，关键依赖失效时keep=false。"
    "未明确对象或领域时保留歧义，条件解释可以合理准备回应，不能凭资料主题猜定。"
    "例如‘又不行了’不能被无依据收窄为操作失败或卡在某个步骤，即使最后准备澄清也须修正该前提。"
    "reasoning是训练正文，reasoning_trace是私有审计；工具名、来源标识或编辑方法只在trace出现，"
    "不能直接判作训练正文泄露。编辑反馈可以指导表达修订、提醒核对某种风险，"
    "它不证明用户经历、所指领域、资料内容或任何外部业务事实。"
    "区分方法依据与事实依据：trace记录‘避免擅定用户所指’这类编辑要求本身不需拒绝；"
    "若用该要求证明用户在经历某种故障、假称用户已经说过某话或替代来源事实，则必须拒绝。"
    "发现正文泄露时指出实际reasoning中的句子，不用trace字段冒充正文；"
    "发现审计虚构业务依据时指出被虚构的事实及其不成立原因，不只因编辑反馈出现就否决。"
    "按实际任务的真实依赖和用户风格判断解释充分性，不要求固定步骤、编号或通用字数下限。"
    '仅返回JSON：{"keep":bool,"adherence":1到5整数,"reason":"具体依据或需修复位置"}。'
)
