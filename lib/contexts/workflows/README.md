# 工作流

管理一次生成任务的创建、执行、恢复、停止和过程查询。数据集交付与人工审核分别属于 datasets 和 review。

- 对外使用 [api.py](api.py) 中的 `WorkflowRunApplication`。
- [application](application/README.md) 决定执行顺序；[domain](domain/README.md) 决定依赖和状态含义。
- [infrastructure](infrastructure/README.md) 承担持久化、锁和阶段操作。
- 装配入口是 `lib/bootstrap/workflows.py`、`workflow_execution.py`、`dialogue_generation.py`、`production_execution.py`、`node_generation.py` 和 `workflow_requests.py`。

维护执行顺序从 `ExecuteWorkflowRun`、`GenerateDatasetBatch`、`GenerateMultiturnDialogue`、`ExecuteProductionRounds` 和 `GenerateProductionBatch` 开始。SFT、CoT 和推理修剪分别由 `GenerateSft`、`GenerateDirectedSft`、`GenerateCot`、`TrimReasoning` 处理；`GeneratePreferencePair` 负责版本化偏好构造；`RequestNodeModel` 管理一次节点请求与检查点。评分修复用例属于 review 域；供应商请求重试属于 models 域。CPT、部分旧版偏好路径及资料缓存仍有旧实现，不能把已有拆分理解为整个引擎已经迁移。

查找“要调用哪个服务”时，从 [组合根导航](../../bootstrap/README.md) 按业务操作定位。`api.py` 还公开来源辅助生成、配置的推理检查及经验版本用例；它们不是页面启动时自动执行的后台任务。

验证：`tests/test_workflow_execution_application.py`、`tests/test_workflow.py`、`tests/test_workflow_production.py`、`tests/test_workflow_reasoning_route.py`。

节点与请求验证：`tests/test_generation_node_use_cases.py`、`tests/test_node_request_application.py`，以及 SFT、CoT、提示词角色、原生推理和恢复回归。

`api.py` 同时公开 `ImportConversations`：先预览经过外部适配器还原的会话，再确认选入持久资料缓存。手动会话库目前只接 DSH；供应商格式解析属于独立插件，不能进入核心用例。验证见 `tests/test_conversation_import.py` 和 `tests/test_dsh_import_ingestion.py`。
