# 工作流运行适配

`driver.py` 的 `FilesystemWorkflowRunDriver` 处理对外运行端口。只读状态查询不会加载模型执行引擎。

`execution_runtime.py` 提供两个明确角色：

- `FilesystemRunSession`：运行锁、最新状态、快照核验、持久化结果、已过滤错误。
- `CheckpointedBatchRuntime`：输入记录、磁盘候选、单阶段执行、最终打包及完成事实。

它们连接旧 `TrainingWorkflowRuntime`，不决定普通批次的阶段顺序。该顺序属于 application。存储文件格式、恢复检查点和部分交付策略必须兼容历史任务。

| 文件 | 责任 |
| --- | --- |
| `dialogue_adapter.py` | 读取并验证来源记忆；复用已有请求调度、角色、流式输出和检查点 |
| `production_adapter.py` | 轮次会话、落盘、事务提交、部分结果与最终交付 |
| `production_storage.py` | SQLite 来源窗口、按收据选择的流式产物集合 |
| `production_batch.py` | 一个窗口的检查点节点执行与上游质量证据保存 |
| `production_runtime.py` | 旧运行时接入钩子、规划检查点、收据校验、去重索引和核验后对账 |

`production_runtime.py` 中的兼容方法把调度交给应用用例。不要向这里重新添加轮次主循环。`lib/infrastructure/workflow_production.py` 仅保留同一模块的导入别名。组合根向生产适配器注入数据集域的磁盘索引，适配器不直接导入邻域实现。

不要记录原始供应商异常，可能包含密钥或原文。不得因“已有检查点”跳过导出核验。验证包括工作流、取消、恢复、批量生成和凭据阻断测试。
