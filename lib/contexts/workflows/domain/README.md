# 工作流规则

经验提案允许没有新规则（`rules=[]`），以合并后的风格概述保留当前意图；没有新证据不强迫模型凑经验。该合同不会把候选升级为通过，已有规则仍逐条认证原句。验证见 `test_experience_evolution.py`。

`public_experience.py` 提供“简洁全面 · 初始 v1”和已冻结经验的投影。新任务显式保存 `native_experience_version=1`；原生 CoT 的经验成为实际 learner system 消息并随 question 导出，阅读对话将推理经验与规则、资料一同导出，场景和用户经验只进入各自私有角色。没有标记的旧任务不追加经验。

`experience_evolution.py` 定义有适用条件与反例的少量经验提案，绑定完整来源轨迹中的逐字引文，不按错误标记自动判断样本正负。公开概述和条件规则可形成下一版紧凑经验；原始试错、来源收据与基版正文留在谱系中，不直接注入后续提示词。引文匹配只证明来源存在，提案仍是 `model_proposed_not_verified`。

阅读会话的用户模拟协议 2 只传私有故事和真实对话，避免全文论文使求助者退化为答题专家；规划者和回答者仍读取各自授权的材料。旧任务缺省协议 1 保持原请求。工作流图同时直连清洗资料到阅读多轮节点，不再显示未执行的旧指导员支路。

`reading_session.py` 定义共享阅读材料、私有故事、用户角色翻转、结束信号和上下文收据。通过实际导出 system 消息与资料 SHA 确认“任务规则和资料确实可见”，不靠一个阅读标签豁免隐藏上下文。此目标允许思考讨论公开任务规则或所给资料；缺失上下文、私有制作字段、事实错误和未核验状态仍分别处理。故事是合成情景，不能当成来源事实；短句诊断不是正确率证明。

