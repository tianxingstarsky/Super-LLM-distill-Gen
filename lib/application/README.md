# 旧应用层与兼容入口

这里仍保存知识库、人工增强、提示词库、输入缓存、发布和报告等尚未完整迁移的用例，以及已经迁移模块的导入转发。

`workflow_service.py` 是兼容组合门面，委托给运行、数据集和审核应用；不要继续增加跨业务职责。模型、SFT/CPT/偏好审核与训练转换的实际实现见 [contexts](../contexts/README.md)。

服务文件协调操作，端口文件声明消费方需要的能力。所有用例只依赖纯规则和端口，不能直接读取磁盘、创建模型客户端或导入页面。

`CreationDraftApplication.compare_update(changes, expected)` 用于显式保存节点规则。
存储端在同一锁内检查字段前置值后仅合并本次修改，返回完整已保存草稿；`expected`
中的 `None` 表示字段必须不存在。冲突抛出 `CreationDraftConflict`，不覆盖其他会话的
修改。旧的整表 `replace` 与增量 `update` 保持原语义。验证见 `test_creation_draft_storage.py`。

迁移一个功能时一起确认规则、用例、适配器、组合根和测试，不只移动文件。新实现只保留一份，旧路径使用兼容转发。
