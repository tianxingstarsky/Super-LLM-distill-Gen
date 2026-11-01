# 旧领域层

下游 CoT 改写沿用 SFT 上下文时，通过审核域公开合同退休已变动回答的旧生成档位；不把来源记录的收据贴到新内容上，未改轮次和原始历史仍保留。

`provider_reasoning.py` 仅从候选对象的可信 `response_details` 属性读取生成控制收据。新记录通过 workflows 域的逐助手映射保存控制来源；JSON 正文里的同名键没有来源权限，旧记录与训练消息保持原合同。

`training_surface.py` 对协议 2 的记录先通过工作流合同认证 `native_generation.review_guidance`，只把匹配的公开 system 注意事项计入各助手轮当时可见的规则。提醒不会扩大资料阅读范围，不豁免后台字段或臆造篇幅设置；后来的提醒不能回溯解释旧轮泄露。`multiturn.py` 只有调用方传入认证位置才接受 user 后连续的 system 提醒；旧调用仍拒绝中途 system。验证：`test_public_review_guidance_surface.py`。

`agent_experience.py` 默认经验包 v5 仅更新 reasoning 范围为“充分推敲 · 初始 v2”：按任务充分展开，不设默认简短、字数、段数或固定步骤，保留角色归属、证据与真实反思。v1–v4 文本及摘要不变；已冻结任务和个人覆盖不迁移。工作流的 `initial_experience_text` 及“使用初始”动作采用 v2，新经验版本仍由用户显式保存。验证：`test_agent_experience.py`、`test_workflow_agent_experience.py`、`test_experience_evolution_ui.py`。

`creation_draft.py` 为 SFT／多轮分别保存 `answer-mode`（普通或原生同次生成）及节点内思考要点。新节点的要点只接受协议 7；独立 CoT 的历史协议范围不变。草稿允许未完成的文本编辑，实际启动仍走节点生成合同校验，不以表单保存替代任务校验。

节点模板目录按可选 `conversation_reasoning.version` 冻结补充解释提示词：1 固定 `workflow.conversation_reasoning@1.1.0`；2 分别固定 `workflow.conversation_reasoning_hints@1.0.0` 和 `workflow.conversation_reasoning@2.0.0`；3 分别固定 hints 1.1.0 与正文补写 2.1.0，由程序提供当前回合的 `response_kind`。已冻结正文、自定义覆盖和未选择该策略的历史模板快照保持不变。缺失推理的业务规则位于 workflows 域，这里只维护节点模板与草稿兼容合同。

配方 41 增加多轮节点可选 `reading_session`。`training_surface.py` 仅对已认证的实际共享阅读上下文允许公开任务规则及资料引用；原知识应用、隐藏制作字段和旧任务校验不变。配置不会把过去的失败记录重新标成通过。训练 schema 不变，共享规则与材料保存在既有 messages 的 system 内容中，私有故事仅在审核收据中。

`native_joint` 配方 40 是显式选择的原生联合输出方式。新模板目录只在版本 40 加入，旧任务模板与缓存不迁移；语言指导明确作用于本次原生思考，保留原字节。节点草稿保存模式选择；训练投影字段不变，报告保存原生生成收据。

`workflow_generation.py` 通过 workflows 合同校验可选 CoT 抽检策略，风格选择排除此非风格字段。`creation_draft.py` 持久保存节点级比例与模式，`workflow_report.py` 只投影覆盖收据，不将未评审状态转成质量分数。旧冻结记录没有新字段时不改变行为。

CoT 的 `draft_then_finalize` 作为答案方式沿用现有草稿保存、任务复用和节点提示词选择；算法由 workflows 域实现。其他答案方式及已封存任务不变。

`workflow_generation.py` 注册 workflows 合同提供的“审慎探索”预设；正文由新域维护。旧预设及混合风格选择不变，运行仍优先使用冻结风格正文。

`training_surface.py` 区分明确归属于用户可见文章的普通 “this prompt” 讨论与当前执行提示词泄漏；必须有实际可见文章及同句归属，私有字段、执行要求和第一人称泄漏仍阻断。验证见 `test_public_prompt_reference.py` 与原训练正文回归。

`workflow_generation.py` 仅透传 workflows 域的 `source_lookup` 合同校验；它是 CoT 节点可选的来源工具能力，不属于推理风格文本，不改变旧任务默认配置。

本目录保留尚未迁移的纯业务规则：对话行为、知识运用、推理风格、逐轮审核、候选质量、人工增强等。工作流核心、数据集交付、模型配置和部分审核文件已成为兼容导入。

按业务去 [contexts](../contexts/README.md) 查找新代码。修改别名文件不会修复其真实实现；先确认模块实际归属。

领域规则不得打开文件、读写数据库、联网或启动进程。规则接受普通值，返回决定或校验结果。文件路径可以作为值校验，不能在这里检查文件是否存在。

`workflow_quality.py`、`reasoning_*`、`review_*` 具有真实业务差异，不能为缩减文件数量合并成一个通用校验工具。业务语义相同的规则才应复用。

`agent_experience.py` 按实际提示词职责选择长期经验；负例类型核验使用 review 角色，不继承生成方的答案设计经验。新增节点提示词需同步检查角色映射，已有任务的经验正文及版本继续冻结。

CoT 草稿新增 `workflow-cot-evidence-policy`（0/1），只保存新旧核查策略选择；具体来源覆盖与规则位于 workflows 域。

CoT 证据草稿字段现支持 0/1/2；2 是完整训练目标和实际风险核查，不重写历史保存值。

CoT 草稿版本范围包含协议 7（生成中推敲）；规则和模式由 workflows 合同校验。活动提示词仅包含当前模式会调用的模板，历史协议 1–6 仍保留原来的评分与清单入口。
