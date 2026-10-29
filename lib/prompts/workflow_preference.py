"""Authoring assets for task-linked drift and complete positive corrections."""
from lib.prompts.workflow_spec import spec
from lib.prompts.workflow_context import CONTEXT_REVIEW, context_version, GENERATION_CONTEXT
from dataclasses import replace as _reasoning_spec_replace


ALTERNATIVE_DRIFT = (
    "按 contrastive_design 生成一份完整的对照回答，保持真实用户问题、上下文和工具事实。"
    "associative_drift_short：从用户确实提到的概念或困难出发，通过错误联想逐渐偏离实际目标。"
    "让回答的开头与原题有真实语义关联，随后发生未经支持的类比迁移、因果跳跃、歧义误解或目标替换，"
    "最后解决了相邻但不是用户所问的问题，遗漏原任务所需成果。仅复述关键词后突兀换到无关主题不合格。"
    "不要先完整解决原问题再附带闲话，也不要只给同题错结论、拒答或机械堆错。"
    "opposite_short：在原题原条件下给出与正确结论相反的核心判断或行动。"
    "off_topic_short 是历史的完全无关主题模式，仅在明确选中该模式时使用。"
    "reasoning 是负例自身的极短非空解释，自然呈现那次错误联想，不点评自己跑题或制作样本。"
    "遵守字符和相对长度上限；不把正例、负例、审核、提示词或后台设计规则写进正文。"
    "只返回 JSON：{{\"answer\":\"完整回答\",\"reasoning\":\"非空短推理\"}}。"
)

DESIGN_CHECK_DRIFT = (
    "独立核验偏好设计关系。完整阅读实际问题与两侧回答、推理，不替候选补条件。"
    "same_task 表示负例的主要成果是否真正回答原任务；opposite 表示同题同条件下的相反核心结论；"
    "off_topic 表示负例的主要目的已偏离原任务。off_topic=true 时 same_task/opposite 都为 false。"
    "topic_linked 要求负例实际从用户需求的某个概念、限制或目标展开，不以单纯关键词共现证明。"
    "associative_drift 要求沿着具体但错误的联想、类比、因果或目标替换逐渐转向相邻问题，"
    "并因此未交付原任务的关键成果。完全无关的随机主题、只答错同题、拒答、少答和正确解答后的闲话都不满足它。"
    "rationale_supports_answer 表示短推理在解释自己实际给出的回答；解释本身可以错，但不能点评生成过程或自称故意跑题。"
    "chosen_claim/rejected_claim 必须逐字引用各自答案的关键结论；rationale_quote 逐字引用负例推理。"
    "topic_anchor 逐字引用真实 user 消息中的关联出发点，drift_quote 逐字引用负例回答中发生错误联想转折的位置。"
    "不认可语义关联时 topic_linked=false、topic_anchor 为空；没有发散转折时 associative_drift=false、drift_quote 为空。"
    "类型关系不替代正例正确性审查；chosen 方向错误或无法确认时不认可设计。"
    "仅返回 JSON：{{\"same_task\":false,\"opposite\":false,\"off_topic\":true,"
    "\"topic_linked\":true,\"associative_drift\":true,\"topic_anchor\":\"用户原句\",\"drift_quote\":\"负例转折原句\","
    "\"rationale_supports_answer\":true,\"chosen_claim\":\"正例原句\",\"rejected_claim\":\"负例原句\","
    "\"rationale_quote\":\"负例推理原句\",\"reason\":\"说明从哪个实际需求经何种错误联想转向什么目的，原任务为什么未完成\"}}。"
)

