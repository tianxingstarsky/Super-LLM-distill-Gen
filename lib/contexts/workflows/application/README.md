# 工作流用例

| 文件 | 责任 |
| --- | --- |
| `runs.py` / `ports.py` | 面向页面和 CLI 的创建、恢复、运行查询接口 |
| `execute_run.py` | 在独占会话内核验快照，选择普通批次、生产或修正分支，处理完成、取消与失败 |
| `generate_batch.py` | 普通批次阶段依赖：清洗输入、指导、SFT、多轮、偏好、CoT、最终交付 |
| `execution_ports.py` | 执行用例需要的会话和阶段端口，以及可重复遍历的记录批次合同 |
| `generate_dialogue.py` / `dialogue_ports.py` | 多轮资料准备、原样导入评审、分角色逐轮生成和完整对话评审 |
| `execute_production.py` / `production_ports.py` | 恢复已提交轮次、选择下一窗口、最终核验与有界补充 |
| `generate_production_batch.py` | 单个生产窗口内的 CPT、指导、SFT、偏好、CoT 依赖顺序 |

这里不能导入文件锁、模型 SDK、Streamlit 或具体运行时。`RecordBatch` 可能包含百万条磁盘记录，传递批次不等于 `list(records)`。`PreparedBatch` 区分来源记录与生成候选，避免把它们都叫作 data。

改变流程顺序在用例中完成，改变节点内部算法在对应实现中完成。`tests/test_workflow_execution_application.py` 使用纯内存端口验证流程，不需要真实模型或存储目录。

`GenerateMultiturnDialogue` 保留旧请求键和停止原因，导入对话只评审。`ExecuteProductionRounds` 只在收据持久化成功后推进轮次；源文件索引和样本迭代不进入调度层。`tests/test_pipeline_use_cases.py` 用不可遍历的百万记录替身及内存评审端口验证这些边界。该规模测试验证内存与调用合同，不代表真实百万样本压测。
