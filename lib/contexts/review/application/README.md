# 审核用例

当前新任务以 `review_advisory_policy_version=1` 为准：`ReleaseAdvisoryCandidate` 复用已有绑定评语或调用一次 `review_once`，保留原始拒绝/未决/错误后按策略输出，不启动返工、自检、焦点核查或付费格式重试。完整观察进入既有经验库；`released_advisory` 与评审通过分别记录。

普通多轮额外冻结 `multiturn_advisory_review_version=1` 时，生成阶段的 `deferred` 表示模型未评审，不是通过。最终建议评审仍只有一次；`multiturn_advisory_review.local_validation=rejected` 的完整候选保留原始拒绝、正文和推理，即使建议评审给出通过也不解除程序阻断。不完整或没有有效响应的候选记录 `not_called`，不为补齐状态购买评审；恢复复用绑定观察。旧配方及 `repair_inputs` 不自动采用此分支，显式首答返工仍先认证全部父版本。验证见 `test_advisory_release_binding.py`、`test_native_dialogue_style_contexts.py` 和 `test_expression_profile_routing.py`。

`ReviewConsolidatedClaims` 保留此前 v6 对比记录与严格协议的读取和认证。它允许明确问题的一次指定复核；当前建议式策略优先于此协议，不再走这套默认评审。原分组、展开和源校验历史不改。

中断任务的 `checkpoint_context` 仅允许自动零返工重新评审。工作副本恢复已认证的作者规则，原问答、推理、来源及检查点不改。原生生成阶段的已知问题继续保留：新评分通过不等于未修改的原稿已修复。

`ControlTextSuggestionApplication` 提供 `list_pending(node, limit=30)`、`get(id)` 与
`resolve(id, "applied" | "dismissed")`。列表最多 100 条，读取单项重新认证来源收据。
返回候选原句、配置基版与增补后的配置，不暴露文件路径，也不自行保存节点配置。
页面须先比较当前配置与基版，用户确认且节点保存成功后才标记 `applied`；取消仍为待确认。
本用例不热改已冻结任务，不改变样本质量状态，不启动独立模型请求。

v7 公开 system 注意事项重新生成与旧原生工具续写均可能在普通逐轮评审前停止；`review_guidance` 与 `tool_feedback` 按实际来源区分，完成状态仍为 `not_assessed`，不额外购买评审补状态。

显式原生工具返工可能在逐轮评审前停止待审。该分支的 `review_repair.status` 与 `assessment` 均为 `not_assessed`，不能写成已经执行普通多轮审核或语义拒绝；原有局部缺陷与新稿仍保留。审核入口仅记录此次修复尝试，不为补齐状态额外购买模型评审。

开放对话可显式复用已认证的完整首答做诊断续跑。`reuse_initial_native_response` 的收据使用 `attempts=0` 与 `reuse_first_native_answer_for_diagnostic_continuation`，不把读取原稿计为新修正。继续收集后续对话与训练合格是两项状态；逐轮和整段校验仍由原多轮用例执行，保留拒绝理由和完整原生推理，未解决的入库阻断不能被后续对话覆盖。

新冻结 `native_dialogue_continuation_version=1` 的显式首轮原生返工，先由 `ExecuteRepairBranch` 验证全部父版本和原作者绑定，再通过组合根进入 workflows 的 `ContinueNativeDialogue`。该用例已执行逐轮、风格、契约和整段校验，`ReviewCandidate` 只绑定对应收据，不再次购买相同候选的独立评审。普通多轮文本返工和历史任务保持原路径。未通过的新稿与完整旧推理都保留，不能用改变状态冒充修正成功。

显式原生集成路线的 SFT/CoT 是同一响应的两个格式：`ReviewGeneratedCollections` 只评审与修正完整 SFT，再逐条投影 CoT；既有 CoT→SFT 路线保持不变。生产部分目标不补出未选择的格式；最终汇总验证各自已绑定的审核收据，不重复请求模型。SFT 导出选择去掉推理时，配套 CoT 的完整推理仍需进入本次审核。验证见 `test_workflow_reasoning_route.py`。

`VerifyReferenceAnswer` 只处理显式、版本化的无单位数值表达式参考合同。缺少参考是 `not_applicable`，不支持或工具失败是 `unresolved`，两者均不是正确证明。数学端口由组合根注入；通过只表示该最终数值等价，不证明推理、历史或来源范围正确。

协议 7 的生成中推敲不属于独立评审。显式审核节点仍照常评分和保留拒绝结果，但不把这份生成指引再交给 `CheckReasoning` 追加清单请求；生成收据不能替代审核决定。验证：`tests/test_integrated_reflection.py`。

版本 6 语义复核与前述暂停边界相同：确认的具体缺陷可返工；证据不足和未完成的工具核查保留全文，等待处理。解除误判必须有绑定原稿的复核证据，不修改原始评审记录。

显式开启 CoT 自检的任务在修正正文后重新取得逐项证据。旧收据只有规则和正文均匹配时可复用；新认证的返工分支重新核查。组合准入失败保留原质量分数及原 verdict，自检缺口随返工反馈传递；没有自检配置的历史任务不增加请求。

