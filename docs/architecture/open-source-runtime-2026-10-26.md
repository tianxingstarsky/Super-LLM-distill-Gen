# 开源组件取舍与验证

目标是减少已确认的重复维护，不是消除所有项目代码。本轮没有调用模型 API，没有修改提示词、质量阈值、任务配方、资料缓存格式或训练导出字段。

## 当前保留的改动

| 边界 | 组件与实际收益 |
| --- | --- |
| 请求重试 | Tenacity 统一三条请求通道的执行与等待；业务层继续决定哪些错误可以重试。取消、保存失败和结构错误不自动重试，等待发生在释放并发槽和结算之后。 |
| 远端知识库 | 官方 Qdrant client 负责报文和协议类型；OpenAI SDK 负责 embedding 请求。保留维度、命名向量、来源、响应体大小和重定向限制，SDK 内部重试关闭。大小限制使用 HTTPX response hook，保留 SDK 默认代理选择，避免自定义 transport 关闭环境代理。 |
| 可选追踪 | Langfuse observation API 替换失效的旧 trace API。追踪失败不会触发模型重跑或改变业务结果。 |
| 旧 JSON 正文兼容 | 标准库 JSONDecoder 替换手写括号与转义扫描。严格工具提交和语义校验保持原样。 |

直接依赖固定为 Tenacity 9.2.1、Langfuse 4.15.1、Qdrant client 1.20.0、OpenAI 3.7.0、Anthropic 1.11.0。已有 Docling、PDFium、pypdf、pylatexenc、jsonschema 等继续使用。

请求失败边界进一步采用 SDK 的真实异常类型和 HTTPX 响应头对象，避免按类名猜测。参考 [OpenCode 重试职责划分](https://github.com/anomalyco/opencode/blob/ed74ec82ce1fe3a36da6c0815a935473ef4bb0fb/packages/core/src/session/runner/retry.ts) 及 [OpenAI SDK](https://github.com/openai/openai-python/blob/main/src/openai/_base_client.py)、[Anthropic SDK](https://github.com/anthropics/anthropic-sdk-python/blob/main/src/anthropic/_base_client.py) 的上游信号处理，补齐禁止重试及毫秒等待信息；没有调用 SDK 私有方法，也没有同时开启 SDK 内部重试。项目保留原次数上限和最长等待约束；上游允许重试不能覆盖额度不足、认证失败等终止条件。

## 撤回不合适的框架包装

新增 LangGraph 曾只将模型调用和工具处理包装成两个节点，未使用它的持久化后端。检查点、恢复和业务判断仍由原代码完成，收益不足以抵消新增依赖和转发层。

已撤回该包装及其专用端口、装配和测试，从改动前快照恢复 15 个业务与测试文件。LangGraph 和 langchain-core 不再是项目直接依赖。既有用例的调度、工具记录和认证检查点保持原实现；未把回退伪装成新的调度能力。

撤回内容及早期对照记录保存在本地 `.dataforge/oss-migration/`，仅用于追溯。早期五份记录的调度对照不代表当前框架接入，也不证明模型质量。

## OpenCode 的实际做法与本项目的对应选择

本次核对官方 dev 提交 `ed74ec82ce1fe3a36da6c0815a935473ef4bb0fb`：

- [仓库约定](https://github.com/anomalyco/opencode/blob/ed74ec82ce1fe3a36da6c0815a935473ef4bb0fb/AGENTS.md) 强调边界校验、避免提前抽象、测试实际实现和按包验证。这里采用适合 Python 项目的原则，不照搬 Bun、TypeScript 或其目录规则。
- [会话执行](https://github.com/anomalyco/opencode/blob/ed74ec82ce1fe3a36da6c0815a935473ef4bb0fb/packages/core/src/session/runner/llm.ts) 将模型传输、历史、压缩和步骤恢复分别交给明确服务。它自己保留会话协调；本项目也保留任务、质量判定和缓存等业务责任。
- [规则加载](https://github.com/anomalyco/opencode/blob/ed74ec82ce1fe3a36da6c0815a935473ef4bb0fb/packages/core/src/session/instructions.ts) 根据可见历史记录已注入的路径，并处理并发读取。值得借鉴的是来源与状态清楚，而不是把所有规则反复塞进提示词。
- [单元测试实践](https://github.com/anomalyco/opencode/blob/ed74ec82ce1fe3a36da6c0815a935473ef4bb0fb/.opencode/skills/unit-tests/SKILL.md) 明确使用受影响范围运行，避免真实等待和昂贵的重复进程启动。这不意味着取消必要的业务验证。

按[官方规则文档](https://opencode.ai/docs/rules/)，OpenCode 读取项目根目录的 `AGENTS.md`；个人全局规则放在 `~/.config/opencode/AGENTS.md`，其他文件可通过 `opencode.json` 的 `instructions` 引用。本项目已有根规则，本次就地补充“改动与验证尺度”，不安装另一套全局约定。

## 验证边界

已完成的定向验证包括模型请求、知识库协议和 SDK 故障隔离。它们只证明所覆盖的程序行为，不证明线上吞吐或生成质量。

曾启动的全仓回归已按用户要求停止，不能报告为全量通过。回退后 DDD 边界与生成来源查询 48 项通过。SDK 传输简化另验证知识库协议与响应上限；没有扩展全仓测试。

本轮未修改现有任务、缓存或导出数据，也未执行付费模型请求。
