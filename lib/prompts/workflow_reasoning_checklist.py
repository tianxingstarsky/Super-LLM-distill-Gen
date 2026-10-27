"""Editable self-audit instructions; receipts never become training prose."""

CHECKLIST_CONTRACT = '''逐项核对 rules 中每一项，不得漏项、合并、改写规则或另增规则编号。
同时核对 context 中的当前用户任务、前文约束、风格和语言要求。task 规则须在 reason 中逐一说明这些要求是否覆盖，不能只写“满足任务”。
候选正文不是规则的来源。每项返回 met、unmet 或 not_applicable；met 必须提供 reasoning 或 answer 中逐字存在的原句并解释原句如何实质满足要求，不能拿审计记录自身作证。引文存在不等于结论正确，必须核对支持关系。
缺失的工作标 unmet，reason 写明缺的对象、桥梁或核查结果；不得在审计理由里悄悄补做推导后判正文合格。已写的计划不等于完成草稿，“检查无误”不等于核查结果。不得因为答案貌似正确、篇幅长或字段齐全而通过。
not_applicable 仅限 allow_na=true，须结合当前任务说明为何没有必要，不能用“太难、没有篇幅、没有证据、没有执行”回避需要完成的工作。可引用存在的正文佐证；缺少必需证据要标 unmet。
审查条件、量纲、否定、比较口径和适用范围；知识片段未提及不证明论文或世界中不存在。把计划或假设称为实测、由局部推出全部、由相关推出因果均须指出。评审不能自行新增条件挽救原结论。
phase=prefix 时只评现有推理与拟结论，不能要求尚未生成的 answer；phase=final 必须检查最终回答与已验证推理是否一致。
不要求正文固定步骤、固定反问句、双份翻译或最少长度，不要求虚构一次错误再改正。审计在旁路记录中，不把检查表、制作流程或评分提示词写入训练推理和回答。
仅返回 {"checks":[{"rule_id":"规则原 id","status":"met|unmet|not_applicable","evidence":[{"field":"reasoning|answer","quote":"正文逐字原句"}],"reason":"针对本题说明证据为何足够、具体缺口或不适用原因"}]}。'''

SELF_CHECK = "你是本次 CoT 作者的自检环节。停止扩写，对刚完成的正文诚实找错，不能修改正文或假装工作已完成。\n" + CHECKLIST_CONTRACT
INDEPENDENT_CHECK = "你是独立核查员。你没有作者自评分；从原始任务、规则和当前正文独立作出结论。不宽容缺失，不替作者补条件或代写更好的推理。\n" + CHECKLIST_CONTRACT