版本 3 工具核查区分实质缺陷与未完成调查。当前核查未完成时保留正文、完整证据和原分数，等待处理，不先购买正文重写；已确认缺陷仍进入修正。旧的未完成收据不能阻断新判定的修正。修正请求只投影具体失败项，完整工具会话仍留在审核记录中。

版本 5 环境协议同样遵守该边界：阶段、来源范围或未完成证据引起的待复核不能触发猜测式返工。`tests/test_reasoning_environment.py` 使用拒绝任何 repair 请求的离线端口验证这一点。

`sft_review_service.py`、`corpus_review_service.py`、`preference_review_service.py` 分别协调三类训练内容；`agent_review.py` 协调 Agent 轨迹。每个用例消费对应端口。

职责是接收决定、校验业务约束、请求持久化并返回结果。不要创建 SQLite 连接、访问 Streamlit 状态，或把原始模型调用放入人工审核入口。

| 自动处理入口 | 责任 |
| --- | --- |
| `review_candidate.py` / `AutomaticReviewPort` | 候选输入检查、人工决定或自动评分修复循环、重复稿停止、待复核与收据绑定 |
| `review_native_sft.py` | 新版本普通原生 SFT 仅评审首稿一次，同次收集临时修正要求及可选通用经验增量，再决定是否重答一次 |
| `review_collections.py` / `ReviewCollectionsPort` | 流式评审集合、验证可复用的候选收据、CoT 与派生 SFT 对齐 |
| `execute_repair.py` / `RepairBranchPort` | 全部父版本认证后才开始返工，保持独立修正分支 |
| `review_claim_windows.py` / `ClaimWindowPort` | 有界评审返回、明确输出上限后的递归细分、完整覆盖后汇总；不截断或重写候选 |
| `authorize_format_view.py` / `FormatLineagePort` | 认证本次父版本与历史发布所有者，核对同源任务的不同训练视图；独立重复任务仍隔离 |
| `assess_preference_pair.py` / `PreferenceAssessmentPort` | 匿名评审完整正负例，再校验既有分差；合成负例另验类型与短推理，真实失败稿另验原文和审核证据，不颠倒标签或调整原始评分 |
| `review_preference_view.py` | 将同一偏好对的评分、修正请求归一化，再投影 DPO/ORPO/RLAIF；内容、依据或指令不同不能复用检查点 |

`automatic_ports.py` 中的端口不暴露文件路径、模型客户端或数据库。评分不等于发布；候选变更后必须重新绑定内容哈希，缺失条件支持仍保留为待人工复核。新用例可以仅用内存端口执行，见 `tests/test_pipeline_use_cases.py`。

新 `review_reference_reassessment_version=1` 在用户追加已固定的核验资料时先重评原文。新增依据可能解决旧的未决项，不应先购买一次无必要的重写；若仍有实际错误，再进入原来的修正循环。历史配方的执行顺序不变。偏好正例未通过时，不评分负例，也不保留旧标签。

新增审核字段先明确它属于候选内容、审核意见还是发布证据。候选 ID 与来源哈希必须继续传递，不能只凭列表下标提交决定。

`training_format_lineage_version=1` 只改变新返工任务的视图身份。相同认证祖先、资料及完整学习者输入共享任务 ID；同一格式一次不能选择两份冲突版本，所有父版本认证后、任何模型请求前就拒绝这种选择。多轮对话保持自己的完整输入身份，不和单轮任务合并。`claim_repair_feedback_version=1` 只给修正模型补全已认证的争议上下文，不改变未知前提的待复核停止策略。

窗口用例只捕获模型域的显式 `ModelOutputLimitError`；连接、认证、取消与无效评审仍按原策略传播，不能泛捕异常后重复购买。不可再分的原文单元仍超限时保留未完成状态。检查点、模型选择与共享并发由端口实现。

`native_sft_review_evolution_version=1` 冻结的普通指导式原生 SFT 采用指导员一次、作者首答一次、评审一次；首稿通过即交付。只有明确拒绝且带有可执行临时要求时，才由原作者重答一次，之后不追加语义评审。同次评审可附候选通用经验增量，沿已有经验仓库保存与索引，无需另一次模型提炼；本次重答的 SYSTEM 可使用这份明确标为候选的增量，任务的冻结公共经验与全局采用版本不热改。首稿、完整原生推理、审核、二稿和候选增量均保留，二稿显式 `not_assessed`；本地结构与来源阻断仍生效。历史配方、普通多轮最终建议策略及显式返工输入保持原流程，收据缺失或被改动时不重新购买评审。验证：`tests/test_native_sft_review_evolution_application.py`、`tests/test_review_evolution_contracts.py`。

`rework_collection.py` 的 `ReviewReworkApplication` 将退回队列分批保存到持久返工集合；没有退回记录时不创建工作室。`existing_collection` 只查询已存状态，保存完全成功后页面才直接进入返工；保存本身不启动模型。验证见 `tests/test_review_rework.py` 与 `ui_tests/test_rework_collection_ui.py`。
