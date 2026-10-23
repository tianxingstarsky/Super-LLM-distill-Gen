# 工作流用例

| 文件 | 责任 |
| --- | --- |
| `runs.py` / `ports.py` | 面向页面和 CLI 的创建、恢复、运行查询接口 |
| `execute_run.py` | 在独占会话内核验快照，选择普通批次、生产或修正分支，处理完成、取消与失败 |
| `generate_batch.py` | 普通批次阶段依赖：清洗输入、指导、SFT、多轮、偏好、CoT、最终交付 |
| `execution_ports.py` | 执行用例需要的会话和阶段端口，以及可重复遍历的记录批次合同 |

这里不能导入文件锁、模型 SDK、Streamlit 或具体运行时。`RecordBatch` 可能包含百万条磁盘记录，传递批次不等于 `list(records)`。`PreparedBatch` 区分来源记录与生成候选，避免把它们都叫作 data。

改变流程顺序在用例中完成，改变节点内部算法在对应实现中完成。`tests/test_workflow_execution_application.py` 使用纯内存端口验证流程，不需要真实模型或存储目录。
