# 组合根

这里选择具体实现，构造应用用例。页面调用工厂取得应用对象，不自行创建数据库或生成引擎。

| 工厂 | 装配内容 |
| --- | --- |
| `workflows.py` | 运行、数据集、Agent 审核与报告应用 |
| `workflow_execution.py` | 执行会话、批次用例、检查点运行时 |
| `dialogue_generation.py` | 多轮生成用例与来源/请求适配器 |
| `production_execution.py` | 轮次调度、窗口生成、文件存储与注入的去重索引 |
| `automatic_review.py` | 自动候选评审、集合评审与已认证返工分支 |
| `document_corpus.py` | 文档语料用例与本地文本读取器 |
| `datasets.py` | 数据集预览、统计和交付 |
| `backends.py` | 模型配置 |
| `*_reviews.py`、`agent_review.py` | 人工审核 |
| `knowledge.py`、`prompt_library.py`、`local_inputs.py` | 知识库、提示词、持久输入缓存 |

组合根可以同时依赖 application 和 infrastructure，但不能增加业务判断。新适配器通过这里替换，避免在用例里写“如果使用某 SDK”的分支。构造对象与执行任务要保持明确区分。
