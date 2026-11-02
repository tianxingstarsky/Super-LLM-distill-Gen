# 工作流用例

`CreatePlannedSftReanswer` 选择已封存的原生 SFT 父版本，建立只更新经验的子任务；公开入口是 `WorkflowRunApplication.reanswer_sft(parent_run_id, candidate_ids=..., agent_experience=...)`。`RegeneratePlannedSft` 复用原问题、真实历史、合同与已认证资料选区，通过既有原生端口新生成推理和答案；不调用指导员、不向作者回灌旧稿或旧推理。创建只排队，启动沿用原工作流入口；每条仅建议评审一次，不能复用旧稿评语冒充新稿评审。

`ReasoningBank` 提供已认证版本索引、按任务召回与新配方冻结；算法执行和向量缓存由基础设施调用 Google 原函数。`EvolveExperience` 收集完整观察、事务认领批次，并将上游提炼版本绑定原批次；提案、按策略交付及质量通过各自记录。原版入口由 `bootstrap/google_reasoning_bank.py` 装配。

`EvolveExperience.complete_official_observation_batch` 保留版本 ID 调用，并接受已持久化、摘要认证的 `no_update` 官方学习结果。专用端口按单条观察与 scope 原子完成批次；不将无更新当成新规则，也不再释放已完成观察。普通自定义提炼的 batch_id 认证仍走原端口。

`GeneratePreferencePair` 仅把源候选明确携带的 v3 控制文字配置保留在复制的助手消息上，
供成对标记导出使用；消息自身的配置优先，未知来源的负例不套用正例规则。正文仍是原始
推理与答案，不执行文字转换，也不把新增来源配置放进生成负例的模型请求。

原生 CoT、SFT 与多轮回答保留可信请求的 `generation_control`，并以 `generation_controls={version:1,assistant_turns:[...]}` 标出真实助手消息索引。多轮各轮独立绑定 `reasoning_call`；阅读场景与用户模拟调用不作为回答标签。原生返工使用实际最终回答收据，未知历史不补标。既有原生 CoT/SFT 投影保持该映射，旧请求没有收据时不新增字段。验证见 `test_provider_reasoning_sft.py`、`test_pipeline_use_cases.py`、`test_model_response_details.py`。

`RegenerateWithReviewGuidance` 承接新任务 v7：复用已有评审理由，将 system 注意事项追加到真实 user 后，原作者只重新生成一次。不追加当前失败助手轮，不伪造工具或推理。完整旧稿归档；新推理、答案与可见提醒一起保留。SFT／多轮包装把提醒传入质量检查和训练记录，再次返工按真实 user 位置恢复。完成仍是 `not_assessed`，没有独立评审调用。

`ContinueNativeFeedback.execute_from_feedback` 是 v5/v6 的显式已交付反馈续写入口（试验，非默认）。先认证原 session、原稿、findings 与完整父回执，再保留父 `continuation_session` 全部消息、真实工具调用及回执，追加该冻结版本的 system 指导并仅调用一次 `final=True`。原会话、模型与参数绑定继续由适配器核对；父回执完整保存，新原生响应不裁剪。不重购读取反馈或独立评审，检查点可恢复；完成仍是 `not_assessed`。调用者必须提供已认证持久父记录，普通页面不会自动挑选任意旧回执走此分支。

`ContinueNativeDialogue → GenerateMultiturnDialogue → generate_native_turn` 原样传递已保存评审理由和人工备注，再由 `ContinueNativeFeedback` 作为真实工具观察返回。旧任务冻结 `native_feedback_guidance_version=4`，由领域合同标明审查员、受审助手、应用返工与真实用户来源，并附原文通道定位；第一人称反思由原作者自己续写。v4 明确新答案替换受审助手轮，内部修改事件不进入用户对话，也不设默认篇幅目标。没有新增评审或“转换成第一人称”的模型调用。v1/v2/v3 文本、请求身份与检查点保持不变，再次返工按收据自己的引导版本认证。

