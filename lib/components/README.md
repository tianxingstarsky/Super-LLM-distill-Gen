# 浏览器组件

- [editable_chat](editable_chat/README.md)：对话内容编辑。
- [review_workspace](review_workspace/README.md)：审核工作台布局与浏览器交互。
- [workflow_stream](workflow_stream/README.md)：节点请求的增量流式输出。

组件接收明确的视图数据和操作事件，不直接访问模型服务或任务文件。业务决定必须返回应用层校验。

修改消息协议时同步 Python 调用方与浏览器测试。保留来源身份和版本字段，避免用户在旧页面提交到新候选。
