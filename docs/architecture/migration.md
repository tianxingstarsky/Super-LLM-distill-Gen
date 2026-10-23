# DDD 整理记录

本次迁移调整代码归属、依赖和装配。运行目录、配方版本、数据库结构、导出字段、审核事件、缓存键及 `dataforge.control` v1 保持原有合同。没有迁移或改写用户资料。

## 已处理的问题

| 原问题 | 本次处理 |
| --- | --- |
| 工作流驱动承接运行、数据、审核 | 拆成 `WorkflowRunPort`、`DatasetPort`、`AgentReviewPort`；独立用例和适配器，由组合根注入 |
| 数据预览、统计、打包导入生成引擎 | 提取运行记录和清单文件边界；数据集工厂可在执行引擎及模型 SDK 不可用时工作 |
| 模型领域配置依赖 API 报文序列化 | 协议类型校验归属模型领域，报文适配器反向使用它 |
| CPT 领域判定直接建 SQLite 表 | 内存判定保持纯领域，磁盘索引和重复簇计数迁入基础设施，复用判定过程 |
| 应用服务散落，职责难定位 | 将工作流核心规则、数据集交付、模型配置和四类人工审核迁入对应 context |
| 架构测试只看顶层文件和部分 import | 增加递归依赖、跨域接口、组合根和独立进程隔离测试 |
| 架构文档不断追加实现历史 | 主指南描述当前结构，历史细节单独保留 |

## 常用路径迁移

| 旧位置 | 实际实现 |
| --- | --- |
| `application/workflow_service.py` | 兼容组合入口；运行用例在 `contexts/workflows/application/runs.py` |
| `infrastructure/workflow_driver.py` | 兼容组合适配器；新装配使用各域适配器 |
| `domain/workflow_{creation,graph,targets,scale,production}.py` | `contexts/workflows/domain/` |
| `domain/workflow_delivery.py` | `contexts/datasets/domain/delivery.py` |
| `domain/sample_distribution.py` | `contexts/datasets/domain/distribution.py` |
| `domain/trainer_formats.py` | `contexts/datasets/domain/trainer_formats.py` |
| `infrastructure/workflow_{statistics,archive}.py` | `contexts/datasets/infrastructure/{statistics,archive}.py` |
| 模型配置用例、端口、存储 | `contexts/models/{application,infrastructure}/` |
| 模型能力、网络、推理、调度规则 | `contexts/models/domain/` |
| SFT、CPT、偏好审核用例及适配器 | `contexts/review/{application,infrastructure}/` |
| Agent 审核存储和事件处理 | `contexts/review/infrastructure/agent_review_driver.py` |
| `CorpusNearDuplicateIndex(sqlite_path)`、`CorpusClusterSizes` | `contexts/datasets/infrastructure/corpus_index.py` |

表内路径以 `lib/` 为基准。多数旧模块保持导入兼容；CPT 磁盘索引属于明确的内部接口迁移，调用方已调整。`lib.domain.corpus_quality.CorpusNearDuplicateIndex()` 继续提供纯内存算法，不接受文件路径。

## 保持的业务规则

- 重复检测仍是既有精确/保守近重复方法，不宣称语义上永不重复。
- 统计按原生记录计数，格式副本不重复累计；实际字节数与缓存版本绑定。
- 预览租约不能代替下载、打包和发布的完整性核验。
- 推理和训练记录投影保留既有字段，不通过删除推理使测试通过。
- 审核决定仍核对来源哈希，后台发布仍复核来源与锁状态。
- 外部 dsh 适配包继续独立安装和发布，主程序不引入其 SDK。

## 后续迁移边界

1. **生成执行**：普通批次、运行生命周期、多轮生成、生产轮次及评分修复已进入对应应用层；继续拆分 `TrainingWorkflowRuntime` 的单节点 SFT/CoT 生成和通用模型请求。运行时仍保留规划检查点、来源和发布支持钩子，不能向其重新加入已迁出的主循环。
2. **资料与知识**：文档纯规则、本地语料用例及 Docling 适配已归入 documents；资料库缓存和知识库的版本所有权仍需独立整理。不把 Docling 内部结构变成业务合同。
3. **人工增强**：会话、轮次、问题批次和返工状态需要独立的一致性边界，不能仅按页面名拆文件。
4. **旧入口退出**：第一方调用全部迁移、兼容窗口结束后删除转发模块；期间不向转发文件增加业务规则。

本次未引入消息中间件、微服务或统一大仓储。一致性继续由现有文件锁、SQLite 事务、不可变快照和哈希验证维护。

## 执行职责与可维护性整理

第二轮整理不只移动文件：

| 原责任 | 当前归属 |
| --- | --- |
| 巨型 `Workflow.execute` 中的运行控制 | `workflows/application/execute_run.py` |
| 普通批次的节点依赖与输出顺序 | `workflows/application/generate_batch.py` |
| 目标隐含依赖、结束后是否需要关注 | `workflows/domain/execution.py` |
| 文件锁、快照核验、检查点、错误落盘 | `workflows/infrastructure/execution_runtime.py` |
| 旧 `Workflow` 类 | 明确命名为 `TrainingWorkflowRuntime`，旧名字指向同一类 |
| `doc2corpus.py` 的业务流程与文件 I/O | `documents/application/prepare_corpus.py` 与 `documents/infrastructure/` |
| 文本分块、TeX 保留规则 | `documents/domain/text_chunks.py`、`documents/domain/latex.py` |
| 平铺的 38 个 `docling_*` 实现 | `documents/infrastructure/docling/` |

Docling 旧 import 路径保留模块身份，旧 worker 脚本保留执行入口。worker 的仓库根目录、全局解析锁和同目录入口路径按新位置调整，算法和策略版本保持原样。

批量取消改为显式 `WorkflowCancelled` 类型判断，不再依赖类名字符串。持久化状态仍使用原来的 `cancelled` 值。其他存储字段、统计口径、文本身份和导出字段未做迁移。

各维护目录补充 README，说明职责、真实入口、依赖和测试；复杂旧代码仍明确标记，不将剩余迁移描述为已完成。

## 多轮、生产轮次与评分修复

| 原职责 | 当前归属 |
| --- | --- |
| `TrainingWorkflowRuntime.multiturn/_multiturn` | `workflows/application/generate_dialogue.py`，旧方法只装配并调用 |
| 多轮来源记忆与请求执行 | `workflows/infrastructure/dialogue_adapter.py` |
| 生产窗口大小、覆盖和饱和决定 | `workflows/domain/production_rounds.py` |
| 生产主循环及最终核验补充 | `workflows/application/execute_production.py` |
| 单轮节点编排 | `workflows/application/generate_production_batch.py` |
| 来源索引、收据、轮次存储与执行钩子 | `workflows/infrastructure/production_{adapter,storage,batch,runtime}.py` |
| 候选评分修复循环 | `review/application/review_candidate.py` |
| 集合评审、已认证返工分支 | `review/application/{review_collections,execute_repair}.py` |
| 候选、人工编辑和推理历史规则 | `review/domain/{repair,candidate_content,reasoning_history,automatic_rules}.py` |
| 模型协议、风格解析与运行接入 | `review/infrastructure/{review_protocol,review_style,automatic_adapter,automatic_runtime}.py` |

此轮不升级配方、提示词、缓存或导出协议。`DialogueProgress` 和 `ProductionRoundPlan` 都是内存值，不引入数据库表和新的持久字段。跨域配置验证通过 review/contracts，生产去重索引由组合根注入，旧 import 继续指向唯一实现。
