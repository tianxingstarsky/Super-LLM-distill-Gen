# 组合根：找到服务，再选择实现

`review_evolution.py` 为原生 SFT 的一次评审装配既有严格工具传输，以及同次经验提案的追加保存和本地索引。它不调用 Google 提炼入口；通用提案与任务临时指令分别留痕，当前派生回答可用候选增量，冻结配方与全局采用版本不热改。

`dialogue_style_context_catalog` 返回可冻结的初始交流风格目录，不启动模型。具体正文归 prompts，类型与认证归 workflows domain；目录按直接表达、情境叙述、探索讨论、协作推进和情绪交流组织，与知识领域无关。

`contextual_reasoning_memory` 以这些风格的简短说明检索已采用的原生推理经验，在创建时冻结完整目录和来源收据。它只用本地嵌入，不学习、不激活版本、不调用生成模型；未选目录不会进入本轮作者请求。

`google_reasoning_bank_application` 为新提炼显式装配跨任务经验指导 1.0.0。原版入口、共享模型网关与检索仍复用 Google 适配器；`memory_policy_version=None` 保留旧提炼请求。构造服务不会调用模型，旧经验仅在用户明确学习并采用后才有新版本。

`planned_sft_reanswer.py` 装配固定任务重答用例、原生生成端口与审核域的已封存父版选择器。这里只连接端口，不新增指导员、评审循环或模型 SDK；旧任务没有显式版本标记时维持原路由。

`review_control_text.control_text_suggestion_application(output)` 装配评审匹配建议的读取与状态服务。
构造和读取不会发出模型请求；只有同次自动评审产生真实原句证据时才写入待确认队列。
节点页面使用其有界列表、认证详情和确认后状态接口，节点配置仍经 CreationDraft 保存。

`datasets.qwen35_training_encoder(tokenizer, ...)` 装配可选训练加载适配器。
调用方提供实际本地 tokenizer；该工厂不安装训练框架、不加载权重，也不改变生产导出。
可选 `reasoning_reference_text` 设置训练读取时固定控制标记的目标文字，默认“思考已开启”。
显式 `source_control_profile="deepseek-v4.1-flash-control/v1"` 标记已观察到的
DeepSeek 详尽程度自述；`verbosity_reference_text` 可再选择目标文字，省略则保留标记。
详尽程度文本不参与五档思考强度映射，旧调用默认保持原文。
`control_text_config` 接收已读取的 JSON 配置，支持总开关及逐条修改，完整取代旧文字参数；
关闭后保留控制原文。工厂不读配置文件；示例见 `scripts/training/control-text-deepseek.json`。
用例 `datasets.api.iter_training_examples` 负责惰性遍历，说明见
`docs/training-reasoning-control.md`。

新原生任务冻结 `native_feedback_guidance_version=7`；`native_feedback_repairer` 仍装配同一用例，其 `execute` 转交 `RegenerateWithReviewGuidance`，在真实 user 后附公开 system 提醒并重新生成。旧 v1–v6 路由、检查点与冻结任务不热改；组合根不自动挑选父回执或启动返工。

`native_feedback_repairer(runtime)` 对显式采用 v5/v6 的冻结配方返回支持 `execute_from_feedback` 的同一用例；此处描述保留的旧工具续写路径，新任务默认为公开注意事项 v7。v5/v6 真实尝试未解决原生旁白，不能自动升级为默认。该入口显式复用已交付工具反馈的认证回执，只追加一次原作者请求；正常 `execute` 和历史指导继续保留。组合根不挑父回执、不自动返工，不新增 MCP 服务或第二套调度器。

`native_feedback_repairer` 按任务冻结的 `native_feedback_guidance_version` 选择工具反馈引导；缺少标记使用 v1。新版 v2 允许评审理由以原作者第一人称自然转折继续，但不将工具文字当成原生推理。

`native_feedback_repairer` 装配原作者工具反馈续写，复用模型调度、原节点身份与检查点。构造不会请求模型，调用不会购买独立评审。

`dialogue_generation.continue_native_dialogue` 只连接已认证的显式原生首答返工。它通过 workflows API 装配普通多轮用例及固定作者节点的适配器，不自行产生问题、评分或调度请求。

这里把应用用例与文件、数据库、模型客户端等适配器连接起来。工厂返回应用对象；执行任务、模型请求或返工，仍须调用该对象的相应方法。页面不应自行拼装生成引擎。

先按需要完成的业务动作查下表，再进入对应 application 用例看规则。工厂里找不到业务主循环是正常的：这里负责依赖选择，不负责调度决策。

## 页面与外部入口使用的服务