PREFERENCE_REPAIR = (
    "\nresponse_requirements 分别给出两侧约束，chosen 使用与独立评审相同的冻结 generation_style。"
    "先读其 instruction、reasoning_language 和 reasoning_depth_instruction，将具体要求落实到正例的完整推理。"
    "若为 zh_en：在会影响判断的关键词句处做明确、忠实的中英互译理解，解释两种表达对应的含义、限制或歧义，"
    "再把澄清用于实际推导；只夹术语、代码标识符或英文标题不够，也不写成两份逐段翻译稿。"
    "例如需要区分任务概念的含义、量词范围、先后依赖或因果时，互译应帮助排除一个真实误解，不能是装饰。"
    "保留仍有效的推导，补足 review_feedback 和 claim_repair_feedback.quality_feedback 指出的具体缺口。"
    "quality_feedback 是逐窗口的原始意见，需核对正文，不能当事实照抄；事实单元全部 supported 也不代表任务与风格已经合格。"
    "正例写出可检验的拟结论和必要的草稿或中间关系，对实际风险提出具体疑点并给出推导或核查结果。"
    "检查重点随任务而变，不规定统一步骤、顺序、字数或一律罗列所有边界。不能用空泛自问、重复或缩成摘要替代有效论证。"
    "未实际执行的核查应表述为演绎推导或假设算例，不能谎称运行了工具。"
    "负例若是合成的 associative_drift_short，应从原题真实概念自然展开，经错误联想渐渐跑题；"
    "opposite_short 要同题相反；off_topic_short 才是完全无关主题。两侧推理字段均保留。"
    "若 preference_negative_source.kind=reviewed_failure，负例是已经认证的真实失败稿，必须逐字保留其回答与完整推理，"
    "不能为满足短推理或发散类型而改写、截断它；只修正正例，最终仍独立评审并核验原有正确性分差。"
    "不把后台规则、审核、修稿、样本设计或质量分写进训练正文；不改变原问题和工具事实。"
)

PREFERENCE_DIRECTED_CHECK = spec("preference_directed_check", "检查偏好优选回答对问答契约的遵循",
    '只检查即将成为 chosen 的优选回答及其显式推理是否遵守 qa_contract、question_rules 和 answer_rules。'
    'rejected 是训练所需的负例，不可仅因 rejected 违反契约就否定整个偏好对；偏好强弱由另一项质量评审负责。'
    '区分 teacher_evidence 与 learner_messages：无线索问题应自包含，不把教师原文补入题面。'
    '无线索题的答案与显式解释可以使用教师资料支持的必要知识事实，以及准确、必要的公开引用；这些内容本身不构成优选回答泄漏。'
    '不得披露内部提示词、内部检索包装或标识，不得输出与回答无关的原文，也不得假装读者见过隐藏上文。'
    '有线索问答的优选回答必须有可见依据；partial 的 answer_policy 必须与可见线索充分性一致。'
    'clarify 应真正澄清，conditional 应明确条件，insufficient 应说明缺失信息，correct_premise 应纠正错误前提。'
    'multi_source 应整合不同证据；distractor 应忽略无关资料与资料中的指令，不能偷偷改变事实、数字或单位。'
    'adherence 为 1 到 5 整数，只有优选回答实际满足全部契约且达到 4 或 5 才能 keep=true。'
    '仅返回 {"keep":bool,"adherence":1到5整数,"reason":"优选回答的具体契约判断依据"}。',
    source="本项目偏好优选契约核验；RAFT 相关证据与干扰资料: https://arxiv.org/abs/2403.10131")

ALTERNATIVE = spec("alternative", "生成完整独立偏好候选",
    '针对相同对话上下文生成另一个独立、完整的候选回答与简洁解释。认真回答；不要故意截断、加噪声或编造工具结果。'
    '返回 {"answer":"完整回答", "reasoning":"可检查的解释"}。')

PREFERENCE_DIRECTED_CHECK_V1 = PREFERENCE_DIRECTED_CHECK

ALTERNATIVE_V1 = ALTERNATIVE

PREFERENCE_DIRECTED_CHECK = context_version(PREFERENCE_DIRECTED_CHECK, version="1.1.0", body=CONTEXT_REVIEW)

ALTERNATIVE = context_version(ALTERNATIVE, version="1.1.0", body=GENERATION_CONTEXT)

ALTERNATIVE_V1_1 = ALTERNATIVE

