# Python 回归

领域和应用测试使用普通值、临时目录与模拟端口。模型协议测试使用本地模拟服务，不应调用真实付费 API。

| 修改内容 | 先运行 |
| --- | --- |
| 业务边界与装配 | `test_ddd_architecture.py`、`test_onion_architecture.py` |
| 执行顺序与状态 | `test_workflow_execution_application.py`、工作流及 production 测试 |
| 文档准备 | `test_document_corpus_application.py`、`test_doc2corpus.py`、`test_latex_document.py` |
| Docling 适配 | `test_docling*.py`，必要时使用独立解析环境补测 |
| 数据集交付 | package、statistics、verified_preview、credential 相关测试 |

完整执行：`python -m pytest tests -q --tb=short`。测试使用固定本地模拟端口，不要并行启动多个 pytest 进程；之后再单独运行 `ui_tests`。

跳过项需要说明原因。依赖版本不匹配的解析测试应在正确环境补测；Windows 无法表达的 POSIX 进程语义单独注明。不得通过删除断言、放宽校验或跳过失败来完成重构。