| 要完成的操作 | 工厂入口 | 返回服务与边界 |
| --- | --- | --- |
| 创建、恢复、停止任务及查询状态 | [workflows.py](workflows.py) · `workflow_application` | `WorkflowApplication` 兼容门面，分别注入运行、数据集、Agent 审核和报告服务 |
| 查看样本、统计分布、打包和下载 | [datasets.py](datasets.py) · `dataset_application` | `DatasetApplication`；读取已有产物，不创建生成引擎 |
| 查找可复用资料与已生成文件 | [asset_catalog.py](asset_catalog.py) · `asset_catalog_application` | `AssetCatalogApplication`；资料目录和版本身份校验 |
| 查看任务完整报告 | [workflow_reports.py](workflow_reports.py) · `workflow_report_application` | `WorkflowReportApplication`；便携报告读取与生成 |
| 导入 DSH 会话数据库 | [conversation_import.py](conversation_import.py) · `conversation_import_application` | `ImportConversations`；外部转换、预览、明确确认后保存到资料缓存 |
| 按选项补充导入会话的缺失推理 | [conversation_reasoning.py](conversation_reasoning.py) · `conversation_reasoning_preparer` | `PrepareConversationReasoning`；复用输入节点的冻结模型、提示词及检查点，构造不调用模型 |
| 缓存上传文件 | [local_inputs.py](local_inputs.py) · `local_input_application` | `LocalInputApplication`；持久输入缓存 |
| 查看文档片段 | [document_previews.py](document_previews.py) · `document_preview_application` | `DocumentPreviewApplication`；本地来源预览 |
| 准备文档语料 | [document_corpus.py](document_corpus.py) · `document_corpus_application` | `PrepareDocumentCorpus`；本地读取、保留结构的分块与去重 |
| 维护知识库连接和来源 | [knowledge.py](knowledge.py) · `knowledge_application` | `KnowledgeApplication`；本地持久知识库服务 |
| 编辑节点模型选择 | [workflow_node_models.py](workflow_node_models.py) · `workflow_node_models_application` | `WorkflowNodeModelsApplication`；复用模型目录，区别于直接发出模型请求 |
| 保存端点、能力与并发设置 | [backends.py](backends.py) · `backend_application` | `BackendApplication`；配置服务，不代表另设模型配置页面 |
| 保存生成设置与创建草稿 | [generation_settings.py](generation_settings.py) / [creation_drafts.py](creation_drafts.py) | `GenerationSettingsApplication` / `CreationDraftApplication`；设置与未启动草稿 |
| 管理个人提示词 | [prompt_library.py](prompt_library.py) · `prompt_library_application` | `PromptLibraryApplication`；按用户隔离的模板库 |
| 查看、提炼和保存风格经验版本 | [experience_evolution.py](experience_evolution.py) · `experience_evolution_application` | `EvolveExperience`；仅显式提案时加载模型，不热改正在执行的配方 |
| 人工设计问答与连续返工 | [workflows.py](workflows.py) · `human_augmentation_application` | `HumanAugmentationApplication`；持久会话，返回的工作流须显式启动 |
| 人工制作数据集 | [manual_datasets.py](manual_datasets.py) · `manual_dataset_application` | `ManualDatasetApplication`；本地手工样本库 |
| 发布对话数据与质量报告 | [releases.py](releases.py) · `release_application` | `ReleaseApplication`；原有对话发布入口，区别于工作流数据集打包 |
| 读取命令事件 | [monitor.py](monitor.py) · `monitor_application` | `MonitorApplication`；指定事件文件，不承担工作流调度 |
| 读取本地工作空间信息 | [workspaces.py](workspaces.py) · `workspace_application` | `WorkspaceApplication`；保留历史内部标识，不要求用户选择工作区 |

## 生成执行中的装配入口

这些工厂接收现有运行时 `runtime`，沿用已冻结配方、模型绑定、检查点和收据。名字中的 execution、generation、request 表示返回对象的用途；调用工厂本身不是开始执行。