工具返工完成后，`ContinueNativeFeedback` 保存最后一次真实请求的 `continuation_session`；`GenerateNativeCot` 把该会话、实际最终 assistant 与响应明细连同完整工具收据留在 `native_generation`。展示用推理可合并本次两个原生片段，后续请求必须使用收据认证的真实最后 assistant，不能把展示合并文本冒充单次响应。配方 v2 只影响带旧稿或显式工具反馈的当前候选，普通后续生成保持原路径。显式工具返工先停止待审，`ContinueNativeDialogue.assessments` 为 `not_assessed`，不自动购买逐轮或整段评审。

新原生任务冻结 `native_author_session_version=1` 后，模型请求设施仅在全部历史 assistant 原生字段完整时保存作者会话。没有该版本的历史任务不补写作者字段；旧回合只能依据经认证的原始请求／响应记录显式恢复，不能从展示合并推理反推原始会话。

原生返工 `native_revision_context_version=2` 使用保存的作者会话及已有反馈，通过真实工具回执继续推理并重答；`GenerateNativeCot` 保留局部检查，结果明确待审核，不新增独立评审。新原生响应在原检查点保存作者会话，已认证首答返工可自动选择 v2；缺少作者会话的历史配方保持旧行为。旧记录需以完整原始请求和响应认证恢复后才能显式工具续写，不能猜补。

`ContinueNativeFeedback` 对已认证作者会话执行一次工具反馈续写。原响应没有反馈工具调用时，先让原模型真实调用 `read_review_feedback`，再用该调用 ID 回传已有逐字定位的意见，最后继续原生思考并重答；最多两个请求，已有匹配工具调用只需一个。端口负责原作者绑定、请求准入与检查点，恢复不重购已完成回合。原稿、反馈、新原生片段和新答案各自保存，不购买独立评审，不把完成状态视作训练资格。未知工具、缺失原生通道或不完整结束保留回执后停止。已认证的历史补全收据继续传递，不能声称补全前后 wire 前缀相同。验证：`tests/test_native_feedback_continuation.py`。

`native_revision_context_version=1` 仅投影原生返工请求中的 `previous_candidate`：发送旧 `role/content` 答案及原样缺陷反馈，不再把旧私有 `reasoning_content` 重播进 system 的修订上下文。完整旧稿仍保存在 `native_generation.previous_candidate`，新推理由同一次原生请求重新生成并完整保存；旁路收据绑定原稿／请求摘要、包含与排除字段。请求摘要自然改变，不能复用先前污染请求；没有版本标记的冻结任务保持原请求。真实 2026-10-31 英文失败记录证实旧推理携带的错误篇幅数值曾进入 system，但此投影不声称已单独证明错误的唯一成因。验证见 `test_native_instruction_scope.py` 的实际消息投影对照。

`GenerateNativeCot` 可由已认证的返工用例显式传入 `native_previous_candidate`，只作为私有待修订旧稿；不插入真实对话历史，与末尾 assistant 旧稿同时存在时拒绝歧义。`native_revision_feedback` 继续承接实际缺陷反馈。显式冻结 `native_instruction_scope_version=1` 后保存逐处引用归属、阻断或未决证据，原生推理和答案原样保留；旧版本无新增字段或判断。该用例不自行购买循环修复或独立评审。

回答上下文范围由 `dialogue_context_scope_version=1` 冻结。`native_generation.py` 对私有合成场景仅提供实际对话，资料收据写为 `learner_dialogue_only`、零资料字符和 `private_scene=not_provided`；原始来源与完整推理仍保存。首轮也不把场景的 `guidance` 当已陈述用户条件。文档知识及人工提供资料继续通过已有认证端口。

`GenerateMultiturnDialogue` 的整段评审为精确匹配冻结公开经验的首条 system 附上来源回执，避免把明确导出的训练输入误认成私有泄露。仅带该回执的新范围协议请求以完整评审上下文与候选摘要区分检查点，旧判定保留；没有回执的旧任务、未知或改写 system 沿用原键。回执不删除消息、不替代评分，也不自动放行。

显式 `native_generation_version=1` 且 SFT 开启 `native_joint` 的新任务以 SFT 为完整响应。普通批次和生产轮次不再调度独立 CoT 生成；若仍请求 `cot` 导出，`TrimReasoning.finalize` 先处理一次完整 SFT，再由端口逐条投影同一结果。历史 CoT→SFT 路线不变。验证：`test_workflow_execution_application.py`、`test_workflow_reasoning_route.py`。

