# 独立外部适配包

[dsh-dataforge](dsh-dataforge/README.md) 独立适配 DeepSeek Harness。上游 SDK 变化在插件包内消化，主程序通过版本化控制协议提供调度与监控能力。

[codex-shujian](codex-shujian/README.md) 提供独立的 Codex skill 和可分发插件描述。安装器只复制自包含技能；控制辅助只序列化并调用既有协议，不带另一套业务调度。

统一安装目标与验证口径见 [Agent 安装说明](../docs/agent-setup.md)。DeepSeek Harness 负责其原生后台 jobs，Codex 使用其宿主终端会话；二者通过持久任务与共享模型准入协作。

插件拥有自己的依赖、兼容声明、测试和发布周期。不要把其 SDK 安装进 Python 领域或应用层。修改主协议时同步兼容版本与双侧合同测试，不能只修改插件调用名。
