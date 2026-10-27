# 模型规则

旧 JSON 外层兼容由标准库 `JSONDecoder.raw_decode` 解析对象边界，移除自写括号／转义扫描。严格工具通道仍独立使用完整 Schema 验证，不由此修补或放宽字段。

显式输出 token 上限使用 `ModelOutputLimitError`，区别于连接中断和没有正常结束的响应。该错误不自动重复同一请求；需要更小工作单元的用例可以据此有界拆分。原始部分输出仍只能作为诊断，不能成为训练记录。

`configuration.py` 定义服务配置；`capabilities.py` 定义能力证据；`reasoning.py` 定义推理参数；`network.py` 和 `scheduling.py` 定义网络、并发限制；`api_format.py` 定义支持的协议名称。

规则只处理数据。请求序列化属于协议适配器，联网探测属于基础设施。人工设置上下文窗口不等于已经验证上游支持该长度。

保持连接签名与能力证据的绑定，修改连接后不能沿用不匹配的探测结论。配置范围变更应覆盖上下文、输出上限、推理强度和共享模型并发测试。

`request_errors.py` 定义文本、JSON、流式、排队及旧预算兼容错误，不携带原始供应商报文。`json_response.py` 解析完整 JSON，保留文档内部的代码围栏。原 `model_*` 路径重新导出同一错误类型。验证包括 `test_model_request_use_cases.py`、`test_llm_client.py` 与请求可靠性回归。

`JSONResponseDecodeError` 是兼容 `ValueError` 的专用语法错误，提供行列与原因。它不会自动补括号、删正文或猜测字段；应用层据此区分模型语法问题与程序错误。