`native_generation.py` 在 SFT／多轮的实际回答边界复用 `GenerateNativeCot`，由同一次原生请求保存推理与答案。`GenerateDirectedSft` 消费指导员的问题和已认证资料范围；`GenerateMultiturnDialogue` 按真实回合保留历史原生推理并使用现有回合、契约和完整对话评审。资料收据只证明 `context_provided`，不冒充模型逐字引文或事实通过。渠道、范围或审核失败仍保留完整候选。新路线需冻结 `native_generation_version=1`；无标记的旧任务及显式 CoT 后处理保持原请求路径。

导入推理策略 v3 在两阶段正文补写中冻结当前响应的时点类型：`tool_action` 只解释为什么采取工具行动，`answer` 才要求最终断言的证据。原始历史不含未来结果；该类型由程序从当前响应决定，不由提炼模型猜测。v1/v2 的提示词与请求键保持原样。

SFT、多轮和 CoT 继续携带可选的输入推理处理收据。保留消息的路径保留逐轮来源；显式风格改写只携带输入收据，不将合成解释重新标记为原始思考。生成和审核状态保持独立，合成完成不代表质量通过。验证：`test_imported_reasoning_provenance.py`。

`PrepareConversationReasoning` 在输入节点按冻结选项处理完整会话。v2 每个缺失轮次先提炼答案提示，再以独立请求补写；后一步白名单只包含原始历史前缀、提示和范围，不传当前答案、提炼模型的原生推理、未来工具定义或先前合成解释。提示是待核验条件，不能当事实；两阶段分别按输入摘要保存检查点，恢复不重购已完成提炼。v1 仍直接读取当前答案。答案、工具调用及已有推理不改写，异常或不足响应保留诊断并隔离整个会话，网络错误与取消交给请求运行时；缺省、保留和排除均不购买模型调用。`execute_many` 保持流式处理；由 `bootstrap/conversation_reasoning.py` 装配。验证见 `tests/test_conversation_reasoning_policy.py`、`tests/test_conversation_reasoning_runtime.py`。

`EvolveExperience` 管理初始／个人经验版本及模型提案。通过端口完成一次提炼，先认证父版本、再购买提案并绑定原句；保存为新的候选版本，不自动覆盖旧经验或应用到任务。继续进化携带最近父版本及其对应候选，保留用户实际修改、规则、指令和未验证状态；历史最多两版、16,000 字符，超出时注明省略的完整版本 ID，不截半条规则或重放原始试错。用户编辑只证明偏好，不自动成为质量成功证据。人工编辑、恢复和继续进化都通过父版本保留谱系；未验证状态不因保存改变。验证见 `test_experience_evolution.py`。

生产反馈通过同一用例的 `record_observation` 收集，明确保存 `success`、`failure`、
`unknown`、`advisory_release` 及其证据等级。放行和模型自评不等于事实正确。
`claim_observation_batch` 按最多八份完整记录认领一批；`propose_observation_batch` 仅在显式
请求时调用既有提炼端口，保存候选后把批次标为 `proposed`，不自动采用、不对每条样本买提炼。
同批同参数重复提交返回已保存版本；失败保留 `needs_attention`、原材料和请求身份。
`release_observation_batch` 可退回未完成批次，`observation_progress` 显示待整理进度。
完整原始候选由 `get_observation` 读取；超出提炼窗口的完整 episode 标为 `needs_capacity`，
不截断推理或偷偷消费记录。验证见 `test_experience_observations.py`。

`ImportConversations.evolution_sources(import_id, selected_ids=None, max_records=8, max_chars=60000)` 读取已认证暂存中的完整规范化轨迹。失败工具调用、中间尝试和已有推理全部保留，只用于经验候选提炼；不运行 SFT / Agent 训练资格过滤，也不授予训练通过。按完整记录的紧凑 JSON 数组字符数（包含方括号和逗号）和条数选择，超限记录整体跳过并报告，未知 ID 或没有记录能完整装入时拒绝；最多返回 100 条跳过明细并标明是否截断。`confirm` 原有训练选择和缓存写入合同保持不变。验证：`test_conversation_import.py` 的完整失败轨迹、超限、未知 ID 和暂存改动回归。

