# Agent 功能与维护入口

经验泛化由 `workflow_experience.py` 的 `workflow.memory_generalization@1.0.0` 与 `workflow.evolve_experience@1.1.0` 持有：提炼信息归属、判断判据、证据支持及行动依赖等可迁移关系，不把本题答案、专名或固定补条件话术作为长期策略。Google 新宿主适配在同一次原版提炼中提供父经验；完整条目索引，策略版本进入两层缓存。无可靠新经验时保存 `no_update` 收据并完成观察批次，不制造空经验版或热改旧任务。评审新模板 1.28 只提供实际观察，不强制写长期规则，也不增加调用。

初始系统的控制引用格式由 `workflow.cot_native_joint@1.6.0` 和 `lib/prompts/workflow_runtime_control.py` 持有。原生 SFT、多轮和 CoT 的首条 system 约定 `[[SJ_REASONING_EFFORT]]`，在确需提及本轮思考配置时使用，不要求每条必有。训练读取时由 datasets 按已保存的目标配置直接匹配替换，保留原记录，不经过外部 Agent 或额外模型调用；已有评审提出匹配建议是独立职责。旧模板、冻结任务和自定义正文不热改；新建或显式恢复新版默认的节点采用。模型实际遵循率仍需实测。

输出视图的控制文字标记由 datasets 的 `ControlTextAdapter` 管理，与上述模型提示词分开。
显式启用 v3 时，程序在导出与读取视图中生成 `<reason-strength>原控制文字</reason-strength>`，
标签名由节点 `annotation_tag` 配置；原生响应和候选正文不改，也不要求模型生成标签。
目标文字替换只发生在训练加载时。旧 v1 占位符与 v2 旁路标注按冻结版本保留。
此标签用于定位观测文字，不能把 verbosity 自述当作已认证的五档思考强度。

v3 的 `prefix_char_limit` 默认 512、可设 64–2048，只处理完整落在推理开头的控制片段；
正文同词不改，已识别标签不占原文字数。`review_suggestions` 新默认开启，字段缺失按关闭处理。
已有评审可在同一次调用中查看作者的冻结规则并提出增补精确匹配；建议以独立附属协议与质量响应分开，
不更改原质量合同，不为建议追加独立评审。review 域的 `control_text_suggestions.py` 负责出处、前缀与配置绑定，
不认证同义关系，也不直接改配置。节点查看预览、确认保存后才对新任务生效。
模型不能通过建议改变替换目标、标签名、匹配范围、开关或质量标准；旧冻结任务不热改。

当前原生返工采用公开注意事项 v7：评审模板 `workflow.rationale_check@1.12.0` / `workflow.review_score@1.25.0` 产出可独立理解的提醒；`RegenerateWithReviewGuidance` 将提醒作为 system 放在真实 user 后，用原作者重新生成。领域合同在 `review_guidance.py`，请求复用 `NativeFeedbackAdapter`。提醒保留在训练上下文，失败稿单独归档，新原生推理不拼入人工文字。允许自然引用实际提醒，事实错误与臆造规则不豁免。仍为未评审，不增加独立评审或理由改写调用。仅改变新任务默认和新模板版本，自定义提示词及冻结任务不热改。离线协议、导出及兼容验证已完成；两条 V4.1-Flash 实测共2次调用、26.25秒，输入6245/输出3935 tokens（含2713推理），估算$0.002470，未核实账单。备用响应前提和OCR可选路径改善，但01仍出现无用户依据的verbosity 5，02仍有节选/全文范围问题。后续离线溯源确认：该谱系22个请求有21个在发送前就带具体5，02由复跑脚本补入旧reasoning_content；01初次来源仍未定，撤回直接归为幻觉的定性。SDK白名单还漏记reasoning_effort，当前离线完整HTTP重建未发现SDK加提示词或本地检查点串用。来源证据和未执行的干净对照在 `.dataforge/verbosity-leak-audit-20261101/`；旧原生推理存档不删除。已有旧返工system原样复用，不能把本轮视作干净上下文消融。完整记录见 `.dataforge/native-system-guidance-v7-live-20261101/`，结果仍待处理。

