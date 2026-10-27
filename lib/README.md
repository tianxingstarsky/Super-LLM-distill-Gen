# 代码导航

网络重试、可选追踪和知识库协议复用 Tenacity、Langfuse 和 Qdrant/OpenAI SDK。现有业务编排保持原实现；组件取舍见 [开源运行时说明](../docs/architecture/open-source-runtime-2026-10-26.md)。

先按业务找代码，再看所在层。完整约定见 [架构指南](../docs/architecture.md) 和 [命名与职责](../docs/architecture/naming.md)。

| 目录 | 找什么 |
| --- | --- |
| [contexts](contexts/README.md) | 工作流、数据集、审核、模型配置、文档处理的正式业务边界 |
| [bootstrap](bootstrap/README.md) | 创建用例，注入文件、数据库、运行时实现 |
| [presentation](presentation/README.md) | 页面和交互，不承担业务判断 |
| [domain](domain/README.md) | 尚未迁入 context 的纯规则，以及旧导入别名 |
| [application](application/README.md) | 尚未迁移的用例、端口和旧入口兼容门面 |
| [infrastructure](infrastructure/README.md) | 剩余执行器、文件存储、模型与解析适配器 |
| [prompts](prompts/README.md) | 有版本的提示词资产 |
| [adapters](adapters/README.md) | CLI 管线的数据格式入口 |
| [components](components/README.md) | 控制台嵌入的浏览器组件 |

`webapp.py`、`cli.py` 是入口。`llm_client.py` 和 `model_tool_calls.py` 只转发到 models 域的唯一实现；其他 `model_*` 仍承接共享调度、流式传输及协议辅助。`doc2corpus.py`、`workflow.py` 保留兼容入口。不要继续向这些入口追加领域规则。

新增功能先决定所属业务域；用例中需要外部能力时，在该域 application 定义端口，在 infrastructure 实现，并由 bootstrap 装配。历史任务、缓存键、提示词版本及导出字段的变化须单独设计迁移。

执行链维护入口：

| 想修改的行为 | 从哪里开始 |
| --- | --- |
| 普通批次、运行恢复和取消 | [工作流应用层](contexts/workflows/application/README.md) 的 `ExecuteWorkflowRun` / `GenerateDatasetBatch` |
| 多轮角色调用、自然停止、逐轮与整段评审 | 同目录的 `GenerateMultiturnDialogue` |
| 大批量窗口、收据恢复、质量饱和与最终补充 | 同目录的 `ExecuteProductionRounds` / `GenerateProductionBatch` |
| 自动评分修正、重复稿停止、人工待复核和返工 | [审核应用层](contexts/review/application/README.md) 的三类自动评审用例 |
| SFT、指导问答、CoT 与推理修剪 | [工作流应用层](contexts/workflows/application/README.md) 的 `GenerateSft` / `GenerateDirectedSft` / `GenerateCot` / `TrimReasoning` |
| 节点提示词、来源角色、检查点与流式请求 | 同目录的 `RequestNodeModel`，具体请求由 `CheckpointedModelRequest` 适配 |
| 模型重试、排队、工具决策与 JSON 结果 | [模型应用层](contexts/models/application/README.md) 的请求用例 |
| 改变模型或存储实现而保持上述行为 | [组合根](bootstrap/README.md) 注入新端口实现 |
