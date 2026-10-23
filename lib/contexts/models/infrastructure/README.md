# 模型配置存储

`configuration.py` 实现配置端口，读写本地服务配置。能力发现和实际连通测试仍在 `lib/infrastructure/model_discovery.py` 与 `model_capability_probe.py`。

不要在配置读取时发起生成请求。密钥只留在服务端配置或受控环境中，不进入任务导出、报表和错误文本。

新增供应商连接通过现有协议能力适配，避免让 application 直接依赖供应商 SDK。测试应使用临时配置目录与模拟响应。
