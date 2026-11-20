# Agent 功能与维护入口

## 披露式推理链怎样生成

试产协议 `4.10.1` 允许教师按学生最新续答，把已核验的操作写成具体的局部推导。教师交代本轮用到了哪个前提、由此推出什么、还有什么需要学生完成。独立审查读取学生将实际收到的全文，核对题面引文、前一轮提示和新推断。某个操作只有在相应的讲解真正发给学生后才记为已使用。被否决的原稿留在本轮记录中，后续有新上下文时仍可对同一操作提出不同讲解。

新任务默认使用 4.10.0。对有独立签发的算术步骤的题，它先让学生作答，按需要逐步交付所求量、算式和示范，逐次记录学生贡献。常规题继续沿用已有的师生流程，旧任务按创建时的清单恢复。[运行示例、来源核验及导出](../guided-reasoning.md)

学生关闭原生思考，从用户原题独立作答。只要求结果的题若首答通过独立答案核验，可进入非思考数据。需要解释的题还要检验推导。学生稿审查员私下读取参考答案和题源推导。4.9.1 的披露教师可在有金标和题源解时私下对照两者，从题面和已核实的学生发现中选择下一步。学生只看到通过配对检查并发布的提示。教师引用的原句定位失败时，重试从题面逐字选取，检查者仍须判断这条前提是否支持当前提示。引用题目时使用双引号逐字引用。另一份核查检查这一步与题面、此前披露和当前困难能否配对。程序只发布通过核查的内容。4.9.1 新任务也默认关闭本机指导教师的原生思考。在 SFT 节点的“本地指导训练资料（CLI）”里可选择已登记的本机教师、单独开启教师思考；命令行对应 `--coach-model`、`--coach-url` 和 `--coach-thinking`。恢复任务按冻结清单读取。

每轮学生用新会话读取原题、累计发布的提示和已核实的学生发现。教师处理最近一次学生尝试，保留自己的披露记录。案例档案按时间顺序保存解题过程。4.9.1 单独导出训练目标：修正题的错稿与公开提示放入上下文，已给完整参考解的题归入教师示范。训练器用目标模型模板包装推理字段。[各目标的来源和监督范围](../local-training-validation.md)

常规披露任务沿用 4.9.1 的学生交付机制。推导通过核查后，程序用原题和完整解题轨迹构造助手前缀，关闭思考块，由完成这轮推导的模型续写正式正文。强模型接手解题时也负责交付该题。需要展示解法的题会检查正文中的解法和结论，只要求结果且首次直接答对的题可当场结束。在案例的 `finalization` 中可定位正式答复收据，`final_answer_provenance` 记录通过交付时的作者、模型和解题收据。解题轨迹含实际发布的教学提示，应按师生来源解读。

4.9.1 在原有独审之前精确复算教师提示中的显式数值等式。确认有错时保存原句、位置和各边计算值，自写提示走既有的一次重写；目录里的提示同样接受复算。学生受审推导及其正式正文中可确认的算术错误也会阻止交付。服饰题存档里一条被教师支持的连等式，其三边实际为 514、1126、610。烧杯题共 22 个烧杯，每个测试用三滴，共用 45 滴，已测 15 个，其中 8 个含铜，已测阴性为 7；存档里的教师提示指出了这一关系。学生使用的 22−8=14 对整个库存成立，是否答到所求对象仍由题面和语义审查决定。局部复算只提供可重现的数值反证。引文、反例、近似表达和含未知量的推导仍可送独审。

教师私下对学生的诊断也会复算。例如学生给出的中间量有独立依据，而教师评语把 `270 + 40 × 50` 算成 3500。程序保存教师原话、原文位置和复算值 2270，保留学生有证据的步骤，让这份教师意见保持待核。提炼长期经验时也会检查历史原稿。单位换算目前只覆盖能明确识别的英文存储量与页数、像素数关系。每件与总计、每天与每餐等范围仍要结合题意判断。

如果学生看过完整解仍然交付错，可以用同一份已独立核对的解做两项诊断。先在原生交付前缀上固定模型、采样和种子，仅改变输出语法，分别核对数值与格式。再将解放进标准聊天消息，明确要求读出已经写明的结论。对照逐次请求的题面、模板、输入 token、停止原因和正文。后一组衡量已知答案的读出能力，应独立于解题正确率及训练入库量统计。