机制参考：[IBM 文档多轮对话](https://arxiv.org/abs/2409.11500) 的连续上下文与追问／澄清／纠正，[tau2 用户模拟器](https://github.com/sierra-research/tau2-bench/blob/main/src/tau2/user/user_simulator.py) 的独立场景、角色翻转与结束信号，以及 [ADK](https://adk.dev/evaluate/user-sim/) 的人物行为与目标分离。复用其机制，保留本项目检查点、调度和模型端口，未引入整套框架依赖。

`native_cot.py` 定位原生通道中可直接观察到的执行提示复述，保留原文字符范围及证据，不删改推理。真实用户讨论同名术语时不按该词封禁；规则命中只是确定的泄露缺陷，未命中不代表逻辑或风格合格。验证使用原生响应中的真实缺陷片段。

`cot_style_transfer.py` 定义保真风格改写的原稿输入与对照收据；原答案由程序保留，缺失推理不补造，领域函数不做语义放行或 I/O。由 `GenerateCot` 调用，结果导出仍使用既有训练投影。验证见 `test_cot_style_transfer.py`。

`authored_edits.py` 定义同作者局部修改的严格工具协议。补丁绑定原候选哈希，逐项使用唯一的精确文本替换，不做模糊定位；修改在副本上原子完成，完整推理及未修改正文保留。已有 `jsonschema` 和 `prepare_authored_response` 负责合同与推理检查；该模块不执行代码、I/O、模型调用或语义放行。没有为字符串替换引入额外 Agent 框架。

`cot_finalization.py` 仅绑定草稿、定稿和内容身份，不判定语义正确，不读取存储或调用模型。可编辑的定稿要求在 `workflow.cot_generate@1.12.0`，应用用例控制两次请求。原稿旁路保存，已有训练导出字段不变。

`reasoning_styles.py` 提供可选的“审慎探索”v1 要求与人工编写短示范，借助 `contracts.py` 接入现有风格选择。它描述用户意图、角色、证据与判断变化，不声称复制任何模型私有思考，不增加模型调用或放行规则。正文按既有创建路径冻结到 `node_generation_prompts`；旧预设、混合轮换、冻结任务和导出字段保持不变。验证沿用风格冻结与节点生成回归。

默认生成中推敲的 `draft` / `revision` 采用随证据推进的探索叙述：局部猜想、具体核对与结论取舍相连，断言强度随支持程度变化；不虚构故事、强演反转或用犹豫词掩盖无依据推断。没有新增固定思考步骤、评分调用或放行条件。规则仍在节点中可编辑，已冻结任务与用户自定义内容逐字保留。验证沿用 `test_integrated_reflection.py` 和 `test_reflection_guidance_placement.py`，真实生成效果需另查原始输出。

`authored_task_contract.py` 从真实用户请求编译当前调用的资料范围和答案语言。明确只读选段时，临时投影仅包含可见选段；原资料、消息、缓存不改写。它复用既有训练正文检查，并定位严重语言错配和可核对的选段外原文引用；不能用没有这些问题冒充语义正确。通过请求适配器和生成用例接入，不调用模型，不规定推理步骤。验证见 `tests/test_authored_task_contract.py`。

`authored_submission.py` 定义严格工具的字段合同与确定性记录组装。上游不支持的长度/数组数量关键词仅从传输 Schema 移除，本地完整 Schema 与内容核查保持不变。重复字段、缺项、多次提交和未归属正文拒绝，不修补推理或认证质量。该模块无 I/O；SDK 与检查点仍在基础设施。

配方 35 的 `reflection_guidance_placement_version=1` 由 `integrated_reflection.py` 选择当前阶段的冻结要点，供请求末尾使用。新默认要点区分历史助手断言、用户约束、必要条件与可选设置，允许可核对的真实资料名引用；推理用我／用户／工具锚定身份，不提前向用户交付完整答案。它不执行字符串替换，不按人称或长度认证逻辑正确性。验证见 `tests/test_reflection_guidance_placement.py`。

默认 `task` / `revision` / `delivery` 进一步区分用户确认事实、希望采用的结论、比较设计、实际观测与归因范围；标题、比较名称和末尾总结不能恢复正文已纠正的历史断言。该区分按任务适用，不规定思考步骤，也不一概禁止有证据的因果推断。只更新新选用的默认规则，原始历史、冻结配方、自定义规则与导出字段不迁移；实际语义效果以保存的完整生成结果核对。

`continuation_basis` 是默认的 final 专用要点，在联合生成与原生答案续写中携带具体证据、单位及可选协议边界；推理前缀不能代替这些约束或被视为独立事实证据。它沿用现有阶段选择与规则冻结，不新增模型调用，也不重放全部共享要点。用户仍可编辑、保存或恢复默认模板。

`comparison_scope` 仅在比较／实验任务中适用：共享起点不证明后续配置匹配，未训练基线不具有本轮训练数据或预算，比较设计不能替代观测或归因证据。正文和 `reasoning_trace` 使用相同证据边界；这是一条可编辑生成要求，不是用关键词自动认证因果正确性的程序。

`authored_stage_contract.py` 定义配方 34 的推理撰写／答案续写交付边界：字段类型、用户引述、回答者判断和用户操作的归属。它只生成纯文本系统合同，不规定思考顺序、不处理存储或网络。验证入口是 `tests/test_authored_stage_contract.py`。

新建协议 7 的默认要点以真实 user 请求及仍有效的多轮约束为锚点，区分用户要求、待核验前提、历史助手和引文。最终交付要求使用 final 阶段规则；原生前缀续写只补充 final 专用要求，不重复全部规则。用户保存的自定义规则与冻结任务不自动追加或改写。验证见 `tests/test_integrated_reflection.py`。

默认 `perspective` / `user_words` 要点使正式推理保持回答者视角：引用影响判断的真实用户短句，说明其对理解、取舍和拟答的影响，区分用户引语、助手判断、资料作者及扮演角色。明确作用于 `reasoning` 正文；用户将执行的操作不能写成回答者自己的行动。它们参与联合生成或原生前缀写作，不另开评审，也不按代词次数放行；没有文字时不得编造引语。表达顺序和句式保持开放，最终答案继续遵守用户要求。

`generation_sources.py` 定义可选的生成中来源检索合同。仅将已认证来源的显式正文作为证据，保留精确字符范围、可用页码和行号；不将历史助手、元数据或检索排名当作事实。收据不输出质量通过判定。验证见 `tests/test_generation_source_lookup.py`。

`integrated_reflection.py` 定义协议 7 的生成中推敲。规则仅作为生成指令，按阶段选择、逐字保留自定义要求，不构造评分或“已通过”声明。收据绑定规则和输出正文，并明确 `independent_review=false`。它与协议 1–6 的独立评审语义分开；不能送入 `CheckReasoning` 冒充核验。验证：`tests/test_integrated_reflection.py`。

`review_submission_batch.py` 组合证据工作区，首轮批量提交，后续只补缺失或无效条目。有效检查、风险项、发现和裁决分别保存，逐项错误不丢弃兄弟条目。负面判断不能在修字段时变成通过；已提交条目冲突、无法定位身份的条目保留未决。合并后仍调用原有完整校验，局部有效不代表评审完成或样本可用。`validate_review_entries` / `validate_resolution_entries` 仅用于临时收集，不能用于放行。原始模型回执检查点可重放恢复，不在 Domain 增加 I/O。验证：`tests/test_review_submission_batch.py`。

`authored_trace_transport.py` 使用 JSON Schema 校验推理响应边界。显式 `basis` 文本列表可逐字按换行转换为既有文本字段；所有原值、路径和摘要记录在旁路收据，原始检查点不变。缺失、空项、混合类型不推测补齐；列表内空洞、循环支持和重复依据仍拒绝。此步骤只确认结构，语义状态始终为 `not_assessed`，所有质量核查保持原标准。验证：`tests/test_authored_trace_transport.py`。

`presentation_admission.py` 只决定表达评分是否阻断。协议 6 可显式选择 `style_admission=advisory`，低风格评分完整保留为建议；未配置与旧版本仍严格。它不决定事实正确性，不豁免检查清单、任务契约或证据不足。训练投影不包含建议元数据，旧导出字段不变。

版本 6 的 `semantic_review.py` 将全文段落覆盖、逐项规则证据和实质缺陷分开。文字说明瑕疵可留为提示；事实、单位、结论范围与编造前提仍阻断。来源引文可以与正文共同支持规则核验，但每个适用规则仍必须有正文证据。Decimal 工具仅计算有逐字依据的操作数，不决定跨句语义。协议 6 的策略版本 3 区分双语释义与明确归属的直接引语；引号本身不构成逐字引用声明。明确数值矛盾仍由确定性检查阻断，检索到新的精确片段算定位进展，缓存读取不重复计进展。旧版本 1–5 不迁移。验证见 `tests/test_semantic_reasoning_review.py`。

`reasoning_references.py` 提供逐字上下文定位、竞争数量清单和有引文约束的分子/分母比较。计算收据仅证明选定操作数下的算术，不证明跨句指代；未知保留未知。版本 5 的运行绑定包含 `evidence-recovery-5`，使更新后的工具反馈不误用旧核查检查点。计算参数错误返回 tool_input_error，有效计算单独标记 conditional_calculation；引用诊断只定位原文，不改写或模糊接纳引文。正文锚点 ID 可无损展开为真实引文及上下文；未知 ID、冲突表示和缺失反例核查仍拒绝，批次逐项处理。十进制参数由 Schema 和领域双重校验；“约一半”不伪造成正文中的 0.5，仅可对有引文的部分/整体数量复算。验证见 `tests/test_reasoning_references.py`。

版本 5 的 `reasoning_environment.py` 根据阶段编译适用条款，保留冻结原规则；仅逐字匹配的内置 scope 规则具有预定义子条款，用户文本不靠猜测拆解。自定义规则可显式声明 `phases`。`reasoning_workspace.py` 管理不可覆盖的核查记录，`reasoning_environment_tools.py` 使用条款 ID 和统一来源引用，字段别名修复不得改变任何值。`reasoning_assertions.py` 为显式比例比较增加有限确定性检查；跨句指代和全文否定缺证据时保留未决，不宣称通用语义证明。验证见 `tests/test_reasoning_environment.py`。

版本 4 的 `reasoning_tool_contracts.py` 使用完整 JSON Schema，`reasoning_evidence.py` 追加主张语态、规则观察与不可豁免的缺口记录。批量提交逐项保留，纠错不改证据或投票；历史版本 3 保持旧协议。验证：`tests/test_reasoning_agent_recovery.py`；设计见 `docs/architecture/reasoning-agent-recovery-2026-10-25.md`。

`reasoning_investigation.py` 是版本 3 Agent 的纯证据工作区：按需查询来源、原文范围读取、Decimal 复算和单项证据提交。核验继续复用 `reasoning_checklist.py` / `reasoning_claims.py`；改变的是工作选择与提交粒度，不是通过标准。有效提交不可覆盖，实质负面判断不可重抽，查不到来源不等于证明错误。完整覆盖前不能成功结束。此目录不执行模型请求或文件操作。

`reasoning_checklist.py` 定义版本 1 的可编辑 CoT 自检清单。逐项覆盖、适用性许可和正文引文由纯函数校验；引文存在不代表论证正确。规则与正文分别计算摘要，修改正文后不能重用原自检。历史未配置清单的任务不增加字段或请求。

新默认清单为版本 2；版本 1 继续按原合同恢复。`reasoning_claims.py` 定位正文中含数值或强结论的语句，要求逐句覆盖，并用 Decimal 复算差值、倍率、占比百分数和相对增幅。`percent_of` 为部分除以总量乘 100，与 `relative_change` 的增幅分开。原文单位引文不得被替换；条件、语义单位分类和证据是否蕴含结论仍需独立模型审查，计算收据不能冒充通用逻辑证明。未知主张不自动通过。`assessment_contract_issue` 只识别可修复的评审合同错误，不将实质负面判断送去重抽。验证见 `tests/test_reasoning_claims.py`。

`creation.py` 校验创建约束；`graph.py` 描述记录依赖；`targets.py` 定义训练目标及训练记录合同；`production.py` 定义批量目标和饱和停止策略；`scale.py` 定义规模配置。

`execution.py` 包含 `BatchGenerationPlan`、`TargetOutcome`、`BatchOutcome` 和 `WorkflowCancelled`。它区分“运行结束”和“仍需关注”，不读取运行目录，不构造模型客户端。

`dialogue.py` 定义一次对话的临时进度、来源合同、公开推理控制和指导错误。`DialogueProgress` 不产生新的持久化格式。`production_rounds.py` 负责从已提交收据重建状态、窗口大小、来源覆盖及饱和停止；它只接受普通值，不保存文件或事务连接。

不要根据异常类名字符串识别取消，使用显式异常类型。不要把画布的连线当作并行执行调度。领域决定依赖，具体并发由运行时执行。

依赖只向内，或使用其他业务域的 `contracts.py`。规则变更须覆盖相应领域测试和历史配方回归。

`preference_design.py` 校验负例类型与推理长度对照。新任务通过 `new_preference_design` 默认选择 `associative_drift_short`，优先复用已认证的同题真实失败稿。没有合适原稿才生成从原问题逐渐错误联想、偏离任务的短推理负例；保留相反结论和历史完全无关话题选项。真实失败稿保留完整推理，不套合成负例的长度限制，质量和分差检查仍必须通过。

控制台在偏好节点提交显式配置；程序接口未传 `preference_design` 时保留旧采样合同。传 `{}` 启用版本 3 的新默认配置。`validate_preference_design` 对旧配方的缺省行为不变，历史版本 1/2 不迁移。`preference_design_prompt_version=1` 将负例类型核验与优选问答契约分开。版本、检查点和训练导出字段保留；回归见 `test_preference_revision_v3.py`。

`training_identity.py` 以实际学习者可见消息标识任务。SFT、CoT 和偏好视图可共用同一输入；保留系统约束、用户、工具内容及多轮前文，仅排除私有推理元数据。它沿用旧 canonical JSON 哈希，不改历史索引身份。通过 `contracts.py` 提供给审核域，旧运行时路径仅兼容转发。

`director_audit.py` 形成交付报告的指导员计数。来源全部不可用、指导员未执行时，覆盖数和批次为零；已有审计缺字段时明确报错，不推算或补造计数。由工作流合同导出，回归见 `tests/test_director_delivery_audit.py`。

`conversation_import.py` 定义规范会话和导入收据校验、有限预览及训练适用性。保留真实角色、工具配对和已有推理；没有推理时不生成替代字段。真实性与训练适用性分开判断，不放宽既有训练输入规则，不解析 DSH 原生日志或读写文件。

`generation.py` 保存风格选择、学习者可见提问、问答元数据和空答案信号。`model_request.py` 的 `NodeModelRequest` 表示临时请求意图，并校验图片与原生回答尝试参数、角色和已捕获响应。它不取代原有检查点格式，不新建持久字段；纯规则不读取来源文件或模型配置。

`review_source_inventory.py` 校验资料页范围及文本哈希。没有导入清单时覆盖状态保持 unknown，选区不冒充全文；页覆盖不保证图片、公式或表格解析准确。`evidence_scoped_review.py` 为显式 `evidence_policy=1` 提供来源范围、训练表述和答案语言的独立核查义务；`review_surface_contract.py` 仅拦截有明确反例的后台资料旁白与严重语言错配，不宣称通用语义正确。原版清单 6 及更早任务不变。

`semantic_review_feedback.py` 将工具合同错误定位到具体检查项和引用字段。论文引用不能代替被审核正文的证据；反馈不补写引用、不更改判断、不放宽逐字校验。新证据策略的工具 Schema 明确两类引用的用途；旧策略 Schema 不变。反馈版本参与会话绑定，完整评审收据仍按原稿、来源、规则和模型身份复用。验证见 `test_review_evidence_policy.py`。

`source_span_navigation.py` 只做 PDF 换行与行尾连字符的检索定位，不改写来源，也不作语义相似匹配。新证据工作区把读取/搜索得到的原文范围登记为引用 ID；ID 展开仍保留原始换行和字符，未知 ID 或错误逐字引文继续拒绝。缺陷的 body/basis 错误定位到原字段，负面判断不因工具纠错被替换。验证见 `test_review_evidence_policy.py`，包含否定词、数值、行内连字符和跨工作区引用反例。

工具参数诊断复用 `jsonschema`，裁决 Schema 直接枚举冻结的完整 finding ID（含评审角色前缀），不给短 ID 自动补前缀。精确错误路径帮助恢复格式，领域的证据、缺陷与裁决校验仍独立执行。

证据策略 2 使用 `review_obligations.py` 生成当前候选实际触发的数量、历史、资料范围及互译检查对象，`obligation_review.py` 校验原文引用、覆盖与数量复算。历史助手内容成为被审核训练目标，不再仅作为背景。Decimal 只证明所引操作数下的结果，不能证明比较行/跨句绑定或语义真值；该部分仍需独立评审。风格要求未满足不按建议豁免，旧策略 1 不变。验证见 `tests/test_review_obligations.py`。

策略 2 的数量字段只接受十进制字符串，单位、模型名和说明放在 reason/evidence 中。数量合同和计算器的恢复反馈定位到具体风险索引与操作数字段，不替换数据、不将工具错误认定为候选事实错误。相对增幅可由非百分比量表计算；ROUGE 分数点不能因原文笼统写 `%` 就成为百分点。数值/单位合同不替代比较行与事实语义评审。
# 保真编辑合同

`cot_review_policy.py` 定义显式内容抽检比例、基于输入内容的稳定选择和已知拒绝的预检。未抽中表示 `not_assessed`，不生成通过分数。仅保真改写与生成中推敲可配置；没有字段的历史任务维持逐条核验。抽检是成本/覆盖率取舍，不证明事实正确，也不保证小批次实际比例。验证见 `test_cot_review_policy.py`。

`cot_style_transfer.py` 定义可选、冻结版本的局部编辑阶段合同，复用 `authored_edits.py` 的摘要绑定、唯一匹配、原子替换和完整输出校验。工具协议由配方决定，上传内容不能开启它。协议有效不代表语义正确；不得通过替换接口放宽质量条件。
