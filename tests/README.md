# Python 回归

`test_workflow_node_prompts.py` 按任务冻结的模板范围验证节点隔离；新增模板不能进入旧配方，缺失模板的请求在模型调用前拒绝。

`test_reading_session.py` 使用真实磁盘工作流和可控模型边界，验证中断恢复不重买已完成响应、用户模拟角色翻转、共享材料／规则与导出一致、助手看不到故事、原生推理逐字保存、自然停止和隐藏制作字段仍拦截。批次和生产组合在 `test_workflow_execution_application.py` 覆盖，避免旧指导员重复调用。真实线上对话另存 `reading-session-live`，离线替身不证明语义质量。

`test_native_joint_cot.py` 通过真实磁盘工作流及模型边界替身验证原生双通道逐字保存、答案同步改变、未评审状态、资料范围投影与重启不重复调用。程序生成的短执行状态区分历史说法与待修订旧稿，绑定当前回合及资料范围；旧配方不追加，训练投影不携带，标记泄漏保留证据。它不证明线上语义质量或状态提示必然纠错；真实结果另存于 `native-guided-cot-live` 实验记录。

`test_cot_style_transfer.py` 覆盖 NeMo / OpenThoughts 导入合同、原答案不可改、语义拒绝保留、真实磁盘任务与恢复不重买调用。模型响应为离线替身，不能据此宣称线上质量或成本。已有报告测试覆盖原稿对照与训练字段隔离。

`test_open_source_runtime.py` 验证 Tenacity 重试边界、Langfuse observation 故障隔离和 SDK 传输限制。`test_knowledge.py` 用本地模拟服务验证官方 Qdrant/embedding SDK 的真实报文；测试响应遵守上游结构。验证范围按根目录 `AGENTS.md` 选取，不默认运行整个测试目录。

`test_public_prompt_reference.py` 覆盖论文中公开提示方法的指代误杀及相邻真实泄漏对照，不把技术主题作为绕过正文检查的许可。

`test_authored_submission_repair.py` 检查一次有界修复保留原消息与原生思考、实际文本绑定、严格重验和重复恢复不重新购买请求。

`test_authored_task_contract.py` 检查真实请求中排除范围外全文、两阶段语言合同、检索工具范围一致、原生前缀越界提前停止、错配正文完整保留，以及历史配方不变。真实失败稿固定在 `fixtures/reasoning_agent/authored_case09.json`，离线回放不能通过删推理或改写原文获得通过。可观察校验不冒充完整语义评审。

`test_strict_authored_submission.py` 使用 MockTransport 和真实 SDK 流式片段验证严格提交：完整长推理、转义/双语、原生思考、单次计费、端点局部切换、截断、无结束标记、观察器错误、重复字段、额外正文、缺失/空核查、旧内容校验、检查点恢复、来源工具与配方 36 选择。没有调用模型 API；真实单例另存报告，不能由格式测试推断百万级质量。

`test_reflection_guidance_placement.py` 验证配方 35 将冻结要点放在真实对话之后，仅出现一次，按阶段保留自定义规则；旧任务、原始错误历史、完整推理前缀和无额外评审合同不变。具名引用并不自动视为泄露，也不自动证明正确。实际角色、结论条件和旧错纠正必须另查完整真实输出。

`test_authored_stage_contract.py` 检查配方 34 的真实请求边界：历史对话和长资料前缀原样保留、系统阶段合同不冒充用户、完整原生推理前缀、断点复用，以及非 JSON 失败仍拒绝。测试证明传输与格式合同；角色语义的实际效果需另查完整模型输出。

`test_generation_source_lookup.py` 验证生成前原文查证：精确引文/页码、来源范围隔离、历史助手不作为事实、完整原生思考回传、工具错误恢复、重复调用停止、原生前缀完整复用、旧任务兼容，以及真实磁盘任务恢复和导出字段。全部离线，不把工具合同通过当成模型纠错效果。

`test_integrated_reflection.py` 验证协议 7：单次联合生成、两阶段原生前缀、零额外清单/风格/契约评分、失败保留全文且不自动整条重做，以及真实磁盘任务的冻结、导出和断点复用。显式审核节点仍保留拒绝。全部使用内存端口或注入响应，不发付费 API；测试通过不代表新提示词已通过真实模型质量评测。

角色与用户措辞引导也在该文件检查：共享要点进入推理撰写，不在答案续写重复，自定义与冻结规则不会被默认要点替换。测试验证配置传递，不能按代词或引号数量证明角色理解正确；真实两例的观察与失败见生成中推敲设计文档。

`test_review_submission_batch.py` 验证一次多项提交、局部引用错误、缺项补交、负面判断不可改成通过、单位检查不放宽、发现关联完整性、独立角色隔离、断点重放和新建配方版本冻结。模型端口用离线替身；调用次数仅证明调度合同，不代表真实语义通过率或成本。

`test_authored_trace_transport.py` 验证推理字段无损表示转换、原稿不变、具体错误路径、不可补条件、不可用结构成功覆盖负面评审，以及协议错误不触发整稿重写。`test_model_request_use_cases.py` 另验证 JSON 位置反馈、重复坏响应终止、收据发布或适配器异常不额外请求。它们是离线合同测试，不声明真实模型可用率提高。

`test_presentation_admission.py` 证明表达建议不会覆盖内容否定、强制规则缺口或未决证据，保留原评分和完整推理，且不会为纯风格不足再购买整稿生成。旧模式、草稿持久化和训练导出字段一并验证。

`test_semantic_reasoning_review.py` 覆盖协议 6 的全文覆盖、用户规则不可豁免、原文引证、比例工具、三类问题区分、一次争议复核、不可补条件与仅确认缺陷回流。离线端口测试证明合同和调度，语义正确率另由真实模型实验记录，不将前者冒充后者。

