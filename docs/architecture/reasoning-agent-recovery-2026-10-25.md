# 核查 Agent 的工具恢复与证据约束

613 次请求、3/10 流程通过的实验暴露了 schema 空泛、缺少字段诊断、重复检测只看单轮、否定语句被误当断言、自检擅自豁免语言要求等问题。本次不降低原有引用、覆盖、单位、评分或风格要求。

## OpenCode 对照

官方提交 `e0b6039e1f26b753c07d690f6ea0ba7035e72c9d`：

- [tool/runtime.ts](https://github.com/anomalyco/opencode/blob/e0b6039e1f26b753c07d690f6ea0ba7035e72c9d/packages/core/src/tool/runtime.ts)：完整输入 schema、字段路径与具体纠错反馈。
- [plugin/tool-input-repair.ts](https://github.com/anomalyco/opencode/blob/e0b6039e1f26b753c07d690f6ea0ba7035e72c9d/packages/core/src/plugin/tool-input-repair.ts)：基于 schema 的无歧义归一化。
- [session/runner/step.ts](https://github.com/anomalyco/opencode/blob/e0b6039e1f26b753c07d690f6ea0ba7035e72c9d/packages/core/src/session/runner/step.ts)：工具局部失败与模型请求失败分别结算。

使用项目已有 jsonschema，没有嵌入 OpenCode 运行时。工具输入采用扁平字段，旧外层对象仅做无损拆包，不删除未知字段，不补证据，不改数字、单位或判定。

## 新协议

清单版本 4、配方 26、Agent 模板 1.2.0 是显式新协议。版本 1/2/3 的任务和缓存不迁移；新会话键为 investigation_v2。训练导出不变，新证据进入旁路收据。

Domain 的 reasoning_tool_contracts.py 提供完整 schema；reasoning_evidence.py 复用原校验并增加：批量提交逐项保留；observations 逐字绑定并覆盖要求；已报告 violated 不能被 met 覆盖；unknown 待复核；interpretation 绑定完整定位句及语态，否定或纯引用不能冒充作者断言的计算。混合语句核对真实正面主张，not_claim 不免除全稿规则。小来源一次完整提供，大来源仍按需读取；相同本地操作复用收据，训练推理与原生工具推理不删减。

Application 的 reasoning_session.py 按证据目标累计跨回合错误。改措辞、混入搜索或换成批量提交不能无限重试。无命中检索不是进展，故障上限只能停止为未完成，不判正文错误、不无依据返工、不放行。

Chat 请求可显式开启坏参数反馈：损坏参数原样留存，包装成不可执行的错误输入，配对 tool 消息请求同会话修正。原生推理保留，其他签名协议不改写。明确输出上限使用 ModelOutputLimitError，不当普通断线重买相同请求。

## 边界和验证

程序能阻止已报告缺口被覆盖，不能证明模型没有漏报。作者与独立评审仍是不同会话，后者不接收作者通过意见。改稿后仍完整检查，不复用过期语义判断。减少的是工具往返，不取消核验。

验证：tests/test_reasoning_agent_recovery.py、既有 reasoning、模型工具协议、DDD 和洋葱架构回归。真实试验在离线验证后固定 V4.1-Flash、同十题、来源、生成模板、七项规则、输出上限及并发，工具协议与核查模板变化单独记录。流程通过率不等同真实正确率。

真实试跑发现多层 JSON 易损坏，1.2.0 改为扁平记录；1.1.0 逐字保留。损坏 JSON 返回行列和字符位置，不执行损坏参数。已有完整生成可在请求合同与模型绑定逐字一致时复用，评审必须重做。作者与独立评审的负面意见若不重合，保留争议不强迫改稿，也不放行。

工具领域错误同样携带失败的 rule_id/anchor_id、冻结定义、已保存记录与待办 ID，避免模型把数组中另一项当成失败项。有效负面观察即使有损坏的同批兄弟项，也立即留下不可覆盖的缺陷记录。

计数占比等真实样本暴露 reviewer 的 operand_scale 与 metric_kind 填写冲突。版本 4 将该输入类型冲突、未绑定操作数等列为局部恢复，原数值/单位错误仍是负面结果；所有校验函数保持原判定，未通过的工具记录不能提交。规则引文错误返回逐字差异，不自动更改原规则或候选证据。

## 当前实测边界

最终十题实验因上游 `service_quota_exhausted` 中断：一条隔离、九条额度错误，四份完整候选稿留存。不能用未完成结果计算十题通过率或宣称成本改进。第 1 题的 50.6%／不到一半矛盾仍未被两路评审一致认定；争议留存，不覆盖放行。程序不等于独立事实真值。

实验包装器曾把额度异常吞成空结果，因此继续派发了被拒请求；现已在实验 SDK 入口设共享停止，并将致命异常重新抛给现有生产调度。该包装器的四项离线回归覆盖全局停止、并发待发请求、首个故障保留及非致命局部隔离。实验断点清单只列九条空外层检查点，完整生成与工具缓存保留。此修正不改既有任务或训练导出。
