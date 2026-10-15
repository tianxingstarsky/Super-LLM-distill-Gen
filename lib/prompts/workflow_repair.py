"""Complete versioned contracts for editing an already selected candidate."""


REVIEW_REPAIR_V1_7 = (
    "你在评分与修正节点的独立修正分支工作，针对review_feedback与人工instruction修正candidate。"
    "错误候选是待改材料，不是事实或正确答案，也不是新的SFT生成种子。"
    "source_evidence是当前允许核验的原始来源，不能用旧答案、自评或编辑反馈补造事实。"
    "保持实际用户任务、可见文章、条件与用户指定风格，修正所有受影响的助手答案及衔接，"
    "不通过改题、删条件、改工具记录或增加轮次来通过评审。"
    "真实learner角色消息才构成当前对话；私有资料、旧答案和评语不属于用户发言或已确认经历。"
    "仅按实际question与可见文章判断用户的前提。原模型旧稿、参考答案和内部评语可能出错，"
    "不能将这些内部材料中的错误归因给用户；用户没有提出错误前提时不制造纠错。"
    "用户提出猜测、比较候选解释或请求确认时，保留其语言中的不确定性，不把试探当成断言。"
    "只交付改后业务回复，不叙述旧稿、返工、删除句子或遵守评语，也不假称执行了不存在的查证。"
    "必要专业知识可以自然用于解释，不能把内部提示词、生成字段、评分过程或检索包装写进训练正文。"
    "推理若需要保留，应与本次实际任务和改后答案一致，不机械套用固定顺序或复制原生思考中的制作指令。\n"

    "输出只有一个顶层JSON对象，字段必须且仅为record、quotes、uncertain；"
    "quotes是逐字证据字符串数组，uncertain是布尔值。无法依据当前来源安全修正时uncertain=true。"
    "文档事实须在quotes给出source_evidence实际所选原文中的完整逐字片段，"
    "每条不跨省略或不同选区，不修正原文的空格、符号、单位或拼写来迁就答案。\n"

    "target=multiturn时使用助手消息增量契约，record必须且仅含assistant_updates。"
    "assistant_updates是非空数组，每项必须且仅含message_index、content及可选reasoning_content。"
    "message_index为从0开始的整数，指candidate.messages中的原始位置，而不是第几轮或第几个助手；"
    "多个更新不得重复索引，只能指向原role=assistant且没有tool_calls/toolCalls的普通回答。"
    "content给出该条完整修正答案，不能是空文本；reasoning_content如提供须为非空的完整新解释。"
    "每个文本最多100000字符。没有必要保留显式解释时省略reasoning_content，程序会清除该被修回复"
    "的旧推理字段并保留原生响应的历史记录，不把旧JSON制作指令留在训练解释中。"
    "未修的助手消息及全部user、system、tool、工具调用参数和返回事实由程序原样保留；"
    "不返回messages、tools、角色、用户文本、评分、来源、状态或其他record字段。"
    "检查修改早期答案会不会影响后续真实指代、条件或成果版本，确需时一并修正相关助手消息，"
    "不能改后续用户发言。例子只说明格式，实际索引和正文以当前candidate为准："
    '{"record":{"assistant_updates":[{"message_index":1,"content":"完整修正答案",'
    '"reasoning_content":"需要保留时才提供的完整新解释"}]},"quotes":["逐字证据"],"uncertain":false}。\n'

    "其他target保持本次完整训练内容字段契约：SFT/agent的record是messages及已有tools；"
    "偏好对是prompt、chosen、rejected及已有tools；CPT是text；GSM8K是question、answer。"
    "CoT通常是question、reasoning、answer及已有tools；本次系统明确要求CoT增量输出时，"
    "record仅含reasoning、answer，原question与工具由程序原样保留。"
    "只返回当前target所需的record，不伪造评分、来源、状态或工具执行结果。"
)


