# 运行时与旧基础设施

此目录仍较大，包含未完成迁移的实现和旧路径兼容文件。已迁移的实现不要继续在这里追加。

| 查找内容 | 当前入口 |
| --- | --- |
| 生成节点算法 | `training_workflow.py` 的 `TrainingWorkflowRuntime`；`Workflow` 是兼容别名 |
| 普通批次执行顺序 | `contexts/workflows/application/generate_batch.py` |
| 大批量轮次与饱和停止执行 | `contexts/workflows/application/execute_production.py`；本目录旧路径仅转发 |
| 对话指导与知识上下文 | `workflow_qa_director.py`、`workflow_knowledge_agent.py` |
| 多轮生成 | `contexts/workflows/application/generate_dialogue.py` |
| 自动评分、修正、重新评分 | `contexts/review/application/review_candidate.py`；本目录旧路径仅转发 |
| 资料缓存与原件快照 | `source_library.py`、`source_snapshot.py`、`workflow_source_library.py` |
| Docling 解析 | `contexts/documents/infrastructure/docling/`，本目录同名前缀文件仅转发 |
| 运行只读与文件合同 | `workflow_store.py` |
| 流式输出与磁盘记录 | `workflow_stream_journal.py`、`workflow_rows.py` |

路径均相对 `lib/`，表中本目录文件除外。基础设施实现应用端口；领域判断不要写进 SQL、SDK 参数拼接或 UI 回调中。剩余运行时通过适配器接到新用例，避免一次改动同时改变历史配方和存储格式。

临时数据、权重、真实任务报告放在受控缓存目录，不提交到代码目录。外部 dsh SDK 仍在独立插件包中，不在这里引入。
