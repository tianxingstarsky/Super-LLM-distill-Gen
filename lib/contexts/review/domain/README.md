# 审核规则

`sft_review.py`、`corpus_review.py`、`preference_review.py` 定义各类内容的可审核形式与决定规则。`review_queue.py` 和 `review_audit.py` 定义队列、身份与审计合同。

这里不能读取样本文件、创建版本或调用评分模型。规则必须可用普通 Python 值验证。保留多轮消息、推理与证据对应关系，不通过删字段让审核通过。

维护者修改决定语义时，同时覆盖来源变化、无效决定和并发冲突测试；存储锁实现属于 infrastructure。
