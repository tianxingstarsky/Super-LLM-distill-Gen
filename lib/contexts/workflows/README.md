# 工作流

管理一次生成任务的创建、执行、恢复、停止和过程查询。数据集交付与人工审核分别属于 datasets 和 review。

- 对外使用 [api.py](api.py) 中的 `WorkflowRunApplication`。
- [application](application/README.md) 决定执行顺序；[domain](domain/README.md) 决定依赖和状态含义。
- [infrastructure](infrastructure/README.md) 承担持久化、锁和阶段操作。
- 装配入口是 `lib/bootstrap/workflows.py` 与 `lib/bootstrap/workflow_execution.py`。

维护执行行为从 `ExecuteWorkflowRun` 和 `GenerateDatasetBatch` 开始。剩余模型生成算法、批量轮次调度和评审修复仍在旧运行时中；不要把它们误认为已经全部完成迁移。

验证：`tests/test_workflow_execution_application.py`、`tests/test_workflow.py`、`tests/test_workflow_production.py`、`tests/test_workflow_reasoning_route.py`。
