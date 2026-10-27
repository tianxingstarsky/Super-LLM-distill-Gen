"""Evidence-policy 2; explicit task rubrics instead of a fixed reasoning recipe."""
from .workflow_evidence_review import EVIDENCE_REVIEW

OBLIGATION_REVIEW = EVIDENCE_REVIEW + '''

新证据合同（environment.evidence_policy=2）：
你的工作是识别实际缺陷，不是认同另一位评审或替原稿解释成正确。先从原始依据形成自己对关键数值、适用范围、角色与语言要求的判断，再比较原稿；这是评审方法，不要求生成正文按固定步骤书写。

environment.risk_obligations 是当前样本真正触发的检查对象，非通用思维模板。一次提交 risk_checks 可完成全部，不为每个检查单独请求工具。已有全文和段落引用足够时不重复检索。

图表和量化：定位指标名称、轴、表头、比较组和基线。百分比的相减产生百分点，相对增幅必须除以基线；ROUGE 等评分差通常是分数点，不是百分点。论文叙述本身也可能把单位写含糊：不能因为引用逐字正确就认为候选说法准确。对于实际声称的变化，quantities 提供原文有据的 baseline/current/reported 和 operation（percentage_points / relative_percent / score_points）；同段有多个变化要逐项核对。图表行对应或量纲无法确认则 uncertain 并说清缺少什么，不从模糊措辞臆造数值。候选明确给出百分点时才能用 percentage_points；只是 by 10% 不等同于 10 percentage points。如果仅是端点、比例统计、假设反例而非实际变化，说明实际用途，不强行做不相关计算。

多轮：candidate.history_* 每个助手回答及已有推理都是将来被监督的训练目标，必须逐段覆盖。后文纠正并不擦除前文错误；原始历史错误要作为 history_* finding 留下，由整段对话返工处理。不要重复修改当前答案试图抵消历史错误。

资料范围：逐项对照用户实际可见文字和教师全文。正确的全文事实也可能错误归属于用户所给选段。用户未限制外部知识时可以补充，但须让归属明确；其范围不能由评审替原稿补上。scope 核对必须同时引用当前用户段落与原稿中的实际归属表述，不能只引教师全文。知识运用任务自然解释知识，不能暴露私有资料投喂或生成规则。

互译理解：以 generation_style 的实际要求为准。中英术语并列不等于跨语言理解；引用关键中英文对应及其用于本题的含义、条件辨析或推导证据。检查否定、单位、强度、限定是否一致。不要靠两种字符、字数或格式判合格；program 只检查引文出处，意义仍由你判断。未实现明确语言要求是 task 缺陷，不能改成 presentation 建议。允许自然选择术语和句子，不要求全文翻译、固定数目、排列或长短。

发现问题时用 defect/uncertain 同步写 findings、对应 checks。not_applicable 仅适用于确实不构成数量变化主张的引用反例/假设等情况，必须有原句理由；不能用它绕过困难的实质核查。多个独立评审同意也不构成外部事实证据。程序发现单位、覆盖、引用问题时修正的是评审证据格式，不是把原稿默认为正确。
'''
