# 运行时与旧基础设施

`CreationDraftFile.compare_update` 在同一文件锁内检查字段前置值、合并本次更改并
原子保存，防止不同会话采用规则时互相覆盖。冲突、无效输入或不可读取的旧草稿均
保持文件不变；未修改的字段继续保留。该接口不改变旧 `replace` 的整表自动保存语义。

显式 v3 控制文字配置只在最终训练文件写盘时生成成对标记；打包评审、生产验收及
候选收据仍使用原稿。报告、RLAIF 人工发布和返工的导出身份索引使用同一标记视图
核对文件，返工取出的记录与原生推理保持原样。模型请求移除 v3 程序配置元数据，
不把规则内容变成生成或评审提示。v1/v2 与未配置任务的文件和请求形状不变。
旧 `dpo()` 入口与新用例共用来源配置投影。评分排序后配置跟随原回答，即使原回答
成为 rejected 也保留其规则，不把相同规则猜给独立生成的 alternative。

新原生任务继续冻结 `native_feedback_guidance_version=4`；v5/v6 仅供显式试验，真实复跑未稳定消除原生旁白且消耗上升，不能默认推广。v4 让已保存的评审理由连同原文通道定位和真实用户来源进入原作者工具续写。审查员与受审助手保持各自身份，反思由原作者继续产生；新答案替换受审轮，不叙述内部编辑事件，不设默认篇幅目标，也不新增改写调用。缺少标记的旧配方沿用 v1，已冻结 v2/v3/v4 不热改。

新原生首答返工在全部父候选持有作者会话时冻结 `native_revision_context_version=2`，否则保持历史 v1 路由；不会热改已保存任务。v2 原稿和审查反馈经工具回合返回同一作者，输出待审核且不新增独立评审。

`workflow_qa_director` 的开放对话路由按每个来源选择：新配方显式 `auto` 的纯合成场景复用指导员的首问与续问，省去无私有资料时的简报核验、独立改写和适配核验；附加资料和显式 `separated` 仍走原分离发问路径。初次请求和后续步骤保留 `question_routing` 回执，混合批次逐候选声明路由；回答、完整对话与入库校验不在此处变更。旧配方没有新字段时，请求和检查点保持原样。

`create_run` 新任务默认 `open_dialogue_question_mode="auto"`，冻结按来源选择的发问路径：纯合成开放场景可由指导员直接发言；有资料、工具或私有事实时保留独立发问。`separated` 明确强制分离，显式 `None` 保持旧创建行为。`reuse_initial_native_response=True` 仅允许已认证的首轮原生失败返工：复用完整首答并诊断续写，不代表训练合格；该动作不向后续人工会话自动继承。原任务和其缓存不升级。

创建任务冻结 `dialogue_context_scope_version=1`，具体回答范围规则归 workflows 域。普通回答角色仅转发该域已投影的 `answer_context_scope`，不在旧基础设施再实现一套来源判断。旧冻结任务的来源与请求不自动升级。

`workflow_qa_director._closed_book_question` 为用户发言者及其简报核查投影公开历史时，先通过 workflows 合同排除该任务确切注入的首条原生经验 system，再复用 `visible_dialogue` 检查角色与正文。未知 system 仍拒绝；原生推理不进入用户模拟，训练历史与已有指导员请求键保持不变。验证见 `test_separated_dialogue_generation.py`。

指导员 1.13 起的定向修复会区分遗漏、空数组和非原文引用，并指出当前候选；开放需求的目标和限制可作为私有任务依据，不当成事实认证。闭卷任务仅对用户隐藏资料，不能省略私有引用。旧模板的修复请求保持原样，逐字、选区和可见性校验不变。验证见 `test_qa_director_typed_contract.py`。

执行入口接受已创建的第 42 版原生 SFT／多轮配方，与 `create_run` 的版本一致。
仍在运行锁内核对冻结配方哈希、节点提示词和来源；未知版本或被修改的配方继续拒绝。
验证见 `test_native_joint_cot.py` 的组合根启动校验。

新建资料阅读任务在 `create_run` 校验后调用 workflows 的 `normalize_new_reading_generation`，统一走可见的指导员与原生多轮节点。旧场景提示词保留但不参与新任务，`reading_session_migration` 明确记录不生效的自定义 ID；载入已冻结旧任务时不做迁移。

新建问答任务在 `training_workflow.create_run` 复制固定来源后，调用 workflows 的 `prepare_new_dialogue_director` 固定必需的指导员。`structured_source_kind.py` 流式复用现有对话格式适配和完整性合同，只有全部来源都是非空完整会话时才允许跳过；JSON 中的文档、混合或未知记录不能凭后缀绕过。CPT-only 与返工不强制；已冻结任务及检查点不迁移。

导入对话的可选 `conversation_reasoning` 配方由 workflows 用例处理。运行时在读取可复用的原始来源缓存后、写入任务输入检查点前装配该用例；普通批次与生产轮次共用这一入口。它不回写资料库，没有该字段的历史任务保持原行为。

`workflow_report.py` 在样本正文前显示输入推理的原有、合成和缺失轮数，逐条标明助手消息的位置。输入收据与后续改写分开，补全不作为审核通过；训练文件不附加这些内部收据。验证：`test_imported_reasoning_provenance.py`。

`workflow_report.py` 对 CoT 抽检未覆盖的记录展示中英文说明，区分节点本地检查与后续实际审核。训练投影及历史收据不改写。

新配方的 `external_reasoning_import_version=1` 在现有流式来源库入口调用 datasets 的纯合同，接收 NeMo / OpenThoughts 明确分隔的推理结果。旧任务不改变解释方式；原文件与解析缓存不改写。`dialogue_role_messages` 将风格改写原稿放入私有编辑工作单，不冒充用户发言或事实资料。验证见 `test_cot_style_transfer.py`。

