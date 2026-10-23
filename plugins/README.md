# 独立外部适配包

[dsh-dataforge](dsh-dataforge/README.md) 独立适配 DeepSeek Harness。上游 SDK 变化在插件包内消化，主程序通过版本化控制协议提供调度与监控能力。

插件拥有自己的依赖、兼容声明、测试和发布周期。不要把其 SDK 安装进 Python 领域或应用层。修改主协议时同步兼容版本与双侧合同测试，不能只修改插件调用名。