`test_reasoning_references.py` 验证跨句竞争数量、精确操作数绑定、错误来源字段诊断、句末引号边界和已提交核查不可覆盖。数学收据不自动授予语义通过；旧协议锚点不变。

`test_reasoning_environment.py` 覆盖阶段适用性、用户规则、正文原样保留、完整条款覆盖、强制缺口、引用字段无损修复、明确比较的正反例、跨句不确定性、重复工具停止与稳定检查点。五段真实历史会话离线回放和受控反例分开报告；不伪造新模型响应，不以未知代替正确或错误。

`test_explicit_scenario_review.py` 保留算例误判回归：明确写出的前件需绑定候选，凭空补入的同一条件仍被隔离，原始高分不能绕过条件校验。该测试只验证出处与保守放行合同；模型能否理解真实算例须用另行保存的实际评审验证。旧评审模板哈希继续固定。

`test_contrastive_preference.py` 覆盖两种偏差、匿名完整评审、相反与答非所问的区分、非空短推理、原始分差、不可交换标签以及跨格式检查点复用。设计核验不等于事实证明，实际程序执行和人工阅读另行记录。

`test_preference_revision_v3.py` 覆盖正例修正与评分要求一致、没有事实争议的窗口质量反馈、渐进跑题的逐字关联、同题真实失败稿复用与原文认证。真实失败稿仍重新评分、保持分差；样式不合格、待复核、伪造证据与不同输入不能冒充已确认内容失败。

`test_claim_review_windows.py` 覆盖完整推理与来源不变、逐项覆盖、嵌套字段诊断、严格普通偏好评分、输出超限细分、中断恢复与失败原始检查点保留。与条件评审、推理候选、模型流式及 DDD 回归共同验证；真实付费组合结果另保存在本机验收报告。

领域和应用测试使用普通值、临时目录与模拟端口。模型协议测试使用本地模拟服务，不应调用真实付费 API。

`test_training_format_lineage.py` 覆盖跨格式身份、真实重复阻断、历史发布所有者认证、封存文件损坏与任务计数。`test_claim_repair_feedback.py` 覆盖争议原句及关联例外的精确传递、过期评审和伪造窗口阻断、默认模板版本隔离。它们不以删除推理、降低分差或修改待复核阈值达成通过。

| 修改内容 | 先运行 |
| --- | --- |
| 业务边界与装配 | `test_ddd_architecture.py`、`test_onion_architecture.py` |
| 执行顺序与状态 | `test_workflow_execution_application.py`、工作流及 production 测试 |
| DSH 会话导入与原记录保真 | `test_conversation_import.py`、`test_dsh_import_ingestion.py`；独立插件执行 `npm test`，页面另跑 `ui_tests/test_conversation_import_ui.py` |
| 多轮、生产轮次、评分修复边界 | `test_pipeline_use_cases.py`、multiturn、production、review_repair 及 reasoning 回归 |
| SFT、CoT、原生推理和修剪节点 | `test_generation_node_use_cases.py`，以及 SFT/CoT、reasoning、trim 回归 |
| 节点提示词、检查点和请求结果 | `test_node_request_application.py`、角色与流式日志回归 |
| 模型准入、重试、JSON 和工具决策 | `test_model_request_use_cases.py`、`test_llm_client.py`、`test_model_*` |
| 文档准备 | `test_document_corpus_application.py`、`test_doc2corpus.py`、`test_latex_document.py` |
| Docling 适配 | `test_docling*.py`，必要时使用独立解析环境补测 |
| 数据集交付 | package、statistics、verified_preview、credential 相关测试 |

完整执行：`python -m pytest tests -q --tb=short`。测试使用固定本地模拟端口，不要并行启动多个 pytest 进程；之后再单独运行 `ui_tests`。

跳过项需要说明原因。依赖版本不匹配的解析测试应在正确环境补测；Windows 无法表达的 POSIX 进程语义单独注明。不得通过删除断言、放宽校验或跳过失败来完成重构。

`test_workflow_business_combinations.py` 遍历训练目标的非空组合及适用的指导员开关，分别验证普通批次和生产窗口的依赖共享、流式传递、CoT 与 SFT 联动，以及隐藏中间格式不进入生产交付。它验证编排合同；真实引擎、模型协议、人工审核返工、图片及会话导入另由对应业务测试和浏览器验收覆盖，不代表已对全部组合运行付费模型。

`test_review_evidence_policy.py` 覆盖页缺失/篡改、选区范围、来源/历史/规则/模型变化使收据失效、严重语言错配与后台资料旁白、合法翻译阅读反例。真实历史正文离线复放与付费十题实验另存报告，模拟端口通过不等于语义通过率。

同一测试文件覆盖评审引用错误的精确路径、论文证据不可代替候选正文、错误类型不崩溃、无损纠错，以及新工具 Schema 不污染旧协议和其他字符串字段。所有校验仍拒绝伪造引文。

该文件也覆盖 PDF 换行检索定位与原文引用 ID：查询可以跨行，证据仍必须逐字来自原文；否定词、数值、行内连字符不得被丢弃，来源引用不能替代候选正文，未登记 ID 不可跨工作区复用，纠正引用后原负面结论仍保留。

裁决还覆盖完整 finding ID、额外字段和 Schema 不污染历史协议。应用层回归证明一份完整正/负角色评审能跨另一个角色的中断复用，实质缺陷不会因减少重复调用而消失。

`test_review_obligations.py` 对证据策略 2 检查正确对照及最小变异：百分点/相对增幅/评分点、零基线、伪造操作数、历史覆盖、资料归属和互译不可豁免。没有网络调用；结构覆盖与计算测试不能冒充模型语义正确率。
