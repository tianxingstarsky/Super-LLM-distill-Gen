# 项目协作约定

- UI 设计参考统一放在仓库根目录的 `参考/`。修改控制台页面时，先查看相关样板图片，再检查实际页面在浏览器中的效果。
- 样板用于视觉层级、布局和交互方式参考；图中的任务数、质量分、日期与文件名是示意数据。页面必须展示真实工作区状态，不得把示意数据写入产品逻辑。
- 主要视觉语言是浅蓝白色工作台、紧凑侧栏、页首横幅、清晰卡片、阶段进度和可读的样本预览。生成、审核、打包页之间保持一致的术语与状态。
- 当前只面向桌面端设计和验收，不开展手机端适配或窄屏验收，除非用户后续明确要求。

# DDD 与依赖边界

- 新的工作流、数据集、审核、模型配置和文档处理逻辑放在 `lib/contexts/<业务域>/`。先看 `docs/architecture.md` 确认归属；不要继续向旧的全能工作流接口追加职责。
- 每个业务域分为 `domain`、`application`、`infrastructure`。跨域用例只走 `api.py`，共享的纯业务合同走 `contracts.py`；具体实现统一在 `lib/bootstrap/` 装配。
- Domain 不读写文件、数据库，不调用网络或模型 SDK。存储与运行时实现放在 Infrastructure。兼容导入文件只转发，不能增加第二份业务实现。
- 调整依赖时运行 `tests/test_ddd_architecture.py` 和对应业务回归。重构不得改变既有任务、缓存、审核记录与导出字段；涉及格式变化必须单独设计版本迁移。
- 修改代码前看所在目录 README；新增维护目录同时写清职责、入口、依赖和验证。命名遵循 `docs/architecture/naming.md`，区分来源、候选、训练记录、审核决定，避免跨层使用含糊的 data/result。
- 普通批次编排归 `ExecuteWorkflowRun` / `GenerateDatasetBatch`；旧 `TrainingWorkflowRuntime` 只承接尚未拆分的节点算法。取消使用显式异常类型，不能比较类名字符串。批次端口保持流式记录，不能因重构将完整数据集转成列表。
- 多轮编排归 `GenerateMultiturnDialogue`；生产调度归 `ExecuteProductionRounds` / `GenerateProductionBatch`；自动评审与返工归 review 域的 `ReviewCandidate` / `ReviewGeneratedCollections` / `ExecuteRepairBranch`。通过组合根接入，不能将主循环重新塞回兼容运行时。保存收据成功后才能推进轮次；认证全部父版本后才能开始返工请求。
