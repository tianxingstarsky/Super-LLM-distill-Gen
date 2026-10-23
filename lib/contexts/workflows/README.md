# 工作流

管理一次生成任务的创建、执行、恢复、停止和过程查询。数据集交付与人工审核分别属于 datasets 和 review。

- 对外使用 [api.py](api.py) 中的 `WorkflowRunApplication`。
- [application](application/README.md) 决定执行顺序；[domain](domain/README.md) 决定依赖和状态含义。
- [infrastructure](infrastructure/README.md) 承担持久化、锁和阶段操作。
- 装配入口是 `lib/bootstrap/workflows.py`、`workflow_execution.py`、`dialogue_generation.py` 和 `production_execution.py`。

维护执行行为从 `ExecuteWorkflowRun`、`GenerateDatasetBatch`、`GenerateMultiturnDialogue`、`ExecuteProductionRounds` 和 `GenerateProductionBatch` 开始。评分修复用例属于 review 域。SFT、CoT 单节点生成和通用请求运行时尚未全部拆出，不能把本轮迁移理解成整个引擎重写。

验证：`tests/test_workflow_execution_application.py`、`tests/test_workflow.py`、`tests/test_workflow_production.py`、`tests/test_workflow_reasoning_route.py`。
