# 审核持久化

新原生 SFT 的同次评审由 `review_evolution_request.py` 复用共享严格工具、模型并发和请求检查点。一次 `submit_review_evolution` 同时提交质量意见、任务临时指令和结构化经验；程序渲染 Memory Item，调用保存与本地索引不再执行模型提炼。无效或不可用响应持久化后停止，不购买格式重试。后续是否一次优化由应用用例决定，第二稿不复审，打包与恢复必须认证其独立收据。

工具传输使用 `review_evolution_flat_v1`：质量值与经验字段放在参数顶层，由程序组装既有 verdict、scores 和 memory_proposal 合同，再执行原业务校验。严格 Schema 只用上游支持的关键字；字符串非空等条件继续由入口验证。协议版本进入请求身份，不能把旧的无效嵌套响应当作新协议结果。真实格式失败保留原始 SDK 响应，不截括号、不丢字段、不抽取其中的放行值。

显式 `sft_reanswer_version=1` 子任务在 `AuthenticatedRepairAdapter.review` 内先执行已有 SFT 原生生成，再将新候选交给单次建议评审。所有父版本在生成前整体认证；旧评语与旧稿不进入新作者。新稿证据由已认证 `sft_reanswer` 收据提供，旧 `checkpoint_context` 的 review-only 语义不改变。

新默认为 `advisory_review.py::review_once`：完整候选和来源只审查一次，意见或不可用状态均先持久化，恢复后不再购买；没有逐句单元或大格式包。`experience_observations.py` 本地收集原文与意见，模型提炼由用户明确触发。下述 consolidated 设施用于旧协议/对比记录，不是当前默认。

建议评审是结构化请求，只传 `structured_response_attempts=1`；原生生成专用的 `native_request_attempts` 不能用于评分请求。实际首跑发现的参数冲突在发包前中断，恢复时复用指导员与 SFT 检查点，不重购生成。

`consolidated_scoring.py` 将冻结候选接到集中评审用例；`consolidated_adapter.py` 复用原请求调度、流式输出和付费检查点。动态格式由程序生成，模型只返回语义判断；有明确格式错误最多重试一次，不逐句重买评审。控制文字建议仍附在同次请求，需用户确认才能修改节点规则。

集中评审同次区分训练消息与教师资料，检查来源范围、实际用户要求和历史角色；不另买相同候选的学习者上下文评审。质量评分、全部单元覆盖、真实引文、显式前提与来源认证仍保留。单次模型意见不是独立交叉验证；未决、拒绝和原生成阻断分开记录。

`control_text_suggestions.py` 把可选建议附在既有评分请求中。上游返回封套里的 `review`
由程序原样拆出，继续交给原质量校验器；缺失或不合规建议不触发额外付费重试。
窗口质量审计的 `raw_response` 指封套中的原质量对象；完整上游封套保留在原请求检查点，
非空建议另附不可变来源收据。建议收据在任务目录下，轻量 SQLite 索引用于有界查询，
同一配置基版、节点、规则和原句跨任务重复出现时只保留首条认证来源；已采用或已忽略的
同一建议不重新打开。空、无效及已收录建议保留普通请求检查点，不复制第二份大回执。
收据和索引在读取单项时重新认证，存储异常不阻断已取得的质量判断。验收使用
`tests/test_control_text_review_suggestions.py` 与既有窗口、逐轮评审回归，不调用真实模型。

已接通聚合/逐轮评分、主路径事实挑战与裁定，以及 `judge_answer` 中带明确作者配置的
消息、对话或候选。盲参考生成不看受审候选；纯学习者上下文、风格检查、缺少作者配置的
旧节点局部评分及已投影掉配置的打包数据仍用原协议，不从当前页面或评审模型猜测配置。

自动评审与返工使用未标记的原候选。仅当候选带有 v3 输出配置时，通过 datasets
合同从模型请求的已知消息容器移除程序配置字段；完整推理、角色和工具内容不动。
训练文件中的成对标签是导出视图，不反向写入候选或审核输入。

`AuthenticatedRepairAdapter` 对显式原生首轮续跑同时核对父记录、父配置及父作者的模型绑定和端点快照；全部选中父版本确认之后才进入任何模型调用。`CheckpointedReviewAdapter` 通过组合根连接 workflows 的续跑用例，不拥有第二份多轮调度器。

`checkpoint_parent.py` 让已停止但尚未打包的任务进入新的只评审任务。
`lib.bootstrap.automatic_review.checkpoint_review_input` 读取原候选，认证父配方、原始来源、
资料包、阶段文件和现有检查点，并把摘要固定在新配方里；执行前重新认证。
只允许自动评审且 `max_rounds=0`，不改父任务、不补造发布清单、不重生成。
已有条件登记和 JEV 检查点仅是带来源的历史观察，不等于新评审或合格入库。
原生推理的既有阻断仍需独立处理，评分不能自动消除。验证见
`tests/test_checkpoint_review_parent.py`。

