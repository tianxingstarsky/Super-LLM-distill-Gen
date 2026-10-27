# 模型配置与请求适配

`retry.py` 是唯一 Tenacity 请求执行器。`telemetry.py` 使用 Langfuse observation API 记录实际模型请求、用量与失败；未配置时不连接追踪服务，追踪故障不重试业务。

`provider_failures.py` 用 OpenAI/Anthropic SDK 与 HTTPX 的真实异常类型识别传输失败，并统一读取响应头。重试边界遵循上游 `x-should-retry: false`，优先读取毫秒等待信息，再读取 Retry-After 秒数／日期；显式允许重试不覆盖额度、认证和质量校验等终止条件。SDK 内部重试仍关闭，次数、取消、共享冷却和结算仍由现有请求用例负责。验证见 `test_model_request_reliability.py`、`test_model_stream_transport_recovery.py`。

`strict_tool_stream.py` 为显式严格 Chat 工具提交提供真实流式传输，使用 OpenAI SDK 累积完整响应。官方 DeepSeek 请求局部切换 beta 地址，不改变共享客户端或关闭思考。工具参数、原生推理与使用量完整保留；缺结束标记、截断和无效参数不作为完整候选，不降级为正文 JSON。验证见 `tests/test_strict_authored_submission.py`。

工具通道 `recover_arguments=True` 仅恢复完整 Chat 响应：损坏 JSON 留在 `tool_argument_errors`，回放参数包装为不可执行的错误对象，调用方返回 tool 诊断。同一次响应只计费一次；不补参数语义、不删推理。默认仍严格拒绝，Responses/Anthropic 签名不改写。工具的 length 终止映射到 `ModelOutputLimitError`，不当断线重试。

Chat 的 `finish_reason=length`、Anthropic 的 `max_tokens` 和 Responses 明确报告的 `max_output_tokens` 映射到显式输出上限错误；没有终止事件仍是未完成响应。流式及兼容非流式回退均不得把平衡 JSON 前缀认定为完成。对应 `test_model_streaming.py` 验证不会按断线重复请求。

| 文件 | 责任 |
| --- | --- |
| `configuration.py` | 模型配置端口，本地服务配置读写 |
| `request_runtime.py` | `ChatClient`、SDK 构造、Chat/Responses/Anthropic 协议协商、端点快照和既有预算台账 |
| `request_adapter.py` | `SDKModelRequestAdapter`、`ClientJSONResponseAdapter`；连接共享准入、使用量结算与请求传输 |
| `tool_transport.py` | 工具历史、协议报文、响应校验和完整工具决策 |
| `tool_request_adapter.py` | `SDKToolRequestAdapter`；工具请求准备、使用量提取和结果读取 |

`lib/llm_client.py`、`lib/model_tool_calls.py` 指向上述唯一模块，保留旧调用方和 monkeypatch 的模块身份。文本、JSON 和工具调用的重试策略属于 application，通用循环由注入的 Tenacity 执行，客户端入口通过 `bootstrap/model_requests.py` 装配用例。

共享文件队列仍在 `lib/model_request_scheduler.py`，流式消费在 `lib/model_streaming.py`，诊断、冷却和错误分类在 `lib/model_request_reliability.py`，其余 `lib/model_*` 保留协议辅助实现。能力发现和实际连通测试仍在 `lib/infrastructure/model_discovery.py` 与 `model_capability_probe.py`。这些适配器可继续整理归属，不复制第二套调度或网络逻辑。

DeepSeek beta 原生推理前缀的传输收据版本 2 使用一个换行作为 assistant.content 的可见续写起点，reasoning_content 原样发送。真实 V4.1-Flash 对空 content 的部分请求仅返回 reasoning_content 后 stop；同一实际问题与推理换成非空空白起点后恢复了可见答案。此修复不把推理通道当答案、不清洗或补写模型答案；恢复答案仍需原有评审。版本 1 的历史收据仍可读取，不改旧缓存或导出字段。回归见 `test_reasoning_prefill.py`、`test_reasoning_prefill_history.py`。

`SDKToolRequestAdapter.response_details` 保留上游明确截断的响应正文，供应用层标记为未完成诊断。原始正文可包含未完成的工具参数，不能回放为可执行调用；不包含请求头。完整响应的字段合同保持不变。

配置读取不发送生成请求。密钥只留在服务端配置或受控环境中，不进入任务导出、报表或错误文本。不要让 application 导入供应商 SDK。

验证：`tests/test_llm_client.py` 和 `tests/test_model_*` 使用临时目录、模拟响应及本地模拟服务；完整回归须顺序运行，避免多个进程占用同一模拟端口。