可选 `after_id` 从已认证、唯一的会话 ID 后继续选取下一组，返回 `next_after_id` 与资料序号；未知游标明确拒绝。累计字符预算不足时不再越过该记录填入后面的较小记录，防止从最后纳入 ID 继续时遗漏中间轨迹。游标不改变原始记录或训练资格。

`GenerateMultiturnDialogue` 的显式 `reading_session` 路线先生成一段私有情景故事，再让用户模拟器和回答者交替发言。用户模拟器使用翻转的真实对话角色，读取故事与已发生对话，不读取整篇论文或助手原生思考；故事规划者已读取来源材料；回答者只收到最终会导出的系统规则、资料和真实对话。一次回复同时取得原生思考和答案，不先生成 SFT 再改写 CoT。精确结束信号只控制停止、不写入训练消息。调用身份绑定实际上下文，恢复复用已完成响应；未独立评审标记 `not_assessed`。

普通批次与生产轮次都将此模式直接接在清洗后的候选上，避免先让旧指导员生成问题再丢弃。并行选择的普通 SFT 仍走原指导员。文档范围、导入会话和旧配方不自动改写。验证：`test_reading_session.py`、`test_workflow_execution_application.py`。

`GenerateNativeCot` 实现 `native_joint`：一次节点作者请求直接取得供应商原生思考和同次答案。旧稿是待修订候选，允许两个正文同步纠正；原稿与完整原生通道保留。来源范围先投影，再通过现有端口读取认证资料包。新任务冻结 `native_environment_version=1`，程序将简短角色／资料边界状态绑定到作者请求和内部收据；旧任务无标记时不追加，不改训练投影，也不增加调用。只复用既有正文、范围和语言核查，不伪造显式推理的 JSON trace；没有独立评审的记录明确标记 `not_assessed`。断点复用已完成调用，不自动重写。验证：`test_native_joint_cot.py`。

保真改写可显式配置 `review_policy={"mode":"sampled","sample_percent":20}`，也可用 `strict` 保留逐条核验。仅与生成中推敲协议 7 组合；结构、正文、任务范围、语言和明确数值参考检查在抽检决定之前执行。历史拒绝原稿先隔离，未抽中候选完整保留但不附加虚构 judge。抽中项一次内容评审，关注实质缺陷，非明确要求的文风偏好作为建议；不自动循环补采样。后续独立审核仍按原配置运行。训练字段不变，覆盖收据只保存在内部记录。

`GenerateCot` 的可选 `faithful_restyle` 模式消费完整原稿，经已有严格推理提交协议改写，固定答案由程序装配，随后进行一次原稿/改稿内容核验。缺失推理在请求前拒绝；失败保留原稿和候选，不自动重复整篇改写。沿用既有端口、检查点和训练投影，来源格式适配通过 datasets 合同完成。回归见 `test_cot_style_transfer.py`。

`RepairAuthoredSubmission.edit` 对已有 CoT 严格提交执行一次显式局部修改。通过原提交检查点加载准确消息、原生思考、工具声明和生成模型身份；只追加实际工具反馈，不切换评审模型、不重写系统前缀。模型提交唯一匹配的文本替换及更新后的论证记录，程序原子应用并继续执行完整结构与推理校验。错误补丁保存在请求检查点，不通过重发整稿掩盖失败。返回值始终为待质量核验候选，不能自动发布或冒充独立评审；内容审核、角色与资料范围检查仍由原发布流程承担。

调用入口为 `node_generation.authored_submission_repairer(runtime).edit(edit_key, original_request_key, findings=[{"quote": "原文", "issue": "需要核实的问题"}], instruction="可编辑的补充要求")`，运行阶段应与原 CoT 提交一致。原键指向真实请求，不从重建提示词推测历史。未保存原请求身份的旧记录需要保留为旧方式，不能宣称同作者或缓存复用；已有会话工具集发生变化时收据明确标注。新任务预注册编辑工具，不自动触发修改或增加模型调用。验证：`test_authored_local_edit.py`、严格提交与 DDD 回归。

