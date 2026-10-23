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

1. **生成执行**：按阶段用例拆分 `training_workflow.py`，通过明确的状态和结果合同衔接，保持现有检查点与恢复合同。
2. **资料与知识**：先定义来源版本、解析版本和复用资料的所有权，再统一公开入口；不把 Docling 内部结构变成业务合同。
3. **人工增强**：会话、轮次、问题批次和返工状态需要独立的一致性边界，不能仅按页面名拆文件。
4. **旧入口退出**：第一方调用全部迁移、兼容窗口结束后删除转发模块；期间不向转发文件增加业务规则。

本次未引入消息中间件、微服务或统一大仓储。一致性继续由现有文件锁、SQLite 事务、不可变快照和哈希验证维护。