### 让教师看见一条提示会把学生带向哪里

在 SFT 节点选择本机 Qwen 聊天模型，展开「本地指导训练资料（CLI）」，开启「引导学生执行下一步」，然后执行页面生成的命令。命令行也可在新输出目录使用 `--protocol-version 4.10.12`。选择的指导教师模型兼任预测器，两个角色各用自己的对话上下文。

1. **披露。** 教师发布一条通过核查的提示。预测器随后看到原题、已发提示、学生已确认的进展和本次局部任务。参考答案和未来的提示保留在教师侧。
2. **预测。** 预测器写下学生可能如何理解这条提示，选择一个可以从学生回复中观察的行为。它可以指向公开提示或已确认的学生原句。程序找到相符的出处后，才把操作引导送给学生。一次符合条件的续写增加一次本地模型请求。独立首答、已披露完整解的交付，以及由已认证算式计划驱动的计算练习沿用各自流程。
3. **执行与反馈。** 学生从独立会话作答。下一次拟写披露时，教师可对照预测、实际送达的引导，以及学生提交的结果。动作和结果分别记录相符、不符或未知。预测器对原因的文字解释始终标为推测。判断该题是否可入库，仍看完整推导与正式答案的核验。

教师写提示时就能看到每个可执行操作对应的学生提交要求。复核者把公开提示与选中操作的要求逐项对照。交给模型的提交要求只说明要做什么和要填哪些字段；各字段的期望值由程序保留，用于核对学生的提交。局部练习有一个当前任务。程序把先前的教师提示按发布顺序完整保留，并单列学生已核实的成果。预测器可以指出公开文字中的相关位置。回到整题时，最后一条用户消息是完整原题。排查「学生答了另一道题」时，可以直接核对学生请求的最后一条消息和当轮提交字段。

教师收到学生的实际提交、程序核验状态和预测类别。预测器设想的解法保存在预测收据中。某个值若只出现在预测收据里，就不能算作学生已取得的进展。恢复旧任务时以原清单的协议版本为准。

还原一轮时，到案例目录查看同轮的 `NNN-behavior_prediction.json` 和 `NNN-student.json`，可逐字核对模型看到的材料、作出的预判及学生实际收到的引导。`case.json` 的对应尝试保留两者的关联和观察。遇到引文定位失败，可从预测收据中的来源标识查起。若观察结果为未知，应先看学生停止原因与提交格式。

### 教师经验的同题对照

公开的[所求范围对照题](../../configs/guided-tutor-scope.example.jsonl)共用同一库存与抽样条件，分别询问已测合格数和全库存合格数。参考推导由输入适配器私下保留。先在开发题里选取具体失败，通过 `lib.guided_experience --role disclosure_teacher` 从实际教师草稿提炼候选经验。经过本地复算驳回的草稿可以作为负例，参数 `--case`、`--attempt` 和 `--revision` 指向原始记录。独审放行表示提示通过了该次检查，教学效果用后续作答衡量。

下面是独立封存分片的一次有界对照。先冻结一份带金标的 `worked_solution` 分片，按题文和来源排除开发、提炼及训练所用案例。两臂使用相同端点、协议与预算，只替换教师经验。示例里的 `candidate.json` 是冻结的 `disclosure_teacher` 经验；`holdout.jsonl` 是该轮单独保管的留出题。也可把输入换为上面的公开文件来演练两条命令。

```powershell
$common = @('--input', 'holdout.jsonl', '--protocol-version', '4.9.1', '--student-url', $env:STUDENT_URL, '--student-model', $env:STUDENT_MODEL, '--coach-url', $env:COACH_URL, '--coach-model', $env:COACH_MODEL, '--max-attempts', '3', '--no-until-correct')
python -m lib.guided_answers @common --output eval/baseline
python -m lib.guided_answers @common --output eval/candidate --experience candidate.json --reuse-worked-first-from eval/baseline
```