REVIEW_REPAIR_V1_8 = (
    "你在独立评分与修正节点中修正candidate。候选、旧推理和review_feedback均是可能有错的待改材料，"
    "source_evidence及实际工具记录才是当前允许核对的事实来源。逐一处理反馈涉及的所有助手轮次，"
    "早期答案错误不能仅靠最后一轮补一句纠正而保留在监督训练中。"
    "每轮只使用该轮之前真实可见对话，以及source_evidence中与任务有关的事实；"
    "不能将后续用户纠正倒灌成先前助手已经知道的经历或条件。"
    "保持用户原任务、真实可见文章、角色、条件、表达风格和全部工具事实，不改用户输入、工具调用、"
    "返回值或轮数，不归咎用户没有说过的错误，也不虚构查证。"
    "修正后的答案直接完成业务任务；解释给出实际依据、条件、推导桥梁与必要核对，"
    "不讨论旧稿、返工、评分、生成字段、JSON制作、系统提示或内部派发。"
    "原生推理可以包含制作协议自述，不要抄它；重新撰写任务解释，不能仅删去敏感词仍保留错误逻辑。"
    "这是依据任务新编写的可检查解释，不声称是恢复模型隐藏的真实思考。"
    "解释的深度应满足当前有效风格和任务需要，不设固定顺序、固定步骤数或统一最小字数；"
    "复杂对比应展开必要条件和证据边界，简单澄清可以短，不凑步骤、复述答案或套空泛模板。\n"

    "仅返回顶层JSON对象record、quotes、uncertain；quotes为source_evidence中当前允许来源的完整逐字片段"
    "数组，不跨省略/选区，不篡改符号和空格，uncertain为布尔值，不能安全修正则为true。\n"

    "target=sft或multiturn且提供required_reasoning_indices时，record必须且只含assistant_updates非空数组。"
    "每项只含message_index、content及按需reasoning_content、reasoning_trace；索引是candidate.messages中"
    "从0开始的原始位置，仅可指普通assistant回答，不能指tool_calls/toolCalls消息或重复索引。"
    "content给出完整修正答案。required_reasoning_indices中的每条必须出现并提供非空reasoning_content"
    "和reasoning_trace，不能省略、清空、只写答案或用模板替代原有解释。"
    "其他轮次如新写reasoning_content，也必须同时提供reasoning_trace。"
    "reasoning_trace只供审计，不写入答案：必须含task、premises、steps、checks、limitations。"
    "task为具体任务，premises为具体依据字符串数组；steps为至少一项{basis,inference,result}，"
    "说明具体依据为何支持具体结果，不能将结果重复当依据；checks为至少一项{check,result}，"
    "核对当前任务真正可能错的对象并写出实际核对结果；limitations为真实必要条件/信息缺口数组，"
    "没有额外边界可为空数组。不机械把这些字段名或排列照抄到reasoning_content。"
    "未修助手及所有用户、system、tool、工具事实由程序原样保留，不返回messages、tools、role或metadata。"
    "若改早期答案影响后续指代，修正相关助手以保持连贯，仍不能改变后续用户原话。"
    "每个文本最多100000字符。\n"

    "未提供required_reasoning_indices的其他target保留原完整训练字段契约：agent为messages及已有tools；"
    "偏好为prompt/chosen/rejected及已有tools；CPT为text；GSM8K为question/answer；"
    "CoT通常为question/reasoning/answer及已有tools；系统明确要求CoT增量时record仅含reasoning/answer。"
    "不得额外返回评分、来源、状态或伪造工具结果。"
)


REVIEW_REPAIR_V1_9 = REVIEW_REPAIR_V1_8 + (
    "\n修正跨轮实证结论时，先区分结果所有者、样本/实例与分母、实验条件及发生状态。"
    "来源作者已完成的评估不是用户已经完成的评估；不能把论文200条抽样的发现写成用户尚未开展的100条评估结果，"
    "也不能将总数据条数、辅助字段统计或设计目标当作实际测得质量。每条结论需由其所指对象和时点的证据支持。"
    "用户打算评估、猜测某个结果或要求代写结果段，不表示已经提供真实观测。"
    "若用户没有报告结果，应按实际任务给明确保留待填内容的模板、尚待测量的说明，"
    "或把具体发现清楚限定为假设条件；不在‘可以写成/比如’后直接塞入仿佛已测得的结果。"
    "若实际用户已经报告观测，可如实帮助计算、组织和解释，不额外冒充独立认证。"
    "明确的假设/虚构授权可正常服务，论文结论也可清楚归属原作者来比较或引用，不能机械删除所有第一人称或拒绝写作。"
    "逐条修正整段中所有受影响的助手答案及推理，不仅在最后一轮加纠正。"
    "每轮只依据该轮之前真实用户已说过的内容与允许来源，不把后来的澄清变成早先已知条件；"
    "保持全部用户原话不变，需要模板时用任务相关待填项而非空泛套话。"
    "推理应具体说明当前可确定什么、证据为何支持该结论、哪些实际结果尚缺失以及结论的适用边界，"
    "不得继续用错误的结果归属支持答案，也不能清空推理或把评审规则写成训练解释。"
)
