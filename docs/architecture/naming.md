# 命名与职责约定

名称应说明业务含义，而不仅是 Python 类型。改名不能破坏已保存的 JSON 字段、缓存身份、提示词版本、任务链接和公开导入合同。

## 数据名称

| 名称 | 含义 |
| --- | --- |
| `source` / `source_records` | 原始来源描述或可复用来源记录 |
| `generation_candidates` | 准备交给生成阶段的候选输入 |
| `training_record` | 可按目标训练格式投影的记录 |
| `review_decision` | 绑定候选及版本的审核决定 |
| `stage_result` / `stage_collections` | 某阶段或各阶段的结果集合 |
| `recipe` | 已锁定的运行配置，不等同于可编辑 UI 草稿 |
| `state` | 当前持久化运行状态；界面状态另称 `view_state` |
| `source_id` / `candidate_id` / `run_id` | 各自对象身份，不用一个 `id` 参数跨层含糊传递 |

局部循环索引可以简短；跨函数传递的数据不要仅叫 `data`、`obj`、`res`。多个值总是一起流转时使用有业务名的记录，例如 `PreparedBatch`、`DocumentSource`、`BatchOutcome`。

## 函数、类与文件

用例采用动作名：`ExecuteWorkflowRun`、`GenerateDatasetBatch`、`PrepareDocumentCorpus`。方法名应表达读、准备、生成、发布或决定，避免一个 `process()` 兼做多个阶段。

多轮生成用 `GenerateMultiturnDialogue`；批量调度用 `ExecuteProductionRounds`，一个窗口用 `GenerateProductionBatch`；自动处理分为 `ReviewCandidate`、`ReviewGeneratedCollections`、`ExecuteRepairBranch`。候选修复与生产补充是不同动作，不能共用一个含糊的 retry 接口。

领域值表达规则和事实，例如 `BatchGenerationPlan`、`TargetOutcome`。端口按使用方需要命名，例如 `RunSessionPort`、`BatchRuntimePort`、`DocumentTextPort`，不机械复制所有实现方法。

`DialogueProgress` 表示当前对话状态；`ProductionProgress` 表示从收据重建的生产状态；`ProductionRoundPlan` 表示下一窗口，`PreparedProductionRound` 表示已准备的流式输入。它们不取代现有持久化字典，也不改变字段名称。

适配器说明实际责任：`FilesystemRunSession` 管理运行会话，`CheckpointedBatchRuntime` 执行阶段，`LocalDocumentTextReader` 读取文档。既有 `Driver` / `Application` 公共名字继续兼容，避免无收益的全仓改名。

文件按完整职责拆分。应用层的 `generate_batch.py` 可以展示流程顺序；文件锁、字节哈希、SDK 类型留给适配器。不要用更多继承混入类代替明确的端口组合，不建立新的全能 Manager、Helper 或 Utils。

## 兼容与验证

旧模块只能转发到唯一实现。需要暂时保留旧类名时使用同一类的别名，不能复制另一份实现。旧脚本还要验证可执行入口，不能只验证 import。

结构重构先验证行为：清洗先于问答、CoT 在最终 SFT 投影前完成、偏好复用同一对结果、取消不重试、恢复不重做已核验工作、打包重新核验。替换文件适配器后，应用用例应能使用内存端口运行。

每个业务目录 README 描述职责、入口、允许依赖和验证位置。文档写当前事实，明确剩余旧代码；不要把计划迁移的内容描述成已经完成。
