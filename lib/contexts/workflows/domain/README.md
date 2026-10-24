# 工作流规则

`creation.py` 校验创建约束；`graph.py` 描述记录依赖；`targets.py` 定义训练目标及训练记录合同；`production.py` 定义批量目标和饱和停止策略；`scale.py` 定义规模配置。

`execution.py` 包含 `BatchGenerationPlan`、`TargetOutcome`、`BatchOutcome` 和 `WorkflowCancelled`。它区分“运行结束”和“仍需关注”，不读取运行目录，不构造模型客户端。

`dialogue.py` 定义一次对话的临时进度、来源合同、公开推理控制和指导错误。`DialogueProgress` 不产生新的持久化格式。`production_rounds.py` 负责从已提交收据重建状态、窗口大小、来源覆盖及饱和停止；它只接受普通值，不保存文件或事务连接。

不要根据异常类名字符串识别取消，使用显式异常类型。不要把画布的连线当作并行执行调度。领域决定依赖，具体并发由运行时执行。

依赖只向内，或使用其他业务域的 `contracts.py`。规则变更须覆盖相应领域测试和历史配方回归。

`director_audit.py` 形成交付报告的指导员计数。来源全部不可用、指导员未执行时，覆盖数和批次为零；已有审计缺字段时明确报错，不推算或补造计数。由工作流合同导出，回归见 `tests/test_director_delivery_audit.py`。

`conversation_import.py` 定义规范会话和导入收据校验、有限预览及训练适用性。保留真实角色、工具配对和已有推理；没有推理时不生成替代字段。真实性与训练适用性分开判断，不放宽既有训练输入规则，不解析 DSH 原生日志或读写文件。

`generation.py` 保存风格选择、学习者可见提问、问答元数据和空答案信号。`model_request.py` 的 `NodeModelRequest` 表示临时请求意图，并校验图片与原生回答尝试参数、角色和已捕获响应。它不取代原有检查点格式，不新建持久字段；纯规则不读取来源文件或模型配置。
