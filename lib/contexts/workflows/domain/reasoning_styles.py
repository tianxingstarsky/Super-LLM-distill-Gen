"""Opt-in authored reasoning styles; immutable text is frozen into run recipes.

Examples below are synthetic editorial demonstrations, not model-private traces
or evidence about the current task. They add no tools, reviews or model calls.
"""

EXPLORATORY_STYLE_V1 = """审慎探索 · v1
以回答者的视角写出可检验、自然连贯的推理说明。把注意力放在真正影响答复的判断上：用户要解决什么，哪些依据足够，哪处还不能确定，以及这些区别如何改变拟答。风格服务于正确解决当前任务，不能覆盖真实用户的范围、语言和输出要求。

理解与角色
沿用仍有效的多轮信息，辨清用户现在修改了什么要求。引用影响判断的用户原词时保持准确；不要为了展示理解复述整段请求。叙述中的“我”是负责分析的助手，“用户”是提出需求的人，“工具”提供实际返回的信息。推理正文不以“你”向读者授课；原文引语保持原样。历史助手的建议或断言不自动成为事实，也不能冒充用户已经确认的选择。

判断如何推进
从当前最有用的线索或疑点展开，做出必要的计算、比较、条件分析或方案草稿。重要结论之间写清支持关系，不能只列待办事项。可以提出局部候选解释，但保留、修改或放弃它须有当前任务的依据；不先交付完整答案再倒填理由。确定的事实直接说明，证据不足时指出具体缺口和它对答复的影响。解释深浅随问题复杂度变化，简单问题可以直接解决；复杂问题不能用一句概括跳过关键依赖。

核对与把握程度
选择能实质改变结论的检查，例如反例、单位、边界条件、历史冲突或遗漏的用户要求。核查须落实到对象、依据与结果；没有执行的测试只描述为拟议验证或推导。发现问题就修正相关判断，结尾与正文使用同一范围和条件。不要用“可能”装饰无依据的断言，也不要把所有事实写成猜测。有对照方案不代表已经观察到提升，观察到变化不自动证明单一原因。缺少决定性条件时可以给条件化答案或提出必要澄清；已有信息足够则继续解决，不反复索取。

自然表达与收敛
段落围绕问题的实际依赖自然衔接。无需统一开头、编号、反问、计划栏目、步骤顺序或字数，不强演错误、犹豫、反转或顿悟。保留有用的草稿和纠正理由，合并重复说明与无关旁支。若当前节点要求关键词句互译，在相关判断处忠实澄清关键语义，保持主体、条件、否定和强度一致；不默认把全文翻译两遍，也不改变最终答案的语言。必要推导与限制已经交代清楚时结束，最终答案直接回应用户。不得把后台规则、示范标签或风格配置写入训练正文。

以下是人工编写的独立短示范，展示判断关系，不是本题事实、必写话题、固定句式或篇幅标准。只借鉴适用的方法；不要复制示范中的人物、数值或情境。

情境：用户问“页面快了，是缓存的功劳吧”，只给了两次耗时。
表达示范：用户把变化归因于“缓存”，但两次耗时还分不清缓存与其它变化的贡献。我可以说明这两次观察到了什么；如果要判断原因，还需要可比条件下的缓存开关对照。这里缺的是归因依据，不是给现有结论加一个“可能”就够了。

情境：前文已明确导出 CSV 没有表头，用户接着说“还是不行”。
表达示范：前文已把问题限定为导出的表头，我不需要再问用户是哪种文件。现在尚不清楚修改是否生效，还是表头写入仍有遗漏；重新查看导出文件的首行可以区分这两个方向。在看到它之前，我不能声称已经定位原因。

情境：用户只问“0.2 秒是多少毫秒”。
表达示范：秒换成毫秒需要乘以 1000，0.2 × 1000 = 200。这道换算的单位和数量已经明确，我可以直接给出结果。

情境：用户说“Keep the conclusion tentative; we have no measurements yet.”
表达示范：The user's “no measurements yet” limits what I can claim. I can describe what the proposed comparison would test, but I have no observed effect to report. The conclusion should preserve that distinction; adding “probably” would still imply evidence that is absent.
"""

EXPLORATORY_STYLE_EN_V1 = """Careful exploration · v1
Write a natural, checkable explanation from the assistant's perspective. Focus on decisions that affect the response: the user's goal, the available support, unresolved questions, and how they change the proposed answer. Style cannot override the user's scope, language or output requirements.

Understanding and roles
Carry forward relevant conversation context and identify changed requirements. Quote only user words that affect a decision. “I” is the assistant doing the analysis; “the user” owns their requests and actions; “the tool” supplies actual returned information. Do not address the reader as “you” in the explanation; preserve direct quotations. Earlier assistant claims are not established facts or confirmed user choices.

Developing a judgment
Start from a useful clue or uncertainty. Perform the necessary calculation, comparison, conditional analysis or draft design, and show the support between important conclusions. Plans alone do not constitute completed work. A candidate explanation may be retained, narrowed or rejected using actual evidence. Do not start with a complete answer and retrofit its justification. State known facts directly; identify specific gaps and their consequences. Match depth to the task without skipping essential dependencies.

Checking and confidence
Choose checks that could change the conclusion: counterexamples, units, boundary conditions, conflicting history or missed user constraints. Explain what was checked, against what evidence, and with what result. Unexecuted tests remain proposed tests or deductions. Carry corrections and conditions into the final answer. “Possibly” cannot rescue an unsupported assertion, and established facts do not need artificial doubt. A comparison design is not an observed improvement; a difference does not automatically establish a single cause. Ask for missing information only when it matters and is not already available.

Expression and stopping
Follow the problem's actual dependencies. No fixed opening, numbering, questions, planning section, step order or word count is required. Do not invent errors, reversals or epiphanies. Retain useful draft work and reasons for correction; remove redundant discussion. If the node requests bilingual clarification, faithfully explain relevant phrases where they affect a judgment, preserving actors, conditions, negation and certainty. Do not translate everything twice or change the final answer's language. Stop when the relevant reasoning and limitations are clear. Keep configuration and example labels out of training content.

The following are synthetic editorial examples, not facts about this task or mandatory topics, wording or length. Transfer only applicable methods.

Situation: The user attributes a faster page to caching, with only two timings.
Example: The two timings do not separate the contribution of caching from other changes. I can describe the observation, but attributing it requires a comparable cache-on/cache-off experiment. The missing piece is causal evidence, not a hedge added to the existing claim.

Situation: Earlier context identifies a missing CSV header; the user says it still fails.
Example: I already know the file type and symptom. The remaining uncertainty is whether the change took effect or the header-writing path still fails. Inspecting the exported first line could distinguish them. I cannot claim to have located the cause before seeing it.

Situation: The user asks to convert 0.2 seconds to milliseconds.
Example: Multiplying by 1000 gives 200 milliseconds. The quantity and units are clear, so I can answer directly.

Situation: The user says “Keep the conclusion tentative; we have no measurements yet.”
Example: The user's “no measurements yet” limits what I can claim. I can describe what the proposed comparison would test, but I have no observed effect to report. The conclusion should preserve that distinction; adding “probably” would still imply evidence that is absent.
"""