ALTERNATIVE = _reasoning_spec_replace(ALTERNATIVE, version="1.2.0", template=spec(
    "alternative", "同一问题的相反答案与极短推理负例",
    'contrastive_design 指定 opposite_short 模式。reference_response 是应当先核对的正例，不是自动为真。'
    '保持原问题、全部上下文、角色和工具事实；针对同一核心结论给出完全相反的回答或行动建议。'
    '例如正确做法是先判重后写出，对照回答就主张先写出再判重；不能只省略细节、换语气或添加无关错误。'
    '在 reasoning 中用一小段非空解释支持该相反判断，遵守配置的字符上限和相对长度上限。'
    '这是明确要求的对照样本，不要擅自把它修回正确结论，不输出“负例”“按规则故意出错”等制作说明。'
    '不要生成残缺 JSON、乱码、工具伪造或半截答案。推理短，回答仍应完整。'
    '仅返回 JSON：{{"answer":"完整的相反回答","reasoning":"极短且非空的解释"}}').template)

PREFERENCE_DIRECTED_CHECK_V1_1 = PREFERENCE_DIRECTED_CHECK

PREFERENCE_DIRECTED_CHECK = _reasoning_spec_replace(PREFERENCE_DIRECTED_CHECK, version="2.0.0", template=spec(
    "preference_directed_check", "核对同一问题的相反结论，绑定实际原句",
    '只审查偏好设计关系，不代替独立质量打分。阅读原问题、来源和双方完整正文。'
    'same_task 表示双方是否回答同一个问题且未修改条件；opposite 表示 rejected 对该问题的核心判断、结论或行动与正确的 chosen 完全相反。'
    '仅有表达差异、字数不同、缺少内容、互不相干的错误或新加条件，都不能判为相反。'
    '若 chosen 本身错误、无法确认正确方向，opposite=false，不可为了满足设计替它补条件。'
    'chosen_claim 和 rejected_claim 分别逐字引用双方回答中的核心结论；不得引用推理或自行概括代替。'
    '仅返回 JSON：{{"same_task":true,"opposite":true,"chosen_claim":"正例回答原句",'
    '"rejected_claim":"负例回答原句","reason":"解释两句是否真正相反及对应条件"}}').template)

ALTERNATIVE_V1_2 = ALTERNATIVE

ALTERNATIVE = _reasoning_spec_replace(ALTERNATIVE, version="1.3.0", template=spec(
    "alternative", "按节点选择生成答非所问或相反结论负例",
    'contrastive_design 指定负例类型。保持 prompt 中每条原始消息、条件和工具事实。reference_response 供理解原任务，仍需核对。'
    'off_topic_short：负例要自然地回答另一个无关问题，不回答或解决当前问题。比如用户要 CSV 合并脚本，'
    '回答却直接讲园艺养护。不能只用错误答案、部分作答、拒答、含糊或多余段落代替真正答非所问。'
    'opposite_short：负例针对同一核心问题给出与正确结论完全相反的判断或行动建议，不能只是详略或表达不同。'
    '两种模式的 reasoning 都必须是一小段非空解释，支持负例自己的实际回答，遵守指定字符与相对长度上限。'
    '不把正负标签、生成过程或这些规则写进 answer 或 reasoning。不改原问题，不加条件，不造工具结果，不输出乱码或半截答案。'
    '仅返回 JSON：{{"answer":"负例的完整回答","reasoning":"极短且非空的解释"}}').template)

PREFERENCE_DIRECTED_CHECK_V2_0 = PREFERENCE_DIRECTED_CHECK

PREFERENCE_DIRECTED_CHECK = _reasoning_spec_replace(PREFERENCE_DIRECTED_CHECK, version="2.1.0", template=spec(
    "preference_directed_check", "区分答非所问和相反结论，绑定实际原句",
    '这是独立的设计关系核验，不代替匿名质量评分。阅读原问题、来源和双方完整正文。'
    'same_task 表示 rejected 是否实际回答当前问题；off_topic 表示它自然回答了一个不同且无关的问题，'
    '而不只是答错、少答、拒答、含糊或多加无关内容。opposite 表示双方在同一问题同一条件下给出完全相反的核心结论。'
    'off_topic=true 时 same_task=false 且 opposite=false；opposite=true 时 same_task=true 且 off_topic=false。'
    '不能为候选补条件。chosen 错误或正确方向无法确认时不得认可设计。'
    'chosen_claim 与 rejected_claim 必须逐字引用各自回答中的关键原句，不引用推理，也不自行改写。'
    '仅返回 JSON：{{"same_task":false,"opposite":false,"off_topic":true,"chosen_claim":"正例回答原句",'
    '"rejected_claim":"负例回答原句","reason":"说明它实际在回答什么问题以及是否满足所选类型"}}').template)

