# 上游组件

这里保存原始上游代码，不在组件目录修改产品业务。

| 组件 | 固定来源 | 实际用途 |
| --- | --- | --- |
| `reasoning-bank/` | Google Research 官方完整 Git 子模块，提交 `ed80611788292ea739f1effd31f16c53823b8a0d` | 原版提炼、同任务对照提炼、缓存向量检索和记忆选择 |
| `google-chrysalis/` | GoogleCloudPlatform/generative-ai 的完整 `agents/self-evolving-ai` 目录 | SRE 参考工程；没有冒充数据生成运行时。来源和逐文件摘要见旁边的 snapshot 文件 |
| `magpie/`、`opencua/` | 既有 Git 子模块 | 保持原接入范围 |

安装、使用、实际入口与限制见 [Google 原版接入](../docs/architecture/google-reasoning-bank-integration.md)。更新上游时同步核对摘要和许可证，不跟随 main 悄悄改变冻结任务。
