# Python 回归

`test_claim_review_windows.py` 覆盖完整推理与来源不变、逐项覆盖、嵌套字段诊断、严格普通偏好评分、输出超限细分、中断恢复与失败原始检查点保留。与条件评审、推理候选、模型流式及 DDD 回归共同验证；真实付费组合结果另保存在本机验收报告。

领域和应用测试使用普通值、临时目录与模拟端口。模型协议测试使用本地模拟服务，不应调用真实付费 API。

| 修改内容 | 先运行 |
| --- | --- |
| 业务边界与装配 | `test_ddd_architecture.py`、`test_onion_architecture.py` |
| 执行顺序与状态 | `test_workflow_execution_application.py`、工作流及 production 测试 |
| DSH 会话导入与原记录保真 | `test_conversation_import.py`、`test_dsh_import_ingestion.py`；独立插件执行 `npm test`，页面另跑 `ui_tests/test_conversation_import_ui.py` |
| 多轮、生产轮次、评分修复边界 | `test_pipeline_use_cases.py`、multiturn、production、review_repair 及 reasoning 回归 |
| SFT、CoT、原生推理和修剪节点 | `test_generation_node_use_cases.py`，以及 SFT/CoT、reasoning、trim 回归 |
| 节点提示词、检查点和请求结果 | `test_node_request_application.py`、角色与流式日志回归 |
| 模型准入、重试、JSON 和工具决策 | `test_model_request_use_cases.py`、`test_llm_client.py`、`test_model_*` |
| 文档准备 | `test_document_corpus_application.py`、`test_doc2corpus.py`、`test_latex_document.py` |
| Docling 适配 | `test_docling*.py`，必要时使用独立解析环境补测 |
| 数据集交付 | package、statistics、verified_preview、credential 相关测试 |

完整执行：`python -m pytest tests -q --tb=short`。测试使用固定本地模拟端口，不要并行启动多个 pytest 进程；之后再单独运行 `ui_tests`。

跳过项需要说明原因。依赖版本不匹配的解析测试应在正确环境补测；Windows 无法表达的 POSIX 进程语义单独注明。不得通过删除断言、放宽校验或跳过失败来完成重构。

`test_workflow_business_combinations.py` 遍历训练目标的非空组合及适用的指导员开关，分别验证普通批次和生产窗口的依赖共享、流式传递、CoT 与 SFT 联动，以及隐藏中间格式不进入生产交付。它验证编排合同；真实引擎、模型协议、人工审核返工、图片及会话导入另由对应业务测试和浏览器验收覆盖，不代表已对全部组合运行付费模型。
