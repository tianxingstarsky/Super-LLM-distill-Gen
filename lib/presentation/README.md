# 展示层

[streamlit](streamlit/README.md) 承担桌面控制台页面。浏览器组件位于 [components](../components/README.md)。展示层把用户操作转换为用例调用，并呈现真实状态。

不在页面中定义生成策略、重复判定、审核准入或发布条件。页面不创建基础设施，应用对象从组合根注入。

代码整理不应改变界面路由和历史链接。确实修改 UI 时，遵循根目录 AGENTS.md，并打开真实页面验收。

`file_sizes.py` 统一控制台与 HTML 报告的大小展示，按 1,024 进位显示 B、KB、MB、GB 等单位。仅格式化文字；清单、统计下载和排序仍使用原始字节整数。

`training_cli.py` 提供可选的 `df train-validate` 参数与退出码。它只把参数交给 `RunTrainingValidation`，默认仅预检；模型加载、训练和文件持久化不在展示入口实现。独立脚本和环境说明见 [本地训练验证](../../docs/local-training-validation.md)。

`grpo_cli.py` 对应 `df train-grpo` / `RunGrpoValidation`，独立依赖和验证说明见
[本地 GRPO](../../docs/local-grpo-validation.md)。它不是生成工作流的新增默认阶段。