CoT 的 `draft_then_finalize` 是可选的两段作者流程：一次 `cot_rationale` 形成未认证草稿，一次 `cot_generate` 同时定稿完整推理、答案和审计。草稿作为私有编辑上下文，不伪造 learner 历史或原生推理；两次请求分别保存检查点，失败不自动重买整稿。`cot_finalization` 保存原稿与定稿身份，不标记质量通过，训练投影仍只使用定稿。来源工具与显式选择的独立审核保留原设置；原生前缀及旧任务不迁移。验证见 `test_cot_finalization.py` 和答案方式持久化回归。

2026-10-26 的 V4.1-Flash 单条论文实跑完成两次请求，但定稿继承了总体比例外推到子流程的错误，并新增过强断言。因此该方式仅标为试验，不升级默认路线；工程回归通过不能作为可用率或语义纠错证据。

`GenerateWithSources`、`run_reasoning_session` 和旧版本调查用例保留各自的业务循环。不要仅为统一循环形式增加框架；原检查点键、角色历史、原生思考与审核阈值保持不变。

`RepairAuthoredSubmission` 是显式的一次同会话修复：绑定原工具调用及实际文本中的发现，保留完整原生思考、工具参数与工具外正文，回传未接受的原因后重新提交。复用持久端口，按输入哈希续跑，不主动循环。新稿仍经过同一严格组装，不删正文或认证语义正确。验证见 `tests/test_authored_submission_repair.py`。

配方 39 的 `authored_task_contract_version=3` 让作者与来源工具使用相同的用户选段范围。原生前缀中的可观察越界在答案调用前隔离，完整正文保留；最终语言错配也隔离，不触发重新生成整条样本或独立付费评审。旧配方不接入新规则。请求边界与正文检查由 `authored_task_contract.py` 提供，原有结构、内容、角色和原生续写校验仍执行。

配方 36 的 CoT 作者通过严格工具提交，应用层不再将完整 Schema 拼成提示词要求手写 JSON。后续 `prepare_authored_response`、推理与正文校验和原生前缀校验全部保留。可选 `GenerateWithSources(strict_submission=True)` 允许同一生成会话查资料后提交，末轮只提供提交工具，不增加评审回合；旧会话保持原协议。

`GenerateCot` 对配方 35 的末尾要点配置不再提前拼接共享规则，交由请求适配器按阶段放在实际对话后；生成次数、完整正文、结构校验、失败保留与原生前缀合同均不改变。单元端口及真实磁盘工作流验证规则不会重复或丢失，旧配方仍走原位置。

协议 7 可显式配置 `node_generation.cot.source_lookup={version:1,max_rounds:2,max_hits:3}`。`GenerateWithSources` 在作者会话内按需查原文，再继续原任务；每回合完整保留原生推理与工具历史，支持一回合批量查询，不调用独立评审。重复查询复用结果，无进展停止；最多配置轮数加一次完成回合。现有校验仍由 `GenerateCot` 执行，工具记录不进入训练投影。默认及已冻结任务不自动启用；节点原提示词与自定义风格继续有效。验证见 `tests/test_generation_source_lookup.py`。

CoT 协议 7 在生成中完成草稿推敲：联合生成只请求一次，原生前缀模式请求一次推理和一次答案，不自动追加清单、前缀、风格、契约评分。现有结构、正文泄漏和原生前缀收据校验保留；失败保留完整草稿并停止，不重写整条推理。显式的审核节点仍可执行，不能把生成中推敲当独立审核通过。旧冻结协议不变。验证：`tests/test_integrated_reflection.py`。

新建证据策略 2 任务使用配方 32，继续冻结 `authored_trace_transport_version=1`，另在自检配置冻结 `batch_submission_version=1`。`ReviewSemanticReasoning` 使用批量提交缓冲区；`reasoning_session` 首轮要求一次覆盖全部适用项，工具反馈只列后续缺项与错误项。作者、独立评审和争议裁决仍各自保留会话与完整校验，未完成不能放行。配方 31 及以前没有新调度标记的任务保持原行为，收据身份包含显式配置和工作区绑定。

`GenerateCot` 在模型边界应用程序持有的 Schema 和无损表示转换；不可恢复的字段错误保留完整响应并以 `cot_response_protocol_invalid` 终止，不消耗整稿内容返工次数。内容缺陷继续原有返工和独立评审，格式有效不能作为质量通过。没有该标记的既有任务、检查点与导出字段保持原行为。旁路 `response_transport` 只用于诊断，不进入训练正文。