v5/v6 反馈续写（历史显式试验）通过 `ContinueNativeFeedback.execute_from_feedback` 显式复用已认证的真实工具回执，保持原会话前缀后仅追加一次原作者调用。`native_feedback_provenance.py` 统一认证旧回执及嵌套父版本；完整父推理和旧答案保留，本次完整原生响应不裁剪。新指导要求思考业务依赖和证据范围，不播报内部审查；完整性不等于语义合格。普通页面不会自动选择旧回执。2026-11-01 两条真实 v5/v6 尝试保留全部推理，节选范围修正，但内部旁白和臆造篇幅仍存在；未解决项保留，不通过默认升级掩盖。

本轮不增加 MCP 服务：[DeepSeek 原生工具思考协议](https://api-docs.deepseek.com/guides/thinking_mode/) 已能保留 reasoning 与真实工具回合；[MCP 架构](https://modelcontextprotocol.io/docs/2026-07-28/learn/architecture) 提供上下文交换，不承诺模型理解或输出正确。需要真正的资料检索时才考虑把检索证据与覆盖范围作为工具结果接入，不能把已有评语工具冒充读取了文档。

当前默认原生生成采用 `workflow.cot_native_joint@1.6.0` 与经验包 v5；可恢复的初始经验为“充分推敲 · 初始 v2”。完整规则说明角色、证据和交付边界，不设默认简短或字数目标，明确用户指定的格式继续有效。原生返工指导 v4 增加替换受审助手轮的交付意图，不把内部审查或臆造设置讲给任务用户；保留实际任务需要的纠错解释。已有任务、个人经验与旧提示词原样冻结，不能为了采用新默认重写历史。

`workflow.rationale_check@1.11.0` 与 `workflow.review_score@1.24.0` 明确审查模型是审查员，候选中的助手是受审助手；审查理由不再冒充原作者第一人称思考。原字段、决定和质量标准不变。第一人称反思由受审助手收到工具反馈后自行续写。历史 1.10.0／1.23.0 模板和自定义提示词原样保留。

`native_feedback_guidance_version=3` 区分应用返工、审查意见和实际最后用户请求；程序为每条引文定位原推理或原答案的通道、位置与邻近原文。引文定位证明出处，不证明审查结论正确。工具观察复用已保存反馈，不增加提炼或评审；旧 v1/v2 任务不热改。

原生工具反馈续写入口为 `ContinueNativeFeedback`，由 `native_feedback_repairer` 装配；已有审查意见按真实 `tool_call_id` 回传，`NativeFeedbackAdapter` 认证原节点作者。`GenerateNativeCot` 的显式返工 v2 调用它；完整原稿、工具反馈、新原生片段、新答案分别保存，完成不等于合格。未持有真实工具调用的旧正文先读取反馈，最多两个模型回合，不增加独立评审。

这里按实际调用路径定位功能、提示词和状态。修改一个功能时，从它的用例和提示词所有者进入；模型传输、持久化和生产调度继续共用现有设施。并非每个模型节点都是工具 Agent，也不为拆文件新增一套执行框架。

## 角色调度与验收经验（2026-10-31）

用户确认：调度不同角色的 Agent 时，JSON 请求中的角色代号与归属会影响模型判断和行为，必须作为协议设计的一部分。排查异常先看真实请求、消息角色、被引用对象及对应响应，不能只看提示词文件有没有写到某条规则。

需要分清三层：上游 API 的消息 `role`、当前模型承担的职责、输入材料中各参与者的身份。审查请求的当前模型是审查员；嵌套材料中的 `assistant` 是受审助手，其原生推理不是审查员自己的历史思考。材料中的 `user` 是实际任务用户；程序要求返工或工具提供评语，不代表用户又说了一句话。业务称谓通过明确的指令与材料归属表达，上游消息仍使用该接口支持的合法角色。

本次已观察到的故障是身份说明冲突与引文通道不清：旧审查模板让审查员以原回答者的“我”写理由；返工模型又将原生推理引文误当正文引文，并把程序要求归给用户。新版分开审查员与受审助手，并由程序附上引文位置和实际用户来源。两条复测中，通道否认得到纠正、程序要求误归用户未再出现；这是局部观察，不能证明只改角色代号就能保证所有输出正确。

验收以实质影响为准。用户允许个别措辞偏强，不为此单独拒绝样本、反复返工或增加模型调用；仍检查是否真的造成事实错误、条件缺失、范围越界、角色错位或误导。此条记录新的人工验收尺度，不追改旧评审记录，不把未做的独立评审补记为通过，也不宣称生产校验器已经全部迁移到该尺度。

## 提示词按功能管理

[`lib/prompts/workflow.py`](../../lib/prompts/workflow.py) 只显式转发既有名称，注册表仍由 `lib.prompts.get(id, version=...)` 访问。各功能模块拥有自己的 `PromptSpec` 构造、历史别名和版本推进。下面的提示词 ID 均省略 `workflow.` 前缀。

| 功能 | 唯一版本定义入口 | 提示词范围 |
| --- | --- | --- |
| 工作流公共请求协议 | [workflow_protocol.py](../../lib/prompts/workflow_protocol.py) | `system` |
| 文档读取与 CPT | [workflow_corpus.py](../../lib/prompts/workflow_corpus.py) | `document_parse`、`document_vision`、`corpus`、`cpt_clean`、`cpt_review` |
| 开放任务与对话指导 | [workflow_planning.py](../../lib/prompts/workflow_planning.py) | `plan`、`qa_director`、`question_brief_check`、`question_user`、`question_fit` |
| SFT 回答与契约 | [workflow_sft.py](../../lib/prompts/workflow_sft.py) | `sft`、`sft_styled`、`sft_directed`、`sft_answer`、`sft_directed_check`、`style_check` |
| 普通多轮对话 | [workflow_dialogue.py](../../lib/prompts/workflow_dialogue.py) | `multiturn_user`、`multiturn_assistant`、`multiturn_consistency`、`multiturn_directed_check` |
| 资料阅读会话 | [workflow_reading_session.py](../../lib/prompts/workflow_reading_session.py) | `reading_scene`、`reading_user`、`reading_answer`；场景、用户与回答者分别定义 |
| 导入对话正文 CoT 补写 | [workflow_conversation_reasoning.py](../../lib/prompts/workflow_conversation_reasoning.py) | `conversation_reasoning_hints`、`conversation_reasoning`；v1/v2/v3 沿用各自冻结选择 |
| CoT 作者与前缀续写 | [workflow_cot.py](../../lib/prompts/workflow_cot.py) | `cot_generate`、`cot_rationale`、`cot_native_answer`、`cot_prefix_check`、`rationale_check`、`cot_directed_check` |
| 同次原生推理与答案 | [workflow_native_cot.py](../../lib/prompts/workflow_native_cot.py) | `cot_native_joint` |
| 清单、主张与工具核查 | [workflow_reasoning_checks.py](../../lib/prompts/workflow_reasoning_checks.py) | `cot_self_check`、`cot_self_check_review`、`cot_claim_check`、`cot_claim_review`、`cot_check_agent` |
| 偏好候选与设计核验 | [workflow_preference.py](../../lib/prompts/workflow_preference.py) | `alternative`、`preference_directed_check`、`preference_design_check` |
| 独立评分与打包抽检 | [workflow_review.py](../../lib/prompts/workflow_review.py) | `jev_score`、`judge`、`review_score`、`review_reference`、`package_review`、`learner_context_check`、`human_augmentation_check` |
| 已有候选修订 | [workflow_revision.py](../../lib/prompts/workflow_revision.py) | `review_repair` |
| 推理修剪 | [workflow_trim.py](../../lib/prompts/workflow_trim.py) | `trim`、`trim_check`、`trim_rules_check`、`trim_directed_check` |
| 经验与个人风格提案 | [workflow_experience.py](../../lib/prompts/workflow_experience.py) | `evolve_experience`；原 `workflow_native_cot.EVOLVE_EXPERIENCE_BODY` 只转发 |

已有的 `workflow_multiturn.py`、`workflow_repair.py`、`workflow_agent_contracts.py`、`workflow_fact_review.py`、`workflow_prefill.py` 等文件保存历史正文或正文变换，功能模块按需引用，不复制第二份。部分历史正文名称与 `PromptSpec` 别名相同，例如 `REVIEW_REPAIR_V1_7`：前者仍是字符串，后者仍从 `workflow` 兼容导出为 `PromptSpec`，不能为了合并文件改变类型。早期 1.0 模板仍由 `workflow_dialogue_legacy.py` 原样定义。

## 用例、适配器与状态归属

### 当前用户表达调度

开放场景的发问归属由 `domain/open_dialogue_routing.py` 判断，新任务默认冻结 `open_dialogue_question_mode="auto"`。纯合成场景由指导员直接发言；有私有资料、工具、人工答案或显式 `separated` 时保留独立发问路径。显式 `None` 可保留旧创建行为；该策略只改变调用路径，不增加风格规则，不修改历史提示词或质量阈值。

对话进度与训练资格分别保存在 `dialogue_progress` 和既有 `status`。已认证的原生首答可显式诊断续跑，保留全文、每项拒绝、旧父版本与实际调用；已生成历史不等同于合格前缀。`ContinueNativeDialogue` 复用原稿，`GenerateMultiturnDialogue` 负责后续对话，review 域只绑定已执行的审核回执，不重复购买相同审核。

`workflow.qa_director@1.15.0` 和 `workflow.question_user@1.5.0` 共享
`lib/prompts/workflow_user_expression.py` 的场景表达策略。简洁承接与情境叙事均可选、
可混合；指导员在已有的批次设计及 `next_turn` 请求中选择，不增加风格选择请求。
`expression_design.form / verbosity / interaction_style` 是当前轮的可编辑建议，
不随用户身份固定，也不是长度、句式或轮次配额。后续可以更新
`dialogue_state.expression_design`；已有继承规则只保留相容的语言习惯。

有线索等普通路径由指导员生成实际发言；开启问题分离的 `closed_book` 路径将合规
简报和本轮表达建议交给独立发言者，保留简报边界与问题适配核验。
`workflow.multiturn_user@1.7.0` 同步支持此策略，供仍调用普通用户节点的路径使用。
这些是表达策略接入，不改变读取会话的角色翻转协议，也不将私有设计写入训练消息。
助手建议和用户的暂时接受不构成外部事实认证；场景不能凭空补写已执行工具或实验。

指导员 1.13.0 将私有任务依据与外部事实证据分开说明：`evidence_quotes` 必须逐字
引用已有来源，开放需求也可引用明确目标、约束与当前条件。它只说明任务的依据，
不为生成的技术建议或实验结论背书。`closed_book` 仅清空用户可见资料，独立发言者
仍不接收私有引文；非空依据和来源范围校验保持原样。1.12.0 及更早正文原样注册。

指导员 1.14.0 按实际未解决的事项选择发言方式，不重复套用“先认可、再追加要求”。
用户当前陈述、用户认可的具体条件、助手虚构示例和指导员状态摘要分别处理；
摘要可以出错，草案交付不代表用户验收或真实执行。`next_turn` 的
`context_authority` 由 `domain/dialogue.py` 根据冻结的
`dialogue_context_scope_version=1` 提供来源角色说明，不替模型认证摘要。
新版本的合成开放场景只作为指导员和发言者的创作输入，不能悄悄变成回答者
掌握的用户经历。回答请求的实际资料投影归 `domain/dialogue_context.py`；
真实对话、文档、人工资料及没有新协议标记的冻结任务保留原处理方式。

新任务冻结新版默认；历史模板和已保存任务逐字保留，用户的节点覆盖仍可保存和恢复。
指导员 1.15.0 的 `dialogue_constraint_provenance_version=1` 让模型选择实际用户消息
索引，由 `domain/dialogue_constraints.py` 生成整句约束及来源收据；助手示例、尚未
发出的拟稿和模型自行改写的约束不能成为已确认用户条件。来源真实不等于解释正确，
仍保留后续语义核查。没有此标记的旧任务继续使用旧返回合同。

原生生成模板 1.3.0 与 `native_instruction_scope_version=1` 按每处引用分别记录：
外部提示词设计、实际公开规则、作者的内部执行引用和来源未决。不能用附近出现
API 一词豁免整个推理；无来源的数值要求仍拦截，未决也不能算通过。
`GenerateNativeCot` 保留完整通道及字符定位证据，检查不改写推理正文。

`native_dialogue_continuation_version=1` 支持开放场景首轮原生失败的显式返工。
`ExecuteRepairBranch` 认证全部父版本后，经 `ReviewCandidate` 调用 workflows API
的 `ContinueNativeDialogue`，复用原场景、指导员契约和首问，仅修正失败首答一次，
随后使用普通多轮校验。旧稿放在独立修订上下文，不冒充已接受对话。当前不支持
任意文档或工具断点；不完整状态明确拒绝。修复失败保留原稿、新稿和待处理状态。
此类新返工另冻结 `native_revision_context_version=1`：旧推理完整保存在审计记录，
修订请求只接收旧答案与明确反馈，不把旧私有思考序列化成系统指令再次播放。
新推理仍由模型原生生成并完整保留；该投影不是产物修剪，也不豁免任何输出检查。

运行与导出字段没有迁移。验证复用 `test_expression_profile_routing.py` 的真实磁盘
调度及冻结读取用例，配合提示词注册、节点快照和 DDD 边界测试；语义效果另看真实调用报告。

下表的入口是现有业务策略入口，不是新增的公开 API。跨域调用仍只使用 `api.py` / `contracts.py`，具体实现由 `lib/bootstrap/` 装配。普通批次和生产轮次共享节点用例；尚在旧运行时的算法明确列出。

| 功能 | 业务入口与实际调用路径 | 状态归属与停止条件 |
| --- | --- | --- |
| 文档输入 | `TrainingWorkflowRuntime.prepared_source_units` → `iter_source_units` / `WorkflowSourceLibrary`；文档解析继续复用 documents 域与既有读取适配器 | 来源缓存与运行 `ingest` 检查点；来源无效则隔离，不把转录成功当事实认证 |
| CPT | `TrainingWorkflowRuntime.cpt` → `WorkflowCPTProcessing` 中的清洗与核查 | `cpt` 阶段记录、证据和重复谱系；仍是旧节点算法，本次未搬入新用例 |
| 开放任务规划 | 普通批次由 `TrainingWorkflowRuntime.plan/_plan_rows`；生产窗口由 `WorkflowProduction._production_plan` / `_production_plan_soft` | 规划身份与窗口检查点；资料耗尽及无新增情境沿用既有停止合同 |
| 指导员与问题角色 | `WorkflowQADirector.directed_generation` → 批次设计、`_closed_book_question` / `next_dialogue_step` | `qa_contract`、真实对话状态、来源行范围和任务身份；指导员简报不冒充用户发言 |
| SFT | `bootstrap/node_generation.sft_generator` / `directed_sft_generator` → `GenerateSft` / `GenerateDirectedSft` | `sft` 阶段候选及对应请求键；导入消息、来源证据、评审决定分开保存 |
| 普通多轮 | `bootstrap/dialogue_generation.dialogue_generation` → `GenerateMultiturnDialogue.execute` → `generate` | `DialogueProgress` 是本次调用的临时状态；逐轮及整段收据持久化，自然停止与轮数上限不互相替代 |
| 资料阅读会话 | 同一 `GenerateMultiturnDialogue.execute` 按冻结配置分派到 `_reading_session` | `reading_scene_v1`、`reading_user_v1`、`reading_answer_v1` 检查点；场景私有，用户模拟器与回答者历史分别投影；结束信号与缺失输出各自处理 |
| 导入正文 CoT | `bootstrap/conversation_reasoning.conversation_reasoning_preparer` → `PrepareConversationReasoning.execute_many/execute` | 输入节点独立提示提炼与正文补写检查点；逐助手消息记录原有/合成/缺失来源，补写完成仍是 `not_assessed` |
| CoT 作者与前缀 | `bootstrap/node_generation.cot_generator` → `GenerateCot` | 作者正文、原生前缀、答案续写与评审各用原有键；不能把作者审计或成功提交当独立通过 |
| 同次原生 CoT | `GenerateCot.generate_styled` 按 `answer_mode=native_joint` → `GenerateNativeCot.execute` | 同一供应商响应的 `reasoning_content` 与 `content` 分开保存；`cot_native_joint_v1` 及内容绑定收据不变 |
| 生成中查资料 | `GenerateCot` → `GenerateWithSources.execute`；`GenerationSourceAdapter` 只提供来源读取、搜索与工具回合 | `GenerationSources` 管理已认证资料，`source_lookup_v1` 管理本作者会话；有界回合与提交结束，不混入独立评审身份 |
| 推理核查 Agent | `CheckReasoning.execute` 统一分派：v1/v2 使用清单/主张请求；v3–v5 → `InvestigateReasoning`；v6 → `ReviewSemanticReasoning` | workspace 保存条款、主张、工具反馈和待处理项；作者自检、独立核验及争议调查各有历史。`run_reasoning_session` 管理工具循环，`CheckpointedReasoningAgent` 只传输并记录单次回合 |
| 偏好数据 | `bootstrap/node_generation.preference_generator` → `GeneratePreferencePair`；质量与类型核验由 review 域 `AssessPreferencePair` 负责 | 正负例、实际评分和设计收据分开；真实失败负例不可借类型要求改写，分差不由生成端补造 |
| 自动评分/修订 | `bootstrap/automatic_review` → `ReviewCandidate`、`ReviewGeneratedCollections`；显式返工走 `ExecuteRepairBranch` | review 域拥有候选哈希、审核历史、有界修订次数、人工待审和父版本认证；修正正文不覆盖原稿证据 |
| 打包评分 | `TrainingWorkflowRuntime.package` → `_review_package_candidates`，复用已核验的审核收据 | 发布选择、抽样覆盖、完整性和导出清单；抽检通过不等于未抽中记录已评审 |
| 推理修剪 | `bootstrap/node_generation.reasoning_trimmer` → `TrimReasoning` | 原答案保持不变，修剪收据独立；有显式导入收据且正文改变时，原收据保留为 `input_only` |
| 经验提炼 | `bootstrap/experience_evolution.experience_evolution_application` → `EvolveExperience.propose/save_version`；`ExperienceEvolutionModelPort` 负责一次严格提交 | 用户独立版本库、父版、episode 引文及请求收据；提案不自动成为已采用经验，不热改运行中的配方 |

## 共用设施与隔离规则

`workflow_spec.py` 只构造 `PromptSpec`，没有业务规则；`workflow_shared_rules.py` 保存确实被多个功能复用的知识边界、精确事实评审、用户不确定性和历史版本变换。`workflow_context.py`、`workflow_reasoning.py` 等既有共享正文继续保留单一实现。共享规则的修改需要关注所有显式导入者，不应把独有规则追加到共享模块。

`RequestNodeModel` / `CheckpointedModelRequest` 负责冻结节点提示词、模型绑定、角色投影、流式日志与请求检查点；models 域的 `ExecuteModelRequest` / `ExecuteToolTurn` 负责供应商协议、重试、并发和冷却。功能模块不直接建立另一套模型客户端或调度池。

`ExecuteWorkflowRun`、`GenerateDatasetBatch`、`ExecuteProductionRounds`、`GenerateProductionBatch` 继续拥有运行与批次编排。功能拆分不重命名任务 ID、阶段、模型角色、检查点键、缓存目录或导出字段。窗口收据成功保存后才推进轮次；审核和发布仍遵守既有内容绑定。

## 兼容与验证

本次按功能迁移原赋值语句与版本构造；`workflow.py` 兼容入口无模板定义、运行状态或模型调用。没有新增运行时框架，也没有改应用循环、正文、模型参数或版本选择规则。

整理前后用真实注册表在独立 Python 进程中比较全部 `PromptSpec` 字段，并检查 `workflow` 的全部既有 `PromptSpec` 别名及既有提示词模块的字符串/模板导出。基线为 **86 个 ID、279 个版本、247 个 workflow 模板别名**；比较包括 purpose、source、template、variables、constraints、notes，而非只比较 ID。v2/v3 导入补写和用户本轮已有改动均包含在整理前基线中。

业务验证复用 `tests/test_prompts.py`、`tests/test_workflow_node_prompts.py` 与相关已有功能回归；跨目录依赖仍检查 `tests/test_ddd_architecture.py`。本次注册表等值检查不调用模型，也不证明模型效果；不为文件拆分新增大量镜像测试。
