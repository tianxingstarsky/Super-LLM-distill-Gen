# 组合根：找到服务，再选择实现

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
