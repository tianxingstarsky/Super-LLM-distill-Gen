# 模型用例

| 文件 | 责任 |
| --- | --- |
| `service.py` / `ports.py` | `BackendApplication` 的配置操作与读写端口 |
| `execute_request.py` | `ExecuteModelRequest` 的共享准入、限次重试、格式降级、取消传播和响应发布 |
| `request_json.py` | `RequestJSONResponse` 的 JSON 校验与有界重试；认证、网络和取消错误不在此重复重试 |
| `execute_tool_turn.py` | `ExecuteToolTurn` 的完整工具决策请求；仅返回工具调用，不执行工具 |
| `request_ports.py` | 文本、JSON、工具决策以及调度、结算和错误分类端口 |

用例只依赖领域和端口，不构造 SDK、不读写文件、不等待系统时钟。应用层决定何时获取与释放资源，适配器负责实际调度、网络和存储。调用方取消和观察器异常不得包装成可重试供应商错误；部分输出不能作为完整响应发布。

格式能力拒绝沿用旧降级语义，不额外消耗用户的重试次数。已发送请求的未知费用、确定拒绝的释放、成功使用量与共享冷却时机保持原合同。JSON 结果只提交成功尝试的响应证据。工具响应先记真实使用量，再校验其内容，不把工具请求执行与工具执行混在一起。

验证：`tests/test_model_request_use_cases.py` 使用内存端口覆盖排队、重试、取消、失败结算和结果发布；模型协议、调度及流式回归验证实际适配器。
