# 工作流规则

`creation.py` 校验创建约束；`graph.py` 描述记录依赖；`targets.py` 定义训练目标及训练记录合同；`production.py` 定义批量目标和饱和停止策略；`scale.py` 定义规模配置。

`execution.py` 包含 `BatchGenerationPlan`、`TargetOutcome`、`BatchOutcome` 和 `WorkflowCancelled`。它区分“运行结束”和“仍需关注”，不读取运行目录，不构造模型客户端。

`dialogue.py` 定义一次对话的临时进度、来源合同、公开推理控制和指导错误。`DialogueProgress` 不产生新的持久化格式。`production_rounds.py` 负责从已提交收据重建状态、窗口大小、来源覆盖及饱和停止；它只接受普通值，不保存文件或事务连接。

不要根据异常类名字符串识别取消，使用显式异常类型。不要把画布的连线当作并行执行调度。领域决定依赖，具体并发由运行时执行。

依赖只向内，或使用其他业务域的 `contracts.py`。规则变更须覆盖相应领域测试和历史配方回归。