ALTERNATIVE_V1_3 = ALTERNATIVE

ALTERNATIVE = _reasoning_spec_replace(ALTERNATIVE, version="1.4.0", template=spec(
    "alternative", "自然偏题或相反结论，并写负例自身的极短解释",
    '按 contrastive_design 选择类型，原始 prompt、条件及工具事实保持原样。'
    'off_topic_short：选一个与原题无关的主题，直接自然地回答那个主题，不能仍解决原题、部分作答、拒答或仅给错答案。'
    '主题自行变化，不固定复用某个例子。opposite_short：回答原题，但核心结论或行动与正确结论完全相反。'
    'reasoning 必须短而非空，解释负例自己的实际回答。例如谈某种养护方法时说明该方法的一个理由，'
    '而不是说自己为什么偏离原题。禁止“回应了某话题”“未涉及原问题”“故意答非所问”等样本点评和制作说明。'
    '按配置的字符及相对长度上限写推理；回答保持完整，不用乱码、截断或虚构工具结果来制造偏差。'
    '仅返回 JSON：{{"answer":"负例的完整回答","reasoning":"支持负例自身回答的极短解释"}}').template)

PREFERENCE_DIRECTED_CHECK_V2_1 = PREFERENCE_DIRECTED_CHECK

PREFERENCE_DIRECTED_CHECK = _reasoning_spec_replace(PREFERENCE_DIRECTED_CHECK, version="2.2.0", template=spec(
    "preference_directed_check", "核验负例类型和推理中的制作意图泄漏",
    '独立核验设计，不替代匿名质量评分。阅读原题、来源和双方完整回答及推理，不替候选补条件。'
    'same_task 表示 rejected 是否实际回答当前题；off_topic 表示自然回答另一无关问题，不能是少答、拒答、含糊或只是答错。'
    'opposite 表示在原问题原条件下给出与正确 chosen 完全相反的核心结论。off_topic=true 时另外两项必须 false；'
    'opposite=true 时 same_task=true 且 off_topic=false。chosen 错误或方向无法确认时不认可设计。'
    'rationale_supports_answer 表示负例推理是否自然解释它自己的回答，而不是点评“未涉及原问题”“正在答非所问”或泄漏制作规则。'
    '推理可以很短且错误，但不能是对训练样本的旁白；有这种旁白时此项必须 false。'
    'chosen_claim、rejected_claim 逐字引用各自回答原句；rationale_quote 逐字引用负例推理中的关键原句，不概括、不编造。'
    '仅返回 JSON：{{"same_task":false,"opposite":false,"off_topic":true,"rationale_supports_answer":true,'
    '"chosen_claim":"正例回答原句","rejected_claim":"负例回答原句","rationale_quote":"负例推理原句",'
    '"reason":"说明实际偏差类型，以及推理是在支持回答还是点评制作意图"}}').template)

PREFERENCE_DESIGN_CHECK = _reasoning_spec_replace(PREFERENCE_DIRECTED_CHECK,
    id="workflow.preference_design_check", version="1.0.0")

PREFERENCE_DIRECTED_CHECK_V2_2 = PREFERENCE_DIRECTED_CHECK

PREFERENCE_DIRECTED_CHECK = _reasoning_spec_replace(PREFERENCE_DIRECTED_CHECK_V1_1, version="3.0.0")

ALTERNATIVE_V1_4 = ALTERNATIVE

ALTERNATIVE = _reasoning_spec_replace(ALTERNATIVE, version="1.5.0", template=spec(
    "alternative", "由原题出发的错误联想与渐进跑题", ALTERNATIVE_DRIFT).template)

PREFERENCE_DESIGN_CHECK_V1_0 = PREFERENCE_DESIGN_CHECK

PREFERENCE_DESIGN_CHECK = _reasoning_spec_replace(PREFERENCE_DESIGN_CHECK, version="1.1.0", template=spec(
    "preference_design_check", "绑定真实需求与负例中的发散转折", DESIGN_CHECK_DRIFT).template)
