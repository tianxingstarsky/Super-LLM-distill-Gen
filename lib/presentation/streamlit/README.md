# 桌面控制台页面

`workflow_page.py` 组织创建任务与画布；`workflow_node_settings.py` 处理节点配置；`task_management_page.py` 展示运行任务；`dataset_browser_page.py`、`dataset_preview_page.py`、`package_page.py` 展示资料与交付；审核页面调用各自审核应用。

`i18n.py` 保存语言转换。路由使用稳定内部标识，不能将英文显示文案用作页面字典键。持久化任务状态由应用层读取，Streamlit session state 只保留界面状态。

模型与提示词在节点上配置。避免新增重复的总配置页面。组件键包含内容版本时，测试必须使用真实组件键，不模拟已经失效的旧键。

验收使用 `ui_tests`，并检查真实桌面截图。不要把参考图中的数量、文件名或质量分写成产品数据。
