# 工作流运行适配

`driver.py` 的 `FilesystemWorkflowRunDriver` 处理对外运行端口。只读状态查询不会加载模型执行引擎。

`execution_runtime.py` 提供两个明确角色：

- `FilesystemRunSession`：运行锁、最新状态、快照核验、持久化结果、已过滤错误。
- `CheckpointedBatchRuntime`：输入记录、磁盘候选、单阶段执行、最终打包及完成事实。

它们连接旧 `TrainingWorkflowRuntime`，不决定普通批次的阶段顺序。该顺序属于 application。存储文件格式、恢复检查点和部分交付策略必须兼容历史任务。

不要记录原始供应商异常，可能包含密钥或原文。不得因“已有检查点”跳过导出核验。验证包括工作流、取消、恢复、批量生成和凭据阻断测试。
