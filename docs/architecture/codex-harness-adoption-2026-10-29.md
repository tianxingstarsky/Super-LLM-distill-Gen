# Agent 运行环境与 Codex 机制取舍

本次重点是让 Agent 清楚当前任务、证据范围、历史和可用动作。运行时只提供事实与边界，不规定推理必须经过几步，也不把“我已自查”当作质量通过。

## 模型能力与运行时分别负责什么

指令理解、角色区分、论证和纠错依赖模型训练与实际能力。Codex CLI 的开源代码可以说明如何组织环境、工具和历史，不能证明复制其提示词就能让 V4.1-Flash 获得相同能力。

运行时负责可确定的部分：装载哪些规则、模型看得到哪些资料、工具是否执行、响应是否保存、哪些步骤已完成。把这些状态交给模型猜测，会扩大重复调用与错误继承。节点自定义提示词、完整原生推理和现有质量条件仍保留。

## 核对到的可复用机制

Codex 源码按提交 `7f4d53e928d16ccbb47c00f0fdc39f6f16782437` 核对；OpenCode 与 ADK 的链接用于说明当次查到的机制，后续接入前仍须固定实际版本。

| 机制 | 上游依据 | 在本项目中的取舍 |
| --- | --- | --- |
| 环境状态增量 | Codex 的 [environments_instructions.rs](https://github.com/openai/codex/blob/7f4d53e928d16ccbb47c00f0fdc39f6f16782437/codex-rs/core/src/context/world_state/environments_instructions.rs) 根据前态决定是否输出环境指引；[updates.rs](https://github.com/openai/codex/blob/7f4d53e928d16ccbb47c00f0fdc39f6f16782437/codex-rs/core/src/context_manager/updates.rs) 按角色与显式消息边界合并更新。 | 状态由程序推导。稳定规则与变化的任务状态分开，避免反复贴全量约定；不能把不同角色全部拼成 user 正文。 |
| 工具回执与稳定身份 | Codex 的 [normalize.rs](https://github.com/openai/codex/blob/7f4d53e928d16ccbb47c00f0fdc39f6f16782437/codex-rs/core/src/context_manager/normalize.rs) 配对工具调用与返回；部分缺失返回补为 `aborted`，生成稳定的合成 ID，避免恢复时随意改变缓存前缀。 | 成功、失败、中断与未执行分别记录。恢复复用已有请求收据；缺少回执不能解释成执行成功。稳定前缀是缓存命中的条件之一，不是命中保证。 |
| 明确的规则来源与会话恢复 | [OpenAI 官方 AGENTS.md 文档](https://learn.chatgpt.com/docs/agent-configuration/agents-md) 描述按范围装载规则；[App Server 文档](https://learn.chatgpt.com/docs/app-server) 区分 thread、turn、item，并提供读取、恢复和分支。 | 继续用本项目的冻结配方、节点提示词版本和检查点。规则有来源、范围与版本；模型最终文本不能替代任务完成收据。 |
| 可见历史中的规则防重 | OpenCode 的 [instructions.ts](https://github.com/anomalyco/opencode/blob/dev/packages/core/src/session/instructions.ts) 从可见历史识别已注入路径，同轮并发读取另有临时 claim；压缩或回退后可重新装载丢失的规则。 | 后续长任务需要区分“磁盘保存过”和“当前模型仍看得到”。不能只用永久 loaded 标志，也不应每次盲目重贴全部经验。 |
| 有界工具纠错 | ADK 的 [Reflect and Retry](https://adk.dev/integrations/reflect-and-retry/) 向模型返回错误类型、细节和次数；OpenCode 的 [model-retry.ts](https://github.com/anomalyco/opencode/blob/dev/packages/core/src/model-retry.ts) 区分普通重试与可能再次计费的单次生成重试。 | 参数错误提供具体反馈；传输重试、局部修正与整条重生成保持不同职责。复用现有请求策略，不另叠一层通用重试循环。 |

## 本次实施范围

本次只在 `native_joint` 联合生成请求中加入极简执行状态。它由程序根据当前输入投影，说明：

- 当前用户回合与允许使用的资料范围；
- 历史助手发言尚未被认证为事实；
- 已有草稿只是待核对的提案；
- 当前调用没有可执行工具，不能声称刚刚进行了检索或实验。

这不是新增评审节点，也不要求模型套用固定的思考句式。状态随新配方标记冻结；旧任务不自动迁移。状态只用于模型请求，不增加训练导出字段，不删改原生推理，也不以状态声明放行样本。

该改动是明确环境的起点，不等于已经移植 Codex 的完整 Agent 循环、动态状态差分、工具系统或长任务压缩。本次没有引入另一套 Agent 框架依赖。

## 尚未接通与不可直接移植的部分

- Codex 原生 `/responses/compact` 依赖相应供应商协议和模型支持，不能直接搬给 V4.1-Flash。可借鉴“近期原始历史加较早工作摘要”，但摘要不能替代来源原文、完整推理、用户原话与持久收据。ADK 的 [上下文压缩](https://adk.dev/context/compaction/) 也是可配置且有损的机制，不是准确性保证。
- reading 长任务的状态增量、规则可见性、防重复调用与按主题工作记忆尚待单独接入。不能因本次 `native_joint` 有执行状态，就宣称全部生成路径已覆盖。
- 失败与修正的自动收集、待吸收经验、版本提炼和可选旧稿返工尚未形成完整生产闭环。沿用[经验进化分析](google-evolving-agents-2026-10-27.md)中的缺口，不将已有实验或候选版本冒充自动进化已完成。

## 验证边界

本次应验证状态来自实际输入、历史未变成事实、无工具时不伪造能力、标记能冻结并恢复，以及旧任务与训练投影保持原样。相关代码的定向回归由实现记录给出；本说明本身不新增付费实验或全仓回归。

本次冻结标记为 `native_environment_version=1`，请求块为 `<native_execution_state>`，旁路位于 `native_generation.execution_state`。实际入口为 `GenerateNativeCot.execute` → `native_cot_messages`。受影响的原生节点、DDD、节点请求和资料对话回归共 52 项通过。

随后复用既有第 07 条多轮业务历史，新建任务只用 V4.1-Flash 发起一次真实生成，耗时 10.18 秒；第三个用户回合、未核实助手历史、待修订旧稿等状态确已进入请求。原生推理与答案逐字保留，私有状态标记未出现在正文，未增加独立评审。人工直接阅读看到最终答案明确要求实际观测、没有把对照设计本身写成已证明提升；仍有额外背景与中英互释不足。完整记录与检查保存在本地忽略目录 `.dataforge/release-check-20261029/`，未冒充独立评审通过。

离线合同通过只能说明状态传递与兼容行为。单个已知场景也不是受控 A/B 或可用率估计。是否减少历史错误继承、越界结论和重复调用，还需同输入、同 V4.1-Flash 的进一步对照；本次没有据此宣称语义质量已提高。