新建严格提交任务额外冻结 `authored_local_edit_version=1`，CoT 直接生成和来源工具会话都预先声明局部编辑工具，使后续显式编辑可保持原提交请求的同一工具前缀。标记不自动购买编辑请求，不修改既有任务或导出。具体工具、作者身份和修改用例均位于 workflows 域；来源会话收据提供实际提交键，并保留已发生的工具历史。

知识指导员与旧 CLI Agent 保留既有调度。资料缓存、SQLite 租约和认证收据继续保留既有格式；知识库远端协议委托 documents 域官方 SDK 适配器。

官方 DeepSeek 新任务现冻结为配方 39，同时启用严格工具和 `authored_task_contract_version=3`。配方 36 及以前的缓存、请求、任务和导出不迁移。新任务增加当前调用的选段范围投影与交付语言检查，具体规则仍在 workflows 域；历史记录继续可复现。

新建配方 35 任务若 CoT 生成绑定的固定端点为官方 DeepSeek Chat，则冻结为配方 36 并启用严格工具提交。按端点快照选择，不凭后端别名猜测；其他供应商、旧运行与导出字段不变。协议与投影位于 workflows 域，SDK 严格传输位于 models 域。设计见 `docs/architecture/strict-authored-submission-2026-10-26.md`。

`training_workflow.create_run` 为显式配置 CoT 生成中推敲（`self_check.version=7`）的新任务冻结配方 35，增加末尾要点位置标记，并保留配方 34 引入的阶段合同。配方 34 及更早任务保持原行为。规则选择、阶段合同和生成行为位于 workflows 域；既有任务、缓存键及导出格式不迁移。控制台新草稿使用此配置，直接 API 调用可显式传入，历史调用未指定时保留旧行为。

此目录仍较大，包含未完成迁移的实现和旧路径兼容文件。已迁移的实现不要继续在这里追加。

| 查找内容 | 当前入口 |
| --- | --- |
| SFT、CoT 与推理修剪算法 | `contexts/workflows/application/{generate_sft,generate_directed_sft,generate_cot,trim_reasoning}.py` |
| 节点请求与流式记录 | `contexts/workflows/application/request_node_model.py` 与 `infrastructure/model_request_adapter.py` |
| 尚未拆分的节点与运行支持 | `training_workflow.py` 的 `TrainingWorkflowRuntime`；`Workflow` 是兼容别名 |
| 普通批次执行顺序 | `contexts/workflows/application/generate_batch.py` |
| 大批量轮次与饱和停止执行 | `contexts/workflows/application/execute_production.py`；本目录旧路径仅转发 |
| 对话指导与知识上下文 | `workflow_qa_director.py`、`workflow_knowledge_agent.py` |
| 多轮生成 | `contexts/workflows/application/generate_dialogue.py` |
| 自动评分、修正、重新评分 | `contexts/review/application/review_candidate.py`；本目录旧路径仅转发 |
| 资料缓存与原件快照 | `source_library.py`、`source_snapshot.py`、`workflow_source_library.py` |
| Docling 解析 | `contexts/documents/infrastructure/docling/`，本目录同名前缀文件仅转发 |
| 运行只读与文件合同 | `workflow_store.py` |
| 流式输出与磁盘记录 | `workflow_stream_journal.py`、`workflow_rows.py` |

路径均相对 `lib/`，表中本目录文件除外。基础设施实现应用端口；领域判断不要写进 SQL、SDK 参数拼接或 UI 回调中。剩余运行时通过适配器接到新用例，避免一次改动同时改变历史配方和存储格式。

临时数据、权重、真实任务报告放在受控缓存目录，不提交到代码目录。外部 dsh SDK 仍在独立插件包中，不在这里引入。

新建证据策略 2 任务以配方 32 冻结批量首审、定向补交标记；已保存配方不自动升级。运行准入清单同时支持 31 的无损响应边界和 32 的批量提交调度。业务校验仍由 workflows 域执行，创建入口只冻结版本和配置。

SFT、CoT、推理修剪和通用节点请求的旧方法只负责装配及调用新用例。CPT、偏好构造、质量辅助、资料缓存和部分状态管理仍有旧实现，不将此目录描述为全部迁移完成。

配方版本 29 仅负责冻结证据策略 1 与核查模板 1.5.0，具体判断和调度仍在 workflows 的 domain/application。旧配方模板与导出合同不升级。

配方 30 对应显式证据策略 2，并冻结核查模板 1.6.0；历史配方 29 及以前仍使用原模板、缓存身份与输出字段。

## 原生首轮续跑的人工返工入口

`FilesystemHumanSessionDriver.create_session` / `save_feedback` / `revise_round` 为原生多轮首轮失败创建独立子任务；保留原指导员配置供后续轮次使用。仅逐字匹配历史注册默认值的指导员和原生回答模板改用当前默认；用户自定义模板、旧任务与旧检查点不改。父版本与原作者端点由 `AuthenticatedRepairAdapter` 在全部模型调用前认证。当前仅支持开放需求的完整首轮失败记录，其他断点不声称可续跑。

显式首答续跑在人工会话准备和 `create_run` 两个边界均保留原 `director` / `multiturn` 模型与端点快照。审核配置只覆盖 `review` 角色，不得替换作者；即使关闭打包抽检，每轮评审使用的 `review.jev` 仍保留。纯文本返工继续裁剪不执行节点。验证：`test_native_dialogue_continuation.py` 的真实会话创建、反馈、子任务准备及父版认证回归，整个准备过程不请求模型。
