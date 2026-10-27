# 按疑点推进的核查会话

## 已观察到的问题

原版本把一次评审视为完整 JSON 表格：长稿按六个主张分批，每批重复完整正文和来源，首批还要输出全部全局规则。一个字段或一条引文有误就修整张表；实质否定和表格不完整又容易被合并成同一个覆盖缺项。这会增加请求、上下文和返工，不能仅用更长提示词解决。

## OpenCode 源码对照

2026-10-25 检查官方 `anomalyco/opencode` 的 `dev`，固定提交 `2dc8db4a9da777dc03aec112aec891d5bd79211d`，没有将可变网页说明当成已集成的代码。

- [run-coordinator.ts](https://github.com/anomalyco/opencode/blob/2dc8db4a9da777dc03aec112aec891d5bd79211d/packages/core/src/session/run-coordinator.ts)：按会话键串行，同键唤醒合并，独立键可并行；中断与清理完成分开。
- [runner/step.ts](https://github.com/anomalyco/opencode/blob/2dc8db4a9da777dc03aec112aec891d5bd79211d/packages/core/src/session/runner/step.ts)：模型流、工具执行与事件落地分别处理，保留完成、继续、溢出恢复和失败的区别。
- [inbox.ts](https://github.com/anomalyco/opencode/blob/2dc8db4a9da777dc03aec112aec891d5bd79211d/packages/core/src/session/inbox.ts)：持久待办区分 steer 与 queue，在步骤边界消费输入。
- [compaction.ts](https://github.com/anomalyco/opencode/blob/2dc8db4a9da777dc03aec112aec891d5bd79211d/packages/core/src/session/compaction.ts)：上下文管理独立于业务结果，不把摘要当原始证据。

本次借鉴持续会话、工具反馈、增量提交和请求重放。没有安装 OpenCode 运行时，没有复制它的 TypeScript 协调器，也没有宣称已实现其全部实时调度能力。

## 当前实现

清单版本 3 / 配方 25 使用 `InvestigateReasoning`。完整任务、正文和可编辑规则进入当前会话，资料以目录呈现；Agent 自主搜索并读取所需原文，调用确定性计算工具，再逐项提交规则和主张证据。没有固定工具顺序，不要求每一步重交完整表格。

`ReasoningInvestigation` 是纯领域工作区。已有严格引用、规则覆盖、数值单位和范围字段校验继续生效。错误工具参数返回局部错误，已提交证据不消失。有效提交不可被后续相反投票覆盖；实质缺陷停止当前会话，交给原有生成返工逻辑。搜索无结果、工具次数用尽、无进展和中断均不能通过。

作者与独立评审使用不同会话，后者不接收作者的正面评语。模型原生工具状态保留，工具结果使用 `tool` 角色。适配器复用模型域的并发、排队、冷却和用量统计，每个模型回合有检查点；恢复时重放本地工具以重建工作区，不重新购买已有模型回执。

所有旁路核查记录与训练正文分离。新模板仍在 CoT 节点编辑和恢复默认。复制历史任务保留原检查协议，版本 1/2 的提示词、任务、缓存与导出格式不迁移。

按需来源读取复用既有认证缓存与行范围投影：选择片段时不越界，整份资料保留完整行号视图。过期焦点文本不能替代原文。未完成调查保留稿件和证据，只有实质缺陷进入正文修正；更早的未完成收据不能阻断本次已确认缺陷的修正。

计算工具字段或引用格式错误返回未提交项，允许就地修正，不伪造正文错误。`uncertain` 表示调查依据尚未解决，单独停止为待复核；不会据此重写正文，也不能获得通过。确定性复算不符和有证据的实质缺陷继续保留负面结论。

## 边界与后续验收

本次不是对正确率或速度的实测保证。新工具会话先用本地替身验证任意顺序、局部纠错、断点、取消、负面意见不可重抽及独立评审隔离，再做产品真实页面检查。已有十条真实实验仍属于旧调度，不能拿它们冒充新路径验证。

资源上限是故障保护，不是固定业务步骤。当前不压缩正在使用的证据或原生工具链，不自动复用改稿前的语义判断。跨稿件依赖失效、业务级并行子任务、用户实时插入核查任务和上下文压缩尚未在本次实现；不能将更改单位后其它结论仍然有效视为未经验证的假设。

测试入口：`tests/test_reasoning_investigation.py`、主张/清单回归、`tests/test_ddd_architecture.py` 和 `tests/test_onion_architecture.py`。真实模型仍限定 V4.1-Flash。本次设计调整期间不追加付费试跑。
