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