| 执行职责 | 工厂入口 | 返回用例 |
| --- | --- | --- |
| 独占运行会话、恢复与取消 | [workflow_execution.py](workflow_execution.py) · `workflow_execution` | `ExecuteWorkflowRun`，内含普通批次 `GenerateDatasetBatch` |
| 多轮角色、历史与自然停止 | [dialogue_generation.py](dialogue_generation.py) · `dialogue_generation` | `GenerateMultiturnDialogue` |
| 大批量轮次与收据恢复 | [production_execution.py](production_execution.py) · `production_execution` | `ExecuteProductionRounds` |
| 单个生产窗口的阶段依赖 | 同文件 · `production_batch` | `GenerateProductionBatch`；不负责推进下一轮收据 |
| 普通 SFT / 指导员指定问题 | [node_generation.py](node_generation.py) · `sft_generator` / `directed_sft_generator` | `GenerateSft` / `GenerateDirectedSft` |
| 原生、风格化和前缀 CoT 路径 | 同文件 · `cot_generator` | `GenerateCot`，注入来源查询与显式数值参考核验 |
| 推理修剪与训练格式投影 | 同文件 · `reasoning_trimmer` | `TrimReasoning`，保留流式记录接口 |
| 构造正负偏好对 | 同文件 · `preference_generator` | `GeneratePreferencePair`，优先复用可用的同题失败稿 |
| 原作者修复严格提交或局部编辑 | 同文件 · `authored_submission_repairer` | `RepairAuthoredSubmission`；认证原作者与完整请求前缀，不换独立编辑模型 |
| 节点请求与检查点重用 | [workflow_requests.py](workflow_requests.py) · `workflow_model_request` | `RequestNodeModel`；`workflow_request_adapter` 单独提供对应检查点端口 |
| 供应商文本 / JSON / 工具决策请求 | [model_requests.py](model_requests.py) · `model_request` / `json_response_request` / `tool_turn_request` | `ExecuteModelRequest` / `RequestJSONResponse` / `ExecuteToolTurn`；注入 SDK 与 Tenacity 重试端口 |

节点恢复、供应商重试、人工返工是不同层级。不能因为它们都能“再试一次”，就合并为同一个循环。工具决策请求只返回模型的工具调用，工具执行由调用方负责。

## 审核、修复与证据核验

| 要完成的操作 | 工厂入口 | 返回服务与边界 |
| --- | --- | --- |
| 人工审核 Agent 轨迹 | [agent_review.py](agent_review.py) · `agent_review_application` | `AgentReviewApplication` |
| 人工审核 SFT / CPT / 偏好对 | [sft_reviews.py](sft_reviews.py) / [corpus_reviews.py](corpus_reviews.py) / [preference_reviews.py](preference_reviews.py) | 各训练格式的审核应用；偏好服务通过 `target` 区分格式 |
| 保存问题样本并交给人工返工 | [review_rework.py](review_rework.py) · `review_rework_application` | `ReviewReworkApplication`，连接持久人工增强会话 |
| 给旧版字段编辑器提供模型建议 | [review_edits.py](review_edits.py) · `review_edit_application` | `ReviewEditApplication`；不是自动审核发布入口 |
| 单候选自动评分与有界修复 | [automatic_review.py](automatic_review.py) · `automatic_review` | `ReviewPreferenceView` 包装 `ReviewCandidate`，偏好格式共享同一份候选审核 |
| 流式审核集合并对齐 CoT/SFT 血缘 | 同文件 · `review_collections` | `ReviewGeneratedCollections` |
| 认证父版本后执行修复分支 | 同文件 · `repair_branch` | `ExecuteRepairBranch`；不在工厂中发起返工 |
| 分窗口核验论断 | 同文件 · `claim_windows` | `ReviewClaimWindows`；请求配置交给原检查点适配器 |
| 认证同一内容的训练格式视图 | 同文件 · `training_format_lineage` | `AuthorizeTrainingFormatView`；格式认证不等于质量通过 |
| 核验偏好回答质量与正负差异 | 同文件 · `preference_assessment` | `AssessPreferencePair` |
| 对修正后的推理运行配置的清单核验 | [reasoning_checklist.py](reasoning_checklist.py) · `reasoning_checklist` | `CheckReasoning`；继续使用冻结的 CoT 模型与提示词，评分原始结果另存 |

`cot_generator` 通过 review 域的 `VerifyReferenceAnswer` 和 Math-Verify 适配器核验显式数值参考。数值等价只证明该项结果，不替代来源范围、推理质量或完整审核。

## 参数与维护边界

- `runtime` 是当前任务的执行上下文。`runtime.recipe` 是冻结配方，不是页面里的可编辑草稿。
- `root` 通常指配置或本地服务根目录，`output` 指任务与产物目录。`experience_evolution_application` 的 `root` 专指个人经验库保存位置；它仍从项目配置加载模型。
- `workspace_id` 是已有缓存隔离标识；`user_key` 是个人模板或经验库身份。保留它们的含义和存储映射，不把界面简化变成历史数据迁移。
- `hooks` 是旧运行时保留的注入点。检查点适配器继续接收同一组钩子，不创建第二套请求实现。
- 可以在这里同时导入 application 与 infrastructure。业务条件放回用例，外部读写放在适配器，跨域规则通过 `api.py` / `contracts.py` 取得。
- 现有工厂名是公开导入合同。说明与局部命名可以改进，不能为了统一外观让旧调用方失效。

修改依赖装配后运行 `tests/test_ddd_architecture.py` 和对应业务回归。纯说明修改不跑业务测试；已有验证通过且没有新疑点时停止追加测试。
