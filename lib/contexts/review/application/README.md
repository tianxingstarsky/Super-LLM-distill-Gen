# 审核用例

`sft_review_service.py`、`corpus_review_service.py`、`preference_review_service.py` 分别协调三类训练内容；`agent_review.py` 协调 Agent 轨迹。每个用例消费对应端口。

职责是接收决定、校验业务约束、请求持久化并返回结果。不要创建 SQLite 连接、访问 Streamlit 状态，或把原始模型调用放入人工审核入口。

新增审核字段先明确它属于候选内容、审核意见还是发布证据。候选 ID 与来源哈希必须继续传递，不能只凭列表下标提交决定。
