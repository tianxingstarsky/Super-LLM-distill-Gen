# 组合根

`experience_evolution.py` 装配按用户隔离的追加式风格版本库和可选节点模型提炼端口。仅显式提案时加载模型连接；查看历史不需要 SDK。工作流、缓存和既有用户模板继续使用原合同。

`model_requests.py` 注入 Tenacity 重试端口。会话编排继续使用既有用例与认证检查点；框架类不进入应用／领域层。

`node_generation.authored_submission_repairer` 装配显式同会话修复，复用生成节点模型、严格工具协议、限流与检查点；它不会在打开页面时自动重试。

其 `.edit` 入口还装配 `AuthoredEditAdapter`，从原始严格提交检查点认证作者及完整前缀，执行一次局部补丁；不能传入单独的编辑或评审模型替代原作者。

`node_generation.cot_generator` 注入生成中来源查证适配器，复用 `workflow_request_adapter` 的冻结规则与角色投影；新查询会话只在显式 `source_lookup` 配置时启用。

`reasoning_checklist.py` 为评分修正后的 CoT 正文装配同一自检用例，模型与提示词仍使用冻结的 CoT 节点设置。评分原始结果独立保留，自检失败会阻止组合准入，不修改模型原始分数。

这里选择具体实现，构造应用用例。页面调用工厂取得应用对象，不自行创建数据库或生成引擎。

| 工厂 | 装配内容 |
| --- | --- |
| `workflows.py` | 运行、数据集、Agent 审核与报告应用 |
| `conversation_import.py` | 规范会话导入用例、独立 DSH CLI 和本机资料缓存；依赖检查延迟到实际导入 |
| `workflow_execution.py` | 执行会话、批次用例、检查点运行时 |
| `node_generation.py` | SFT、指导问答、CoT 和推理修剪用例及检查点适配器 |
| `workflow_requests.py` | 节点请求用例、冻结提示词、模型绑定、流式日志与调用检查点 |
| `model_requests.py` | 文本、JSON、工具请求用例及 SDK/共享调度适配器 |
| `dialogue_generation.py` | 多轮生成用例与来源/请求适配器 |
| `production_execution.py` | 轮次调度、窗口生成、文件存储与注入的去重索引 |
| `automatic_review.py` | 自动候选评审、集合评审与已认证返工分支 |
| `automatic_review.py::claim_windows` | 装配有界评审用例和原检查点请求适配器，不在组合根循环或打分 |
| `automatic_review.py::training_format_lineage` | 装配训练视图认证用例及封存产物读取端口，发布去重仍使用原全局索引 |
| `document_corpus.py` | 文档语料用例与本地文本读取器 |
| `datasets.py` | 数据集预览、统计和交付 |
| `asset_catalog.py` | datasets 域的资料目录应用和文件系统读取器；默认目录与运行记录共用文件身份及版本校验 |
| `backends.py` | 模型配置 |
| `*_reviews.py`、`agent_review.py` | 人工审核 |
| `knowledge.py`、`prompt_library.py`、`local_inputs.py` | 知识库、提示词、持久输入缓存 |

组合根可以同时依赖 application 和 infrastructure，但不能增加业务判断。新适配器通过这里替换，避免在用例里写“如果使用某 SDK”的分支。构造对象与执行任务要保持明确区分。

证据策略 1 的后续返工核查继续使用 reasoning_checklist 组合根；Checklist 适配器转发已认证检查点与评审配置身份，不新增一套评审循环。
# 数值参考核验装配

`node_generation.cot_generator` 将 review API 的 `VerifyReferenceAnswer` 与 Math-Verify 适配器注入 CoT 用例。工作流不直接依赖另一业务域的实现，数学 SDK 不进入 domain。只对新版本保真改写中的显式数值参考执行核验。
