# 模型配置

管理服务配置、模型能力声明、探测证据和推理、网络、并发参数。用户在工作流节点选择模型；这个代码边界不意味着新增一个模型配置页面。

入口为 [api.py](api.py) 的 `BackendApplication`，组合根为 `lib/bootstrap/backends.py`。详见 [application](application/README.md)、[domain](domain/README.md)、[infrastructure](infrastructure/README.md)。

模型 API 报文、共享并发调度器、网络重试目前仍在 `lib/model_*` 与 `llm_client.py`。上游 SDK 的改变不应改变领域的模型配置语义。
