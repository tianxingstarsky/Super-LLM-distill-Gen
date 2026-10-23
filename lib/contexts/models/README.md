# 模型配置与请求

管理服务配置、模型能力证据、推理和并发参数，以及一次模型请求的受控执行。用户仍在工作流节点选择模型，这个业务域不是单独的配置页面。

[api.py](api.py) 发布 `BackendApplication`、`ExecuteModelRequest`、`RequestJSONResponse`、`ExecuteToolTurn` 和消费方端口；[contracts.py](contracts.py) 发布纯规则与稳定错误。具体客户端由 [组合根](../../bootstrap/README.md) 装配，API 模块不导入 SDK 或具体适配器。

- [domain](domain/README.md)：配置约束、能力证据、请求错误与 JSON 解析。
- [application](application/README.md)：配置操作、准入、限次重试、格式降级和完整响应发布。
- [infrastructure](infrastructure/README.md)：配置文件、SDK 传输、协议协商、计费与共享调度桥接。

`lib/llm_client.py` 和 `lib/model_tool_calls.py` 只保留模块身份兼容别名。其他 `lib/model_*` 仍提供共享调度、流式消费、报文与诊断辅助；它们不拥有文本和工具请求的主重试循环。既有端点身份、计费和并发台账、响应字段都保持兼容。

验证：`tests/test_model_request_use_cases.py`、`tests/test_llm_client.py`、`tests/test_model_*`、`tests/test_ddd_architecture.py`。协议测试使用本地模拟服务，不调用付费 API。