已封存的审查阶段另用 `reviewed_checkpoint_input` 的版本 2 来源合同，先验证发布清单，
再固定 `stage-results/review-<target>.jsonl` 及全部检查点。若来源来自未封存旧父任务，
递归认证冻结选择和资料，不把空的新任务 sources 当作原知识来源。版本 1 仍仅接收
失败／取消且尚未封存的任务，原判定与原记录都不改。

`experience_observations.py` 在 `review_advisory_policy_version=1` 下将一次评审与真实交付状态
送入既有个人经验库。通过 `bootstrap.experience_evolution.record_advisory_review_observation`
接入；没有模型调用，也不改变训练状态。完整原记录、推理及原审查存档保留，放行仅记为
`advisory_release`，不伪造质量成功。相同记录重复恢复不会反复新增观察。

`StreamingReviewCollectionsAdapter.align_native_cot` 将已评审的完整 SFT 流式投影为 CoT，并为实际格式正文重新绑定收据摘要及派生身份。它保留原生推理、历史回合、来源和拒绝状态，不把格式变换当成一次独立评审，也不复用旧候选正文的摘要。

`math_reference_adapter.py` 调用 Math-Verify 0.9.0 的等价验证，以及它的 `latex2sympy2_extended` 完整表达式解析器。禁止用面向自由文本的末答案提取器忽略冲突片段、去掉单位或猜修坏公式。工作进程有整体超时，兼容 Windows 和后台线程；不修改依赖源码。失败返回未解决，检查收据经既有任务检查点复用。验证见 `tests/test_math_reference_verification.py`。

`training_review_driver.py` 复用训练内容审核的持久化流程；SFT、CPT 和偏好适配器绑定各自规则。`agent_review_driver.py` 保存 Agent 决定和审计事件；`agent_driver.py` 将运行任务视角的端口转换到该存储接口。

两个 Agent 类职责不同：Adapter 适配调用合同，Driver 拥有存储行为。不要在两处复制审核规则。发布任务继续使用旧审核发布服务，保持来源校验与锁语义。

任何写入都要核对当前候选版本。不要信任浏览器缓存的“已通过”状态，也不要直接改生成文件以表现审核结果。

`rework_collection.py` 认证审核候选并流式读取退回批次；只读查询使用已有会话元数据和 SQLite 的只读连接，不创建空工作室、数据库或资料副本。该查询仅提供导航和已存数量，实际返工仍由原工作室验证父版本、保存请求身份与并发版本。

自动评审维护入口：

| 文件 | 责任 |
| --- | --- |
| `automatic_adapter.py` | 连接候选、集合和返工用例；认证父版本、流式分组、CoT/SFT 谱系对齐与发布 |
| `automatic_runtime.py` | 兼容运行时钩子、来源读取、逐轮/逐句评审与实际修正请求 |
| `review_protocol.py` | 历史模型返回 shape、格式错误诊断与重试协议文字 |
| `review_style.py` | 从当前冻结节点配置解析风格，不凭旧候选自报决定规则 |
| `preference_style.py` | 用与匿名评分相同的 SFT 投影解析偏好正例的风格、互译和推理深度，传给修正；不将正例要求错误套给负例 |
| `claim_window_adapter.py` | 每个评审窗口沿用 `ask`、冻结模型、共享并发与响应检查点；记住已超限窗口，恢复时直接细分 |
| `format_lineage.py` | 从封存产物和已有流式候选索引读取历史发布所有者，认证父版本；不靠历史摘要猜测样本所属格式或来源 |
| `preference_assessment.py` | 逐份调用既有完整 SFT 评审，模型看不到预置正负标签和另一份回答；独立核验偏差类型，格式重试仍验证原句及完整合同 |

`WorkflowReviewRepair` 的旧类名作为运行时接入接口保留；评分修复主循环由 `ReviewCandidate` 拥有。调用仍走原 `ask`，维持共享并发、取消、流式输出和检查点身份。禁止把用户资料、原始供应商错误或密钥带入格式错误反馈。

传输版本 1 的条件评审明确列出嵌套 verdict 合同；普通候选/偏好评分使用独立的六字段合同。格式重试反馈由成熟 `jsonschema` 生成预期字段、路径和类型，不把缺省分数或额外事实补入响应。失败原始检查点保留，第二次失败继续阻断，不通过放宽校验恢复。
