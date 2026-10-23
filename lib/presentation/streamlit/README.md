# 桌面控制台页面

`workflow_page.py` 组织创建任务与画布；`workflow_node_settings.py` 处理节点配置；`task_management_page.py` 展示运行任务；`dataset_browser_page.py`、`dataset_preview_page.py`、`package_page.py` 展示资料与交付；审核页面调用各自审核应用。

`i18n.py` 保存语言转换。路由使用稳定内部标识，不能将英文显示文案用作页面字典键。持久化任务状态由应用层读取，Streamlit session state 只保留界面状态。

模型与提示词在节点上配置。避免新增重复的总配置页面。组件键包含内容版本时，测试必须使用真实组件键，不模拟已经失效的旧键。

`conversation_import_controls.py` 提供 DSH 会话上传、预览、问题提示、重试和确认选择。页面只保存界面状态，不自行解析数据库；已确认文件通过输入缓存跨会话复用。插件提醒使用静态翻译，不直接回显任意插件错误。交互回归见 `ui_tests/test_conversation_import_ui.py`。

验收使用 `ui_tests`，并检查真实桌面截图。不要把参考图中的数量、文件名或质量分写成产品数据。

任务报告的审核、评分修正和导出沿用同一任务上下文；返回任务需恢复原报告或运行视图。`review_rework_controls.py` 的主操作在全部保存成功后进入返工，已存集合从应用查询，不以浏览器会话中的旧数量判断。保存不等于启动模型。

`workflow_page.py` 将自动生成和人工增强的入口选择保存在既有草稿的模式作用域中；冻结运行配方不变。图片解析的阻断原因与启动条件共用同一判断，并提供输入节点跳转。仅当全部选中来源受当前 DSH 确认回执覆盖时，才免去不会执行的指导员配置；混合或未知来源沿用原流程。无变化的重绘不重复保存草稿，失败后仍重试完整草稿。回归见 `ui_tests/test_workflow_operation_logic.py`。

`workflow_report_panel.py`、`review_navigation.py` 负责报告、审核、修正与交付之间的任务定位，保存阅读类型和页码，操作时重新校验产物；现成 ZIP 优先提供下载。回归见 `ui_tests/test_result_actions_ui.py`、`test_package_page.py` 与 `tests/test_review_navigation.py`。