`GenerateCot` 在逐项检查、事实评分和任务契约全部通过后，才应用可选的表达建议策略；仅风格偏差不再强制重写整稿。原风格分与意见保留在 `presentation_advisories`，不改成虚假高分；严格任务及历史配方行为不变。原生前缀的综合质量检查不被该设置豁免。

`ReviewSemanticReasoning`（版本 6）执行两份历史隔离的全文核查，有实质争议时再做一次限定范围的复核。原评语、完整正文和证据全部保留；没有证据不能靠多数投票放行。只有确认的缺陷传入返工，证据不足停止为待复核，不替作者补条件。单次会话最多六回合，首次格式问题允许一次纠正，重复错误仍终止。

清单版本 5 在每个工具回执中提供当前阶段、适用条款、稿件身份、来源范围、已提交项和剩余工作。Agent 自由选择工具顺序；参数错误、内容缺陷与证据不足分开。相同无进展操作经过一次反馈仍重复时终止为未完成；已提供片段的重复读取不能增加进展。`GenerateCot` 对相同完整输入使用稳定核查键，改变正文/任务/规则后必须重新验证；失败稿保留推理和答案。所有旧版本的请求键继续保持原样。

版本 4 通过 `reasoning_session.py` 支持批量证据提交、局部纠错和跨回合故障检测。重复失败不能因改写参数文案而无限继续。故障与正文缺陷分开，未知项不触发无依据返工。作者与独立评审仍各自完成全部核查，旧版本保持可恢复。验证：`tests/test_reasoning_agent_recovery.py`。

清单版本 3 由 `InvestigateReasoning` 执行工具会话。Agent 可自主选择来源查询、局部读取、计算与证据提交顺序，不要求重新输出整张审核表。工具结果使用真实 `tool` 角色，当前正文与前提保持完整；作者和独立评审的会话分离。每次模型回执经适配器检查点恢复，已完成工具操作可确定性重放；无进展、资源上限或缺项只能返回未完成，不能放行。正文变化改变会话身份，尚不宣称能安全复用跨稿件的语义判断。版本 1/2 与旧任务继续原协议。验证：`tests/test_reasoning_investigation.py`。

版本 1/2 的 `CheckReasoning` 在作者自检后让评审独立检查同一正文与冻结规则，不向评审提供作者的通过结论。缺项、无效引文或实质缺口进入 `GenerateCot` 原有有界返工；前缀不通过时不购买答案请求。短稿增加两次检查请求；长稿按主张分批，原生前缀路径在前缀与最终正文分别检查。旁路收据保留完整证据，不进入训练正文，也不替代正确性评审。版本 3 的请求数由实际工具会话决定；未完成调查不直接触发正文重写。

清单版本 2 使用主张级模板，检查键同时绑定正文、规则和任务上下文。作者与独立评审接收相同语句定位和原始证据，分别做覆盖核验和确定性计算；作者发现缺口时停止后续付费评审。缺证据、未知或复算错误仍进入已有有界返工，不新增无限重试。

超过六个定位点时，检查请求每批最多六个定位点，全部保留完整正文与来源。首批检查所有全局规则，其余批只检查指定主张；最后合并并重新验证完整覆盖。失败批保留原始响应，未完成覆盖不能放行。批次键包含索引及定位清单哈希，旧整稿检查点不冒充新批次结果。此拆分限制评审工作量，不截短训练推理或放宽任何校验。

提前停止时单独记录失败批次与原始原因。后续批次未执行导致的整体覆盖不全不能覆盖已发现的实质否定或未知结论；整体校验收据仍保留覆盖缺项，判定仍为不通过。

评审自身的字段、数值字符串、逐字引文或覆盖合同错误可请求同角色修复一次，原稿和第一次评审完整保留；独立评审看不到作者的评审及修复。明确的 `unmet` / `unsupported` / `uncertain` 不进入格式重试，直接按原返工逻辑处理，不能重复抽样直到获得正面意见。修复后的评审仍接受全部原校验，不将模糊匹配或自动改写算作原文引用。

