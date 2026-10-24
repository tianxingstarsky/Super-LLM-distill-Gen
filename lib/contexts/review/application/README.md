# 审核用例

`sft_review_service.py`、`corpus_review_service.py`、`preference_review_service.py` 分别协调三类训练内容；`agent_review.py` 协调 Agent 轨迹。每个用例消费对应端口。

职责是接收决定、校验业务约束、请求持久化并返回结果。不要创建 SQLite 连接、访问 Streamlit 状态，或把原始模型调用放入人工审核入口。

| 自动处理入口 | 责任 |
| --- | --- |
| `review_candidate.py` / `AutomaticReviewPort` | 候选输入检查、人工决定或自动评分修复循环、重复稿停止、待复核与收据绑定 |
| `review_collections.py` / `ReviewCollectionsPort` | 流式评审集合、验证可复用的候选收据、CoT 与派生 SFT 对齐 |
| `execute_repair.py` / `RepairBranchPort` | 全部父版本认证后才开始返工，保持独立修正分支 |
| `review_claim_windows.py` / `ClaimWindowPort` | 有界评审返回、明确输出上限后的递归细分、完整覆盖后汇总；不截断或重写候选 |
| `authorize_format_view.py` / `FormatLineagePort` | 认证本次父版本与历史发布所有者，核对同源任务的不同训练视图；独立重复任务仍隔离 |
| `assess_preference_pair.py` / `PreferenceAssessmentPort` | 匿名评审完整正负例，再校验指定的偏差类型、非空短推理和既有正确性分差；不颠倒标签或调整原始评分 |
| `review_preference_view.py` | 将同一偏好对的评分、修正请求归一化，再投影 DPO/ORPO/RLAIF；内容、依据或指令不同不能复用检查点 |

`automatic_ports.py` 中的端口不暴露文件路径、模型客户端或数据库。评分不等于发布；候选变更后必须重新绑定内容哈希，缺失条件支持仍保留为待人工复核。新用例可以仅用内存端口执行，见 `tests/test_pipeline_use_cases.py`。

新 `review_reference_reassessment_version=1` 在用户追加已固定的核验资料时先重评原文。新增依据可能解决旧的未决项，不应先购买一次无必要的重写；若仍有实际错误，再进入原来的修正循环。历史配方的执行顺序不变。偏好正例未通过时，不评分负例，也不保留旧标签。

新增审核字段先明确它属于候选内容、审核意见还是发布证据。候选 ID 与来源哈希必须继续传递，不能只凭列表下标提交决定。

`training_format_lineage_version=1` 只改变新返工任务的视图身份。相同认证祖先、资料及完整学习者输入共享任务 ID；同一格式一次不能选择两份冲突版本，所有父版本认证后、任何模型请求前就拒绝这种选择。多轮对话保持自己的完整输入身份，不和单轮任务合并。`claim_repair_feedback_version=1` 只给修正模型补全已认证的争议上下文，不改变未知前提的待复核停止策略。

窗口用例只捕获模型域的显式 `ModelOutputLimitError`；连接、认证、取消与无效评审仍按原策略传播，不能泛捕异常后重复购买。不可再分的原文单元仍超限时保留未完成状态。检查点、模型选择与共享并发由端口实现。

`rework_collection.py` 的 `ReviewReworkApplication` 将退回队列分批保存到持久返工集合；没有退回记录时不创建工作室。`existing_collection` 只查询已存状态，保存完全成功后页面才直接进入返工；保存本身不启动模型。验证见 `tests/test_review_rework.py` 与 `ui_tests/test_rework_collection_ui.py`。
