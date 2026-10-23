# 模型配置与请求适配

| 文件 | 责任 |
| --- | --- |
| `configuration.py` | 模型配置端口，本地服务配置读写 |
| `request_runtime.py` | `ChatClient`、SDK 构造、Chat/Responses/Anthropic 协议协商、端点快照和既有预算台账 |
| `request_adapter.py` | `SDKModelRequestAdapter`、`ClientJSONResponseAdapter`；连接共享准入、使用量结算与请求传输 |
| `tool_transport.py` | 工具历史、协议报文、响应校验和完整工具决策 |
| `tool_request_adapter.py` | `SDKToolRequestAdapter`；工具请求准备、使用量提取和结果读取 |

`lib/llm_client.py`、`lib/model_tool_calls.py` 指向上述唯一模块，保留旧调用方和 monkeypatch 的模块身份。文本、JSON 和工具调用的重试循环属于 application，客户端入口通过 `bootstrap/model_requests.py` 装配用例。

共享文件队列仍在 `lib/model_request_scheduler.py`，流式消费在 `lib/model_streaming.py`，诊断、冷却和错误分类在 `lib/model_request_reliability.py`，其余 `lib/model_*` 保留协议辅助实现。能力发现和实际连通测试仍在 `lib/infrastructure/model_discovery.py` 与 `model_capability_probe.py`。这些适配器可继续整理归属，不复制第二套调度或网络逻辑。

配置读取不发送生成请求。密钥只留在服务端配置或受控环境中，不进入任务导出、报表或错误文本。不要让 application 导入供应商 SDK。

验证：`tests/test_llm_client.py` 和 `tests/test_model_*` 使用临时目录、模拟响应及本地模拟服务；完整回归须顺序运行，避免多个进程占用同一模拟端口。
