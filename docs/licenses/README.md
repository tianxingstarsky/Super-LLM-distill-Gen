# 第三方资产来源与许可证

本目录保留已复制或改编资产的上游版本、改编范围及完整许可证，供维护和分发核对；不承接运行时实现。提示词入口是 `lib/prompts/workflow.py`，本目录没有代码依赖。

Windows 在线安装器随包保留 uv 的 [MIT](../../scripts/release/UV-LICENSE-MIT.txt) 和 [Apache-2.0](../../scripts/release/UV-LICENSE-APACHE.txt) 完整许可证。官方工具版本、来源与摘要在 `scripts/release/tools.json`，下载的 Python 与依赖包保留自身许可证元数据。

## DeepSeek Harness 铭牌标记

README 的集成铭牌使用 DeepSeek 官方 favicon 的原始路径，修改仅为在本项目设计的背景中定位和排版。来源固定为 [`f3803c9` 的 favicon](https://github.com/deepseek-ai/deepseek-harness/blob/f3803c96974ad4a94195fb97e5c637e9c2a3afe3/website/public/favicon.svg)，保留 [MIT 全文](deepseek-harness-MIT.txt)。名称和标记只说明独立社区适配关系，遵循上游[品牌说明](https://github.com/deepseek-ai/deepseek-harness/blob/f3803c96974ad4a94195fb97e5c637e9c2a3afe3/BRAND_GUIDELINES.md)，不表示官方背书或合作。

## ReasoningBank 经验提炼提示词

- 上游：[google-research/reasoning-bank](https://github.com/google-research/reasoning-bank)。
- 固定提交：`ed80611788292ea739f1effd31f16c53823b8a0d`。
- 原资产：[WebArena/prompts/memory_instruction.py](https://github.com/google-research/reasoning-bank/blob/ed80611788292ea739f1effd31f16c53823b8a0d/WebArena/prompts/memory_instruction.py) 中的 `SUCCESSFUL_SI`、`FAILED_SI`、`PARALLEL_SI`。
- 上游版权：Copyright 2026 Google LLC。
- 许可证：[Apache License 2.0 完整文本](reasoning-bank-APACHE-2.0.txt)。上游该提交未提供 `NOTICE` 文件。
- 改编位置：`lib/prompts/workflow_native_cot.py` 的 `EVOLVE_EXPERIENCE_BODY`，注册为 `workflow.evolve_experience@1.0.0`。
- 2026-10-27 的改动：把成功、失败与多次尝试对照提炼改为用户风格提案；保留少量、可操作、可迁移的经验原则，增加适用条件、例外、逐字证据、未知结果与用户偏好边界。输出改为一次严格工具提交。没有复制 WebArena、SWE-Bench 或上游模型运行时。

修改改编资产时保留相关版权、许可证、来源版本和显著修改说明。分发时一并提供完整许可证；后续引入其他第三方文件时检查其各自的声明。验证本目录只需核对来源、提交与许可证文本；提示词注册和渲染使用 `tests/test_prompts.py`，离线结构检查不证明提炼质量。