候选运行的清单冻结每题首答收据的摘要。在原始首答缺失时该入口会重新生成，应单列这类题。按同题同首答比较实际下发错误提示、下一轮已证实进展、同一学生在未获得完整参考解时的正确交付与实际调用数。两臂未进入辅导的首答、待核题和缺失收据分别计数。教师经验验证只评价冻结的教师行为，训练后的学生能力另用独立封存题检验。

4.8 系列的冻结任务继续由教师撰写末答。对于已确认的“推导正确、末答写错”，它把两个实际结论的对照补进推导，给交付教师一次重新作答机会，两次原始答复均可追溯。

学生在获得新方法或推出新结果时继续作答。较强模型接手需要单独记录：原学生先实际收到独立验证过的完整依据、过程与结论，如果此后完整作答仍有明确错误、也没有可信的新进展，才把同一份上下文交给配置的较强学生。阶段无法判定的记录留待复核。接手、参考答案已给、教师交付各自有出处，完整包后的答对单独统计。训练后的能力评测开启学生原生思考，另取未见题。

本地披露式师生流程由 `GenerateGuidedAnswer` 调度。`guided_llama.py` 按已冻结的角色、模型和思考开关决定采样；生成收据保存实际发包。4.6 支持教师从学生耗尽输出容量的首稿继续指导，4.7 为原版 Qwen3.5 接入分角色文本采样，并在正式答案中检查私有证据编号。[运行与采样说明](../../lib/contexts/workflows/infrastructure/README.md) · [训练数据的循环筛查](../../lib/contexts/training/infrastructure/README.md)

原生 CoT 返工 v8 使用 DeepSeek Beta 前缀续写：`ContinueNativePrefix` 复用原推理与已有评审，`NativeFeedbackAdapter.continue_prefix` 绑定原作者，模型设施以空 `assistant.content`、`prefix=true` 和完整 `reasoning_content` 发送一次请求。评审意见保留 system 归属，原推理与本次新增推理分别存档，训练正文逐字组合；不调用反馈工具、不增加独立评审。新建且所有原生作者均固定为官方 DeepSeek Chat 的任务采用 v8，其他供应商及旧冻结任务保留原协议。实际工具历史不能经删除伪装成无工具续写。使用、验证与限制见 [Beta 原生续写](native-prefix-continuation-2026-11-15.md)。下文的 v7 及工具反馈段落记录历史协议，不覆盖 v8 接线。

新版 Agent 数据制作由工作流的轨迹编辑作者处理，既有工具重放仍是另一条明确用途。Google 经验进入实际 CoT 编辑请求，流程建议与原执行分开，最终答案及工具证据由程序保留。范围与材料见 [Agent 轨迹优化](agent-trajectory-evolution-2026-11-03.md)。

知识问答与资料问答的逐题用途由 `workflows/domain/qa_learning_goal_routing.py` 持有；指导员在已有批次规划前固定用途，生成、可见资料、重答和审核据此投影。概率与图示在指导员节点配置，不新增选择 Agent，也不改旧配方。[接线与边界](mixed-qa-routing-2026-11-03.md) 说明默认 80/20、资料范围和历史兼容。

知识问答推理的保真表述编辑由 `workflow_knowledge_reasoning.py` 的 `knowledge_reasoning_editor` 和 workflows 域 `knowledge_reasoning_editor.py` 的纯工具／补丁合同持有。1.1 使用程序生成的原稿段落 ID，1.0 的逐字定位保留；问题和答案不改，完整原生推理保留，整理正文标记为派生解释，编辑状态不代表质量通过。[真实验证与剩余问题](knowledge-reasoning-editor-2026-11-02.md) 单独记录；尚未自动接入生产节点、默认导出或另一套评审循环。

原作者自行编辑由同模块 `knowledge_reasoning_self_edit` 提供操作说明，`prepare_self_edit_messages` 保留实际原请求、真实助手的答案及原生推理，再追加应用 system 要求。它不伪造用户发言或工具历史，也不补填缺失的历史推理；纯构造器不认证账号身份或保证缓存。一次 A 实测已经命中缓存，但重复编辑思考使本次总费用高于另开编辑上下文，详见上述验证记录。