格式修复反馈列出所有必须返回的规则/定位 ID、缺项及各项的严格校验错误，避免只修好第一个错误又漏掉剩余主张。`assessment_format_repair_v2` 的键绑定完整反馈；旧修复记录保留但不能冒充不同输入的新检查点。正文保持原样，修复次数仍最多一次。

| 文件 | 责任 |
| --- | --- |
| `runs.py` / `ports.py` | 面向页面和 CLI 的创建、恢复、运行查询接口 |
| `conversation_import.py` | 校验转换收据、预览、显式选择及确认保存会话；通过端口调用外部转换器与资料缓存 |
| `execute_run.py` | 在独占会话内核验快照，选择普通批次、生产或修正分支，处理完成、取消与失败 |
| `generate_batch.py` | 普通批次阶段依赖：清洗输入、指导、SFT、多轮、偏好、CoT、最终交付 |
| `execution_ports.py` | 执行用例需要的会话和阶段端口，以及可重复遍历的记录批次合同 |
| `generate_sft.py` / `generate_directed_sft.py` | 文档和导入对话的 SFT，以及指导员指定问题的回答生成；保留原证据约束和尝试键 |
| `generate_cot.py` | 原生推理保留、风格化生成、原生前缀回答和失败收据恢复 |
| `generate_preference.py` | 新版本优先复用经审核域合同认证的同题失败稿，无合适原稿才生成短推理对照；原始问题和正例保持原样，独立质量判定归审核域 |
| `trim_reasoning.py` / `generation_ports.py` | 推理修剪、交付投影及节点算法消费的来源/请求/存储端口 |
| `request_node_model.py` / `model_request_ports.py` | 请求校验、角色选择、检查点重用、完整响应与流式结束状态 |
| `generate_dialogue.py` / `dialogue_ports.py` | 多轮资料准备、原样导入评审、分角色逐轮生成和完整对话评审 |
| `execute_production.py` / `production_ports.py` | 恢复已提交轮次、选择下一窗口、最终核验与有界补充 |
| `generate_production_batch.py` | 单个生产窗口内的 CPT、指导、SFT、偏好、CoT 依赖顺序 |

这里不能导入文件锁、模型 SDK、Streamlit 或具体运行时。`RecordBatch` 可能包含百万条磁盘记录，传递批次不等于 `list(records)`。`PreparedBatch` 区分来源记录与生成候选，避免把它们都叫作 data。

改变流程顺序在用例中完成，改变节点内部算法在对应实现中完成。`tests/test_workflow_execution_application.py` 使用纯内存端口验证流程，不需要真实模型或存储目录。

`GenerateMultiturnDialogue` 保留旧请求键和停止原因，导入对话只评审。`ExecuteProductionRounds` 只在收据持久化成功后推进轮次；源文件索引和样本迭代不进入调度层。`tests/test_pipeline_use_cases.py` 用不可遍历的百万记录替身及内存评审端口验证这些边界。该规模测试验证内存与调用合同，不代表真实百万样本压测。

节点算法通过 `CheckpointedGenerationAdapter` 或 `StreamingReasoningAdapter` 接入真实来源与检查点；`RequestNodeModel` 通过 `CheckpointedModelRequest` 取得冻结提示词和模型调用。用例不接触 SDK 或流式日志文件。`tests/test_generation_node_use_cases.py`、`tests/test_node_request_application.py` 使用内存端口验证原生推理字节、问题身份、失败收据及调用检查点，不发起模型 API 请求。

新任务以 `native_prefix_review_style_version=1` 将同一冻结风格、互译与深度要求交给推理前缀核验，核验不通过时不进入原生答案请求。没有该标记的历史任务保持原请求及检查点语义。具体教学示范由可编辑的版本化提示词提供，不在应用层硬塞第二份固定推理流程。

证据策略 1 将完整正/负评审绑定正文、任务历史、全部资料、规则、模型配置与提示词保存；改变任一输入重新评审，工具中断不缓存为最终结论。原生前缀与最终稿保持两份独立核查；前缀收据只有在正文及上下文完全相同时才作为已完成工作提供，最终答案的内容、一致性、语言和来源仍必须检查。没有减少质量阈值或移除历史评审。

