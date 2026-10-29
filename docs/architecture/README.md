# 架构维护记录

Agent 与模型节点的唯一提示词归属、实际用例、状态和共享设施见 [功能维护导航](agent-features.md)。`workflow.py` 已改为显式兼容转发，全部既有模板版本和值按注册表快照核对；本次不修改运行时循环。

推理时利用用户发言示范、角色边界，以及 NVIDIA User Sim / Personas / Chat-v3 的采用范围见 [用户模拟与共建底座](user-simulation-2026-10-29.md)。当前只更新可编辑模板，没有引入另一套调度、训练模型或下载数据集。

Codex、OpenCode 与 ADK 的状态、工具反馈和恢复机制取舍见 [Agent 运行环境适配](codex-harness-adoption-2026-10-29.md)，其中明确本次 `native_joint` 的实施范围与尚未接通的长任务、经验闭环。

可使用的初始风格、DSH 轨迹分组、个人版本与操作历史整理见 [简洁全面与个人进化](experience-evolution-initial-2026-10-27.md)。

Google Chrysalis / ReasoningBank 的实际代码核对、依赖取舍与经验投影缺口见 [自进化 Agent 适配分析](google-evolving-agents-2026-10-27.md)。本次尚未引入框架依赖。

NeMo / OpenThoughts 结果互通、保真风格改写与依赖取舍见 [CoT 风格转换](cot-style-transfer-2026-10-27.md)。

数值参考验证、局部编辑与生成/评审资料范围对齐见 [CoT 开源机制接入](cot-open-source-adoption-2026-10-27.md)。

通用能力的开源替换、依赖版本、保留合同与离线验证见 [运行时复用](open-source-runtime-2026-10-26.md)。

当前设计见上级 [architecture.md](../architecture.md)。本目录保存 [命名约定](naming.md)、[迁移边界](migration.md) 和独立验证记录。

`implementation-notes.md` 是保留的历史实现资料，不是新增代码的默认设计。验证报告应注明代码范围、环境、失败修复、跳过项和实际测试结果。

执行流程迁移见 [多轮、生产轮次与自动评审验证](pipeline-verification-2026-10-23.md)。其中区分真实历史文件核验、冻结实现对比、离线端口测试和完整回归，不把内存替身测试写成实际百万数据生成结果。