自编辑 1.1 要求新宿主使用 `apply_local_edits` v3，程序拒绝整篇单块及拆块覆盖全部实质原文的提交，不把正式输出当作另一篇完整 CoT。不能安全局部修复时保留原稿待处理，不自动重答或追加评审；v1/v2 仅用于历史重放。当前独立验证入口已按新冻结任务的合同版本选择应用函数，旧记录不改。两份既有真实响应离线回放分别被阻止全文替换、保留四处局部补丁；这不是新版提示词的付费实测，生产节点尚未自动接入。

原生知识应用的初始规则与用途接线见 [教师知识与角色](teacher-knowledge-roles-2026-11-02.md#初始系统规则与用途接线)。新模板 1.7 明确区分任务事实、教师辅助知识、真实阅读对象和运行控制信息；规则属于生成节点，不是新增评审，默认不为旧任务或个人提示词覆盖热更新。

教师知识的消息角色对照见 [实验记录](teacher-knowledge-roles-2026-11-02.md)：同题、同文仅将 system 改为 assistant，本次仍出现来源旁白。该实验未改变生产默认，程序投放的教师块不作为真实助手历史导出。

内置经验 v6 的公共前缀只描述行为、证据、角色和输出要求，不包含作者署名、产品身份或开发历史。历史 v1–v5 仅用于重放已冻结配置，不能将其来源说明当作新默认要求；个人经验的清理另存版本，不改旧任务、原生推理或既有导出。

新建有指导员的原生 SFT 在 `native_sft_review_evolution_version=1` 下走“三次或四次”闭环：一次规划、一次首答、一次集中评审；首答通过即交付，明确缺陷才让原作者再答一次。`workflow.review_score@1.29.0` 在同次严格工具提交中产出质量判断、当前任务的临时指令与可选通用经验增量；程序生成 Memory Item 格式，复用已有经验库保存与本地索引，不另调提炼模型。`ReviewNativeSft` 决定是否优化，`OptimizeNativeSft` 复用原问题、合同和资料，临时要求与候选通用增量分别作为 system 指导。首稿与完整原生推理归档，第二稿明确未再评审；当前派生步骤的经验快照不改冻结配方或全局采用版。导入的非原生会话及旧配方继续原审核路径。

按需对话风格上下文由 `workflow_dialogue_contexts.py` 持有初始正文，`dialogue_style_context.py` 持有目录及来源合同。指导员 1.16 在原规划／续轮请求中选择少量粗类别；无线索发言者仅见选中卡片，原生作者加载选中正文。新任务显式冻结目录，原生推理和完整实际上下文一同保留；旧任务不迁移。使用与限制见 [对话风格上下文](../dialogue-style-contexts.md)。

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

历史原生返工采用公开注意事项 v7：评审模板 `workflow.rationale_check@1.12.0` / `workflow.review_score@1.25.0` 产出可独立理解的提醒；`RegenerateWithReviewGuidance` 将提醒作为 system 放在真实 user 后，用原作者重新生成。领域合同在 `review_guidance.py`，请求复用 `NativeFeedbackAdapter`。提醒保留在训练上下文，失败稿单独归档，新原生推理不拼入人工文字。允许自然引用实际提醒，事实错误与臆造规则不豁免。仍为未评审，不增加独立评审或理由改写调用。仅改变新任务默认和新模板版本，自定义提示词及冻结任务不热改。离线协议、导出及兼容验证已完成；两条 V4.1-Flash 实测共2次调用、26.25秒，输入6245/输出3935 tokens（含2713推理），估算$0.002470，未核实账单。备用响应前提和OCR可选路径改善，但01仍出现无用户依据的verbosity 5，02仍有节选/全文范围问题。后续离线溯源确认：该谱系22个请求有21个在发送前就带具体5，02由复跑脚本补入旧reasoning_content；01初次来源仍未定，撤回直接归为幻觉的定性。SDK白名单还漏记reasoning_effort，当前离线完整HTTP重建未发现SDK加提示词或本地检查点串用。来源证据和未执行的干净对照在 `.dataforge/verbosity-leak-audit-20261101/`；旧原生推理存档不删除。已有旧返工system原样复用，不能把本轮视作干净上下文消融。完整记录见 `.dataforge/native-system-guidance-v7-live-20261101/`，结果仍待处理。

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

## 分段指导作答

4.5 对新建任务默认启用本机教师的原生思考，包括披露拟稿、披露核查和正式答案。学生独立作答时仍关闭思考。每份教师回执分别记录开关、实际返回的推理通道和正式正文的结构校验。导出时，教师私下看到的答案键和教师私有推理留在运行记录中。旧任务从冻结配置恢复，新建任务可用 `--no-coach-thinking` 做同题对照。

4.4 将经过指导的正式答案交给 `--coach-model`。教师读取原始 system 与用户请求、完整解题记录、已核准的学生推导和答案键，再写可直接交付给用户的答案。常规交付包含结论及最短的充分论证。用户明确要求“仅给数字”“按顺序输出”等格式时，公开答案遵从该要求。教师可以标记证据不足。该调用替代学生的最后一次续答。教师回执归档为 `NNN-teacher_final_answer.json`，生成器在训练消息之外记录正式答案的作者和依据。旧版学生续答回执仍按原任务读取。

4.3 的学生按原题辨认所求对象、约束、信息边界和交付形式。教师用相同题意审查下一步提示。首答只交结果且核验正确的题继续分流到无思考样本。进入披露后，下一轮学生能看到已成立的前缀、独立发现和自己先前提出的可用方法。即使中间算错，后面一句独立成立的方法也可保留。原评审须指出学生原句所属片段、能独立支持它的唯一题面引文及依据。程序检查原文位置，在收据保存出处；私有评语不进入学生请求。方法是否足以推出结论仍需核验。

逐轮学生原稿与实际发布的教师提示按顺序构成训练 `reasoning_content`。完成单轮独立推导时使用原稿推导部分。4.3 由学生在闭合思考标签后续答。4.4 起，训练消息的 `content` 使用教师的正式交付。4.4.1 进一步要求教师说明哪些条件保证唯一、最早或最优，或给出可达到的界。仅需一个可行解的题，展示它符合条件即可。程序核对公开结论、输出格式及依据片段的归属。解释的语义正确性仍需另有证据。回归入口为 `tests/test_guided_answers.py`、`tests/test_guided_student_approaches.py` 和 `tests/test_guided_teacher_delivery.py`。

`lib/prompts/workflow_guided_answers.py` 持有作答者、无答案来源补写和私有指导员的角色模板。`GenerateGuidedAnswer` 运行两种来源用例，共享状态、线索次序、原文段落身份和答案键判定由 `workflows/domain/guided_answers.py` 维护。组合根 `lib/bootstrap/guided_answers.py` 接入 llama-server 原生非思考模板、审核请求、逐调用收据和 JSONL 投影。

输入 `gold` 可采用精确数值、单选字母或字面文本。没有独立答案键时需提供 `source_reasoning`。可选 `disclosure_hints` 按文件顺序提供预审的事实、思路或演算步骤，由程序在需要时逐条发布。每次模型请求都从当前进度重新构造；私人诊断、撤回稿和未来线索保留在审计边界。实际请求与完整响应存于每题目录，按 `student_request_messages` 重建单轮作答者所见内容。

JSONL 每行是一道题，`id` 是文件内唯一的字符串，`source` 保留出处。下列两行分别演示已知答案和只有来源推理的资料。

```jsonl
{"id":"groups","question":"把两组互不重叠的物品合并，原有两件和三件，合并后有几件？","gold":{"kind":"number","value":"5"},"source":{"name":"示例题"},"disclosure_hints":[{"id":"relation","kind":"idea","text":"合并互不重叠的集合时，元素数量可以相加。","source":"题目编辑"}]}
{"id":"demo","question":"给出保存项目的演示清单。","source":{"reasoning_author":"原回答模型"},"source_reasoning":"可以依次检查项目名称、保存操作与重新打开后的内容。","gold":null}
```

运行入口为 `python -m lib.guided_answers --input sources.jsonl --output .dataforge/guided-run --student-url http://localhost:8080/v1 --student-model MODEL_ID`。`--workers` 允许 1 至 4，实际并发取决于模型槽数。原生服务按槽验证至少 90,000 个 token 的上下文并为每次输出预留至少 32,768 个 token。`--max-attempts` 限制每题的学生作答次数，`--experience` 接受带版本的学生经验文件。

指导员按学生原文段编号划定可保留前缀，程序可另外核验错误位置之后的独立发现。程序按清单顺序披露下一条线索。3.1 版遇到与已确认学生原文或既有披露完整匹配的清单项，会记录原作者和证据并继续寻找下一条。匹配仅规范 Unicode 组合形式和空白，保留符号、大小写和标点。没有题级线索的题目使用一般方法提醒。每轮实际发出的模型请求保存在逐调用收据中，`case.json` 的 `clue_ledger`、`draft_versions` 和 `shared_student_findings` 可追溯披露、撤回、复用及最终正文的原作者。

同一输入和配置可在同一输出目录恢复。请求结果未明时使用 `--retry-interrupted` 显式重试。使用 `--export-only` 可离线重建 `accepted.jsonl`、`unverified.jsonl`、`quarantine.jsonl` 和逐轮 `feedback.jsonl`。含解题过程的有键题在程序核验答案且指导员确认正文成立时通过。无键补写即使通过正文审核也保留待核验状态。

对于只需数量、选项或确定文本的题，可加 `"answer_requirement":"final_only"`。3.6 的首轮只发题目和直接作答指令，关闭原生思考。程序将整份自然结束的回复与独立答案键比较，接受常见的答案标题或数学排版。需要单位时把完整值与单位登记为 `exact` 键。已验中的直接原答无需导师调用，训练记录标明 `nonthinking_direct` 并保留学生原文。证明、解释和多项要求的任务使用默认 `worked_solution`，继续核对完整推导。

3.7 的 `unaided_reasoning` 取首稿中经核查的原始推导，以明确的终答标签作为边界。`guided_reasoning` 从实际发出的提示与学生的逐轮原稿重建带来源的思考轨迹。有键题在解答通过核查后，由同一学生模型从这条前缀继续生成终答。程序核对这次新答。终答有误时，剩余的学生轮次可以围绕原题所求的量、单位、选项或文字要求重新推导，再次尝试交付。训练样本的助手消息分别存储推导和新终答，源题可用 `task_system` 保留需要传给学习者的原始系统要求。对照 `case.json` 的 `finalization` 和各轮的 `formal_answer` 可找到服务回执和核验记录。

3.9 将教师续写和教师稿审核放在 [review_guided_disclosure.py](../../lib/contexts/workflows/application/review_guided_disclosure.py)，公开证据身份、引文和发布校验归 [guided_disclosure.py](../../lib/contexts/workflows/domain/guided_disclosure.py)。写稿者看到此前已发布的教师文字、学生已成立的发现及当前困难，题源提示限定本次披露强度。审查者独立核对拟发文字和它与上一条提示的关系。被拒的原稿及理由只留作教师改进依据，学生只见已经通过的教师原文。正常发稿需要 `disclosure_draft` 与 `disclosure` 两份调用回执；若需返修，再产生 `disclosure_rewrite` 与 `disclosure_recheck`。同一轮中学生解答的评审另存 `coach`。

4.0 对独立首答中可直接核对的错误值保留金标比对记录。教师可以从题设找一个可检验的条件来帮助学生；该次答错的数值或选项只标为学生的作答。教师拟稿的 `question_quote` 及独审引文都能定位到具体题面。已拒候选的公开文字如在重写时没有变化，使用原拒绝记录，收据标明 `reused_prior_review_revision`。学生步骤引用自身或后续步骤时，保留前缀止于该处，独立核实过的事实仍可复用。一份已经审过推导的解答可在闭合思考段后只交付约定的最终值，程序单独核对。冻结的 3.9 与更早任务继续使用各自的协议。

4.1 支持经过题源独立检查的可选教学微步。每道题的 `verified_teaching_steps` 记录回执摘要和有序的 `steps`。每项有唯一 `id`、`kind`（`premise`、`operation` 或 `counterexample`）、题面逐字 `quote`、Unicode 字符位置 `span`（左闭右开）、公开操作 `operation` 和私有核验说明 `verification`。回执以规范 JSON 的 SHA-256 绑定题目 ID、全文、金标和全部微步。操作者应从确实完成题源核验的独立记录提供可信回执集合。导入会拒绝签名未受信、引文错位或内容被改过的集合。

教师按当前学生困难和实际已发的提示选择一项，也可以自行写一条。独审看拟发布的原操作及历史，可以否决与本轮无关、重复、跳步或泄底的提示。采用已核验微步时，独审按 ID 定位本题引文与上次披露；程序把已签名的操作原文交给学生。教师和审查者的自由说明留在各自收据。前次已发布的微步不会再次提供给选择器。没有配置微步的任务继续使用自写流程。

4.1.1 在教师当前困难快照里传递独立终值核验状态 `submitted_answer_status`（`incorrect`、`correct` 或 `not_checked`）和有无可检查推导 `derivation_available`。这使教师在只收到错误终值时仍能以题面为依据选择第一步，同时保留对学生出错原因的不确定性。该状态不会附带金标值。4.1.0 的教师发包继续使用原版快照。

4.2 的新任务默认持续辅导。教师遇到困难时，程序可以从本题已核验且未审过的微步中按序提名。独审同时检查这条提示与本轮学生困难、题面证据和上一条已发提示的对应关系。公开提示复述题目时，引号必须成对，引用的字符必须逐字对应原题。教师自己的推导可以独立成句。程序只把通过核查的原文发给学生，下一轮保留此前所有已发提示。核对实际交付时，先看提示的 `review_checkpoint`，再打开下一份 `NNN-student.json` 核对 `new_clue` 和 `previously_disclosed_clues`。只出现教师审稿收据表示该候选已经送审。

独立首答正确时直接收为无思考样本。经过披露的题目需要核对学生最终实际交付的答案；正式答案错误仍可进入下一轮。持续模式用 `--max-attempts` 衡量连续无进展，仍有可核验新步骤时会继续。需要限定整个实验的学生尝试次数时，可显式使用 `--no-until-correct --max-attempts N`。旧任务恢复时读取创建时冻结的配置。

4.2.1 让学生稿审查员在指出证据时只能选题设、演算或该段之前的步骤。程序把段序号恢复为归档中的稳定 ID。若审查员给出的依据引用自身或未来步骤，可定点补充审查证据一次，学生的原推导继续保留。两次都无法给出合法依据时，题目保留待核。需要以新审查合同修复旧任务时，给新输出目录指定 `--reuse-matching-from OLD_RUN`，复用题目、角色、端点、经验、校验清单及完整请求一致的原始成功回执。旧任务的首答本身来自其他运行时，同时指定最初的 `--reuse-first-from FIRST_RUN`。运行摘要分别列出跨任务复用和新产生的调用。

3.2 版由指导员指定第一个无法成立的原文段。程序按段编号计算保留边界。可以为每题加入 `progress_checks`，如 `[{"id":"possible-k","kind":"finite_set","variable":"k","values":[1,4,7],"meaning":"k 表示序列中 1 的个数。","source":"独立枚举"}]`。只有学生以明确肯定的归属关系写出相同变量和完整整数集合时，程序才向下一轮投影其原文公式及审定含义。被撤回段里的其他说法留在档案，未命中的规则留在输入端。程序保存原稿段编号、字符偏移和当时已发布的线索编号。

使用 `--reuse-first-from OLD_RUN` 可在新运行复测同一份首答。程序核对旧任务和原请求的摘要、题目标识、端点与模型，并比较实际发送 JSON 的 UTF-8 字节。匹配成功后，首份学生收据记录原始摘要和重放来源，后续指导与学生续写使用新协议发起实际调用。任一不匹配即报告中断。`summary.json` 的 `model_calls` 分别统计本次运行完成的请求、重放的首答以及结果未明或失败的请求。
从 3.3 起，指导员在完整通过、需修改的具体段落和证据不足的具体段落中选择一项。程序据此生成评审状态和保留范围。有序列表序号随正文保存，单独的编号不能当成学生发现。已启动的任务使用各自冻结的协议。

3.5 会在输出达到上限并隔离整题时，用本题登记的有限集合规则核对学生的可见正文。该轮的 `attempts[].program_confirmed` 逐项记录规则来源、原文字符跨度和轮次。复述已存结论会关联最早的段编号。原生私有推理、未核验的小计和未完成的推导均不进入这些记录。