同一策略分别缓存已完整提交的作者自检和独立评审，包括负面结论。另一个角色或裁决发生工具/连接中断时，不重抽已完成角色的判断。复用键含阶段、角色、规则、完整上下文、正文和冻结模型/提示词；损坏收据报错，不静默重新投票。`test_review_evidence_policy.py` 覆盖中断后复用及负面发现仍进入裁决。

证据策略 2 继续通过 `ReviewSemanticReasoning` 组织独立评审与有界争议处理。完整历史缺陷使用 `reasoning_history_requires_repair`，不能通过重复重写最后回答抵消。`GenerateCot` 保留失败推理前缀并提供当前适用规则，不规定思考步骤；既有策略和收据不改写。

历史返工原因保存在 reasoning_checklist.issue（原生路径为前缀核查收据）。外层兼容隔离原因仍可能是 reasoning_agent_incomplete；呈现时应优先读取完整内层审核结论，区分已确认历史缺陷与未完成工具核查，不能把前者当作重新抽样最后答案的理由。

证据策略 2 的原生前缀若在结构协议或训练表面检查被拒绝，也保留该次真实推理正文及 feedback 中的原始推导记录，包括格式不合格的记录。仍立即停止后续答案请求并隔离；这些诊断不意味着通过，不更改旧策略收据或合格导出。内存端口回归覆盖最后修订稿保留、旧策略兼容和禁止额外答案调用。
# 保真改写的开源方案取舍

`RepairAuthoredSubmission.recover_edit` 显式恢复未应用的错误补丁：读取原作者与原始工具返回，将失败结果作为同一调用的真实 tool 回执追加，再请求一次完整替换补丁。原基稿未发生变化；不模糊匹配、不跳过坏编辑、不自动放行。可选定位文本必须逐字存在于基稿。恢复响应持久化并复用，继续交回完整业务质量检查；不自动循环调用。回归见 `test_style_transfer_edits.py`。

新建严格工具任务冻结 `cot_style_transfer_version=2`。`GenerateCot` 在保真改写中先消费可选的明确数值参考核验，再请求局部替换；开放题不伪造参考答案。原答案、未改推理、真实历史保持原文，语义评审仍保留。评审与作者使用相同任务资料范围。`GenerateWithSources` 的检索路线使用同一编辑协议，`RepairAuthoredSubmission` 可以继续该作者会话。历史配方未设置版本时继续原路径。验证入口：`test_style_transfer_edits.py`、`test_math_reference_verification.py` 和已有 CoT/工具请求回归。

## 原生多轮首答返工与续跑

`ContinueNativeDialogue` 仅接收显式返工分支已经认证的父记录。当前支持完整保留原始问题、失败原生推理和答案的开放需求首轮，尚无已接受助手回合；不支持文档资料包、工具轨迹或任意中途检查点。它复用原资料、指导员契约、首问及公共上下文，把旧回答作为私有编辑候选交给原作者一次，再由 `GenerateMultiturnDialogue` 完成原逐轮、风格、契约和整段校验。失败不再自动重写首答，全文留待处理。调用与恢复沿用现有请求检查点，不另建调度器。验证：`tests/test_native_dialogue_continuation.py`。

新冻结 `native_dialogue_progress_version=1` 将原生响应的生成进度与训练资格分开：领域规则认可的局部推理元叙述问题可进入原有全部语义检查，旧缺陷仍阻止整条训练导出。显式选择 `reuse_initial_native_response=true` 的诊断子任务直接复用已认证首答，每个新回合最多生成一次；逐轮、风格、契约及整段评审照常记录原始分数。语义拒绝作为入库阻断保留，不自动重写已发生对话，也不把拒绝当通过。完整性、角色、工具、资料范围和无效协议仍停止。该模式最终始终隔离；失败收据区分 `generated_prefix` 与 `accepted_prefix`，完整原生推理和父版本保留。未选择诊断模式的普通任务仍按原语义失败逻辑停止，历史配方不改变。

冻结版本 2 的显式诊断继续以完整可读、来源清楚的实际响应推进，不再因某一种训练表面标签提前停止；全部检查、分数、未决引用和原生推理仍保留，最终始终隔离。普通路线和旧版不热改。它不扩展任意父任务恢复、不自行复制检查点；上游已认证的相同请求仍由既有请求缓存负责复用。
