# 审核规则

`sft_review.py`、`corpus_review.py`、`preference_review.py` 定义各类内容的可审核形式与决定规则。`review_queue.py` 和 `review_audit.py` 定义队列、身份与审计合同。

这里不能读取样本文件、创建版本或调用评分模型。规则必须可用普通 Python 值验证。保留多轮消息、推理与证据对应关系，不通过删字段让审核通过。

| 自动评审规则 | 责任 |
| --- | --- |
| `repair.py` | 评分修复配置、显式返工输入与历史 CPT 评审策略 |
| `candidate_content.py` | 可编辑内容、不可变工具事实、训练表面校验及原生推理谱系 |
| `reasoning_history.py` | 原生 prefill 的历史证据随实际内容变化退休，保留原文审计 |
| `automatic_rules.py` | 已冻结配方中的语言、推理和知识运用要求 |

规则文本和版本分支沿用旧任务合同。这里的 `candidate_digest` 与既有 canonical JSON 哈希完全一致，不能换序列化算法来“统一”旧缓存。

维护者修改决定语义时，同时覆盖来源变化、无效决定和并发冲突测试；存储锁实现属于 infrastructure。
