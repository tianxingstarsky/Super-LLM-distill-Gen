# 运行时与旧基础设施

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
